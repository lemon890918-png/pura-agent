# Phase 5 — Subagent + Harness 详细设计

═══════════════════════════════════════════════
0. 目标
═══════════════════════════════════════════════

把 pure-agent 升级到支持 typed Subagent + 完整 Harness：

  - 4 个内置 subagent (general-purpose / explore / plan / verify)
  - typed SubagentRequest / SubagentResponse (pydantic 强类型)
  - Subagent lifecycle: spawn / run / collect / cleanup
  - Harness: retry / timeout / trace / span
  - PlanRunner 集成 subagent + harness
  - token 累加修 Phase 4 遗留

承诺:
  - 4 内置 subagent 真实跑通
  - typed request/response 全部 pydantic 校验
  - retry 完整支持
  - 端到端用 subagent 拆 plan

═══════════════════════════════════════════════
1. Subagent 数据模型
═══════════════════════════════════════════════

1.1 SubagentRole
    - general_purpose: read/write anything
    - explore: read only, no writes
    - plan: decomposes Goal → Plan
    - verify: read + run, no writes

1.2 SubagentRequest (typed)
    - task_id: str
    - role: SubagentRole
    - prompt: str
    - tools_allow: list[str]  (whitelist)
    - tools_deny: list[str]   (blacklist)
    - context: dict[str, Any] (extra context)
    - max_turns: int = 10
    - max_tokens: int | None
    - timeout_s: float = 300.0
    - read_only: bool = False

1.3 SubagentResponse (typed)
    - task_id: str
    - role: SubagentRole
    - status: pending | running | done | failed | timeout
    - result: Any  (role-specific)
    - summary: str
    - files_changed: list[str]
    - usage: Usage
    - turns: int
    - error: str | None
    - started_at / completed_at

═══════════════════════════════════════════════
2. Subagent Lifecycle
═══════════════════════════════════════════════

2.1 spawn(req: SubagentRequest) -> SubagentHandle
    - create new AIAgentLoop
    - apply tool whitelist/blacklist
    - set read_only if role requires
    - return handle (future, status)

2.2 await handle.result() -> SubagentResponse
    - run loop in background task
    - collect result

2.3 cancel(handle) -> None
    - signal abort to loop

2.4 status(handle) -> SubagentStatus
    - query state

═══════════════════════════════════════════════
3. 4 内置 Subagent
═══════════════════════════════════════════════

3.1 general-purpose
    - system prompt: "You are a general-purpose agent. You can use any tool."
    - tools: all
    - read_only: False
    - use case: complex multi-step tasks

3.2 explore
    - system prompt: "You are a code explorer. Read-only — never modify files."
    - tools: read_file / glob / grep / web_search
    - read_only: True
    - use case: gather information, find files

3.3 plan
    - system prompt: "You are a planning agent. Decompose goals into steps."
    - tools: read_file / glob / grep
    - read_only: True
    - use case: PlanAgent
    - special: returns a Plan object

3.4 verify
    - system prompt: "You are a verifier. Check that the work matches the spec."
    - tools: read_file / glob / grep / bash
    - read_only: True
    - use case: verify step_report vs actual state

═══════════════════════════════════════════════
4. Harness
═══════════════════════════════════════════════

4.1 RetryPolicy
    - max_attempts: int
    - backoff: exponential
    - retryable_errors: list[str]

4.2 TimeoutPolicy
    - per_call_s: float
    - per_total_s: float

4.3 Trace
    - 每个 LLM call, tool call, retry 写入 traces 表
    - trace_id, span_id, parent_span_id
    - payload JSON

4.4 Span
    - 子操作的时间区间
    - parent/child 关系

═══════════════════════════════════════════════
5. 关键模块
═══════════════════════════════════════════════

  agent/subagent.py     Subagent 抽象 + 4 内置
  agent/handle.py       SubagentHandle 生命周期
  harness/policy.py     RetryPolicy / TimeoutPolicy
  harness/trace.py      Trace / Span
  harness/decorator.py  @with_retry / @with_timeout

  plan/runner.py        集成 subagent (verify step / explore context)

  tests/
  test_subagent.py
  test_harness.py
  test_subagent_integration.py

═══════════════════════════════════════════════
6. 验收清单
═══════════════════════════════════════════════

  6.1 单元
    □ SubagentRequest / SubagentResponse pydantic 校验
    □ 4 个内置 subagent 注册到 registry
    □ SubagentHandle.spawn / result / cancel
    □ RetryPolicy 3 次后放弃
    □ TimeoutPolicy 触发 WatchdogTimeout
    □ Trace.record 写入 traces 表
    □ Span.start / end + parent

  6.2 集成
    □ explore subagent 真实跑通（read-only, no writes）
    □ verify subagent 检查 step_report
    □ harness retry 真实触发

  6.3 工程
    □ pytest 200+ tests
    □ 文档完整

═══════════════════════════════════════════════
7. 里程碑
═══════════════════════════════════════════════

  M5: 4 subagent + harness 跑通
  时间: ~1 周单人
