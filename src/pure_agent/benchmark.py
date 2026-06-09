"""Benchmark runner: measure pure-agent vs PilotDeck on standard tasks.

Phase 9. Run a set of tasks, time them, count tokens, report.

Usage:
    uv run python -m pure_agent.benchmark
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

hermes_env = dotenv_values("/Users/wenxin/.hermes/.env")
DEFAULT_KEY = hermes_env.get("MINIMAX_API_KEY", "")
DEFAULT_MODEL = "MiniMax-Text-01"
DEFAULT_BASE_URL = "https://api.minimaxi.com/v1"


@dataclass
class BenchmarkTask:
    """A single benchmark task."""

    name: str
    description: str
    prompt: str
    expected_keywords: list[str] = field(default_factory=list)
    max_turns: int = 10


# ─── 5 standard tasks ─────────────────────────────────────────────────


TASKS: list[BenchmarkTask] = [
    BenchmarkTask(
        name="summarize_file",
        description="Read a Python file and return a one-sentence summary",
        prompt=(
            "Read the file /tmp/bench_utils.py and write a single sentence "
            "summarizing what it does. Reply with only the summary sentence."
        ),
        expected_keywords=["function", "multiply", "divide"],
    ),
    BenchmarkTask(
        name="fix_typo",
        description="Fix a typo in a code file",
        prompt=(
            "Read /tmp/bench_typo.py, find the typo (def mulitply instead of "
            "multiply), fix it with edit_file, and confirm."
        ),
        expected_keywords=["fixed", "multiply"],
    ),
    BenchmarkTask(
        name="plan_refactor",
        description="Generate a typed plan for refactoring",
        prompt=(
            "I want to refactor a 1000-line legacy Python module. "
            "Decompose this into a typed plan with 3-5 steps. Return the plan "
            "as ```json ... ```."
        ),
        expected_keywords=["json", "steps"],
    ),
    BenchmarkTask(
        name="search_and_write",
        description="Search the web and write a summary",
        prompt=(
            "Search the web for 'Python pydantic v2' and write a 2-sentence "
            "summary of what it is. Use web_search then write_file to /tmp/bench_search.md."
        ),
        expected_keywords=["pydantic", "validation"],
    ),
    BenchmarkTask(
        name="multi_step_plan",
        description="Multi-step plan: read 2 files, add a function, verify",
        prompt=(
            "Decompose this into a plan and execute it: "
            "1. Read /tmp/bench_a.py and /tmp/bench_b.py. "
            "2. Add a function 'combine(a, b)' to /tmp/bench_a.py that returns a+b. "
            "3. Verify by running 'python /tmp/bench_a.py'."
        ),
        expected_keywords=["combine", "add"],
    ),
]


# ─── task setup helpers ────────────────────────────────────────────────


def setup_files(workspace: Path) -> None:
    """Create the files used by benchmark tasks."""
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "bench_utils.py").write_text(
        "def multiply(a, b):\n"
        "    return a * b\n"
        "\n"
        "def divide(a, b):\n"
        "    if b == 0:\n"
        "        return None\n"
        "    return a / b\n"
    )
    (workspace / "bench_typo.py").write_text(
        "def mulitply(a, b):  # typo: should be 'multiply'\n"
        "    return a * b\n"
    )
    (workspace / "bench_a.py").write_text(
        "def hello():\n"
        "    return 'hello'\n"
    )
    (workspace / "bench_b.py").write_text(
        "def world():\n"
        "    return 'world'\n"
    )


# ─── runner ─────────────────────────────────────────────────────────────


@dataclass
class TaskResult:
    """Result of running one benchmark task."""

    name: str
    elapsed_s: float
    success: bool
    response: str
    keyword_hits: list[str]
    keyword_misses: list[str]
    usage: dict[str, int] = field(default_factory=dict)
    error: str | None = None


async def run_task(
    task: BenchmarkTask,
    *,
    api_key: str,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    project_root: str = "/tmp",
) -> TaskResult:
    """Run a single benchmark task."""
    from pure_agent.agent import AIAgentLoop
    from pure_agent.model.minimax_adapter import MinimaxAdapter
    from pure_agent.tools import (
        EditFileTool,
        GlobTool,
        GrepTool,
        ReadFileTool,
        Sandbox,
        ToolRegistry,
        WebSearchTool,
        WriteFileTool,
    )
    from pathlib import Path as P

    sandbox = Sandbox(root=P(project_root))
    reg = ToolRegistry()
    reg.register(ReadFileTool(sandbox))
    reg.register(WriteFileTool(sandbox))
    reg.register(EditFileTool(sandbox))
    reg.register(GlobTool(sandbox))
    reg.register(GrepTool(sandbox))
    reg.register(WebSearchTool())

    provider = MinimaxAdapter(api_key=api_key, model=model, base_url=base_url)
    loop = AIAgentLoop(
        provider=provider,
        tools=reg,
        model=model,
        system_prompt=(
            "You are a benchmark agent. Complete the task accurately and concisely."
        ),
        max_turns=task.max_turns,
    )

    t0 = time.monotonic()
    error = None
    response = ""
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    try:
        result = await loop.run(task.prompt)
        response = result.final_text or ""
        usage = {
            "prompt_tokens": result.total_usage.prompt_tokens,
            "completion_tokens": result.total_usage.completion_tokens,
            "total_tokens": result.total_usage.total_tokens,
        }
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    elapsed = time.monotonic() - t0

    keyword_hits = [k for k in task.expected_keywords if k.lower() in response.lower()]
    keyword_misses = [k for k in task.expected_keywords if k.lower() not in response.lower()]
    success = error is None and len(keyword_misses) == 0

    return TaskResult(
        name=task.name,
        elapsed_s=elapsed,
        success=success,
        response=response[:200],
        keyword_hits=keyword_hits,
        keyword_misses=keyword_misses,
        usage=usage,
        error=error,
    )


async def run_all(
    *,
    api_key: str = DEFAULT_KEY,
    project_root: str = "/tmp",
    output_path: str | None = None,
) -> dict[str, Any]:
    """Run all tasks and return a summary report."""
    if not api_key:
        return {"error": "no MINIMAX_API_KEY"}
    results: list[TaskResult] = []
    for task in TASKS:
        print(f"\n=== {task.name} ===")
        r = await run_task(task, api_key=api_key, project_root=project_root)
        print(f"  elapsed: {r.elapsed_s:.1f}s")
        print(f"  success: {r.success}")
        print(f"  hits: {r.keyword_hits}")
        if r.keyword_misses:
            print(f"  misses: {r.keyword_misses}")
        if r.error:
            print(f"  error: {r.error}")
        print(f"  response[:200]: {r.response[:200]}")
        results.append(r)

    n_success = sum(1 for r in results if r.success)
    total_time = sum(r.elapsed_s for r in results)
    total_tokens = sum(r.usage.get("total_tokens", 0) for r in results)

    report = {
        "n_tasks": len(results),
        "n_success": n_success,
        "n_failed": len(results) - n_success,
        "total_time_s": total_time,
        "avg_time_s": total_time / max(1, len(results)),
        "total_tokens": total_tokens,
        "results": [asdict(r) for r in results],
    }

    if output_path:
        Path(output_path).write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\nreport saved to {output_path}")
    return report


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", default=os.environ.get("MINIMAX_API_KEY", DEFAULT_KEY))
    parser.add_argument("--project-root", default="/tmp")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()
    report = asyncio.run(
        run_all(
            api_key=args.api_key,
            project_root=args.project_root,
            output_path=args.output,
        )
    )
    print("\n========== SUMMARY ==========")
    print(f"tasks: {report.get('n_tasks', 0)}")
    print(f"success: {report.get('n_success', 0)}")
    print(f"failed: {report.get('n_failed', 0)}")
    print(f"total time: {report.get('total_time_s', 0):.1f}s")
    print(f"avg time: {report.get('avg_time_s', 0):.1f}s/task")
    print(f"total tokens: {report.get('total_tokens', 0)}")


if __name__ == "__main__":
    main()
