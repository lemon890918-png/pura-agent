# Pure-Agent 架构选型与差异化设计

承接 01-source-survey.md 的调研结论。
本文档回答："pure-agent 到底要做什么样的 agent，怎么做。"

═══════════════════════════════════════════════
1. 项目定位（一句话）
═══════════════════════════════════════════════

pure-agent 是一款**纯自研的、不依赖 LangChain 生态的 agent runtime + CLI**，
核心差异化是**结构化输出 + 确定性子任务 + 可调试的多 agent 协作**。
借鉴 PilotDeck / OpenClaw / Hermes 三方优点，绕开三方共同没解决的"输出不稳定"问题。

═══════════════════════════════════════════════
2. 设计原则（5 条硬约束）
═══════════════════════════════════════════════

P1  零 LangChain 依赖
    不引用 langchain / langgraph / langsmith / llamaindex 任何包。
    LLM 协议层用 fetch / httpx 自研（OpenAI 协议 + Anthropic 协议）。
    工具协议用 JSON Schema 自己定义。

P2  单一可执行文件启动
    一个二进制 / 一个 Python 包 `pure-agent` 启动就跑通核心。
    不强依赖 Docker / K8s / Redis。
    SQLite + 本地文件系统足够。

P3  协议先行（manifest-first）
    借鉴 OpenClaw：插件 / 工具 / subagent 全部从 manifest 加载。
    核心代码不写 builtin 列表里"用 if/else 选"。

P4  一切可中断 + 可恢复
    每个 tool_call / subagent run 都有 checkpoint。
    Ctrl-C 之后能 resume 不丢上下文。
    比 PilotDeck 的 session_file_repair 更进一步。

P5  输出结构化优先
    工具调用是结构化的（JSON Schema）；
    subagent 之间通信是结构化的（typed message，不是 free-form text）；
    唯一允许 free-form 的是对用户的最终回复。
    这是解决"输出不稳定"的根本路径。

═══════════════════════════════════════════════
3. 技术栈选型
═══════════════════════════════════════════════

3.1 语言
  候选：TypeScript / Python / Go / Rust
  决策：**Python 3.12 + 部分 Rust 扩展（可选）**
  理由：
    - Hermes 已经验证 Python 能扛住 40 万行 + 17k tests
    - LLM SDK 都是 Python 友好（pydantic / httpx / asyncio）
    - 强类型有 pydantic v2 兜底（不需要 TS 那种编译期类型）
    - 异步生态成熟（asyncio + anyio）
  反 TS 的理由：
    - PilotDeck / OpenClaw 已经在 TS 上做了 — 选 TS 没差异化
    - 调试 Rust 编译错误成本高
  反 Go 的理由：
    - LLM 生态 Python 主导，Go 缺
    - 工具生态弱

3.2 核心依赖（最小集）
  pydantic >= 2.5           # 强类型 + schema 派生
  httpx >= 0.27             # LLM 协议客户端
  anyio                    # 跨平台 async 抽象
  click / typer            # CLI 框架（typer 体验更好）
  textual                  # 终端 UI（比 Ink 学习曲线低，单进程）
  aiosqlite                # 异步 SQLite
  ripgrepy                 # 文件搜索（ripgrep Python 绑定）
  watchfiles               # 文件系统监听
  PyYAML                   # 配置文件
  structlog                # 结构化日志

  禁依赖：
    - langchain / langgraph / langsmith / llama-index
    - openai / anthropic 官方 SDK（用 httpx 自研，避免被 SDK 锁版本）
    - 任何 agent 编排框架（autogen / crewai / smolagents）

3.3 持久化
  - 主存：SQLite (aiosqlite + WAL)
  - 向量：可选 sqlite-vss 或纯 FTS5（不引入 pgvector 复杂度）
  - 文件：~/.pure-agent/projects/<hash>/
  - 配置：~/.pure-agent/config.yaml
  - 日志：~/.pure-agent/logs/{agent,errors,gateway}.log（profile 隔离）

3.4 渲染层
  - 主：Textual（Python TUI，React-like 体验）
  - 备：纯 ANSI 输出（headless 模式）
  - 通道：30+ IM 平台（参考 Hermes 列表，先做 cli + feishu + weixin）

═══════════════════════════════════════════════
4. 核心架构（七层）
═══════════════════════════════════════════════

  ┌──────────────────────────────────────────────────────────┐
  │  L7  Channels      CLI / TUI / IM / Webhook              │
  ├──────────────────────────────────────────────────────────┤
  │  L6  Agent Runtime  AIAgentLoop / SubAgentRegistry       │
  ├──────────────────────────────────────────────────────────┤
  │  L5  Router         task → model 路由 + 成本优化         │
  ├──────────────────────────────────────────────────────────┤
  │  L4  Tools          工具协议 + 执行沙箱（local/docker）  │
  ├──────────────────────────────────────────────────────────┤
  │  L3  Context        memory + compact + 白盒 history     │
  ├──────────────────────────────────────────────────────────┤
  │  L2  Protocol       Canonical message + provider adapter│
  ├──────────────────────────────────────────────────────────┤
  │  L1  Persistence    SQLite + FTS5 + 文件系统             │
  └──────────────────────────────────────────────────────────┘

  L1-L3 是"地基"，从 Hermes + PilotDeck 借鉴。
  L4 是"工具层"，从 OpenClaw 借鉴 manifest-first。
  L5-L6 是"差异化层"，pure-agent 主战场。
  L7 是"接入层"，从 Hermes gateway 借鉴。

