"""Tool-call extractor: when the model describes tool calls in code blocks
or JS/JSON syntax instead of emitting them via the protocol, we extract
the call structure and convert it to a real tool invocation.

Patterns detected:
  1. ```typescript\nfunctions.write_file({"path":..., "content":...})\n```
  2. ```json\n{"name": "write_file", "arguments": {"path":..., "content":...}}\n```
  3. functions.write_file({"path":...})  (inline)
  4. write_file(path="...", content="...")  (python-style kwargs)

Returned: list of (tool_name, arguments_dict) tuples, or empty list.
"""
from __future__ import annotations

import json
import re
from typing import Any


def extract_tool_calls(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Find tool calls described as text in `text`. Returns list of (name, args)."""
    if not text:
        return []
    calls: list[tuple[str, dict[str, Any]]] = []

    # ── 1. JSON blocks: ```json\n{...}\n``` — match the outermost {}
    # We use a balanced-brace search because the content can contain nested
    # JSON (e.g. _raw wraps another object).
    for m in re.finditer(r"```(?:json|typescript)?\s*\n(\{)", text):
        start = m.start(1)
        depth = 0
        end = None
        in_string = False
        escape = False
        for i, ch in enumerate(text[start:], start=start):
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            continue
        block = text[start:end]
        try:
            d = json.loads(block)
        except Exception:
            continue
        # Try multiple tool-call shapes
        name = None
        args = {}
        if isinstance(d, dict):
            if "name" in d and "arguments" in d:
                name = d["name"]
                args = d["arguments"]
            elif "function" in d and isinstance(d["function"], dict):
                name = d["function"].get("name")
                args = d["function"].get("arguments", {})
            elif "_raw" in d and isinstance(d["_raw"], str):
                try:
                    inner = json.loads(d["_raw"])
                    if isinstance(inner, dict):
                        if "path" in inner or "content" in inner:
                            args = inner
                        if "name" in inner:
                            name = inner["name"]
                except Exception:
                    pass
            if not name and ("path" in d or "content" in d):
                # Whole block is the args
                args = d
        if not name and "path" in args:
            # Guess name from common args
            if "content" in args or "old_string" in args or "new_string" in args:
                name = "write_file" if "content" in args else "edit_file"
            else:
                name = "read_file"
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if name and isinstance(args, dict):
            calls.append((name, args))

    # ── 2. JS-style: functions.write_file({...}) or tools.write_file({...})
    js_re = re.compile(
        r"(?:functions|tools)\.(write_file|edit_file|read_file|create_file)\s*\(\s*(\{.*?\})\s*\)",
        re.DOTALL,
    )
    for m in js_re.finditer(text):
        name = m.group(1)
        arg_str = m.group(2)
        try:
            args = json.loads(arg_str)
        except json.JSONDecodeError:
            fixed = re.sub(r"(\w+):", r'"\1":', arg_str)
            fixed = fixed.replace("'", '"')
            try:
                args = json.loads(fixed)
            except Exception:
                continue
        if isinstance(args, dict):
            # Some models wrap the real call in {"_raw": "{...path...content...}"}
            if "_raw" in args and isinstance(args["_raw"], str):
                try:
                    inner = json.loads(args["_raw"])
                    if isinstance(inner, dict):
                        args = inner
                except Exception:
                    pass
            if "arguments" in args and isinstance(args["arguments"], (str, dict)):
                a = args["arguments"]
                if isinstance(a, str):
                    try:
                        a = json.loads(a)
                    except Exception:
                        pass
                if isinstance(a, dict):
                    args = a
            if "function" in args and isinstance(args["function"], dict):
                args = args["function"]
            calls.append((name, args))

    # ── 3. Python kwargs: write_file(path="...", content="...")
    py_re = re.compile(
        r"\b(write_file|edit_file|read_file|create_file)\s*\(\s*([^\)]+)\)",
        re.DOTALL,
    )
    for m in py_re.finditer(text):
        name = m.group(1)
        kw_str = m.group(2)
        # Parse key="value" pairs — group 1 wraps the full string so that the
        # * quantifier doesn't drop intermediate captures.
        args: dict[str, str] = {}
        for km in re.finditer(
            r'(\w+)\s*=\s*("((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\')',
            kw_str,
        ):
            key = km.group(1)
            val = km.group(3) if km.group(2) else km.group(5) or ""
            if key in ("path", "content", "old_string", "new_string", "old", "new", "query"):
                args[key] = val
        if args:
            calls.append((name, args))

    # Dedup: same (name, args) → keep only first
    seen = set()
    unique: list[tuple[str, dict[str, Any]]] = []
    for c in calls:
        key = (c[0], json.dumps(c[1], sort_keys=True, default=str))
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique
