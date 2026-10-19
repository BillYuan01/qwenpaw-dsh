# -*- coding: utf-8 -*-
"""s01 — ACP full chain + streaming granularity (contract)."""

from __future__ import annotations

import pytest

from conftest import assert_contract, require_check, run_spike


@pytest.mark.contract
def test_s01_acp_roundtrip(spike_args, work_root):
    result = run_spike("s01_acp_roundtrip", spike_args, work_root)
    assert_contract(result)

    assert require_check(result, "initialize.protocolVersion == 1").ok is True
    assert require_check(result, "initialize.authMethods empty (no login)").ok is True
    assert require_check(result, "sessionCapabilities has list+resume+close").ok is True
    assert require_check(result, "session/new returns a sessionId").ok is True
    assert require_check(result, "configOptions advertises 'model'").ok is True
    assert require_check(result, "streaming granularity measured (non-empty long answer)").ok is True

    prompt = require_check(result, "prompt stopReason == end_turn")
    if prompt.ok is not None:
        assert prompt.ok is True
    cancel = require_check(result, "cancel path yields stopReason 'cancelled'")
    if cancel.ok is not None:
        assert cancel.ok is True
    assert require_check(result, "session/close succeeds").ok is True
