# DSH ↔ QwenPaw contract tests

End-to-end acceptance criteria for the integration: each test drives a **real
`dsh` process** over ACP v1 through the spike runners, then asserts the protocol
checks it observed. Model-dependent gaps are reported as warnings/skips instead
of architecture failures.

> **Provenance**: ported into this repository on 2026-10-19 from the Phase-0
> research tree (`dsh-integration-research/{spikes,contract-tests}`), which is
> where the protocol behaviour was originally measured. The spike runners now
> ship next to the suite (`spikes/`), so this directory is self-contained.

## Layout

```
tests/contract/
├── pytest              # launcher (uses the QwenPaw interpreter)
├── run-contract.sh     # alias for ./pytest -m contract
├── conftest.py         # fixtures: isolated DSH_HOME, profile materialisation
├── spikes/             # s01…s06 runners + spike_lib + the MCP fixture server
├── tests/              # test_s01…test_s06 (pytest wrapper around the runners)
└── .contract-test/     # scratch state created at run time (gitignored)
```

## Run

```sh
cd qwenpaw-dsh/tests/contract
./pytest -m contract                                  # profile=acp   (stock DSH)
DSH_CONTRACT_PROFILE=qwenpaw ./pytest -m contract      # bridge-backed profile
```

`./pytest` executes the shared interpreter (`~/.qwenpaw/venv/bin/python`,
override with `DSH_CONTRACT_PYTHON`), so the ACP SDK version matches QwenPaw.
An optional `.vendor/` directory is prepended to `PYTHONPATH` when present, for
environments where the venv cannot install pytest.

The `qwenpaw` profile is created on first use from `dsh-base` + `dsh-acp-app` +
`@local/dsh-qwenpaw-bridge`; the bridge is resolved from the repository layout
(`<repo>/dsh-qwenpaw-bridge`) or from `DSH_CONTRACT_BRIDGE`.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `DSH_CONTRACT_PROFILE` | `acp` | profile to boot |
| `DSH_CONTRACT_SEED` | `user` | `user` copies `~/.dsh/profiles/<profile>`; `fresh` lets DSH materialize the shipped template |
| `DSH_CONTRACT_HOME` | `.contract-test/home` | isolated `DSH_HOME`; credentials are symlinked, never copied |
| `DSH_CONTRACT_WORK` | `.contract-test/work` | per-test scratch directory |
| `DSH_CONTRACT_BRIDGE` | `<repo>/dsh-qwenpaw-bridge` | bridge package used for the `qwenpaw` profile |
| `DSH_CONTRACT_PYTHON` | `~/.qwenpaw/venv/bin/python` | interpreter that runs pytest |

## Coverage

| Test | Spike | Criteria |
|---|---|---|
| `test_s01_roundtrip.py` | s01 | initialize/session/new/prompt/cancel/close; model config option; streaming granularity measured |
| `test_s02_session_resume.py` | s02 | cross-process resume, context recall, wrong-cwd/unknown-id negatives, no replay |
| `test_s03_permission.py` | s03 | permission capture, one-shot options, `toolCallId`-only payload, merge, reject path |
| `test_s04_skills.py` | s04 | `customSkillDirs` compose, kebab-case grammar, catalog inclusion/drop |
| `test_s05_mcp.py` | s05 | good MCP server, `failOnStartupError`, retry without MCP |
| `test_s06_image_probe.py` | s06 | `promptCapabilities.image`; inline PNG when true, readable rejection when false |

**Known semantic, deliberately ignored:** s03's
`allow-once executes the approved tool` is excluded from required failures. Under
`read-only`, approving once permits the *attempt*, but DSH still requires a
second approval for the `workspace-write` escalation. The suite still asserts the
permission protocol shape that the QwenPaw adapter consumes.

## Result policy

- A required check that is `False` fails the test with the spike's detail.
- If every meaningful check is pending (usually a billing/quota outage), the
  test is skipped rather than reported as an architecture failure.
- If some model-dependent checks are pending while protocol checks pass, the
  test passes with a warning listing the pending checks.
- Optional checks (for example s01's "delivery is incremental" on the stock
  `acp` profile, or s06's text-only-route branch) never fail the suite.

## Verified runs

| Date | Profile | Result |
|---|---|---|
| 2026-10-19 | `acp` (stock DSH 0.2.0-rc.2) | **6 passed**, 2 warnings, 126 s |
| 2026-10-19 | `qwenpaw` (bridge-backed) | **6 passed**, 2 warnings, 108 s |

The two warnings are `pydantic` serializer notices raised by the ACP SDK while
deserialising `initialize` (`AgentAuthCapabilities`), not test failures.
