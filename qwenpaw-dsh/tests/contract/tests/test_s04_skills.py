# -*- coding: utf-8 -*-
"""s04 — skills projection + name grammar (contract)."""

from __future__ import annotations

import pytest

from conftest import assert_contract, require_check, run_spike


@pytest.mark.contract
def test_s04_skills(spike_args, work_root):
    result = run_spike("s04_skills", spike_args, work_root)
    assert_contract(result)

    assert require_check(result, "--dump-config with skills overlay composes successfully").ok is True
    assert require_check(result, "composed config carries customSkillDirs").ok is True
    grammar = require_check(result, "skill-name grammar is kebab-case only: /^[a-z0-9]+(?:-[a-z0-9]+)*$/")
    if grammar.ok is not None:
        assert grammar.ok is True

    for name in (
        "catalog lists kebab-case skill",
        "catalog lists QwenPaw-metadata kebab skill",
        "catalog DROPS snake_case name",
    ):
        check = require_check(result, name)
        if check.ok is not None:
            assert check.ok is True
