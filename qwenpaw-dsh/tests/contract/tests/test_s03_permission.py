# -*- coding: utf-8 -*-
"""s03 — permission request shape + toolCallId merge (contract).

The spike's ``allow-once executes the approved tool`` check is intentionally
ignored here: the Phase-0 evidence established that under ``read-only``,
approving once permits the *attempt* but DSH still requires a second approval
for the ``workspace-write`` escalation. That is documented in ``spikes/README``
and Part D of the addendum; the contract this suite owns is the protocol shape.
"""

from __future__ import annotations

import pytest

from conftest import assert_contract, require_check, run_spike

KNOWN_ESCALATION = ("allow-once executes the approved tool",)


@pytest.mark.contract
def test_s03_permission(spike_args, work_root):
    result = run_spike("s03_permission", spike_args, work_root)
    # The reject path depends on the model choosing to attempt a write in a
    # fresh session. When that single model-dependent check misses, retry the
    # spike once before treating it as a regression.
    reject = require_check(result, "a permission request was captured (reject path)")
    if reject.ok is False:
        result = run_spike("s03_permission", spike_args, work_root)
    assert_contract(result, ignored=KNOWN_ESCALATION)

    assert require_check(result, "a permission request was captured (allow path)").ok is True
    assert require_check(result, "permission options are one-shot allow/reject").ok is True
    assert require_check(result, "permission toolCall carries only toolCallId").ok is True
    assert require_check(result, "toolCallId can be merged with an earlier tool_call update").ok is True
    assert require_check(result, "a permission request was captured (reject path)").ok is True
    assert require_check(result, "reject path selects a reject option").ok is True
    assert require_check(result, "reject path does not execute the tool").ok is True
