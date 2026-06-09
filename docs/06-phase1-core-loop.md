# Phase 1 — 核心 Loop + 基础三件套 详细设计

═══════════════════════════════════════════════
0. 目标
═══════════════════════════════════════════════

实现 pure-agent 的核心 agent loop，让用户能用 CLI 跟 LLM 对话完成工具调用。

具体承诺：
  - Canonical message + 多 provider adapter
  - AIAgentLoop：while + 容错 + typed Plan 校验
  - 5 基础工具做到最好：read_file / write_file / edit_file / glob / grep / web_search
  - `pure-agent chat` CLI 跑通 "读文件 → 改文件" 真实流程

═══════════════════════════════════════════════
1. 整体架构
═══════════════════════════════════════════════

  ┌──────────────────────────────────────────────────────────┐
  │  CLI  (pure-agent chat "读 x 文件，改 y")                │
  └────────────────────┬─────────────────────────────────────┘
                       │
  ┌────────────────────▼─────────────────────────────────────┐
  │  AIAgentLoop  (core while)                               │
  │  · assemble request → provider.stream → assemble reply   │
  │  · collect tool_calls → execute → append tool_result    │
  │  · 容错: prompt_too_long / max_output / invalid_json    │
  │  · typed Plan 校验: 工具调用前 schema 检查                │
  └────────────────────┬─────────────────────────────────────┘
                       │
  ┌────────────────────▼─────────────────────────────────────┐
  │  ProviderAdapter  (Protocol)                             │
  │  ├── OpenAIAdapter (OpenAI Chat Completions API)         │
  │  ├── MinimaxAdapter (继承 OpenAIAdapter，指向 minimaxi)  │
  │  └── AnthropicAdapter (Phase 2+)                         │
  └────────────────────┬─────────────────────────────────────┘
                       │
  ┌────────────────────▼─────────────────────────────────────┐
  │  Tools  (5 个, JSON Schema + python impl)                │
  │  ├── read_file   (offset, limit, binary detect, cache)   │
  │  ├── write_file  (atomic, diff confirm, checkpoint)     │
  │  ├── edit_file   (search/replace, atomic)                │
  │  ├── glob        (pattern → paths)                       │
  │  ├── grep        (regex → matches)                       │
  │  └── web_search  (multi-provider, 24h cache)             │
  └──────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════
2. Canonical Message (L1 Protocol)
═══════════════════════════════════════════════

借鉴 PilotDeck 的 CanonicalMessage 抽象 + Hermes 的多 provider 支持。

  class Role(str, Enum):
      SYSTEM = "system"
      USER = "user"
      ASSISTANT = "assistant"
      TOOL = "tool"

  class ToolCall(BaseModel):
      id: str
      name: str
      arguments: dict[str, Any]  # 强类型，pydantic 校验

  class TextBlock(BaseModel):
      type: Literal["text"] = "text"
      text: str

  class ToolUseBlock(BaseModel):
      type: Literal["tool_use"] = "tool_use"
      tool_call: ToolCall

  class ToolResultBlock(BaseModel):
      type: Literal["tool_result"] = "tool_result"
      tool_call_id: str
      content: str
      is_error: bool = False

  ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock

  class CanonicalMessage(BaseModel):
      role: Role
      content: list[ContentBlock]
      tool_calls: list[ToolCall] | None = None
      tool_call_id: str | None = None  # when role=TOOL
      name: str | None = None
      metadata: dict = Field(default_factory=dict)

  class CanonicalRequest(BaseModel):
      model: str
      messages: list[CanonicalMessage]
      tools: list[ToolSchema] | None = None
      max_output_tokens: int = 16384
      temperature: float = 1.0

  class ModelEvent(BaseModel):
      type: Literal["text_delta", "tool_call_delta", "message_start",
                    "message_end", "error", "usage"]
      text: str | None = None
      tool_call: ToolCall | None = None
      finish_reason: str | None = None
      usage: Usage | None = None
      error: str | None = None

  class Usage(BaseModel):
      prompt_tokens: int = 0
      completion_tokens: int = 0
      total_tokens: int = 0

  class ToolSchema(BaseModel):
      name: str
      description: str
      parameters: dict  # JSON Schema

