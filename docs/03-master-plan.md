# Pure-Agent Master Plan v2

v1 基础上吸收新需求后定稿。

═══════════════════════════════════════════════
0. v2 相比 v1 的变化
═══════════════════════════════════════════════

新增/强化（用户原话）：
  ✓ Goal/Plan 模型按完整版做（不简化）
  ✓ 整个 fork PilotDeck ui/ 目录
  ✓ **长期 + 短期记忆** 分层（v1 只说"白盒 memory"，现在拆 short / long）
  ✓ **基础三件套做到最好**：read_file / write_file / web_search
  ✓ **上下文获取 + 切换**（context switching）— v1 没写
  ✓ **Harness 能力**（v1 没写 — 现在独立成层）
  ✓ Agent 循环要"做得最好"（容错、稳定性、retry policy 都要精雕）

═══════════════════════════════════════════════
1. 核心能力清单（v2 完整版）
═══════════════════════════════════════════════

1.1 长程任务
  - Goal 输入 → Plan 分解 → Step 执行
  - Plan 持久化 + 恢复 + 编辑
  - 跨 step 上下文摘要
  - 30+ 分钟运行不死

1.2 分层记忆
  ┌─────────────────────────────────────────────┐
  │  Short-term (working memory)                │
  │  - 当前 plan + 当前 step + 最近 N 轮消息   │
  │  - 容量小，更新频繁                          │
  │  - 跟随 conversation context                │
  ├─────────────────────────────────────────────┤
  │  Episodic (session memory)                  │
  │  - 本 session 重要事件                       │
  │  - 用户偏好 / 修正记录                       │
  │  - tool result 摘要                          │
  ├─────────────────────────────────────────────┤
  │  Semantic (long-term facts)                 │
  │  - 跨 session 持久事实                        │
  │  - "项目用 pydantic v2" / "用户偏好 tab=4"  │
  │  - 显式写入 + 自动提取                        │
  ├─────────────────────────────────────────────┤
  │  Procedural (skills)                        │
  │  - "怎么写 plan" / "怎么 debug"              │
  │  - SKILL.md + 例子                            │
  │  - 按需加载                                    │
  └─────────────────────────────────────────────┘
  4 层都要做，每层独立可观察 + 可编辑（白盒）

1.3 基础工具三件套（必须做到最好）
  read_file / write_file / web_search
  具体要求：
  - read_file:
    * 支持偏移量 + 行数（不一次读大文件）
    * 缓存：同一文件 read 多次 → 复用 hash
    * 二进制检测 + 友好错误
    * path 越界保护（白盒沙箱）
  - write_file:
    * 原子写入（tmp + rename）
    * 写入前先做 diff 给 LLM 确认（防误写）
    * 写入后自动 checkpoint
    * 权限：默认 deny，require user confirm 写项目外路径
  - web_search:
    * 多 provider fallback（Bing / Brave / Serper / Tavily / DuckDuckGo）
    * 缓存（同一 query 24h 内复用）
    * rate limit + 失败重试
    * 解析：title / url / snippet / date

1.4 上下文获取 + 切换（context switching）
  - 当前工作目录切换
  - 项目隔离：每个项目独立的 .pure-agent/ 目录
  - Skill 加载：当前 plan/step 决定加载哪些 skill
  - Memory 检索：当前 plan/step 决定检索哪些 memory
  - Tool 白名单：当前 subagent 决定可用的 tool
  - 多 session 并行：互不干扰

1.5 Harness 能力（独立成层）
  借鉴 OpenClaw 的 "harness" 设计 + Hermes 的 checkpoint_manager：
  - Retry policy: 工具失败指数退避
  - Timeout: 工具 / LLM / step 三级超时
  - Abort: 用户可中断
  - Observability: 完整 trace（OpenTelemetry 风格但简化）
  - Test fixtures: e2e 测试用真实模型
  - Eval harness: benchmark 脚本
  - Crash recovery: 进程崩溃后从 checkpoint 恢复
  - Resource limits: 内存 / 文件大小 / 工具并发数
  - Sandboxing: 工具执行可选 docker 隔离
  - Permission UI: 危险操作弹确认

