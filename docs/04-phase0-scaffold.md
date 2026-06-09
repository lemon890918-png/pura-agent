# Phase 0 — 脚手架详细文档

═══════════════════════════════════════════════
0. 目标
═══════════════════════════════════════════════

搭出可运行的项目骨架：
  - pyproject.toml + uv 管理
  - 包结构（10 个模块目录）
  - CLI 入口（typer）
  - logger（structlog）
  - config（yaml + env）
  - SQLite schema（预留全部表）
  - pytest 配置

═══════════════════════════════════════════════
1. 目录结构
═══════════════════════════════════════════════

  /Users/wenxin/work/pure-agent/
  ├── pyproject.toml
  ├── README.md
  ├── .gitignore
  ├── uv.lock                   (生成)
  ├── pytest.ini
  ├── src/
  │   └── pure_agent/
  │       ├── __init__.py       (version, logger init)
  │       ├── py.typed
  │       ├── cli/
  │       │   ├── __init__.py
  │       │   └── main.py       (typer app)
  │       ├── config.py         (Config dataclass + load)
  │       ├── logging.py        (structlog setup)
  │       ├── agent/            (Phase 1 填充)
  │       │   └── __init__.py
  │       ├── model/            (Phase 1 填充)
  │       │   └── __init__.py
  │       ├── tools/            (Phase 1 填充)
  │       │   └── __init__.py
  │       ├── memory/           (Phase 6 填充)
  │       │   └── __init__.py
  │       ├── harness/          (Phase 5 填充)
  │       │   └── __init__.py
  │       ├── persistence/
  │       │   ├── __init__.py
  │       │   ├── db.py         (SQLite 连接 + schema)
  │       │   └── schema.sql    (全部表预留)
  │       ├── server/           (Phase 7 填充)
  │       │   └── __init__.py
  │       └── plan/             (Phase 2 填充)
  │           └── __init__.py
  ├── tests/
  │   ├── __init__.py
  │   ├── conftest.py
  │   └── test_smoke.py
  └── docs/                    (已存在 01/02/03)

═══════════════════════════════════════════════
2. 关键技术选型
═══════════════════════════════════════════════

  Python >= 3.12
  uv                  # 包管理
  typer               # CLI 框架
  structlog           # 结构化日志
  pydantic >= 2.5     # schema
  PyYAML              # 配置
  python-dotenv       # .env 加载
  aiosqlite           # 异步 SQLite (Phase 1 用到，Phase 0 预留)
  pytest              # 测试
  pytest-asyncio      # async 测试
  pytest-cov          # 覆盖

  禁依赖（任何阶段都不能引入）：
    - langchain / langgraph / langsmith / llama-index
    - openai / anthropic 官方 SDK
    - autogen / crewai / smolagents

═══════════════════════════════════════════════
3. pyproject.toml
═══════════════════════════════════════════════

  [project]
  name = "pure-agent"
  version = "0.1.0"
  description = "Pure self-built agent runtime with Goal/Plan long-running support"
  requires-python = ">=3.12"
  readme = "README.md"
  license = {text = "MIT"}
  authors = [{name = "wenxin"}]

  dependencies = [
      "pydantic>=2.5",
      "typer>=0.12",
      "structlog>=24.1",
      "PyYAML>=6.0",
      "python-dotenv>=1.0",
      "aiosqlite>=0.20",  # Phase 1 用
      "httpx>=0.27",       # Phase 1 用
      "anyio>=4.4",        # Phase 1 用
  ]

  [project.optional-dependencies]
  dev = [
      "pytest>=8.0",
      "pytest-asyncio>=0.23",
      "pytest-cov>=5.0",
      "ruff>=0.5",
  ]

  [project.scripts]
  pure-agent = "pure_agent.cli.main:app"

  [build-system]
  requires = ["hatchling"]
  build-backend = "hatchling.build"

  [tool.hatch.build.targets.wheel]
  packages = ["src/pure_agent"]

  [tool.pytest.ini_options]
  asyncio_mode = "auto"
  testpaths = ["tests"]
  addopts = "-v --tb=short --strict-markers"

