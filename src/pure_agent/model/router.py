"""Model router — tier-based model selection.

Phase 3: manual tier override only.
Phase 5: add prompt-based tier judge + auto-routing.

To avoid circular imports with plan.models, this module uses string kind
identifiers rather than the StepKind enum.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


# Tier 0 = smallest/cheapest, Tier 3 = largest/most capable
TIER_DEFAULTS: dict[str, int] = {
    "read": 0,
    "search": 1,
    "code": 1,
    "verify": 2,
    "deliver": 2,
    "plan": 3,
}


@dataclass
class TierConfig:
    models: dict[int, str]  # tier -> model name
    max_tokens: dict[int, int]  # tier -> max output tokens

    def model_for(self, tier: int) -> str:
        return self.models.get(tier, self.models.get(1, "MiniMax-M3"))

    def max_tokens_for(self, tier: int) -> int:
        return self.max_tokens.get(tier, 4096)


DEFAULT_TIER_CONFIG = TierConfig(
    models={
        0: "MiniMax-M3",
        1: "MiniMax-M3",
        2: "MiniMax-M3",
        3: "MiniMax-M3",
    },
    max_tokens={
        0: 2048,
        1: 4096,
        2: 8192,
        3: 16384,
    },
)


class ModelRouter:
    """Decide which model to use for which step.

    Phase 3: tier is derived from step kind (or override) but we only have
    one model wired up. The router is still useful as a placeholder + for
    max_tokens + override surface.
    """

    def __init__(self, config: TierConfig | None = None) -> None:
        self.config = config or DEFAULT_TIER_CONFIG

    def pick_tier_for_kind(self, kind: str) -> int:
        """Pick a tier based on a string kind identifier.

        Accepts the enum value string (e.g. "code") or a StepKind-like value.
        """
        return TIER_DEFAULTS.get(kind, 1)

    def pick_tier(self, step: Any) -> int:
        """Pick a tier for a step. Accepts anything with .kind (enum or str)."""
        kind = getattr(step, "kind", None)
        if kind is None:
            return 1
        kind_value = getattr(kind, "value", str(kind))
        return self.pick_tier_for_kind(kind_value)

    def pick_model(self, step: Any, *, override_tier: int | None = None) -> tuple[str, int]:
        tier = override_tier if override_tier is not None else self.pick_tier(step)
        return self.config.model_for(tier), self.config.max_tokens_for(tier)


__all__ = [
    "ModelRouter",
    "TierConfig",
    "TIER_DEFAULTS",
    "DEFAULT_TIER_CONFIG",
]
