# -*- coding: utf-8 -*-
"""s02 — session resume across process restart (contract)."""

from __future__ import annotations

import pytest

from conftest import assert_contract, require_check, run_spike


@pytest.mark.contract
def test_s02_session_resume(spike_args, work_root):
    result = run_spike("s02_session_resume", spike_args, work_root)
    assert_contract(result)

    assert require_check(result, "process A really exited").ok is True
    assert require_check(result, "session/list includes the closed session").ok is True
    assert require_check(result, "resume of unknown session id is rejected").ok is True
    assert require_check(result, "resume with a different cwd is rejected").ok is True
    assert require_check(result, "session/resume succeeds for same cwd").ok is True
    assert require_check(result, "resume does not replay historical updates").ok is True

    recall = require_check(result, "resumed session restored conversation context (codeword recalled)")
    if recall.ok is not None:
        assert recall.ok is True