═══════════════════════════════════════════════
4. config 设计
═══════════════════════════════════════════════

  ~/.pure-agent/
  ├── config.yaml         (主配置)
  ├── .env                (API key 等)
  ├── projects/<hash>/    (每个项目独立目录)
  │   ├── memory.db       (项目级 memory SQLite)
  │   ├── sessions/       (会话)
  │   ├── files/          (agent 写过的文件)
  │   └── skills/         (项目级 skill)
  ├── logs/
  │   ├── agent.log
  │   ├── errors.log
  │   └── gateway.log
  └── cache/
      └── web_search/     (web search 缓存)

  config.yaml 示例:
    agent:
      max_iterations: 90
      token_budget_per_step: 50000
    providers:
      minimax:
        protocol: openai
        url: https://api.minimaxi.com/v1
        api_key_env: MINIMAX_API_KEY
        default_model: MiniMax-M3
    server:
      host: 127.0.0.1
      port: 18789
    logging:
      level: INFO
      format: json
    paths:
      home: ~/.pure-agent
      projects: ~/.pure-agent/projects

  Config 加载顺序（高优先级覆盖低）：
    1. 环境变量 PURE_AGENT_* 覆盖
    2. config.yaml
    3. 默认值

═══════════════════════════════════════════════
5. SQLite schema 预留（全部表，初始 0 表实现）
═══════════════════════════════════════════════

  schema.sql 包含以下表（Phase 0 只创建空库）:

  -- L0 persistence
  projects                (id, name, hash, created_at, updated_at)
  sessions                (id, project_id, name, created_at, updated_at)
  messages                (id, session_id, role, content_json, created_at)
  tool_calls              (id, message_id, tool_name, args_json, result_json, latency_ms, error)
  checkpoints             (id, session_id, step_id, state_json, created_at)

  -- L6 Goal/Plan
  goals                   (id, project_id, text, constraints_json, created_at)
  plans                   (id, goal_id, status, version, created_at, updated_at)
  plan_steps              (id, plan_id, idx, kind, action, deps_json, status, attempts, last_error, assigned_subagent)

  -- L4 Memory
  memory_short            (id, session_id, kind, content_json, expires_at)
  memory_episodic         (id, session_id, event, importance, created_at)
  memory_semantic         (id, project_id, fact, source, confidence, created_at)
  memory_procedural       (id, name, skill_md, examples_json, created_at)

  -- L5 Harness
  traces                  (id, session_id, turn_id, event_type, payload_json, created_at)
  retries                 (id, op_id, attempt, error, backoff_ms, created_at)

  -- FTS5 虚拟表（用于搜索）
  messages_fts            (FTS5 on messages)
  memory_semantic_fts     (FTS5 on memory_semantic)
  plan_steps_fts          (FTS5 on plan_steps.action)

  Phase 0 任务：
    - schema.sql 写完整
    - db.py 提供 apply_schema(conn) 函数
    - 启动时自动 apply

═══════════════════════════════════════════════
6. CLI 命令
═══════════════════════════════════════════════

  pure-agent --version
  pure-agent --help
  pure-agent init                    # 初始化 ~/.pure-agent/
  pure-agent chat                    # 单轮对话（Phase 1）
  pure-agent plan <goal>             # 长程任务（Phase 2）
  pure-agent plan resume             # 恢复（Phase 2）
  pure-agent plan list               # 列出（Phase 2）
  pure-agent server                  # 启动 gateway（Phase 7）
  pure-agent daemon start/stop       # 后台守护（Phase 4）

  Phase 0 只需要：
    --version / --help / init

═══════════════════════════════════════════════
7. logger 设计
═══════════════════════════════════════════════

  structlog 配置:
    - 控制台：开发模式用 pretty
    - 文件：json
    - 字段：timestamp / level / logger / event / **kwargs
    - 颜色：stderr 上有，文件无

  输出示例:
    2026-06-07T19:00:00Z INFO pure_agent.cli Starting CLI version=0.1.0

═══════════════════════════════════════════════
8. 验收清单
═══════════════════════════════════════════════

  □ uv sync 成功
  □ pure-agent --version 输出 0.1.0
  □ pure-agent --help 显示命令树
  □ pure-agent init 在 ~/.pure-agent/ 创建目录 + config.yaml
  □ pure-agent init 第二次跑幂等（不覆盖已有 config）
  □ pytest 跑通（至少 3 个 smoke test）
  □ structlog 输出 json 格式
  □ config.yaml 加载正常
  □ 真实打开 SQLite db 看到全部表（空）

═══════════════════════════════════════════════
9. 风险
═══════════════════════════════════════════════

  - uv 还没装：要先 install
  - 端口冲突：18789 可能被 PilotDeck 占用 → Phase 0 暂不启动 server
  - Python 版本：Mac 默认 3.9 → 需 3.12（brew install python@3.12）

═══════════════════════════════════════════════
10. 完成 Phase 0 后
═══════════════════════════════════════════════

  - 出 04-phase0-completion.md 验收报告
  - 用户过一遍
  - 进入 Phase 1
