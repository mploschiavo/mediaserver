"""K8sManifestCapturer — Recorder for stdin-applied Kubernetes manifests.

ADR-0015 Phase 4. Pre-Phase-4 the capture logic lived on
``RunnerServicesMixin`` (a god-mixin in commands/) with one
:func:`@staticmethod` (``_is_k8s_apply_with_stdin``) that the
:envvar:`STATIC_METHOD_RATCHET` would have caught if it ran on
that file. Splitting into an SRP class folds the staticmethod
into an instance method on the recorder, dropping the ratchet
count by one.

The recorder writes two paired files per capture:
``applied-manifests/NNN.yaml`` (the resolved manifest stdin) +
``applied-manifests/NNN.meta.json`` (the phase + command +
sequence metadata). The pair gives a per-deploy audit trail that
operators can diff across deploys to spot drift.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

from media_stack.core.cli_common import ts

if TYPE_CHECKING:
    from media_stack.cli.workflows.deploy_orchestration.runtime_artifact_writer import (
        RuntimeArtifactWriter,
    )
    from media_stack.core.phase_tracker import PhaseTracker


class K8sManifestCapturer:
    """Recorder: capture stdin-piped ``kubectl apply -f -`` payloads.

    Holds a monotonic counter (per pipeline run) so each capture
    gets a deterministic three-digit sequence number, plus a
    reference to the :class:`PhaseTracker` so the meta file records
    which phase the kubectl call belonged to.
    """

    def __init__(
        self,
        artifact_writer: "RuntimeArtifactWriter",
        tracker: "PhaseTracker",
    ) -> None:
        self._artifact_writer = artifact_writer
        self._tracker = tracker
        self._counter = 0

    def is_apply_with_stdin(self, args: list[str]) -> bool:
        tokens = [str(item or "").strip() for item in args]
        return "apply" in tokens and "-f" in tokens and "-" in tokens

    def record(self, args: list[str], manifest_text: str) -> None:
        if not manifest_text.strip():
            return
        self._counter += 1
        sequence = self._counter
        base_rel = f"applied-manifests/{sequence:03d}"
        self._artifact_writer.write_text(
            target="kubernetes",
            relative_path=f"{base_rel}.yaml",
            text=manifest_text,
            label="Captured resolved Kubernetes manifest",
        )
        self._artifact_writer.write_json(
            target="kubernetes",
            relative_path=f"{base_rel}.meta.json",
            payload={
                "captured_at": ts(),
                "phase": self._tracker.current_phase or "",
                "command": " ".join(shlex.quote(token) for token in args),
                "sequence": sequence,
            },
            label="Captured Kubernetes manifest metadata",
            log=False,
        )


__all__ = ["K8sManifestCapturer"]
