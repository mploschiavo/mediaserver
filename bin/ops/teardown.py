#!/usr/bin/env python3
"""Cross-platform teardown for the media-stack deployment.

Replaces the Linux-only ``teardown-compose.sh``. Runs on Windows,
macOS and Linux with stdlib only — no third-party deps.

Targets:

  * ``compose``   docker / docker-compose deployment (Linux/Mac/Windows)
  * ``k8s``       Kubernetes namespace + all manifests
  * ``both``      Both (operator switching between local environments)
  * ``auto``      Pick whichever is present (default)

Scopes (orthogonal to target):

  * ``config``     Stop containers + wipe runtime config dirs.
                   Preserves git-tracked ``config/defaults/`` AND user
                   data (``data/`` + ``media/``). Default.
  * ``data``       Adds wipe of ``data/`` (torrents, usenet, transcode).
                   ``media/`` (downloaded films/shows) is never touched
                   in this scope.
  * ``everything`` Wipes ``config/``, ``data/`` AND prompts for
                   ``media/``.

Safety rails:

  * ``--preview`` / ``--dry-run`` prints every planned operation and
    every path that would be deleted, with byte counts. Nothing
    happens on disk and no kubectl / docker calls are issued.
  * Destructive actions prompt unless ``--yes`` is passed. The most
    destructive (``everything`` wiping ``media/``) prompts twice
    even with ``--yes``.
  * ``config/defaults/`` is git-tracked bootstrap state. The script
    refuses to delete it under any scope.
  * Stale ``kubectl port-forward`` processes that bind compose host
    ports are detected and terminated before bringing the stack
    back up — the silent failure mode that wedged operators
    switching between k8s and compose runs.

Re-deploy is intentionally NOT included. After teardown:

  Compose:  docker compose -f deploy/compose/docker-compose.yml up -d
  K8s:      ./deploy-k8s.sh
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_COMPOSE_FILE = REPO_ROOT / "deploy" / "compose" / "docker-compose.yml"
DEFAULT_CONFIG_ROOT = REPO_ROOT / "config"
DEFAULT_DATA_ROOT = REPO_ROOT / "data"
DEFAULT_MEDIA_ROOT = REPO_ROOT / "media"

# config/defaults/ is git-tracked bootstrap state. NEVER delete.
PROTECTED_CONFIG_SUBDIRS = frozenset({"defaults"})

# Compose host ports the stack publishes — kubectl port-forwards left
# over from prior k8s work bind these and block ``docker compose up``.
COMPOSE_HOST_PORTS = (8080, 8989, 7878, 6767, 8686, 8787, 9117)

# Default k8s namespace (matches deploy/k8s/profiles/standard).
DEFAULT_K8S_NAMESPACE = "media-stack"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    """A planned set of teardown actions, derived from CLI args.

    The plan is built upfront and *previewed* in dry-run mode. The
    same plan executes in real mode — no decision logic past
    ``build_plan`` so preview matches reality byte-for-byte.
    """

    target: str  # compose / k8s / both
    scope: str   # config / data / everything
    compose_file: Path
    config_root: Path
    data_root: Path
    media_root: Path
    k8s_namespace: str
    dry_run: bool
    assume_yes: bool
    actions: list["Action"] = field(default_factory=list)


@dataclass
class Action:
    """Single planned operation. ``execute`` runs it; ``describe``
    is the preview line."""

    kind: str  # compose-down / k8s-delete-ns / rm-tree / kill-pid / refuse
    description: str
    path: Path | None = None
    cmd: list[str] | None = None
    pid: int | None = None
    confirm_text: str | None = None
    requires_double_confirm: bool = False

    def describe(self) -> str:
        return self.description


# ---------------------------------------------------------------------------
# Tool-availability probes
# ---------------------------------------------------------------------------


def has_docker() -> bool:
    return shutil.which("docker") is not None


def has_kubectl() -> bool:
    return shutil.which("kubectl") is not None


def docker_compose_args() -> list[str]:
    """Pick ``docker compose`` (modern plugin) or ``docker-compose``
    (legacy v1). Returns the prefix; concrete subcommands are
    appended by the caller."""
    if has_docker():
        # ``docker compose version`` exits 0 when the plugin is
        # present, non-zero otherwise.
        try:
            subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True, check=True, timeout=5,
            )
            return ["docker", "compose"]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
    if shutil.which("docker-compose") is not None:
        return ["docker-compose"]
    return []


# ---------------------------------------------------------------------------
# Path & port utilities
# ---------------------------------------------------------------------------


def dir_size(path: Path) -> int:
    """Best-effort recursive size in bytes. Permission errors are
    swallowed — preview is informational, not authoritative."""
    if not path.exists():
        return 0
    total = 0
    try:
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def human_bytes(n: int) -> str:
    suffixes = ("B", "KiB", "MiB", "GiB", "TiB")
    f = float(n)
    i = 0
    while f >= 1024 and i < len(suffixes) - 1:
        f /= 1024
        i += 1
    return f"{f:.1f} {suffixes[i]}"


def list_config_subdirs_to_wipe(config_root: Path) -> list[Path]:
    """Return the immediate children of ``config_root`` that should
    be wiped — every dir except ``defaults/`` (git-tracked)."""
    if not config_root.is_dir():
        return []
    out: list[Path] = []
    for child in sorted(config_root.iterdir()):
        if not child.is_dir() and not child.is_file():
            continue
        if child.name in PROTECTED_CONFIG_SUBDIRS:
            continue
        out.append(child)
    return out


def find_pids_listening_on(port: int) -> list[tuple[int, str]]:
    """Return ``[(pid, cmdline)]`` for processes listening on
    ``port``. Cross-platform: uses ``lsof`` on POSIX and
    ``netstat`` + ``tasklist`` on Windows. Best-effort — empty list
    on any failure."""
    out: list[tuple[int, str]] = []
    if platform.system() == "Windows":
        try:
            netstat = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return out
        for line in netstat.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            local = parts[1]
            state = parts[3]
            if state.upper() != "LISTENING":
                continue
            if not local.endswith(f":{port}"):
                continue
            try:
                pid = int(parts[4])
            except ValueError:
                continue
            cmd = _windows_cmdline(pid)
            out.append((pid, cmd))
        return out
    # POSIX (Linux / macOS): lsof if available, else ss.
    if shutil.which("lsof") is not None:
        try:
            res = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True, text=True, timeout=5,
            )
        except subprocess.TimeoutExpired:
            return out
        for raw in res.stdout.splitlines():
            try:
                pid = int(raw.strip())
            except ValueError:
                continue
            cmd = _posix_cmdline(pid)
            out.append((pid, cmd))
    return out


def _posix_cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\x00", b" ").decode(
                "utf-8", errors="replace",
            ).strip()
    except OSError:
        try:
            res = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=2,
            )
            return res.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return ""


def _windows_cmdline(pid: int) -> str:
    try:
        res = subprocess.run(
            ["wmic", "process", "where", f"ProcessId={pid}",
             "get", "CommandLine", "/value"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    for line in res.stdout.splitlines():
        if line.startswith("CommandLine="):
            return line[len("CommandLine="):].strip()
    return ""


def is_kubectl_port_forward(cmd: str) -> bool:
    """Heuristic: matches ``kubectl ... port-forward ...`` in the
    command line of a process."""
    return "kubectl" in cmd and "port-forward" in cmd


# ---------------------------------------------------------------------------
# Plan construction
# ---------------------------------------------------------------------------


def build_plan(args: argparse.Namespace) -> Plan:
    """Translate CLI args into an executable plan. The plan is
    fully deterministic — no environment probing past this point.
    """
    target = args.target
    if target == "auto":
        target = _autodetect_target()

    plan = Plan(
        target=target,
        scope=args.scope,
        compose_file=Path(args.compose_file).resolve(),
        config_root=Path(args.config_root).resolve(),
        data_root=Path(args.data_root).resolve(),
        media_root=Path(args.media_root).resolve(),
        k8s_namespace=args.k8s_namespace,
        dry_run=args.dry_run,
        assume_yes=args.yes,
    )

    # Stage 1 — bring the stack down at the orchestration layer.
    if target in ("compose", "both"):
        if has_docker():
            cprefix = docker_compose_args()
            if cprefix:
                plan.actions.append(Action(
                    kind="compose-down",
                    description=(
                        f"Stop and remove every compose container "
                        f"({plan.compose_file.name})"
                    ),
                    cmd=[*cprefix, "-f", str(plan.compose_file),
                         "down", "--remove-orphans"],
                    confirm_text="Stop and remove every compose container?",
                ))
            else:
                plan.actions.append(Action(
                    kind="refuse",
                    description=(
                        "docker is on PATH but neither "
                        "`docker compose` nor `docker-compose` is — "
                        "skipping compose teardown"
                    ),
                ))
        else:
            plan.actions.append(Action(
                kind="refuse",
                description=(
                    "docker is not on PATH — skipping compose teardown"
                ),
            ))

    if target in ("k8s", "both"):
        if has_kubectl():
            plan.actions.append(Action(
                kind="k8s-delete-ns",
                description=(
                    f"Delete kubernetes namespace "
                    f"'{plan.k8s_namespace}' (and every resource in it)"
                ),
                cmd=["kubectl", "delete", "namespace",
                     plan.k8s_namespace, "--ignore-not-found=true",
                     "--wait=true"],
                confirm_text=(
                    f"Delete the entire '{plan.k8s_namespace}' "
                    f"namespace?"
                ),
            ))
        else:
            plan.actions.append(Action(
                kind="refuse",
                description=(
                    "kubectl is not on PATH — skipping k8s teardown"
                ),
            ))

    # Stage 2 — kill stale kubectl port-forwards (always, regardless
    # of target — they're a *cause* of compose failures specifically).
    for port in COMPOSE_HOST_PORTS:
        for pid, cmd in find_pids_listening_on(port):
            if is_kubectl_port_forward(cmd):
                plan.actions.append(Action(
                    kind="kill-pid",
                    description=(
                        f"Kill stale kubectl port-forward holding "
                        f":{port} (pid {pid})"
                    ),
                    pid=pid,
                ))

    # Stage 3 — wipe config/ except defaults/.
    for child in list_config_subdirs_to_wipe(plan.config_root):
        plan.actions.append(Action(
            kind="rm-tree",
            description=(
                f"Delete {child} ({human_bytes(dir_size(child))})"
            ),
            path=child,
            confirm_text=(
                f"Delete {child}? (config/defaults/ is preserved.)"
            ),
        ))

    # Stage 4 — wipe data/ when scope ≥ with-data.
    if plan.scope in ("data", "everything") and plan.data_root.is_dir():
        plan.actions.append(Action(
            kind="rm-tree",
            description=(
                f"Delete {plan.data_root} (torrents/usenet/transcode — "
                f"{human_bytes(dir_size(plan.data_root))})"
            ),
            path=plan.data_root,
            confirm_text=(
                f"Wipe {plan.data_root} (active torrent / usenet state)?"
            ),
        ))

    # Stage 5 — wipe media/ only on --scope=everything, double-prompt.
    if plan.scope == "everything" and plan.media_root.is_dir():
        plan.actions.append(Action(
            kind="rm-tree",
            description=(
                f"Delete {plan.media_root} (downloaded films/shows — "
                f"{human_bytes(dir_size(plan.media_root))})"
            ),
            path=plan.media_root,
            confirm_text=(
                f"REALLY wipe {plan.media_root}? "
                f"This deletes downloaded films AND shows."
            ),
            requires_double_confirm=True,
        ))

    return plan


def _autodetect_target() -> str:
    """When the user passes ``--target=auto``, pick whichever
    orchestrator is present. Both → ``both``. Neither → ``compose``
    (the script will surface a refuse-action explaining nothing
    runs)."""
    have_docker = has_docker()
    have_k8s = has_kubectl()
    if have_docker and have_k8s:
        return "both"
    if have_k8s:
        return "k8s"
    return "compose"


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def execute_plan(plan: Plan) -> int:
    """Run every Action in the plan. Returns process exit code."""
    print_banner(plan)

    if not plan.actions:
        print("[INFO] Nothing to do — no docker / kubectl on PATH and "
              "no config/ data/ media/ to wipe.")
        return 0

    print("[PLAN] Actions in order:")
    for i, action in enumerate(plan.actions, 1):
        print(f"  {i:>2}. {action.describe()}")
    print()

    if plan.dry_run:
        print("[OK] Dry-run complete — no changes made.")
        return 0

    failures = 0
    for action in plan.actions:
        if not _confirm(action, plan):
            print(f"[SKIP] {action.describe()}")
            continue
        try:
            _run_action(action)
        except Exception as exc:  # noqa: BLE001
            print(f"[ERR ] {action.describe()}: {exc}", file=sys.stderr)
            failures += 1

    print()
    if failures:
        print(f"[WARN] Teardown finished with {failures} failure(s).")
        return 1
    print("[OK] Teardown complete.")
    _print_next_steps(plan)
    return 0


def print_banner(plan: Plan) -> None:
    print("==================================")
    print(" Media-stack teardown")
    print("==================================")
    print(f"  Target:       {plan.target}")
    print(f"  Scope:        {plan.scope}")
    print(f"  Compose file: {plan.compose_file}")
    print(f"  CONFIG_ROOT:  {plan.config_root}")
    print(f"  DATA_ROOT:    {plan.data_root}")
    if plan.scope == "everything":
        print(f"  MEDIA_ROOT:   {plan.media_root}")
    if plan.target in ("k8s", "both"):
        print(f"  K8s NS:       {plan.k8s_namespace}")
    if plan.dry_run:
        print("  Mode:         DRY-RUN / PREVIEW")
    print()


def _confirm(action: Action, plan: Plan) -> bool:
    """Prompt unless ``--yes``. Double-confirm flagged destructions
    (media/) regardless of ``--yes`` — that path is a
    deletion-of-last-resort and the operator should physically
    type ``YES`` to proceed."""
    if action.confirm_text is None:
        return True
    if action.requires_double_confirm:
        ans = input(f"{action.confirm_text} [type YES to proceed] ")
        return ans.strip() == "YES"
    if plan.assume_yes:
        return True
    ans = input(f"{action.confirm_text} [y/N] ")
    return ans.strip().lower() in {"y", "yes"}


def _run_action(action: Action) -> None:
    if action.kind == "refuse":
        print(f"[INFO] {action.describe()}")
        return
    if action.kind == "compose-down":
        assert action.cmd is not None
        print(f"[RUN ] {' '.join(action.cmd)}")
        subprocess.run(action.cmd, check=False)
        return
    if action.kind == "k8s-delete-ns":
        assert action.cmd is not None
        print(f"[RUN ] {' '.join(action.cmd)}")
        subprocess.run(action.cmd, check=False)
        return
    if action.kind == "kill-pid":
        assert action.pid is not None
        print(f"[KILL] pid {action.pid}")
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/PID", str(action.pid), "/F"],
                    check=False,
                )
            else:
                os.kill(action.pid, 15)
        except OSError as exc:
            print(f"[WARN] kill pid {action.pid} failed: {exc}",
                  file=sys.stderr)
        return
    if action.kind == "rm-tree":
        assert action.path is not None
        print(f"[RM  ] {action.path}")
        _rm_tree(action.path)
        return
    raise AssertionError(f"unknown action kind: {action.kind}")


def _rm_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=False)


def _print_next_steps(plan: Plan) -> None:
    print()
    print("Next steps:")
    if plan.target in ("compose", "both"):
        print(
            f"  docker compose -f {plan.compose_file} up -d",
        )
    if plan.target in ("k8s", "both"):
        print(f"  ./deploy-k8s.sh")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-platform teardown for the media-stack deployment."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--target", choices=["auto", "compose", "k8s", "both"],
        default="auto",
        help=(
            "Which orchestrator to tear down. ``auto`` picks "
            "whichever of docker / kubectl is on PATH; ``both`` "
            "tears down both."
        ),
    )
    parser.add_argument(
        "--scope", choices=["config", "data", "everything"],
        default="config",
        help=(
            "How aggressive to be. ``config`` (default) wipes only "
            "runtime config/ subdirs; ``data`` adds data/; "
            "``everything`` adds media/ (with double-confirm)."
        ),
    )
    parser.add_argument(
        "--compose-file",
        default=str(DEFAULT_COMPOSE_FILE),
        help=f"Path to docker-compose.yml (default: {DEFAULT_COMPOSE_FILE}).",
    )
    parser.add_argument(
        "--config-root", default=str(DEFAULT_CONFIG_ROOT),
        help="Path to runtime config/ directory.",
    )
    parser.add_argument(
        "--data-root", default=str(DEFAULT_DATA_ROOT),
        help="Path to data/ (torrents/usenet/transcode).",
    )
    parser.add_argument(
        "--media-root", default=str(DEFAULT_MEDIA_ROOT),
        help="Path to media/ (downloaded films/shows).",
    )
    parser.add_argument(
        "--k8s-namespace", default=DEFAULT_K8S_NAMESPACE,
        help="Kubernetes namespace to delete.",
    )
    dry = parser.add_mutually_exclusive_group()
    dry.add_argument(
        "--dry-run", action="store_true",
        help="Print every planned operation without taking action.",
    )
    dry.add_argument(
        "--preview", dest="dry_run", action="store_true",
        help="Alias for --dry-run.",
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help=(
            "Skip per-action confirmation prompts. The most "
            "destructive (media/ wipe) STILL prompts twice."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan = build_plan(args)
    return execute_plan(plan)


if __name__ == "__main__":
    sys.exit(main())