═══════════════════════════════════════════════
5. 关键模块设计
═══════════════════════════════════════════════

5.1 协议层（L2）：Canonical Message
  借鉴 PilotDeck 的 CanonicalMessage 抽象 + Hermes 的多 provider 支持。

  class CanonicalMessage(BaseModel):
      role: Literal["user", "assistant", "tool", "system"]
      content: list[ContentBlock]
      tool_calls: list[ToolCall] | None = None
      tool_call_id: str | None = None
      metadata: dict = Field(default_factory=dict)

  ContentBlock = TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock

  ProviderAdapter 抽象：
    class ProviderAdapter(Protocol):
      async def stream(self, request: CanonicalRequest) -> AsyncIterator[ModelEvent]: ...
      def normalize_tool_schema(self, schema: ToolSchema) -> dict: ...
      def max_context_tokens(self) -> int: ...

  实现：OpenAIAdapter / AnthropicAdapter / MinimaxAdapter(自建)

5.2 Agent Runtime（L6）：AIAgentLoop
  借鉴 PilotDeck AgentLoop 的容错设计 + OpenClaw runner 的事件订阅。

  class AIAgentLoop:
    def __init__(self, deps: AgentDeps, seed_state: SeedState | None): ...
    async def run(self, input: LoopInput) -> AsyncIterator[AgentEvent]: ...
    def snapshot(self) -> SeedState: ...

  内置容错：
    - prompt_too_long:  truncate_head_and_retry  (单次)
    - max_output:      phase A: 2x tokens / phase B: 续写 prompt（最多 3 次）
    - invalid JSON:    self-correct（最多 3 次）
    - all-invalid-tool-call circuit breaker: 3 轮熔断
    - auto-compact:    预算评估 + post-routing recompact

  差异化点：每次 tool_call 前先生成"执行计划"（typed plan），
  plan 通过 schema 校验后才执行。
  这层是解决"输出不稳定"的关键。

5.3 Router（L5）
  借鉴 PilotDeck tokenSaver，但加一个新维度：**确定性优先**。

  scenarios:
    default: minimax/MiniMax-M3
    coding:  minimax/MiniMax-M3   # 编码任务
    plan:    minimax/MiniMax-M3   # 计划/分析
    verify:  minimax/MiniMax-M3   # 验证
  fallback: [minimax/MiniMax-M3, minimax/MiniMax-M2.7]
  zero_usage_retry: enabled

  决策：保持单一 model 起步（你已经验证 MiniMax-M3 可用），
  后期再加 judge model 分类。

5.4 Subagent 体系（OpenClaw 借鉴 + 强化）
  PilotDeck: 4 个 hardcoded
  OpenClaw:  registry + lifecycle
  pure-agent: registry + lifecycle + **typed protocol**

  差异化：subagent 之间通信用 typed message（pydantic），
  不是 free-form text。
  例如 verify agent 收到：
    {
      "directive": str,
      "files_to_check": list[str],
      "expected_outputs": list[ExpectedOutput],
      "schema": ToolSchema,
    }
  返回：
    {
      "verdict": Literal["pass", "fail", "needs_fix"],
      "issues": list[Issue],
      "fix_proposals": list[FixProposal],
    }

  这样 subagent 之间的"对话"是结构化的，
  而不是 LLM 自由生成一段文本再让另一个 LLM 解析。

5.5 Tools（L4）
  借鉴 OpenClaw manifest-first：

  # tools/filesystem/manifest.json
  {
    "id": "filesystem",
    "version": "0.1.0",
    "entry": "fs_tools:create",
    "tools": [
      {"name": "read_file", "schema": {...}},
      {"name": "write_file", "schema": {...}},
      ...
    ]
  }

  加载器（tools/loader.py）：
    - 启动时扫描 ~/.pure-agent/tools/ 和内置 tools/
    - 解析 manifest
    - 注册到 tool registry
    - 第三方 tool 走 manifest，core tool 走 builtin

  沙箱：
    - 内置 local（直接执行）
    - 可选 docker（容器执行）
    - 借鉴 Hermes environments/

5.6 Context（L3）
  - 短期：Canonical message 列表 + tool result
  - 中期：FTS5 索引的"事件历史"
  - 长期：白盒 memory（白盒 = 用户能看到哪条 memory 触发的）
  - auto-compact: token 预算评估
  - dream mode: 闲置时 LLM 整理 memory（可选开启）

  差异化：memory 写入是结构化的（typed MemoryEntry），
  检索时返回 entry 引用 + score，
  不让 LLM 自由生成 memory 描述。

