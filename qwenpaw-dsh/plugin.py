# -*- coding: utf-8 -*-
"""QwenPaw plugin entry — SKELETON (do not install).

The plugin's only jobs are:
  1. build the host context (state dir + approval port),
  2. select and mount the loader seam at startup,
  3. unmount on shutdown/uninstall.

All provider behaviour lives in ``qwenpaw_dsh`` and is shared by every loader.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from qwenpaw_dsh.host import ApprovalDecision, ApprovalPort, ApprovalRequest, HostContext
from qwenpaw_dsh.loaders import mount_provider, unmount_provider

log = logging.getLogger("qwenpaw.dsh")


class QwenPawApprovalPort(ApprovalPort):
    """Bridge provider approvals into QwenPaw's approval cards.

    Real implementation follows the Codex adapter precedent
    (``harnesses/codex/adapter.py:640-710``):
    ``get_approval_service().create_pending_summary(...)`` + ``wait_for_approval(...)``.
    The exact call signature is left unimplemented on purpose — this skeleton
    does not guess host internals.
    """

    async def request(self, request: ApprovalRequest) -> ApprovalDecision:
        del request
        log.warning("DSH approval port is a skeleton stub; rejecting")
        return ApprovalDecision(status="rejected")


def build_host_context() -> HostContext:
    home = Path(os.environ.get("QWENPAW_HOME") or (Path.home() / ".qwenpaw"))
    return HostContext(
        state_dir=home / "harnesses" / "dsh",
        approval=QwenPawApprovalPort(),
        binary=os.environ.get("DSH_BINARY") or None,
        profile=os.environ.get("DSH_PROFILE") or "acp",
        host_api="qwenpaw-2.2",
    )


class DshHarnessPlugin:
    def register(self, api) -> None:
        api.register_startup_hook("dsh_mount", self._startup, priority=10)
        api.register_shutdown_hook("dsh_unmount", self._shutdown)
        api.register_uninstall_hook("dsh_unmount", self._shutdown)

    def _startup(self) -> None:
        host = build_host_context()
        host.state_dir.mkdir(parents=True, exist_ok=True)
        report = mount_provider(host)
        log.info("dsh harness mounted via %s: %s", report.strategy, report.to_dict())

    def _shutdown(self) -> None:
        unmount_provider()


plugin = DshHarnessPlugin()
