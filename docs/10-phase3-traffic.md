# Phase 3 — 流量优化 详细设计

═══════════════════════════════════════════════
0. 目标
═══════════════════════════════════════════════

把 pure-agent 的 LLM 流量降到 PilotDeck 的 30-60%。具体优化点：

  1. **auto-compact** — context 超过阈值时自动 summary
  2. **typed memory 4 层** — short / episodic / semantic / procedural
  3. **diff-only 重发** — 文件修改只发 diff，不是全文
  4. **token budget** — 每个 step 限制 max_tokens 防超用
  5. **tier 路由** — 简单任务用小模型

具体承诺：
  - 5 步 plan 端到端 token 消耗减少 30-60%
  - 长程任务（10 步）仍能继续，context 不爆
  - 跨 step 共享 typed memory 减少重复 context

═══════════════════════════════════════════════
1. 四层 Memory
═══════════════════════════════════════════════

1.1 Short-term (in-context)
    - 当前 turn 的 messages
    - 跨 turn 在同 step 内累积
    - Phase 0-2 已经是这样

1.2 Episodic (per session)
    - 这次 plan 执行的历史 step_reports 摘要
    - 跨 step 但在同一 plan 内
    - 注入：每个新 step 的 system_prompt

1.3 Semantic (per project)
    - 项目级 facts（如 "项目用 Python 3.12", "build 用 uv"）
    - 跨 plan 持久
    - 注入：每个 plan 的 system_prompt

1.4 Procedural (per user)
    - 用户偏好（如 "喜欢用 typer 不用 click", "回复用中文"）
    - 跨项目持久
    - 注入：每个 AIAgentLoop 的 system_prompt

存储:
  - Phase 0 schema 已留表: short_term / episodic / semantic / procedural
  - 实际使用从 Phase 3 开始

═══════════════════════════════════════════════
2. Auto-Compact
═══════════════════════════════════════════════

2.1 触发条件
    - 每次新 turn 前, 估算 prompt_tokens
    - if prompt_tokens > threshold (默认 80% of model max):
        → compact

2.2 compact 流程
    a. 保留 system_prompt + 最近 2 个 turn
    b. 把中间 turn 发给 LLM "summarize as terse facts":
       input: [user1, asst1, tool1, user2, asst2, tool2, ...]
       output: terse summary
    c. 替换中间 turns 为 summary
    d. 保留所有 tool result ids + file:line references
    e. 继续运行

2.3 实现
    - pure_agent/memory/compactor.py
    - Compactor.compact(messages, model, threshold) -> new messages

2.4 风险
    - summary 丢失细节 → 关键 tool result 用 reference + summary 兜底
    - LLM summary 不准 → 至少保留 raw tool result id
    - 反复 compact 累积成本 → 限制 max compact 次数 (e.g. 3)

═══════════════════════════════════════════════
3. Diff-Only 重发
═══════════════════════════════════════════════

3.1 问题
    - 当前 read_file 输出全文 (line-numbered)
    - edit_file 后再 read_file 全文 → 重复 token

3.2 方案
    - read_file 后, 改文件 → 内部记录 file_path + last_read_version
    - 下次 read_file 同文件 → 只返回 diff
    - 实现: tool 内置版本追踪

3.3 状态
    - 跟踪: {path: mtime} in memory, 不跨 turn
    - 跨 turn 持久: file_tracker table (Phase 0 已留)

3.4 风险
    - 文件被外部修改 → 检测 mtime 变化 → 重新 full read
    - 实现: 每次 read 时先 stat, mtime 变 → full read

═══════════════════════════════════════════════
4. Token Budget
═══════════════════════════════════════════════

4.1 预算分类
    - per turn: max_tokens 限制 LLM 单次回复
    - per step: 累计 token 上限
    - per plan: 累计 token 上限
    - per session: 当日上限

4.2 触发超限
    - per turn: 已经被 LLM 端控制
    - per step 超限 → step FAILED + 提示"reducing step scope"
    - per plan 超限 → plan FAILED + 让用户确认
    - per session 超限 → 当日不能再开新 plan

4.3 监控
    - 每次 LLM 调用后 yield Usage
    - 累加到 step / plan / session
    - CLI 显示当前用量

═══════════════════════════════════════════════
5. Tier 路由
═══════════════════════════════════════════════

5.1 模型分级
    - Tier 0 (mini): 简单 grep / read
    - Tier 1 (small): write_file / edit_file
    - Tier 2 (medium): 一般多步骤任务
    - Tier 3 (large): 复杂 reasoning