1.6 Agent 循环
  - 借鉴 PilotDeck 1625 行 AgentLoop
  - while(true) → router → execute → assemble → collect tools → execute tools
  - 容错：prompt_too_long / max_output / invalid_json / all_invalid / circuit breaker
  - sticky tier 透传
  - auto-compact + post-routing recompact
  - **typed Plan 校验在 tool_call 前**（解决"输出不稳定"的核心）
  - **状态可观察**：每个 turn 都有 trace

═══════════════════════════════════════════════
2. 完整架构（v2 8 层）
═══════════════════════════════════════════════

  ┌──────────────────────────────────────────────────────────┐
  │  L8  GUI              React + Vite (fork PilotDeck)      │
  │      http://localhost:5173 dev / :3001 built             │
  │      WebSocket + REST API                                │
  ├──────────────────────────────────────────────────────────┤
  │  L7  Channels         CLI / TUI / HTTP / WebSocket       │
  ├──────────────────────────────────────────────────────────┤
  │  L6  Agent Runtime                                       │
  │      AIAgentLoop (core while + 容错)                     │
  │      SubAgent (typed protocol)                           │
  │      GoalManager / PlanManager (长程)                    │
  │      SteeringHandler (用户插话)                          │
  ├──────────────────────────────────────────────────────────┤
  │  L5  Harness                                              │
  │      Retry / Timeout / Abort / Trace / Checkpoint        │
  │      Permission / Sandbox / ResourceLimits              │
  │      EvalHarness (benchmark 脚本)                        │
  ├──────────────────────────────────────────────────────────┤
  │  L4  Memory (4 层)                                       │
  │      Short-term / Episodic / Semantic / Procedural       │
  │      白盒：可编辑、可观察、可 rollback                    │
  │      Context Switching: 按 plan 加载                     │
  ├──────────────────────────────────────────────────────────┤
  │  L3  Tools (基础三件套优先)                              │
  │      read_file / write_file / web_search (P0 必达)        │
  │      edit_file / bash / glob / grep (P0 必达)             │
  │      + manifest 扩展点 (P5)                              │
  ├──────────────────────────────────────────────────────────┤
  │  L2  Router + Compact                                     │
  │      Tier routing (simple/plan/verify/complex)            │
  │      Auto-compact / diff-only 重发 / token budget         │
  ├──────────────────────────────────────────────────────────┤
  │  L1  Protocol                                              │
  │      Canonical message (pydantic)                         │
  │      ProviderAdapter (OpenAI / Anthropic / Minimax)       │
  │      Tool JSON Schema                                     │
  ├──────────────────────────────────────────────────────────┤
  │  L0  Persistence                                          │
  │      SQLite WAL + FTS5 + 文件系统                         │
  │      ~/.pure-agent/projects/<hash>/                       │
  └──────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════
3. 阶段路线（v2 详细版）
═══════════════════════════════════════════════

阶段 0 — 脚手架（2 天）
  交付物：
    □ pyproject.toml (uv 管理)
    □ 包结构 src/pure_agent/
       ├── __init__.py
       ├── cli/         (CLI 入口)
       ├── agent/       (Runtime / Subagent)
       ├── model/       (Canonical / Provider / Router)
       ├── tools/       (基础工具)
       ├── memory/      (4 层记忆)
       ├── harness/     (Harness 能力)
       ├── persistence/ (SQLite + 文件)
       ├── server/      (Gateway)
       └── config.py / logging.py
    □ CLI 入口 (typer)
       pure-agent / pure-agent chat / pure-agent server / pure-agent plan
    □ structlog 配置
    □ yaml + env config 加载
    □ SQLite schema（全部表预留，初始 0 表）
    □ pytest 配置
  验收：
    □ `pure-agent --version` 跑通
    □ `pytest` 跑通（至少 1 个 smoke test）
    □ SQLite db 创建成功
    □ log 输出格式正确（json）
  文档：04-phase0-scaffold.md