═══════════════════════════════════════════════
3. Provider Adapter (L1 Protocol)
═══════════════════════════════════════════════

  class ProviderAdapter(Protocol):
      def stream(
          self,
          request: CanonicalRequest,
      ) -> AsyncIterator[ModelEvent]:
          ...

      def normalize_tool_schema(self, schema: ToolSchema) -> dict:
          """Convert ToolSchema → provider's expected format."""
          ...

      def max_context_tokens(self, model: str) -> int | None:
          ...

  OpenAIAdapter (实现 chat completions streaming):
    - 用 httpx.AsyncClient
    - 请求体: { model, messages, tools, stream, max_tokens, temperature }
    - 响应: SSE 格式, 解析 chunk
    - 工具: { type: "function", function: { name, description, parameters } }

  MinimaxAdapter(继承 OpenAIAdapter):
    - url = https://api.minimaxi.com/v1
    - model = MiniMax-M3
    - header: Authorization: Bearer <api_key>

  关键设计：
    - 适配 OpenAI 协议的所有 provider 都能复用
    - tool_calls 标准化（无论哪家 provider 都转成 ToolCall 模型）
    - 流式 + 非流式都支持（stream=True 总是）

═══════════════════════════════════════════════
4. 工具设计（基础三件套 P0）
═══════════════════════════════════════════════

4.1 read_file（必须做到最好）

  输入 schema:
    path: str           # 必填，相对或绝对
    offset: int = 0     # 起始行
    limit: int | None   # 读几行
    encoding: str = "utf-8"

  输出:
    { "content": str, "total_lines": int, "returned_lines": int,
      "is_binary": bool, "truncated": bool, "sha256": str }

  关键设计：
    - 偏移量 + 行数 → 不一次读大文件
    - 二进制检测：含 NUL byte → is_binary=true + 友好错误
    - 缓存：同 (path, offset, limit) hash → 复用
    - 沙箱：相对项目根，不让读 ~/.ssh 等敏感路径
    - sha256 输出让 LLM 验证内容一致性

  错误码：
    - file_not_found
    - permission_denied
    - is_binary
    - out_of_project (sandbox)
    - encoding_error

4.2 write_file（必须做到最好）

  输入 schema:
    path: str
    content: str

  输出:
    { "path": str, "bytes_written": int, "sha256": str }

  关键设计：
    - 原子写入：先写 .tmp，再 rename（防半写状态）
    - 写入前给 LLM 提示：内容是 X bytes，是否继续（Phase 5 加 confirmation）
    - 写入后自动生成 checkpoint（Phase 4 实现）
    - 沙箱：默认 allow project 目录；project 外需显式 `sandbox: true` 关闭
    - 备份：写前把旧文件 copy 到 .bak（可选，Phase 5）

4.3 edit_file

  输入 schema:
    path: str
    old_string: str
    new_string: str
    replace_all: bool = False

  输出:
    { "path": str, "replacements": int }

  - 找不到 old_string → 报错
  - 多个 match + replace_all=false → 报错（防误改）
  - 多个 match + replace_all=true → 全替换
  - 写入 = atomic write_file

4.4 glob

  输入 schema:
    pattern: str       # 如 "**/*.py"
    path: str = "."    # 搜索根
    limit: int = 100

  输出:
    { "matches": [str], "truncated": bool, "total": int }

  - 用 pathlib.glob / rglob
  - 跳过 .git / node_modules / .venv 等（默认）

4.5 grep

  输入 schema:
    pattern: str       # Python regex
    path: str = "."
    include_glob: str | None = None
    limit: int = 100

  输出:
    { "matches": [{"path": str, "line": int, "text": str}], "truncated": bool }

  - 用 ripgrepy（rust 版 ripgrep）— Phase 1 退路用纯 Python re
  - 支持 include glob 过滤
  - 大结果集截断 + 提示

