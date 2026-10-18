# -*- coding: utf-8 -*-
"""Loader selection: official (2.3+) → monkeypatch (2.2.x) → direct (tests)."""

from __future__ import annotations

from ..host import HostContext
from ..provider import build_provider_spec
from .direct import DirectLoader
from .monkeypatch import MonkeypatchLoader
from .official import OfficialLoader
from .port import HarnessMountPort, MountReport, MountUnsupported

__all__ = [
    "DirectLoader",
    "HarnessMountPort",
    "MonkeypatchLoader",
    "MountReport",
    "MountUnsupported",
    "OfficialLoader",
    "active_loader",
    "mount_provider",
    "unmount_provider",
]

_active: HarnessMountPort | None = None


def active_loader() -> HarnessMountPort | None:
    return _active


def mount_provider(host: HostContext, *, allow_direct: bool = False) -> MountReport:
    """Mount the DSH provider through the best available loader."""
    global _active
    candidates: list[HarnessMountPort] = [OfficialLoader(), MonkeypatchLoader()]
    if allow_direct:
        candidates.append(DirectLoader())
    diagnosed: list[str] = []
    spec = build_provider_spec(host)
    for loader in candidates:
        report = loader.probe()
        if report.mounted:
            report = loader.mount(spec)
            _active = loader
            return report
        diagnosed.append(f"{loader.strategy}: {report.detail or report.missing}")
    raise MountUnsupported("no harness mount strategy available — " + " | ".join(diagnosed))


def unmount_provider() -> None:
    global _active
    if _active is not None:
        _active.unmount()
        _active = None
