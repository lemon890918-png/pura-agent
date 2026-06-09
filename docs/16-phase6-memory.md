# Phase 6 — Memory 4 层 + 上下文切换 详细设计

═══════════════════════════════════════════════
0. 目标
═══════════════════════════════════════════════

把 Phase 3 实现的 4 层 memory 升级到生产级别：

  - 强化 4 层：自动 truncate / dedup / 评分
  - ContextBuilder：自动把 L1/L2/L3/L4 注入 system prompt
  - ContextSwitching：切换 session 保留 memory
  - 跨 plan 复用 fact
  - L1 short-term in-memory cache

承诺:
  - 4 层 memory 全部自动注入
  - context switch 真实跑通
  - 跨 plan 复用 fact（5 步 plan 第 5 步知道第 1 步的结果）

═══════════════════════════════════════════════
1. 4 层强化
═══════════════════════════════════════════════

1.1 L1 Short-term (Phase 3: in-memory, current context)
    - Phase 6: 加入 TTL + max size + LRU eviction
    - L1 不持久化（lost on restart）
    - 当前 session 的 conversation messages
    - API: get() / append() / clear() / recent(n)

1.2 L2 Episodic (Phase 3: per-session facts)
    - Phase 6: 加 FTS5 全文搜索
    - 评分：recency * importance
    - 注入时按 score 排序取 top-k

1.3 L3 Semantic (Phase 3: per-project facts)
    - Phase 6: 跨 project search
    - dedup：相似 fact 不重复存
    - confidence 评分

1.4 L4 Procedural (Phase 3: skills + user prefs)
    - Phase 6: 跨 project 复用
    - user_id 默认 'default'，可定制

═══════════════════════════════════════════════
2. ContextBuilder
═══════════════════════════════════════════════

2.1 把 memory 转成 system prompt sections:

    ## User Preferences
    - prefers concise responses
    - uses typer for CLI

    ## Project Facts
    - project uses Python 3.12 + uv
    - database at memory.db

    ## Session Context (recent)
    - working on Phase 6 memory
    - last 3 episodic facts

2.2 Token budget for memory:
    - Total memory budget: 2000 tokens (可配)
    - 分配：L4=300, L3=500, L2=1000, L1=200
    - 超限按 importance 截断

2.3 API:
    - ContextBuilder.build(session_id, project_id) -> str
    - 缓存 60s（避免每 turn 重算）

═══════════════════════════════════════════════
3. Context Switching
═══════════════════════════════════════════════

3.1 概念
    - 切换 session：换不同 project 或不同 task
    - L2/L3 跨 session 共享
    - L1 切换时清空
    - L4 全局共享

3.2 API
    - SwitchContext.from_session(old_id, new_id)
    - SwitchContext.to_session_id()
    - 保留 short-term 缓存到 switch_history
    - 5s 内能切回原 session → 恢复 L1

3.3 CLI
    - `pure-agent session list`
    - `pure-agent session switch <id>`

═══════════════════════════════════════════════
4. 跨 Plan 复用 Fact
═══════════════════════════════════════════════

4.1 PlanRunner 启动时：
    - query L3 (project facts) → 注入 system prompt
    - query L2 (session facts) → 注入 step context

4.2 每 step 完成后：
    - 把 step result 加 L2 (episodic)
    - 重要 fact (e.g. "user uses Python 3.12") 加 L3 (semantic)

4.3 auto_extract_facts:
    - LLM 提取 fact from step result
    - 评分 confidence > 0.7 才存
    - 避免噪音

═══════════════════════════════════════════════
5. 关键模块
═══════════════════════════════════════════════

  memory/l1_short.py        ShortTermMemory 强化
  memory/l2_episodic.py     强化（FTS5 + 评分）
  memory/l3_semantic.py     强化（dedup + 跨 project）
  memory/l4_procedural.py   强化（user prefs + skills）
  memory/context_builder.py ContextBuilder
  memory/context_switch.py  ContextSwitcher
  memory/fact_extractor.py  extract_facts from text (LLM)

  plan/runner.py            集成 memory

  tests/
  test_memory_phase6.py
  test_context_builder.py
  test_context_switch.py
  test_plan_memory.py

═══════════════════════════════════════════════
6. 验收清单
═══════════════════════════════════════════════

  6.1 单元
    □ L1 LRU + max size
    □ L2 FTS5 搜索
    □ L3 dedup
    □ L4 cross-project
    □ ContextBuilder token budget 分配
    □ ContextBuilder 缓存 60s
    □ ContextSwitcher save/restore L1

  6.2 集成
    □ PlanRunner 注入 L3 facts
    □ PlanRunner 加 L2 fact after step
    □ 跨 plan 复用 fact（plan A 加 fact → plan B 能看到）
    □ auto_extract_facts from verdict text

  6.3 工程
    □ pytest 230+ tests
    □ 文档完整

═══════════════════════════════════════════════
7. 里程碑
═══════════════════════════════════════════════

  M6: 4 层强化 + context builder/switch + 跨 plan 复用
  时间: ~3-5 天单人
