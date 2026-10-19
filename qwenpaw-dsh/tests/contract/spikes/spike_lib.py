# -*- coding: utf-8 -*-
"""Shared helpers for the DSH↔QwenPaw Phase 0 spikes.

Everything here is deliberately dependency-light: the only hard requirement is
the official ACP Python SDK (``agent-client-protocol``, import name ``acp``),
which already ships inside the QwenPaw virtualenv (``~/.qwenpaw/venv``).

Design notes
------------
* Each spike is an independent script.  Required companion library: this file.
* The DSH home is *isolated* (``--dsh-home`` / ``$DSH_SPIKE_HOME``), seeded
  either from the shipped ``acp`` profile template (``--seed fresh``) or by
  copying the user's real profile (``--seed user``).
* Credentials are never copied: we symlink ``~/.dsh/.credentials.yaml`` into the
  isolated home so the secret stays in one place.  If no credentials are found
  we do not fail: model-dependent checks are marked ``pending``.
* Results are written to ``spikes/results/<spike>.json`` so ``run_all.py`` can
  aggregate them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
RESEARCH_ROOT = HERE.parent
RESULTS_DIR = HERE / "results"
SPIKES_WORK_ROOT = Path(os.environ.get("DSH_SPIKE_WORK", "/tmp/dsh-spikes-work"))

DSH_BIN = shutil.which("dsh") or str(Path.home() / ".local/bin/dsh")
DEFAULT_PROFILE = os.environ.get("DSH_SPIKE_PROFILE", "acp")
DEFAULT_DSH_HOME = os.environ.get("DSH_SPIKE_HOME")


def dsh_install_dir() -> Path:
    """Best-effort location of the installed @deepseek-ai/dsh package."""
    return Path(DSH_BIN).resolve().parent.parent


def dsh_package_entry(package: str, relative: str = "lib/index.js") -> Path | None:
    base = dsh_install_dir()
    for candidate in (
        base / "node_modules" / "@deepseek-ai" / package / relative,
        base / "node_modules" / package / relative,
    ):
        if candidate.exists():
            return candidate
    return None


def is_billing_error(exc: BaseException) -> bool:
    """True when a prompt failed for account/quota reasons, not protocol ones."""
    text = str(exc).lower()
    return any(marker in text for marker in (
        "insufficient balance", "insufficient_quota", "quota exceeded", "402",
    ))


async def safe_prompt(conn: Any, *, session_id: str, prompt: list[Any], result: "Result", label: str):
    """Run a prompt, degrading a billing/quota failure to 'not verified'."""
    try:
        return await conn.prompt(session_id=session_id, prompt=prompt)
    except Exception as exc:  # noqa: BLE001
        if is_billing_error(exc):
            result.note(f"{label}: model call unavailable (billing/quota): {exc}")
            return None
        raise

# ---------------------------------------------------------------------------
# ACP imports (fail loud, with an actionable message)
# ---------------------------------------------------------------------------
try:
    from acp import PROTOCOL_VERSION, spawn_agent_process, text_block  # noqa: F401
    from acp.schema import (  # noqa: F401
        AllowedOutcome,
        ClientCapabilities,
        DeniedOutcome,
        Implementation,
        McpServerHttp,
        McpServerStdio,
        RequestPermissionResponse,
    )
except Exception as exc:  # pragma: no cover - environment guard
    raise SystemExit(
        "This spike suite needs the official ACP Python SDK (import name 'acp').\n"
        "QwenPaw ships it in its venv; run with:\n"
        "    ~/.qwenpaw/venv/bin/python spikes/run_all.py\n"
        f"Underlying import error: {exc!r}",
    )


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------
def dsh_version() -> str:
    try:
        out = subprocess.run(
            [DSH_BIN, "--version"], capture_output=True, text=True, timeout=60,
        )
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception as exc:  # pragma: no cover
        return f"<unavailable: {exc!r}>"


def credentials_status() -> dict[str, Any]:
    """Report where model credentials come from, without ever reading values."""
    cred = Path.home() / ".dsh" / ".credentials.yaml"
    env_keys = [
        k for k in ("DEEPSEEK_API_KEY", "OPENCODE_GO_API_KEY", "ANTHROPIC_API_KEY")
        if os.environ.get(k)
    ]
    return {
        "credentials_file": str(cred),
        "credentials_file_exists": cred.exists(),
        "api_key_env_present": env_keys,
        "available": cred.exists() or bool(env_keys),
    }


def resolve_dsh_home(arg: str | None) -> Path:
    raw = arg or DEFAULT_DSH_HOME
    if raw:
        return Path(raw).expanduser().resolve()
    return (SPIKES_WORK_ROOT / f"home-{os.getpid()}").resolve()


def seed_dsh_home(home: Path, seed: str) -> dict[str, Any]:
    """Create/seed an isolated DSH_HOME.  Returns a small descriptor."""
    home.mkdir(parents=True, exist_ok=True)
    profile_dir = home / "profiles" / DEFAULT_PROFILE
    note: list[str] = []

    if seed == "user" and not profile_dir.exists():
        src = Path.home() / ".dsh" / "profiles" / DEFAULT_PROFILE
        if src.exists():
            profile_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, profile_dir)
            note.append(f"copied user profile from {src}")
        else:
            note.append(f"user profile {src} missing; falling back to shipped template")

    # A fresh home without a profile dir makes `dsh --profile acp` materialise
    # the shipped template on first boot (verified for 0.2.0-rc.2).
    cred_link = home / ".credentials.yaml"
    real_cred = Path.home() / ".dsh" / ".credentials.yaml"
    if not cred_link.exists() and real_cred.exists():
        cred_link.symlink_to(real_cred)
        note.append(f"symlinked credentials from {real_cred}")

    return {"home": str(home), "seed": seed, "profile": DEFAULT_PROFILE, "notes": note}


def dsh_env(home: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["DSH_HOME"] = str(home)
    env.pop("DSH_PROFILE", None)
    env.pop("DSH_PROFILE_DIR", None)
    # Keep spawned DSH from inheriting an outer harness session identity.
    for k in ("DSH_SESSION_ID", "DSH_SHELL", "DSH_TUI", "DSH_WEB"):
        env.pop(k, None)
    if extra:
        env.update({k: v for k, v in extra.items() if v is not None})
    return env


# ---------------------------------------------------------------------------
# ACP client that records everything
# ---------------------------------------------------------------------------
def _dump(model: Any) -> Any:
    if hasattr(model, "model_dump"):
        return model.model_dump(by_alias=True, exclude_none=True, mode="json")
    return model


PermissionDecider = Callable[[list[Any], Any, dict[str, Any]], "str | None"]


def pick_option(options: list[Any], kinds: tuple[str, ...]) -> str | None:
    for opt in options:
        if getattr(opt, "kind", None) in kinds:
            return opt.option_id
    return None


def allow_once_decider(options: list[Any], tool_call: Any, meta: dict[str, Any]) -> str | None:
    return pick_option(options, ("allow_once", "allow_always"))


def reject_decider(options: list[Any], tool_call: Any, meta: dict[str, Any]) -> str | None:
    return pick_option(options, ("reject_once", "reject_always"))


@dataclass
class SpikeClient:
    """Minimal but complete ACP ``Client`` implementation.

    Records every ``session/update`` and ``session/request_permission`` with a
    monotonic timestamp relative to ``t0``.  Tool-call state is folded into a
    ``toolCallId -> merged fields`` map so the permission bridge can be
    demonstrated with the data a real adapter would have.
    """

    decider: PermissionDecider = allow_once_decider
    t0: float = field(default_factory=time.monotonic)
    updates: list[dict[str, Any]] = field(default_factory=list)
    permissions: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: dict[str, dict[str, Any]] = field(default_factory=dict)
    conn: Any = None
    errors: list[dict[str, Any]] = field(default_factory=list)

    # -- plumbing -----------------------------------------------------------
    def on_connect(self, conn: Any) -> None:
        self.conn = conn

    def _record(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["t"] = round(time.monotonic() - self.t0, 4)
        self.updates.append(payload)
        return payload

    async def session_update(self, session_id: str, update: Any, **_: Any) -> None:
        data = _dump(update)
        kind = data.get("sessionUpdate", type(update).__name__)
        record = {"session_id": session_id, "kind": kind, "update": data}
        tc_id = data.get("toolCallId")
        if tc_id:
            record["toolCallId"] = tc_id
            if kind == "tool_call":
                self.tool_calls[tc_id] = dict(data)
            else:  # tool_call_update: merge non-null fields
                merged = self.tool_calls.setdefault(tc_id, {})
                for key, value in data.items():
                    if key in ("sessionUpdate",) or value is None:
                        continue
                    merged[key] = value
        self._record(record)

    async def request_permission(
        self, options: list[Any], session_id: str, tool_call: Any, **_: Any,
    ) -> RequestPermissionResponse:
        dumped_options = [_dump(o) for o in options]
        dumped_tool = _dump(tool_call)
        tc_id = dumped_tool.get("toolCallId")
        meta = {
            "toolCallId": tc_id,
            "from_permission": dumped_tool,
            "cached_tool_call": self.tool_calls.get(tc_id) if tc_id else None,
        }
        chosen = self.decider(options, tool_call, meta)
        record = {
            "session_id": session_id,
            "options": dumped_options,
            "tool_call": dumped_tool,
            "merged": meta,
            "chosen_option_id": chosen,
            "outcome": "selected" if chosen else "cancelled",
            "t": round(time.monotonic() - self.t0, 4),
        }
        self.permissions.append(record)
        if chosen:
            return RequestPermissionResponse(outcome=AllowedOutcome(option_id=chosen))
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    # -- stubs for methods DSH does not call, but the protocol may ---------
    async def read_text_file(self, path: str, session_id: str, **_: Any) -> Any:
        p = Path(path)
        return {"content": p.read_text(encoding="utf-8", errors="replace")}

    async def write_text_file(self, content: str, path: str, session_id: str, **_: Any) -> None:
        Path(path).write_text(content, encoding="utf-8")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        return None

    # -- observation helpers ----------------------------------------------
    def updates_of(self, kind: str) -> list[dict[str, Any]]:
        return [u for u in self.updates if u["kind"] == kind]

    def assistant_text(self) -> str:
        out = []
        for u in self.updates_of("agent_message_chunk"):
            content = u["update"].get("content") or {}
            if isinstance(content, dict) and content.get("type") == "text":
                out.append(content.get("text", ""))
        return "".join(out)

    def thought_text(self) -> str:
        out = []
        for u in self.updates_of("agent_thought_chunk"):
            content = u["update"].get("content") or {}
            if isinstance(content, dict) and content.get("type") == "text":
                out.append(content.get("text", ""))
        return "".join(out)

    def tool_call_summary(self) -> list[dict[str, Any]]:
        return [
            {
                "toolCallId": tc_id,
                "title": entry.get("title"),
                "kind": entry.get("kind"),
                "status": entry.get("status"),
                "rawInput": entry.get("rawInput"),
            }
            for tc_id, entry in self.tool_calls.items()
        ]


# ---------------------------------------------------------------------------
# Spawning / driving a dsh acp process
# ---------------------------------------------------------------------------
@asynccontextmanager
async def open_acp(
    *,
    home: Path,
    workdir: Path,
    client: SpikeClient,
    profile: str = DEFAULT_PROFILE,
    patches: list[Path] | None = None,
    env_extra: dict[str, str] | None = None,
):
    args = ["--profile", profile]
    for patch in patches or []:
        args += ["--patch", str(patch)]
    env = dsh_env(home, env_extra)
    workdir.mkdir(parents=True, exist_ok=True)
    async with spawn_agent_process(
        client, DSH_BIN, *args, cwd=str(workdir), env=env,
    ) as (conn, process):
        yield conn, process


async def initialize(conn: Any) -> Any:
    return await conn.initialize(
        protocol_version=PROTOCOL_VERSION,
        client_capabilities=ClientCapabilities(),
        client_info=Implementation(name="qwenpaw-dsh-spike", version="0.1.0"),
    )


# ---------------------------------------------------------------------------
# Check / result bookkeeping
# ---------------------------------------------------------------------------
@dataclass
class Check:
    name: str
    ok: bool | None = None          # None = pending / not verifiable
    required: bool = True
    detail: str = ""


class Result:
    def __init__(self, spike: str, title: str):
        self.spike = spike
        self.title = title
        self.started = time.time()
        self.checks: list[Check] = []
        self.observations: dict[str, Any] = {}
        self.notes: list[str] = []
        self.raw: dict[str, Any] = {}
        self.env: dict[str, Any] = {}

    def check(self, name: str, ok: bool | None, detail: str = "", required: bool = True) -> Check:
        c = Check(name=name, ok=ok, required=required, detail=detail)
        self.checks.append(c)
        return c

    def note(self, text: str) -> None:
        self.notes.append(text)

    def observe(self, key: str, value: Any) -> None:
        self.observations[key] = value

    def status(self) -> str:
        required_failed = [c for c in self.checks if c.required and c.ok is False]
        if required_failed:
            return "fail"
        pending = [c for c in self.checks if c.ok is None]
        optional_failed = [c for c in self.checks if not c.required and c.ok is False]
        if pending:
            return "pending_verification" if all(c.ok is None for c in self.checks) else "partial"
        if optional_failed:
            return "partial"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "spike": self.spike,
            "title": self.title,
            "status": self.status(),
            "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.started)),
            "durationS": round(time.time() - self.started, 2),
            "env": self.env,
            "checks": [
                {"name": c.name, "ok": c.ok, "required": c.required, "detail": c.detail}
                for c in self.checks
            ],
            "observations": self.observations,
            "notes": self.notes,
            "raw": self.raw,
        }


def write_result(result: Result) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{result.spike}.json"
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI plumbing shared by every spike
# ---------------------------------------------------------------------------
def base_argparser(spike: str, title: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=f"{spike}: {title}")
    p.add_argument("--dsh-home", default=None, help="isolated DSH_HOME (default: $DSH_SPIKE_HOME or a temp dir)")
    p.add_argument("--seed", choices=("fresh", "user"), default=os.environ.get("DSH_SPIKE_SEED", "fresh"),
                   help="'fresh' = shipped acp template, 'user' = copy ~/.dsh profile")
    p.add_argument("--profile", default=DEFAULT_PROFILE)
    p.add_argument("--dry-run", action="store_true", help="print the plan; do not spawn dsh")
    p.add_argument("--keep", action="store_true", help="keep generated work dirs")
    return p


def bootstrap(args: argparse.Namespace) -> tuple[Path, dict[str, Any], Path]:
    home = resolve_dsh_home(args.dsh_home)
    desc = seed_dsh_home(home, args.seed)
    work = SPIKES_WORK_ROOT / f"{Path(sys.argv[0]).stem}-{os.getpid()}"
    work.mkdir(parents=True, exist_ok=True)
    return home, desc, work


def fill_env(result: Result, home: Path, desc: dict[str, Any], args: argparse.Namespace) -> None:
    result.env = {
        "dsh_bin": DSH_BIN,
        "dsh_version": dsh_version(),
        "dsh_home": str(home),
        "seed": desc.get("seed"),
        "profile": args.profile,
        "python": sys.executable,
        "credentials": credentials_status(),
    }
    for note in desc.get("notes", []):
        result.note(note)


def run_spike(spike: str, title: str, runner: Callable[[argparse.Namespace, Result], Any]) -> int:
    args = base_argparser(spike, title).parse_args()
    if args.dry_run:
        print(f"[{spike}] dry-run: would seed an isolated DSH_HOME and run: {title}")
        print("          (no dsh process spawned, no result file written)")
        return 0
    result = Result(spike, title)
    try:
        home, desc, work = bootstrap(args)
        fill_env(result, home, desc, args)
        asyncio.run(runner(args, result, home, work))
    except Exception as exc:  # noqa: BLE001 - spikes must always emit a result
        import traceback
        result.check("spike completed without exception", False, f"{type(exc).__name__}: {exc}")
        result.raw["traceback"] = traceback.format_exc()
    path = write_result(result)
    print(f"\n[{spike}] status={result.status()} -> {path}")
    for c in result.checks:
        mark = {True: "PASS", False: "FAIL", None: "PENDING"}[c.ok]
        print(f"  {mark:7} {c.name}" + (f"  ({c.detail})" if c.detail else ""))
    for note in result.notes:
        print(f"  note: {note}")
    return 0 if result.status() in ("pass", "partial", "pending_verification") else 1


def jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(by_alias=True, exclude_none=True, mode="json")
    return obj
