# pure-agent

A pure self-built agent runtime with Goal/Plan long-running support.

## Status

Phase 0 — scaffold only. See [docs/03-master-plan.md](docs/03-master-plan.md) for the full plan.

## Why

Existing agent frameworks (LangChain, AutoGen, CrewAI) hide output behavior behind chains and abstractions. The user's pain point is agent output instability — LLM calls sometimes succeed, sometimes fail, with mismatched formats. pure-agent attacks this structurally:

- Typed Plan schema validation **before** tool execution
- Typed subagent protocol (no free-form text between agents)
- 4-layer memory (short / episodic / semantic / procedural) with white-box editing
- Goal → Plan → Step decomposition for long-running tasks
- 30+ minute sessions with checkpoint/recovery

## Non-goals (v1)

- Not a LangChain wrapper
- Not a Claude Code clone
- Not multi-tenant SaaS

## Install

```bash
uv sync
```

## Use

```bash
uv run pure-agent --version
uv run pure-agent --help
uv run pure-agent init
```

## Architecture

See [docs/](docs/) — `01-source-survey.md`, `02-architecture-decisions.md`, `03-master-plan.md`, `04-phase0-scaffold.md`.

## License

MIT
