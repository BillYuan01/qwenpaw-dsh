# -*- coding: utf-8 -*-
"""In-memory loader for tests and embedding — no host patching at all."""

from __future__ import annotations

from ..provider import ProviderSpec
from .port import MountReport


class DirectLoader:
    strategy = "direct"
    registry: dict[str, ProviderSpec] = {}

    def probe(self) -> MountReport:
        return MountReport(strategy=self.strategy, mounted=["in-memory registry"],
                           detail="always available; not a host integration")

    def mount(self, spec: ProviderSpec) -> MountReport:
        self.registry[spec.id] = spec
        return MountReport(strategy=self.strategy, mounted=[f"memory:{spec.id}"])

    def unmount(self) -> None:
        self.registry.clear()
