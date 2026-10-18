# -*- coding: utf-8 -*-
"""The seam: how a loader mounts the provider without the core knowing how."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..provider import ProviderSpec


class MountUnsupported(RuntimeError):
    """The running host exposes no mount point this loader understands."""


@dataclass
class MountReport:
    """What a loader mounted (and what it could not find)."""

    strategy: str
    host_version: str | None = None
    mounted: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.mounted) and not self.missing

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy, "host_version": self.host_version,
            "mounted": list(self.mounted), "missing": list(self.missing),
            "ok": self.ok, "detail": self.detail,
        }


@runtime_checkable
class HarnessMountPort(Protocol):
    """One environment-specific way to mount a harness provider.

    Implementations:
      * ``MonkeypatchLoader`` — QwenPaw 2.2.x (patch registry + import bindings)
      * ``OfficialLoader``   — QwenPaw 2.3+ (feature-detected official entry)
      * ``DirectLoader``     — tests / embedding (in-memory registry)
    """

    strategy: str

    def probe(self) -> MountReport: ...

    def mount(self, spec: ProviderSpec) -> MountReport: ...

    def unmount(self) -> None: ...