5.2 路由策略
    - step kind 决定默认 tier:
      - read/grep → Tier 0
      - code → Tier 1
      - search → Tier 1
      - verify → Tier 2
      - plan/decompose → Tier 3
    - 用户可 override

5.3 实现
    - pure_agent/model/router.py
    - ModelRouter.pick_tier(step) -> tier
    - 配置: model_map = {0: "...", 1: "...", 2: "...", 3: "..."}

5.4 Phase 3 实现
    - 只支持 tier override，不强制
    - 后续 Phase 5 Harness 实现 auto-tier

═══════════════════════════════════════════════
6. 关键模块
═══════════════════════════════════════════════

  memory/
  ├── __init__.py
  ├── layers.py          # 4 层 memory 接口
  ├── episodic.py        # episodic layer
  ├── semantic.py        # semantic layer
  ├── procedural.py      # procedural layer
  ├── compactor.py       # auto-compact
  └── tracker.py         # file version tracker

  model/
  ├── router.py          # tier-based model selection

  persistence/
  └── memory_ops.py      # CRUD for 4 memory tables

═══════════════════════════════════════════════
7. 数据模型
═══════════════════════════════════════════════

  episodic_memory:
    id, session_id, kind (user_pref | project_fact | tool_fact), content,
    source_step_id, created_at, ttl (optional)

  semantic_memory:
    id, project_id, kind, content, confidence (0-1), created_at

  procedural_memory:
    id, user_id, kind, content, weight (0-1), created_at

  file_tracker:
    id, path, mtime, size, hash, last_read_at

═══════════════════════════════════════════════
8. 集成策略
═══════════════════════════════════════════════

8.1 AIAgentLoop 改造
    - 新增参数: max_context_tokens, compactor, memory_layers
    - 每个 turn 前检查 token usage → 触发 compact
    - 每个 turn 后: 更新 episodic memory (从 step_report)

8.2 PlanRunner 改造
    - 新增参数: token_budget (per step / per plan)
    - 跨 step: 注入 prior step_reports 摘要 (typed, 不是 raw)
    - 跨 step: 注入 semantic facts
    - 跨 step: 注入 user prefs

8.3 read_file 工具改造
    - 新增: file_tracker
    - if same path read again, mtime unchanged: return diff
    - else: return full content

8.4 CLI
    - pure-agent memory show
    - pure-agent memory add
    - pure-agent memory compact

═══════════════════════════════════════════════
9. 验收清单
═══════════════════════════════════════════════

  9.1 单元
    □ 4 层 memory CRUD
    □ Compactor: full messages → summary, 保留 structure
    □ Compactor: 多次 compact 不破坏 system prompt
    □ FileTracker: 改文件后再读 → full content (mtime change detected)
    □ FileTracker: 同一 session 重复读同文件 → 标记 cached
    □ TokenBudget: 超限触发失败
    □ ModelRouter: 4 tier 正确路由

  9.2 集成
    □ 5 步 plan token 减少 30-60%
    □ 10 步 plan 不爆 context (auto-compact 触发)
    □ 跨 step 共享 semantic facts
    □ 跨 plan 共享 procedural prefs

  9.3 工程
    □ pytest 150+ tests
    □ CLI memory 子命令
    □ 文档完整

═══════════════════════════════════════════════
10. 风险与缓解
═══════════════════════════════════════════════

  风险 1: auto-compact summary 丢细节导致 LLM 迷航
    缓解: 保留最近 2 turn raw, 关键 tool result 用 file:line reference
    缓解: max compact 次数限制 (3 次)

  风险 2: diff-only 重发导致 LLM 看错版本
    缓解: mtime 变化检测, 失效时强制 full read
    缓解: diff 旁注明 old content hash

  风险 3: tier 路由选错模型
    缓解: Phase 3 只做 override, 不做 auto
    缓解: Phase 5 Harness 加 prompt-based tier judge

  风险 4: memory 累积过多反而耗 token
    缓解: episodic: 限每 plan 20 facts
    缓解: semantic: 限每 project 100 facts
    缓解: procedural: 限每 user 50 prefs

═══════════════════════════════════════════════
11. 里程碑
═══════════════════════════════════════════════

  M3: 上述验收清单全部通过
  时间: ~1 周单人
  关键 demo: 5 步 plan 跑通, token 用量比 Phase 2 减少 30-60%