4.6 web_search（必须做到最好）

  输入 schema:
    query: str
    max_results: int = 5
    provider: str | None = None  # auto / ddg / brave

  输出:
    { "results": [{"title": str, "url": str, "snippet": str, "date": str}],
      "provider_used": str, "cached": bool }

  关键设计：
    - 多 provider fallback：
      1. DDG HTML 抓取（不需要 API key）
      2. Brave Search API（要 key，可选）
      3. Tavily API（要 key，可选）
    - 缓存：~/.pure-agent/cache/web_search/{query_hash}.json
      24h 内复用，hash = sha256(query + max_results)
    - rate limit：每秒最多 1 request
    - 失败重试：3 次指数退避
    - provider 优先级：用户配置 > auto fallback

  Phase 1 实现：
    - 至少 DDG 一种能跑通
    - Brave 留接口，Phase 2+ 加
    - 缓存层

═══════════════════════════════════════════════
5. AIAgentLoop 详细设计
═══════════════════════════════════════════════

  class AIAgentLoop:
      def __init__(
          self,
          config: AgentConfig,
          provider: ProviderAdapter,
          tool_registry: ToolRegistry,
          *,
          system_prompt: str = "",
          on_event: Callable | None = None,
          on_durable: Callable | None = None,
      ):
          ...

      async def run(
          self,
          user_message: str,
          *,
          max_turns: int | None = None,
      ) -> AgentRunResult:
          """Run the loop until completion or max_turns."""
          ...

  AgentRunResult:
      final_text: str
      turns: int
      total_usage: Usage
      tool_calls_made: list[ToolCall]
      stopped_reason: Literal["completed", "max_turns", "no_tool_calls",
                               "aborted", "error"]

  主循环 while True:
    1. build CanonicalRequest (messages + tools + system)
    2. provider.stream(request) → 收集所有 ModelEvent
    3. 累积成 CanonicalMessage(role=assistant, content=[...], tool_calls=[...])
    4. 校验 tool_calls: 每个 ToolCall.arguments 通过对应 Tool 的 pydantic schema
       - 校验失败 → 注入 synthetic user message 让 LLM 修
       - 校验成功 → 执行工具
    5. 工具执行结果 → CanonicalMessage(role=tool, content=[ToolResultBlock])
    6. 拼回 messages 继续
    7. 没有 tool_calls + 有 text → 返回

  容错矩阵：
    | 错误类型                | 处理                                       |
    |------------------------|-------------------------------------------|
    | prompt_too_long         | truncate head（保留 system + 最近 N 轮）    |
    | max_output_reached      | phase A: 2x tokens, phase B: 续写 prompt  |
    | invalid_tool_arguments  | 注入错误反馈让 LLM 修（最多 3 次）          |
    | tool_execution_error    | 把 error 喂回 LLM 让其决策                 |
    | provider_error          | 重试 3 次（指数退避），失败报错            |
    | circuit_breaker         | 连续 3 轮 all-invalid 工具调用 → 熔断     |
    | abort                   | 用户 Ctrl-C → 立即 stop                    |

  typed Plan 校验（解决"输出不稳定"）：
    - 每个 ToolCall.arguments 在执行前通过 Tool.parameters_pydantic() 校验
    - 失败 → 不执行 tool，注入错误信息让 LLM 修
    - 成功 → 执行
    - 这层是 pure-agent vs PilotDeck 的关键差异

  AbortSignal:
    - 用 asyncio.Event
    - 用户 Ctrl-C / GUI abort 按钮 → set()
    - 循环中检查 → 优雅退出

