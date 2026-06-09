"""Memory layer: 4 layers + compactor + file tracker.

Phase 3: implement the four memory layers and the auto-compactor.
The four layers (per master plan):
  - L1 short-term: in-context, the current conversation messages
  - L2 episodic: per-session facts derived from step reports
  - L3 semantic: per-project facts (e.g., "uses Python 3.12")
  - L4 procedural: per-user preferences (e.g., "prefers Chinese")
"""

from __future__ import annotations

from pure_agent.memory.compactor import CompactionResult, Compactor
from pure_agent.memory.context_builder import ContextBuilder, ContextBudget
from pure_agent.memory.context_switch import ContextSwitcher, SessionSnapshot
from pure_agent.memory.fact_extractor import extract_episodic, extract_facts
from pure_agent.memory.l1_short import L1Cache, L1Item
from pure_agent.memory.layers import (
    EpisodicMemory,
    MemoryLayers,
    ProceduralMemory,
    SemanticMemory,
    ShortTermMemory,
)
from pure_agent.memory.tracker import FileState, FileTracker, compute_content_hash

__all__ = [
    "CompactionResult",
    "Compactor",
    "ContextBuilder",
    "ContextBudget",
    "ContextSwitcher",
    "EpisodicMemory",
    "FileState",
    "FileTracker",
    "L1Cache",
    "L1Item",
    "MemoryLayers",
    "ProceduralMemory",
    "SemanticMemory",
    "SessionSnapshot",
    "ShortTermMemory",
    "compute_content_hash",
    "extract_episodic",
    "extract_facts",
]
