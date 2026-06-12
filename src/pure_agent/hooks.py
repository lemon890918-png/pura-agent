"""Hook system for pure-agent.

Inspired by Claude Code's hooks (PreToolUse, PostToolUse, etc).
Each hook is a Python function that takes a HookContext and returns a
HookResult. Hooks live in ~/.pure-agent/hooks/<event>/<name>.py or
<project>/.pure-agent/hooks/<event>/<name>.py.

HookContext:
  event: str                # "PreToolUse" | "PostToolUse" | etc
  tool_name: str | None     # the tool being called (None for non-tool events)
  tool_args: dict | None    # tool arguments
  tool_result: ToolResult | None  # result of tool call (PostToolUse only)
  user_message: str | None  # user prompt (UserPromptSubmit only)
  project_root: Path
  extra: dict               # free-form scratch space shared across hooks

HookResult:
  action: "allow" | "deny" | "modify"
  deny_reason: str | None   # filled if action="deny"
  modified_args: dict | None  # filled if action="modify"
  extra: dict               # propagated to next hook
  message: str | None       # optional feedback to inject into agent
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

log = logging.getLogger("pure_agent.hooks")


@dataclass
class HookContext:
    event: str
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: Any = None
    user_message: str | None = None
    project_root: Path | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class HookResult:
    action: Literal["allow", "deny", "modify"] = "allow"
    deny_reason: str | None = None
    modified_args: dict | None = None
    extra: dict = field(default_factory=dict)
    message: str | None = None  # optional user-facing message

    @classmethod
    def ok(cls, extra: dict | None = None, message: str | None = None) -> "HookResult":
        return cls(action="allow", extra=extra or {}, message=message)

    @classmethod
    def deny_(cls, reason: str, extra: dict | None = None) -> "HookResult":
        return cls(action="deny", deny_reason=reason, extra=extra or {})

    @classmethod
    def modify_(cls, new_args: dict, extra: dict | None = None, message: str | None = None) -> "HookResult":
        return cls(action="modify", modified_args=new_args, extra=extra or {}, message=message)


HookFn = Callable[[HookContext], HookResult | None]


class HookRegistry:
    """Discover and run hooks for events.

    Discovery locations (later overrides earlier):
      1. <project>/.pure-agent/hooks/<event>/*.py
      2. ~/.pure-agent/hooks/<event>/*.py
      3. built-in hooks registered via register()

    A hook module exposes either:
      - HOOK(event, ctx) -> HookResult
      - HOOKS = [(event, fn), ...]
      - A class `Hook` with `__call__(ctx)`
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookFn]] = {}
        self._loaded: set[str] = set()

    def register(self, event: str, fn: HookFn) -> None:
        self._hooks.setdefault(event, []).append(fn)

    def load_directory(self, d: Path) -> int:
        if not d.exists():
            return 0
        count = 0
        for event_dir in d.iterdir():
            if not event_dir.is_dir():
                continue
            event = event_dir.name
            for hook_file in event_dir.glob("*.py"):
                key = str(hook_file.resolve())
                if key in self._loaded:
                    continue
                self._loaded.add(key)
                try:
                    count += self._load_file(event, hook_file)
                except Exception as e:
                    log.warning("failed to load hook %s: %s", hook_file, e)
        return count

    def _load_file(self, event: str, path: Path) -> int:
        spec = importlib.util.spec_from_file_location(f"hook_{path.stem}", path)
        if spec is None or spec.loader is None:
            return 0
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        n = 0
        if hasattr(mod, "HOOK"):
            self.register(event, mod.HOOK)
            n += 1
        for evt, fn in getattr(mod, "HOOKS", []):
            self.register(evt, fn)
            n += 1
        if hasattr(mod, "Hook") and inspect.isclass(mod.Hook):
            self.register(event, mod.Hook())
            n += 1
        return n

    def run(self, event: str, ctx: HookContext) -> HookResult:
        """Run all hooks for an event. First deny wins; modifications
        are chained. Returns final HookResult."""
        result = HookResult.ok(extra=ctx.extra.copy())
        for fn in self._hooks.get(event, []):
            try:
                r = fn(ctx)
            except Exception as e:
                log.warning("hook %s failed: %s", fn, e)
                continue
            if r is None:
                continue
            # Chain extras forward
            if r.extra:
                result.extra.update(r.extra)
                ctx.extra = result.extra
            if r.action == "deny":
                return r
            if r.action == "modify" and r.modified_args is not None:
                if ctx.tool_args is None:
                    ctx.tool_args = {}
                ctx.tool_args.update(r.modified_args)
                result.action = "modify"
            if r.message:
                result.message = r.message
        return result


# ── built-in hooks ────────────────────────────────────────────────────────


def _auto_format_python(ctx: HookContext) -> HookResult | None:
    """PostToolUse on write_file/edit_file: run `ruff format` if the
    file is Python and the project has ruff installed. Non-fatal on
    failure — formatting is a quality-of-life improvement, not a
    correctness gate."""
    if ctx.event != "PostToolUse":
        return None
    if ctx.tool_name not in ("write_file", "edit_file", "apply_patch"):
        return None
    if not ctx.tool_args:
        return None
    path = ctx.tool_args.get("path", "")
    if not path.endswith(".py"):
        return None
    project_root = ctx.project_root
    if not project_root:
        return None
    target = Path(path)
    if not target.is_absolute():
        target = project_root / target
    if not target.exists():
        return None
    try:
        import subprocess
        r = subprocess.run(
            ["ruff", "format", "--quiet", str(target)],
            capture_output=True, timeout=10, cwd=project_root,
        )
        if r.returncode == 0:
            return HookResult.ok(message="ruff formatted the file")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # ruff not installed or timed out — skip silently
        pass
    except Exception as e:
        log.debug("ruff format hook: %s", e)
    return None


def _warn_destructive(ctx: HookContext) -> HookResult | None:
    """PreToolUse on write_file: warn (not block) if path looks risky
    (e.g. overwriting __init__.py in src/, or writing outside expected
    project subdirs). Returns allow; just emits a reminder message."""
    if ctx.event != "PreToolUse":
        return None
    if ctx.tool_name != "write_file":
        return None
    if not ctx.tool_args:
        return None
    path = ctx.tool_args.get("path", "")
    # Risky patterns — non-blocking reminder
    risky = []
    if path.startswith("..") or "/../" in path:
        risky.append("path escapes project root")
    if path.endswith("__init__.py") and "src/" in path:
        risky.append("overwriting __init__.py in src/ may break imports")
    if path in ("pyproject.toml", "setup.py", "Cargo.toml", "package.json"):
        risky.append(f"modifying {path} affects project config")
    if risky:
        return HookResult.ok(message="; ".join(risky))
    return None


def _auto_test_on_python_edit(ctx: HookContext) -> HookResult | None:
    """PostToolUse on write_file/edit_file: if the file is Python and
    there are nearby tests, run them and feed back results. Helps
    catch regressions in single-shot edits."""
    if ctx.event != "PostToolUse":
        return None
    if ctx.tool_name not in ("write_file", "edit_file", "apply_patch"):
        return None
    if not ctx.tool_args or not ctx.tool_result or not ctx.tool_result.is_success:
        return None
    path = ctx.tool_args.get("path", "")
    if not path.endswith(".py"):
        return None
    project_root = ctx.project_root
    if not project_root:
        return None
    target = Path(path)
    if not target.is_absolute():
        target = project_root / target
    # Look for test files: tests/test_<name>.py, <name>_test.py
    stem = target.stem
    candidates = [
        project_root / "tests" / f"test_{stem}.py",
        project_root / "tests" / f"test_{target.name}",
        project_root / f"test_{target.name}",
    ]
    test_file = next((c for c in candidates if c.exists()), None)
    if not test_file:
        return None
    try:
        import subprocess
        r = subprocess.run(
            ["python3", "-m", "pytest", str(test_file), "-q", "--tb=line", "--no-header"],
            capture_output=True, text=True, timeout=60, cwd=project_root,
        )
        if r.returncode == 0:
            return HookResult.ok(
                message=f"pytest {test_file.name} passed",
            )
        else:
            tail = (r.stdout + r.stderr).strip().splitlines()[-5:]
            return HookResult.ok(
                message=f"pytest {test_file.name} FAILED:\n" + "\n".join(tail),
            )
    except subprocess.TimeoutExpired:
        return HookResult.ok(message=f"pytest {test_file.name} timed out")
    except FileNotFoundError:
        return None
    except Exception as e:
        log.debug("auto-test hook: %s", e)
    return None


def register_builtins(reg: HookRegistry) -> None:
    reg.register("PostToolUse", _auto_format_python)
    reg.register("PreToolUse", _warn_destructive)
    reg.register("PostToolUse", _auto_test_on_python_edit)


__all__ = [
    "HookContext",
    "HookResult",
    "HookRegistry",
    "HookFn",
    "register_builtins",
]