5.7 Channels（L7）
  起步 3 个：
    - cli（必须）
    - tui（Textual）
    - gateway daemon（HTTP+WebSocket，对接 IM）
  后期按 Hermes / OpenClaw 列表加 IM 平台。

═══════════════════════════════════════════════
6. 差异化设计（pure-agent 主战场）
═══════════════════════════════════════════════

6.1 结构化 subagent 通信
  问题：所有现有 agent framework 让 subagent 用 free-form text 通信，
        上一级的 LLM 解析下一级的输出。
  pure-agent 方案：subagent 输出是 pydantic model，
                  通信通过 typed protocol（类似 protobuf）。
  收益：调试容易 + 失败定位精准 + 可以"重放"。

6.2 Typed Plan 模式
  问题：LLM 直接调用 tool 时，参数经常对不上（"嵌套 API 查询有时成功有时失败"）。
  pure-agent 方案：tool_call 之前先生成 typed Plan 对象，
                  Plan 通过 pydantic 校验后才执行。
                  校验失败时，反馈 schema 错误让 LLM 修正。
  收益：参数对不上 = 校验失败，**不会**执行到一半失败。

6.3 执行中间状态
  问题：长任务（10+ 分钟）无法 progress checkpoint。
  pure-agent 方案：每个 tool_call 都有 checkpoint SQLite 表，
                  中断后从最近 checkpoint 恢复。
                  比 session_file_repair 更细粒度。

6.4 输出确定性增强
  - 工具参数：JSON Schema 强校验
  - subagent 输出：pydantic model 强校验
  - Plan：pydantic model + JSON Schema
  - 唯一 free-form：用户最终回复
  - 这就是"结构化优先"原则的具体实现

6.5 可视化白盒 memory
  - 用户能 grep / filter 所有 memory entry
  - 用户能 edit 单条 entry（不和聊天混合）
  - 检索时返回 entry 引用（不是 LLM 重写）
  - 一键 rollback 到 N 步前

═══════════════════════════════════════════════
7. 实施路线图
═══════════════════════════════════════════════

Phase 0 — 立项（1 周）
  □ 项目脚手架
  □ CLI entrypoint
  □ config 加载
  □ logger
  □ 基础 SQLite schema

Phase 1 — 核心 loop（2 周）
  □ Canonical message + pydantic schema
  □ OpenAI 协议 adapter（httpx）
  □ AIAgentLoop（while + 容错）
  □ 内置 5 工具：read_file / write_file / edit_file / bash / glob
  □ CLI 跑通"读文件 → 改文件"

Phase 2 — 持久化 + session（1 周）
  □ SessionDB（SQLite + FTS5）
  □ 断点恢复
  □ 历史浏览

Phase 3 — Router + 多 model（1 周）
  □ Provider 抽象
  □ MinimaxAdapter
  □ Scenario 路由
  □ Fallback 链
  □ Auto-compact

Phase 4 — Subagent（2 周）
  □ SubAgentRegistry
  □ 4 个内置 subagent
  □ Typed protocol
  □ Lifecycle + announce

Phase 5 — Tools manifest（1 周）
  □ Manifest schema
  □ Loader
  □ Permission
  □ Allowlist

Phase 6 — TUI（1 周）
  □ Textual 接入
  □ 实时 streaming 显示
  □ 多 session tab

Phase 7 — Channel 起步（1 周）
  □ Gateway daemon
  □ CLI channel
  □ Feishu channel

Phase 8 — Memory + Dream（2 周）
  □ 白盒 memory schema
  □ Memory 写入 / 检索 / 编辑
  □ Dream mode（可选）

═══════════════════════════════════════════════
8. 反模式 / 不要做的事
═══════════════════════════════════════════════

- 不要套 LangChain（用户硬要求）
- 不要引入任何"agent framework"包
- 不要用 OpenAI 官方 SDK（httpx 自研可控）
- 不要写死 model 名字在核心代码（必须走 config）
- 不要把 memory 当 free-form text（必须 typed）
- 不要让 subagent 通信走 free-form（必须 typed protocol）
- 不要写"通用 if/else 多 provider"代码（用 ProviderAdapter 抽象）
- 不要在 core 写 builtin plugin 列表（manifest-first）
- 不要做"插件热加载魔术"（简单 importlib.reload 即可）
- 不要做"零配置自动优化"（用户偏好"有根有据"）

═══════════════════════════════════════════════
9. 验证标准
═══════════════════════════════════════════════

每个 Phase 结束的标准：
  □ 有端到端测试（CLI 真跑一遍，不只 unit test）
  □ 有 benchmark 记录 token 消耗
  □ 用户能复现并独立验证
  □ 错误信息能定位到具体模块

═══════════════════════════════════════════════
10. 下一份文档预告
═══════════════════════════════════════════════

- 03-implementation-phase0.md  — Phase 0 详细任务分解
- 04-tool-schema-design.md    — 工具 schema 设计（typed Plan 模式）
- 05-subagent-protocol.md     — Subagent typed protocol 设计
- 06-canonical-message.md     — Canonical message 详细 schema

按需产出。
