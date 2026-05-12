"""Tests for ``build_ui_image_main`` — the build CLI for the UI container.

Mirror of ``test_build_controller_image_main.py`` patterns where they
exist; the UI image is versioned independently via ``VERSION-UI`` so
the dashboard can iterate without forcing a controller rebuild.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from media_stack.cli.commands import build_ui_image_main as ui_build
from media_stack.cli.workflows import build_ui_image_service as ui_build_service
from media_stack.core.exceptions import ConfigError


# ---------------------------------------------------------------------------
# _read_ui_version
# ---------------------------------------------------------------------------


def test_read_ui_version_returns_trimmed_contents(tmp_path: Path) -> None:
    (tmp_path / "VERSION-UI").write_text("  1.2.3  \n", encoding="utf-8")
    assert ui_build._read_ui_version(tmp_path) == "1.2.3"


def test_read_ui_version_returns_dev_when_missing(tmp_path: Path) -> None:
    """A missing VERSION-UI file falls back to 'dev'. The build will
    still synthesize an image tag (``...:vdev``) which is fine for
    local development; CI sets the file to gate releases."""
    assert ui_build._read_ui_version(tmp_path) == "dev"


def test_read_ui_version_returns_dev_when_empty(tmp_path: Path) -> None:
    (tmp_path / "VERSION-UI").write_text("", encoding="utf-8")
    assert ui_build._read_ui_version(tmp_path) == "dev"


def test_read_ui_version_returns_dev_when_only_whitespace(tmp_path: Path) -> None:
    (tmp_path / "VERSION-UI").write_text("   \n  \n", encoding="utf-8")
    assert ui_build._read_ui_version(tmp_path) == "dev"


# ---------------------------------------------------------------------------
# default_ui_image
# ---------------------------------------------------------------------------


def test_default_ui_image_uses_version_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BOOTSTRAP_UI_IMAGE", raising=False)
    (tmp_path / "VERSION-UI").write_text("9.9.9\n", encoding="utf-8")
    assert ui_build.default_ui_image(tmp_path) == (
        "harbor.iomio.io/public/media-stack-ui:v9.9.9"
    )


def test_default_ui_image_env_override_wins(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("BOOTSTRAP_UI_IMAGE", "ghcr.io/example/ui:edge")
    (tmp_path / "VERSION-UI").write_text("9.9.9\n", encoding="utf-8")
    assert ui_build.default_ui_image(tmp_path) == "ghcr.io/example/ui:edge"


def test_default_ui_image_falls_back_to_dev_tag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("BOOTSTRAP_UI_IMAGE", raising=False)
    assert ui_build.default_ui_image(tmp_path) == (
        "harbor.iomio.io/public/media-stack-ui:vdev"
    )


def test_default_ui_image_ignores_blank_env(tmp_path: Path, monkeypatch) -> None:
    """A whitespace-only env var is treated as unset, otherwise we'd
    synthesize a malformed image ref."""
    monkeypatch.setenv("BOOTSTRAP_UI_IMAGE", "   ")
    (tmp_path / "VERSION-UI").write_text("1.0.0\n", encoding="utf-8")
    assert ui_build.default_ui_image(tmp_path) == (
        "harbor.iomio.io/public/media-stack-ui:v1.0.0"
    )


# ---------------------------------------------------------------------------
# _truthy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("v", ["1", "true", "TRUE", "yes", "on", " On "])
def test_truthy_accepts_known_truthy(v: str) -> None:
    assert ui_build._truthy(v, default=False) is True


@pytest.mark.parametrize("v", ["0", "false", "no", "off", "anything"])
def test_truthy_rejects_unknown(v: str) -> None:
    assert ui_build._truthy(v, default=True) is False


def test_truthy_uses_default_when_none() -> None:
    assert ui_build._truthy(None, default=True) is True
    assert ui_build._truthy(None, default=False) is False


# ---------------------------------------------------------------------------
# _detect_engine
# ---------------------------------------------------------------------------


def test_detect_engine_explicit_docker(monkeypatch) -> None:
    monkeypatch.setattr(ui_build.shutil, "which", lambda name: "/usr/bin/docker")
    assert ui_build._detect_engine("docker") == "docker"


def test_detect_engine_explicit_podman(monkeypatch) -> None:
    monkeypatch.setattr(ui_build.shutil, "which", lambda name: "/usr/bin/podman")
    assert ui_build._detect_engine("podman") == "podman"


def test_detect_engine_rejects_unknown_explicit() -> None:
    with pytest.raises(ConfigError, match="Unsupported container engine"):
        ui_build._detect_engine("nerdctl")


def test_detect_engine_explicit_not_installed(monkeypatch) -> None:
    monkeypatch.setattr(ui_build.shutil, "which", lambda name: None)
    with pytest.raises(ConfigError, match="not installed"):
        ui_build._detect_engine("docker")


def test_detect_engine_auto_prefers_docker(monkeypatch) -> None:
    """When neither flag nor env specify, prefer docker over podman if
    both are present (matches the controller build CLI ordering)."""
    monkeypatch.setattr(
        ui_build.shutil, "which",
        lambda name: f"/usr/bin/{name}" if name in ("docker", "podman") else None,
    )
    assert ui_build._detect_engine("") == "docker"


def test_detect_engine_auto_falls_back_to_podman(monkeypatch) -> None:
    monkeypatch.setattr(
        ui_build.shutil, "which",
        lambda name: "/usr/bin/podman" if name == "podman" else None,
    )
    assert ui_build._detect_engine("") == "podman"


def test_detect_engine_fails_when_none_present(monkeypatch) -> None:
    monkeypatch.setattr(ui_build.shutil, "which", lambda name: None)
    with pytest.raises(ConfigError, match="Neither docker nor podman"):
        ui_build._detect_engine("")


# ---------------------------------------------------------------------------
# parse_config
# ---------------------------------------------------------------------------


def test_parse_config_uses_defaults(monkeypatch, tmp_path: Path) -> None:
    """With no flags, parse_config uses VERSION-UI for the image tag,
    PUSH_IMAGE=1 default, and the bundled Dockerfile."""
    monkeypatch.setattr(ui_build, "repo_root_from_script_file", lambda _: tmp_path)
    monkeypatch.setattr(ui_build.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.delenv("PUSH_IMAGE", raising=False)
    monkeypatch.delenv("BOOTSTRAP_UI_IMAGE", raising=False)
    monkeypatch.delenv("DOCKERFILE", raising=False)
    monkeypatch.delenv("CONTAINER_ENGINE", raising=False)
    (tmp_path / "VERSION-UI").write_text("1.0.0\n")
    # Prod resolves DOCKERFILE to ``deploy/compose/ui.Dockerfile`` —
    # the old ``docker/`` location was retired in the deploy/ reorg.
    docker_dir = tmp_path / "deploy" / "compose"
    docker_dir.mkdir(parents=True)
    (docker_dir / "ui.Dockerfile").write_text("FROM nginx:1.27-alpine\n")

    cfg = ui_build.parse_config([])
    assert cfg.image == "harbor.iomio.io/public/media-stack-ui:v1.0.0"
    assert cfg.push_image is True  # PUSH_IMAGE default
    assert cfg.engine == "docker"
    assert cfg.dockerfile.is_file()


def test_parse_config_no_push_flag(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ui_build, "repo_root_from_script_file", lambda _: tmp_path)
    monkeypatch.setattr(ui_build.shutil, "which", lambda n: "/usr/bin/docker")
    (tmp_path / "VERSION-UI").write_text("1.0.0\n")
    (tmp_path / "deploy" / "compose").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deploy" / "compose" / "ui.Dockerfile").write_text("FROM nginx:1.27-alpine\n")
    cfg = ui_build.parse_config(["--no-push"])
    assert cfg.push_image is False


def test_parse_config_explicit_image(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ui_build, "repo_root_from_script_file", lambda _: tmp_path)
    monkeypatch.setattr(ui_build.shutil, "which", lambda n: "/usr/bin/docker")
    (tmp_path / "deploy" / "compose").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deploy" / "compose" / "ui.Dockerfile").write_text("FROM nginx:1.27-alpine\n")
    cfg = ui_build.parse_config(["--image", "ghcr.io/me/ui:1.2.3"])
    assert cfg.image == "ghcr.io/me/ui:1.2.3"


def test_parse_config_rejects_empty_image(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ui_build, "repo_root_from_script_file", lambda _: tmp_path)
    monkeypatch.setattr(ui_build.shutil, "which", lambda n: "/usr/bin/docker")
    (tmp_path / "deploy" / "compose").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deploy" / "compose" / "ui.Dockerfile").write_text("FROM nginx:1.27-alpine\n")
    with pytest.raises(ConfigError, match="cannot be empty"):
        ui_build.parse_config(["--image", "  "])


def test_parse_config_missing_dockerfile(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ui_build, "repo_root_from_script_file", lambda _: tmp_path)
    monkeypatch.setattr(ui_build.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.delenv("DOCKERFILE", raising=False)
    with pytest.raises(ConfigError, match="Dockerfile not found"):
        ui_build.parse_config([])


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def _make_lockfile(root: Path) -> None:
    """Materialize a stub ui/pnpm-lock.yaml so the run() sanity check
    (which gates docker build on the lockfile's presence) is satisfied
    in unit tests that mock out the actual subprocess calls."""
    ui_dir = root / "ui"
    ui_dir.mkdir(parents=True, exist_ok=True)
    (ui_dir / "pnpm-lock.yaml").write_text("# stub\n", encoding="utf-8")


def test_run_invokes_build_only_when_no_push(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def _fake_run_command(args: list[str]) -> None:
        calls.append(list(args))

    _make_lockfile(tmp_path)
    cfg = ui_build.BuildUIImageConfig(
        image="x:v1",
        push_image=False,
        engine="docker",
        dockerfile=tmp_path / "ui.Dockerfile",
        root_dir=tmp_path,
    )
    with mock.patch.object(ui_build_service, "run_command", _fake_run_command):
        rc = ui_build.run(cfg)
    assert rc == 0
    assert len(calls) == 1
    assert calls[0][0] == "docker"
    assert "build" in calls[0]
    assert "-t" in calls[0]
    assert "x:v1" in calls[0]


def test_run_invokes_build_then_push(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def _fake_run_command(args: list[str]) -> None:
        calls.append(list(args))

    _make_lockfile(tmp_path)
    cfg = ui_build.BuildUIImageConfig(
        image="x:v1",
        push_image=True,
        engine="podman",
        dockerfile=tmp_path / "ui.Dockerfile",
        root_dir=tmp_path,
    )
    with mock.patch.object(ui_build_service, "run_command", _fake_run_command):
        rc = ui_build.run(cfg)
    assert rc == 0
    assert len(calls) == 2
    assert calls[0][0] == "podman"
    assert calls[1] == ["podman", "push", "x:v1"]


def test_run_fails_when_pnpm_lockfile_missing(tmp_path: Path, capsys) -> None:
    """If ui/pnpm-lock.yaml is missing, run() must abort with rc=1 and
    a clear remediation message before invoking the container engine."""
    calls: list[list[str]] = []

    def _fake_run_command(args: list[str]) -> None:
        calls.append(list(args))

    cfg = ui_build.BuildUIImageConfig(
        image="x:v1",
        push_image=False,
        engine="docker",
        dockerfile=tmp_path / "ui.Dockerfile",
        root_dir=tmp_path,
    )
    with mock.patch.object(ui_build_service, "run_command", _fake_run_command):
        rc = ui_build.run(cfg)
    assert rc == 1
    assert calls == []
    err = capsys.readouterr().err
    assert "pnpm-lock.yaml" in err
    assert "pnpm install" in err


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_success(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ui_build, "repo_root_from_script_file", lambda _: tmp_path)
    monkeypatch.setattr(ui_build.shutil, "which", lambda n: "/usr/bin/docker")
    (tmp_path / "VERSION-UI").write_text("1.0.0\n")
    (tmp_path / "deploy" / "compose").mkdir(parents=True, exist_ok=True)
    (tmp_path / "deploy" / "compose" / "ui.Dockerfile").write_text("FROM nginx:1.27-alpine\n")
    _make_lockfile(tmp_path)
    monkeypatch.setattr(ui_build_service, "run_command", lambda args: None)
    rc = ui_build.main(["--no-push"])
    assert rc == 0


def test_main_returns_one_on_config_error(monkeypatch, tmp_path: Path, capsys) -> None:
    monkeypatch.setattr(ui_build, "repo_root_from_script_file", lambda _: tmp_path)
    monkeypatch.setattr(ui_build.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.delenv("DOCKERFILE", raising=False)
    rc = ui_build.main([])
    assert rc == 1
    captured = capsys.readouterr()
    assert "Dockerfile not found" in captured.err
