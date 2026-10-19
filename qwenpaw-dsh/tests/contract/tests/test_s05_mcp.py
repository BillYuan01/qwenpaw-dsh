# -*- coding: utf-8 -*-
"""s05 — MCP session/new: good server, bad server, rollback (contract)."""

from __future__ import annotations

import pytest

from conftest import assert_contract, require_check, run_spike


@pytest.mark.contract
def test_s05_mcp(spike_args, work_root):
    result = run_spike("s05_mcp", spike_args, work_root)
    assert_contract(result)

    mcp_pkg = require_check(result, "python env has the 'mcp' package (for the good server)")
    if mcp_pkg.ok is not True:
        pytest.skip("the 'mcp' package is not importable in this interpreter; MCP checks are skipped")
    assert require_check(result, "session/new with a good stdio MCP server succeeds").ok is True
    assert require_check(result, "session/new with a bad MCP server fails (failOnStartupError)").ok is True
    assert require_check(result, "retry without MCP succeeds (rollback is viable)").ok is True

    for name in ("MCP tool appears in the tool lifecycle", "MCP tool result PONG reached the model"):
        check = require_check(result, name)
        if check.ok is not None:
            assert check.ok is True
