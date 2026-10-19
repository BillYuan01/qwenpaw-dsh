# -*- coding: utf-8 -*-
"""s06 — image capability probe (contract)."""

from __future__ import annotations

import pytest

from conftest import assert_contract, require_check, run_spike


@pytest.mark.contract
def test_s06_image_probe(spike_args, work_root):
    result = run_spike("s06_image_probe", spike_args, work_root)
    assert_contract(result)

    image_ok = require_check(result, "initialize advertises promptCapabilities.image")
    if image_ok.ok is None:
        pytest.skip("image capability could not be resolved (model/billing)")
    if image_ok.ok is False:
        # On a text-only route the adapter must feature-detect before sending.
        rejected = require_check(result, "image prompt rejected while capability is false")
        if rejected.ok is not None:
            assert rejected.ok is True
        return

    accepted = require_check(result, "inline PNG prompt is accepted")
    if accepted.ok is not None:
        assert accepted.ok is True
    colour = require_check(result, "model identifies the image colour (red)")
    if colour.ok is not None:
        assert colour.ok is True
