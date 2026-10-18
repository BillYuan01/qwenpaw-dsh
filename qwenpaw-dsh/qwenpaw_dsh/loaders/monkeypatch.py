# -*- coding: utf-8 -*-
"""QwenPaw 2.2.x loader — patch the registry and every by-value import binding.

Why so many targets: ``harnesses/runtime.py`` and the routers do
``from .registry import PROVIDER_CATALOG, create_adapter, ...``, which binds the
symbol *by value* at import time. Patching only ``registry`` would leave the
runtime and the API routers looking at the originals.

Contract: every target must exist and every attribute must be found, otherwise
``mount`` raises — no silent half-mount. ``unmount`` restores originals exactly.
"""

from __future__ import annotations

import importlib
from typing import Any

from ..provider import ProviderSpec
from .port import MountReport, MountUnsupported

# module path -> attributes we must patch there
DEFAULT_TARGETS: dict[str, tuple[str, ...]] = {
    "qwenpaw.harnesses.registry": (
        "PROVIDER_CATALOG", "create_adapter", "adapter_config_key", "get_provider",
    ),
    "qwenpaw.harnesses.runtime": (
        "PROVIDER_CATALOG", "create_adapter", "adapter_config_key", "get_provider",
    ),
    "qwenpaw.app.routers.harnesses": ("get_provider",),
    "qwenpaw.app.routers.agents": ("get_provider",),
}


class MonkeypatchLoader:
    strategy = "monkeypatch-2.2"

    def __init__(self, *, version_range: tuple[str, str] = ("2.2.0", "2.3.0"),
                 targets: dict[str, tuple[str, ...]] | None = None) -> None:
        self.version_range = version_range
        self.targets = targets or DEFAULT_TARGETS
        self._originals: dict[tuple[str, str], Any] = {}
        self._mounted = False
        self.host_version: str | None = None

    # -- helpers -----------------------------------------------------------
    def _host_version(self) -> str | None:
        try:
            module = importlib.import_module("qwenpaw")
        except Exception:  # noqa: BLE001
            return None
        value = getattr(module, "__version__", None)
        if isinstance(value, str):
            return value
        # In QwenPaw 2.2.x, ``qwenpaw.__version__`` resolves to the
        # ``qwenpaw/__version__.py`` submodule, whose ``__version__`` is the string.
        nested = getattr(value, "__version__", None)
        if isinstance(nested, str):
            return nested
        try:
            from qwenpaw.__version__ import __version__ as string_version

            return str(string_version)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _in_range(version: str | None, lo: str, hi: str) -> bool:
        if not version:
            return False

        def key(value: str) -> tuple:
            parts = []
            for piece in value.split(".")[:3]:
                digits = "".join(ch for ch in piece if ch.isdigit())
                parts.append(int(digits) if digits else 0)
            return tuple(parts)

        return key(lo) <= key(version) < key(hi)

    # -- port --------------------------------------------------------------
    def probe(self) -> MountReport:
        version = self._host_version()
        missing = []
        for module_name, attrs in self.targets.items():
            try:
                module = importlib.import_module(module_name)
            except Exception:  # noqa: BLE001
                missing.append(module_name)
                continue
            for attr in attrs:
                if not hasattr(module, attr):
                    missing.append(f"{module_name}.{attr}")
        ok_version = self._in_range(version, *self.version_range)
        return MountReport(
            strategy=self.strategy,
            host_version=version,
            mounted=[] if missing or not ok_version else list(self.targets),
            missing=missing,
            detail=(
                f"version {version} in [{self.version_range[0]}, {self.version_range[1]})"
                if ok_version else f"version {version} outside supported range"
            ),
        )

    def mount(self, spec: ProviderSpec) -> MountReport:
        report = self.probe()
        if report.missing or not self._in_range(report.host_version, *self.version_range):
            raise MountUnsupported(
                f"monkeypatch loader cannot mount: missing={report.missing} "
                f"version={report.host_version} range={self.version_range}",
            )
        if self._mounted:
            return report

        registry = importlib.import_module("qwenpaw.harnesses.registry")
        original_catalog = registry.PROVIDER_CATALOG
        original_create = registry.create_adapter
        original_key = registry.adapter_config_key
        original_get = registry.get_provider
        # Only the loader imports the registry's own dataclass; the core does not.
        catalog_item = registry.ProviderCatalogItem(
            spec.id, spec.name, spec.coming_soon, spec.capabilities,
        )

        def catalog_with_spec() -> tuple:
            return tuple(original_catalog) + (catalog_item,)

        def create_adapter(provider_id, state_dir, settings=None):
            if provider_id == spec.id:
                from ..host import HostContext  # local import: avoid cycles
                values = settings or {}
                return spec.factory(state_dir, settings, HostContext(
                    state_dir=state_dir,
                    binary=str(values.get("binary") or "").strip() or None,
                    profile=str(values.get("profile") or "acp").strip(),
                ))
            return original_create(provider_id, state_dir, settings)

        def adapter_config_key(provider_id, settings=None):
            if provider_id == spec.id:
                return spec.config_key(settings)
            return original_key(provider_id, settings)

        def get_provider(provider_id):
            if provider_id == spec.id:
                return catalog_item
            return original_get(provider_id)

        replacements = {
            "PROVIDER_CATALOG": catalog_with_spec(),
            "create_adapter": create_adapter,
            "adapter_config_key": adapter_config_key,
            "get_provider": get_provider,
        }

        for module_name, attrs in self.targets.items():
            module = importlib.import_module(module_name)
            for attr in attrs:
                self._originals[(module_name, attr)] = getattr(module, attr)
                setattr(module, attr, replacements[attr])
        self._mounted = True
        report.mounted = sorted({m for m, _ in self._originals})
        return report

    def unmount(self) -> None:
        if not self._mounted:
            return
        for (module_name, attr), original in self._originals.items():
            module = importlib.import_module(module_name)
            setattr(module, attr, original)
        self._originals.clear()
        self._mounted = False