═══════════════════════════════════════════════
6. CLI `pure-agent chat`
═══════════════════════════════════════════════

  用法:
    pure-agent chat [PROMPT]
    pure-agent chat --resume [SESSION_ID]

  行为:
    1. 如果有 PROMPT → 单轮跑（适合测试）
    2. 如果无 PROMPT → REPL 模式
    3. --resume → 恢复上次 session
    4. 流式输出 LLM 文本到 stderr（rich 染色）
    5. 工具调用可视化（tool name + args 摘要）
    6. Ctrl-C → 优雅退出

  REPL 命令:
    /help     显示命令
    /tools    列出可用工具
    /clear    清屏
    /history  显示历史
    /exit     退出
    /model    切换 model

═══════════════════════════════════════════════
7. 文件结构
═══════════════════════════════════════════════

  src/pure_agent/
  ├── agent/
  │   ├── __init__.py
  │   ├── loop.py             # AIAgentLoop
  │   ├── tool_registry.py    # Tool + Registry
  │   └── plan_validator.py   # typed Plan 校验
  ├── model/
  │   ├── __init__.py
  │   ├── canonical.py        # CanonicalMessage / Request / Event
  │   ├── provider.py         # ProviderAdapter Protocol
  │   ├── openai_adapter.py   # OpenAIAdapter
  │   ├── minimax_adapter.py  # MinimaxAdapter
  │   └── token_counter.py    # token 估算
  ├── tools/
  │   ├── __init__.py
  │   ├── base.py             # Tool 基类
  │   ├── filesystem.py       # read_file / write_file / edit_file
  │   ├── search.py           # glob / grep
  │   ├── web_search.py       # web_search
  │   └── registry.py         # 全局工具注册
  ├── cli/
  │   ├── main.py             # 主入口（已有）
  │   └── chat.py             # chat 子命令

  tests/
  ├── test_canonical.py       # 10 tests
  ├── test_provider.py        # mock provider 5 tests
  ├── test_tools.py           # 15 tests
  ├── test_agent_loop.py      # 10 tests
  └── test_chat_cli.py        # 5 tests

═══════════════════════════════════════════════
8. 验收清单（M1）
═══════════════════════════════════════════════

  8.1 单元
    □ CanonicalMessage 序列化/反序列化 100% 正确
    □ ProviderAdapter mock 实现 → 跑通
    □ OpenAIAdapter httpx 真实 POST 成功（用 echo mock）
    □ 5 工具 typed schema 强校验（错参数不执行）
    □ read_file 大文件 offset/limit 正确
    □ write_file atomic（kill -9 模拟不丢数据）
    □ web_search DDG 真实返回
    □ AIAgentLoop 容错矩阵全跑（mock 各种 error）
    □ typed Plan 校验失败 → 不执行 tool + 注入错误

  8.2 集成
    □ pure-agent chat "read ~/work/pure-agent/pyproject.toml" 真实跑通
    □ pure-agent chat "把上面文件的 typer 改成 click 然后写回去" 真实跑通
    □ token 计数准确
    □ trace 完整（每个 tool_call 都有 log）
    □ 所有 5 工具真实跑过至少 1 次

  8.3 工程
    □ pytest 跑通 40+ tests
    □ ruff lint 通过
    □ 端到端 < 30s（一次 read + write）

═══════════════════════════════════════════════
9. 风险
═══════════════════════════════════════════════

  风险 1: minimax API 不稳定
    缓解：超时 30s + 重试 3 次 + 失败有结构化错误

  风险 2: DDG HTML 抓取被反爬
    缓解：DDG lite 接口（api.duckduckgo.com）+ 失败时返回空 + 不报错

  风险 3: 大文件 read_file 慢
    缓解：offset/limit 强制 + 默认 limit=2000 行

  风险 4: 工具 schema 描述不清晰导致 LLM 不会用
    缓解：每个 tool description 写详细 example + 边界说明

═══════════════════════════════════════════════
10. 里程碑
═══════════════════════════════════════════════

  M1: 上述验收清单全部通过
  时间：~1 周单人
  完成标准：能 `pure-agent chat "read pyproject.toml" + "edit line 11"`