阶段 1 — 核心 loop（1 周）
  交付物：
    □ Canonical message (pydantic v2)
    □ ProviderAdapter Protocol
    □ OpenAIAdapter / MinimaxAdapter (httpx)
    □ AIAgentLoop（while + 容错 + typed Plan 校验）
    □ 5 工具 P0:
       - read_file  (有偏移/缓存/二进制检测/沙箱)
       - write_file (atomic / diff 确认 / checkpoint)
       - edit_file
       - bash
       - glob / grep
       - web_search (Bing/Brave/DDG 多 provider)
    □ CLI `pure-agent chat` 跑通单轮
  验收（M1）：
    □ 真实跑 1 个 prompt 完整 read_file → write_file
    □ token 计数准确
    □ 5 工具 sandbox 边界清晰（拒绝越界）
    □ web_search 真实返回（多 provider 至少 1 个能跑）
    □ 所有 5 工具 typed schema 强校验
    □ trace 完整（每个 tool_call 都有 log）
  文档：05-phase1-core-loop.md
  注：基础三件套在这里就要做好，不是 Phase 8 才打磨

阶段 2 — Goal/Plan 系统（1.5 周）
  交付物：
    □ Goal / Plan / PlanStep (pydantic, 强类型)
    □ Plan deps DAG 校验
    □ GoalManager / PlanManager
    □ PlanAgent（分解 plan 的 special subagent）
    □ PlanStateMachine（pending / in_progress / done / failed / blocked / skipped）
    □ PlanStorage (SQLite 持久化)
    □ CLI:
       pure-agent plan "goal"
       pure-agent plan resume
       pure-agent plan list
       pure-agent plan edit
       pure-agent plan status
    □ Plan step → AIAgentLoop 触发
    □ Plan step 失败重试 + 升级
  验收（M2）：
    □ "给项目加 X 功能" 跑通 5 步 plan
    □ Ctrl-C 后 resume 完整恢复
    □ Plan 可中途编辑（加 step / 删 step / 改 action）
    □ Plan step 失败 N 次 escalate
  文档：06-phase2-goal-plan.md

阶段 3 — 流量优化（1 周）
  交付物：
    □ Auto-compact（token 预算 > 80% 触发）
    □ 增量上下文（diff-only 重发，文件 hash 比对）
    □ Memory index（top-K 注入，typed）
    □ Router tier（4 档：simple / plan / verify / complex）
    □ Token budget 监控
    □ Benchmark 脚本（对比 PilotDeck baseline）
  验收（M3 part 1）：
    □ 跑同任务 token 比 baseline 显著低（见指标 6.2）
    □ auto-compact 不丢 plan 状态
    □ Memory 检索 top-K 准确率 > 80%
  文档：07-phase3-token-optimization.md

阶段 4 — 长时间运行（1 周）
  交付物：
    □ Checkpoint（每 step + 每 N tool_call）
    □ Resume 完整实现（含 plan / subagent 状态）
    □ Watchdog（心跳检测 hang）
    □ 用户插话（steer）
    □ 后台 daemon 模式（pure-agent daemon start/stop）
    □ Crash recovery 测试
  验收（M3 完整）：
    □ 30+ 分钟任务不挂
    □ 断网 → 重连 → 自动 resume
    □ 用户插话后 agent 优雅改向
    □ Daemon 模式能用
  文档：08-phase4-long-running.md

阶段 5 — Subagent + Harness 完整化（1 周）
  交付物：
    □ Harness 独立层（retry / timeout / abort / trace / sandbox）
    □ SubAgentRegistry
    □ Typed protocol（pydantic + JSON Schema）
    □ 4 个内置 subagent:
       - explore: read-only 文件浏览
       - plan:    只读，不调 tool
       - verify:  读 + 验证产物
       - implementer: 全工具 + 写文件
    □ Lifecycle: spawn / announce / complete / orphan-recovery
    □ Tool 隔离（每个 subagent 自己的白名单）
    □ Trace 工具（可视化所有 subagent 通信）
  验收：
    □ explore 不能写文件（被拒）
    □ verify 给出结构化 verdict
    □ implementer 完成后 announce 父 agent
    □ Trace 工具能完整回放一个 subagent run
  文档：09-phase5-subagent-harness.md

阶段 6 — Memory 4 层 + 上下文切换（1 周）
  交付物：
    □ Short-term: 跟随 conversation
    □ Episodic: session 重要事件
    □ Semantic: 跨 session 事实（自动提取 + 显式写入）
    □ Procedural: skill 加载
    □ Context switcher: 切换 project / session / plan
    □ Memory editor GUI
  验收：
    □ 4 层独立可观察
    □ 切换项目时 memory 自动切换
    □ Semantic memory 检索 top-K 准确
  文档：10-phase6-memory-layers.md

