# -*- coding: utf-8 -*-
"""QwenPaw 2.3+ loader — forward-declared, feature-detected, fail-loud.

Status as of the 2.3 intel review: the
third-party-agent-plugin mechanism is **promised but not in flight**. The only
public 2.3.0 scope (issue #8289) is the plugin lifecycle refactor (#7565) and
channels→plugins (#8187); no harness/agent plugin type exists in
``plugins/architecture.py`` and ``harnesses/registry.py`` is still static.

So this loader does not guess an API. It probes a *candidate* set, and when none
is present it raises :class:`MountUnsupported` with a precise diagnosis. When
2.3 lands, only this file changes.
"""

from __future__ import annotations

import importlib

from ..provider import ProviderSpec
from .port import MountReport, MountUnsupported

# Deliberately explicit about what is NOT the official API:
#   qwenpaw.plugins.api.PluginApi.register_provider  -> custom **LLM** provider
#   qwenpaw.harnesses.registry.ProviderCatalogItem   -> static 2.2 tuple member
# A future harness plugin surface must be one of the candidates below (or an
# entry-point group), and this file is the single place to accept it.
CANDIDATE_TARGETS = (
    ("qwenpaw.harnesses.registry", "register_provider"),
    ("qwenpaw.harnesses.registry", "register_harness_provider"),
    ("qwenpaw.harnesses.plugins", "register_harness"),
    ("qwenpaw.plugins.api", "register_agent_backend"),
)
CANDIDATE_ENTRYPOINT_GROUPS = ("qwenpaw.harness_plugins", "qwenpaw.agent_plugins")


class OfficialLoader:
    strategy = "official-plugin"

    def __init__(self) -> None:
        self._entry: tuple[str, str] | None = None
        self._unmounted = True

    def probe(self) -> MountReport:
        for module_name, attr in CANDIDATE_TARGETS:
            try:
                module = importlib.import_module(module_name)
            except Exception:  # noqa: BLE001
                continue
            if callable(getattr(module, attr, None)):
                self._entry = (module_name, attr)
                return MountReport(
                    strategy=self.strategy, mounted=[f"{module_name}.{attr}"],
                    detail="feature-detected official harness registration entry",
                )
        # entry-point based mechanism (importlib.metadata) could be probed here
        return MountReport(
            strategy=self.strategy,
            missing=[f"{m}.{a}" for m, a in CANDIDATE_TARGETS],
            detail=(
                "no official harness-plugin registration entry found. As of the 2.3 intel "
                "review this mechanism is promised in issue #7396 but not implemented; "
                "wire it here once it lands. Do NOT fall back to PluginApi.register_provider "
                "(that is an LLM provider, not an agent backend)."
            ),
        )

    def mount(self, spec: ProviderSpec) -> MountReport:
        report = self.probe()
        if not self._entry:
            raise MountUnsupported(report.detail)
        module = importlib.import_module(self._entry[0])
        # The 2.3 shape is unknown; the anti-corruption call is isolated here.
        register = getattr(module, self._entry[1])
        register(spec)
        self._unmounted = False
        return report

    def unmount(self) -> None:
        # The official mechanism is expected to own deregistration; leave a hook.
        self._unmounted = True
