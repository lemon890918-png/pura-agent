"""Tests for ModelRouter."""

from __future__ import annotations

import pytest

from pure_agent.model import (
    DEFAULT_TIER_CONFIG,
    ModelRouter,
    TIER_DEFAULTS,
    TierConfig,
)
from pure_agent.plan import PlanStep, StepKind


def _step(kind: StepKind) -> PlanStep:
    return PlanStep(id="x", plan_id="p", idx=0, kind=kind, action="x")


@pytest.mark.smoke
def test_tier_defaults_by_kind() -> None:
    assert TIER_DEFAULTS[StepKind.READ] == 0
    assert TIER_DEFAULTS[StepKind.CODE] == 1
    assert TIER_DEFAULTS[StepKind.VERIFY] == 2
    assert TIER_DEFAULTS[StepKind.PLAN] == 3


@pytest.mark.smoke
def test_router_pick_tier_by_kind() -> None:
    r = ModelRouter()
    assert r.pick_tier(_step(StepKind.READ)) == 0
    assert r.pick_tier(_step(StepKind.CODE)) == 1
    assert r.pick_tier(_step(StepKind.VERIFY)) == 2
    assert r.pick_tier(_step(StepKind.PLAN)) == 3


@pytest.mark.smoke
def test_router_pick_model_default() -> None:
    r = ModelRouter()
    model, max_tok = r.pick_model(_step(StepKind.CODE))
    # default config all uses MiniMax-M3
    assert model == "MiniMax-M3"
    assert max_tok == 4096


@pytest.mark.smoke
def test_router_pick_model_override_tier() -> None:
    cfg = TierConfig(
        models={0: "small", 1: "medium", 2: "large", 3: "xlarge"},
        max_tokens={0: 1024, 1: 2048, 2: 4096, 3: 8192},
    )
    r = ModelRouter(cfg)
    model, max_tok = r.pick_model(_step(StepKind.READ), override_tier=2)
    assert model == "large"
    assert max_tok == 4096


@pytest.mark.smoke
def test_default_tier_config_uses_minimax() -> None:
    assert DEFAULT_TIER_CONFIG.models[1] == "MiniMax-M3"