阶段 7 — Gateway + WebSocket（1 周）
  交付物：
    □ FastAPI server
    □ WebSocket /ws
    □ REST API: projects / sessions / messages / goals / plans / tools / memory
    □ Auth: token from config
    □ 端口 18789（对齐 PilotDeck）
  验收：
    □ 端到端 API 跑通
    □ WebSocket 推送实时更新
    □ 跟 CLI 完全等价
  文档：11-phase7-gateway.md

阶段 8 — GUI（2 周）
  交付物：
    □ 整个 fork PilotDeck ui/ 目录到 pure-agent/ui/
    □ 替换 gateway URL / API 路径
    □ 新增 Goal/Plan 视图
    □ Memory editor 集成
    □ WebSocket 集成（实时更新）
    □ Vite build 成功
    □ Plan tree 可视化
    □ Tool call 实时显示
    □ Memory entry 可编辑
    □ Checkpoint 回放
  验收（M4）：
    □ dev mode npm run dev 跑通
    □ built mode node server/index.js 跑通
    □ 至少能：创建 goal / 看 plan tree / 看 chat / 改 plan / 编辑 memory
  文档：12-phase8-gui.md

阶段 9 — 工具 manifest + 打磨（1 周）
  交付物：
    □ Tool manifest schema (JSON)
    □ Loader（启动时扫描 ~/.pure-agent/tools/）
    □ Sandbox（local + docker）
    □ Permission UI
    □ Eval harness（benchmark）
    □ 三方对比 benchmark
    □ README + USAGE
  验收：
    □ Benchmark 数字 vs 三方
    □ README 跑通
    □ 所有验收复测
  文档：13-phase9-polish.md

═══════════════════════════════════════════════
4. 验收指标（v2 完整版）
═══════════════════════════════════════════════

4.1 功能
  □ CLI + GUI + WebSocket 三种入口
  □ 长程任务 30+ 分钟不挂
  □ Ctrl-C / 断网 / 模型超时 三种故障恢复
  □ Plan 中途修改
  □ 4 层 memory 独立可编辑
  □ 上下文切换 project / session / plan

4.2 流量（vs PilotDeck 同任务）
  □ 简单任务（< 10 step）省 30%
  □ 中等任务（10-30 step）省 50%
  □ 长程任务（30+ step）省 60%
  □ Token 预算有可视化

4.3 基础三件套质量
  □ read_file  处理 100MB+ 文件不卡死
  □ write_file 写入失败零数据丢失（atomic）
  □ web_search 至少 2 个 provider 跑通 + 缓存 24h

4.4 Harness
  □ 工具失败自动 retry（指数退避）
  □ 三级 timeout 工具 / LLM / step
  □ 用户可 abort
  □ 完整 trace
  □ 进程崩溃可恢复

4.5 健壮性
  □ 工具参数错误率 < 5%（typed Plan 校验）
  □ subagent 通信失败上报 + 不挂
  □ 输出确定性 vs 三方显著改善

4.6 工程
  □ 单元测试 > 70% 覆盖
  □ 端到端测试 5 个真实场景
  □ CI 跑通
  □ README 10 分钟跑起来

═══════════════════════════════════════════════
5. 阶段时间估算
═══════════════════════════════════════════════

  Phase 0:  0.4 周
  Phase 1:  1.0 周
  Phase 2:  1.5 周
  Phase 3:  1.0 周
  Phase 4:  1.0 周
  Phase 5:  1.0 周
  Phase 6:  1.0 周
  Phase 7:  1.0 周
  Phase 8:  2.0 周
  Phase 9:  1.0 周
  ─────────────
  合计: 10.9 周 (约 2.7 个月)

═══════════════════════════════════════════════
6. 当前进度
═══════════════════════════════════════════════

  [ ] Phase 0  脚手架
  [ ] Phase 1  核心 loop + 基础三件套
  [ ] Phase 2  Goal/Plan
  [ ] Phase 3  流量优化
  [ ] Phase 4  长时间运行
  [ ] Phase 5  Subagent + Harness
  [ ] Phase 6  Memory 4 层
  [ ] Phase 7  Gateway
  [ ] Phase 8  GUI
  [ ] Phase 9  打磨

下一步：开始 Phase 0 实施，出 04-phase0-scaffold.md 详细子文档。
