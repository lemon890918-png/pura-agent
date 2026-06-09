# Pure-Agent 源码调研报告（一）

调研对象：
  - PilotDeck   /Users/wenxin/work/PilotDeck          (TS + pnpm)
  - OpenClaw    /Users/wenxin/work/openclaw            (TS + pnpm)
  - Hermes      /Users/wenxin/work/hermes-agent        (Python 3.11-3.13 + venv)

调研目标：为自研 agent 项目（pure-agent）的架构决策提供参考。

═══════════════════════════════════════════════
1. 三个项目一图对比
═══════════════════════════════════════════════

| 维度         | PilotDeck            | OpenClaw             | Hermes Agent         |
| 语言         | TypeScript           | TypeScript           | Python               |
| 包管理       | pnpm workspaces      | pnpm                 | uv / venv            |
| 规模（主源） | 7.7 万行 TS          | 86.4 万行 TS         | 40.7 万行 Python     |
| 核心 entry   | src/cli/pilotdeck.ts | src/entry.ts         | run_agent.py         |
| 入口文件 LOC | pilotdeck.ts ≈ 614  | entry.ts ≈ 1000+     | run_agent.py ≈ 12k   |
| 渲染层       | Ink (React for CLI)  | 终端 CLI + TUI       | curses + TUI(TS)     |
| 协议         | 自研 Canonical msg   | 自研 + OpenAI/Anthropic WS | OpenAI + Anthropic |
| Agent loop   | 自研 AgentLoop       | pi-embedded-runner   | run_conversation()   |
| 通道数       | 16 个 channel adapter| 132 个 extensions    | 30+ gateway platforms|
| 插件系统     | extension/plugins    | 完整 plugin SDK      | plugins/ 目录        |
| 协议授权     | AGPL 3.0             | 私有 (claude.md 提及) | MIT                  |
| 学院背书     | THUNLP+ModelBest+OpenBMB| 个人/小团队项目    | Nous Research        |
| 活跃度       | 2026.5.28 开源       | 高频 commit          | 高频 commit          |

PilotDeck 是"学术背书 + 中文 + 紧凑代码"，OpenClaw 是"商业化体量 + 巨量适配器 + plugin-driven"，Hermes 是"Python 生态 + ink-TUI + 稳定迭代"。

═══════════════════════════════════════════════
2. PilotDeck 源码深读
═══════════════════════════════════════════════

2.1 目录结构（src/）
  agent/          自研 ReAct agent 循环
  gateway/        WebSocket 桥接到真实 LLM
  cli/            CLI 入口 + Ink TUI
  tool/           工具定义（JSON Schema）
  model/          自研 Canonical message + provider
  router/         任务路由 + token 优化（核心差异化）
  context/        自动 compact + 白盒 memory
  permission/     权限系统
  cron/           定时任务
  always-on/      后台 agent（"always-on execution"）
  channel/        16 个 channel adapter（feishu / weixin / qq ...）
  extension/      扩展点：plugins / hooks / skills
  mcp/            MCP 客户端（标准 SDK）
  session/        会话持久化
  lifecycle/      Claude Code 风格 hook 系统
  task/           任务管理
  telemetry/      遥测
  web/            Gateway HTTP server
  adapters/       CLI/TUI/IM 适配器

2.2 Agent Loop（src/agent/loop/AgentLoop.ts, 1625 行）
  while(true) 循环：
    1) 自动 compact 评估（context.tryAutoCompact）
    2) 创建 model request
    3) router.decide — 选 model + tier（粘性 sticky）
    4) post-routing recompact — 小窗口模型再压
    5) router.execute — 流式取 model event，applyModelEventToAssembler
    6) assembleAssistantMessage → 拼出完整 assistant 消息
    7) collectToolCalls → 收集 tool_calls
    8) 处理异常：json 自纠错 / prompt_too_long truncate / max_output_reached 三段恢复
    9) 执行工具 → 把 tool result 写回 messages
    10) continue 或返回
  自带：
    - 连续 3 轮 all-invalid-tool-call 熔断（circuit breaker）
    - 粘性 tier（previousTier 透传到下轮）
    - 自适应 maxOutputTokens（2x up to ceiling）

2.3 Router（src/router/）
  - scenarios: 任务类别 → model 映射
  - fallback: 多 model 链
  - tokenSaver: judge 模型分类，simple/medium/complex/reasoning 四档
  - zeroUsageRetry: 0 token 返回重试
  **PilotDeck 的核心差异化** — 把"按 token 花钱"变成工程问题

2.4 Subagent（src/agent/sub/builtinSubagentTypes.ts）
  内置 4 个硬编码 subagent:
    - general-purpose: 全工具 + 全 R/W
    - explore:        read-only，剥掉 project instructions & gitStatus
    - plan:           read-only（read/grep/glob），无 bash
    - verify:         read-only，检验产物
  每个有 system prompt suffix + allowedTools 白名单 + isReadOnly 标志
  SubAgentSession 实现 fork/clone parent context

2.5 Context（src/context/）
  - 自动 compact（评估 token 预算）
  - 白盒 memory（memory generation / extraction / storage / retrieval 可视化）
  - Dream mode（闲置时 LLM 整理 memory）
  - 一键 rollback
  - NullContextRuntime / 真实 ContextRuntime 抽象

2.6 WorkSpace 隔离
  ~/.pilotdeck/projects/<hash>/ 每个项目一个目录，自带 files / memory / skills
  - 完全文件级隔离，避免全局 context 污染

═══════════════════════════════════════════════
3. OpenClaw 源码深读
═══════════════════════════════════════════════

3.1 项目体量
  - 86.4 万行 TS（不含 .test.ts）
  - 132 个 extensions（在 extensions/ 目录下）
  - 17+ 个子包（packages/）
  - 体量比 PilotDeck 大 10 倍

3.2 关键架构（src/ 顶层）
  entry.ts              进程入口
  agents/               agent 运行核心（pi-embedded-runner）
  channels/             通道实现（注意：与 PilotDeck 不同，OpenClaw 的 channels 在 core）
  channels/plugins/     通道插件 manifest + contract
  cli/                  CLI 子命令 200+ 文件
  commands/             命令注册系统
  plugins/              插件加载器 / 注册表 / manifest 校验
  context-engine/       context 管理
  gateway/              HTTP/RPC server（run.py 风格）
  gateway/protocol/     协议定义
  cron/                 定时任务
  daemon/               守护进程
  acp/                  Agent Client Protocol（VS Code / Zed / JetBrains）
  auto-reply/           自动回复
  bootstrap/            启动 bootstrap
  canvas-host/          canvas 渲染
  chat/                 会话
  commitments/          承诺追踪
  compat/               兼容层
  config/               配置
  context-engine/       context 引擎
  crestodian/           内部名称（推测为健康/审计）
  docker-build-cache*   Docker 镜像缓存

3.3 Agent 核心（src/agents/）
  - pi-embedded-runner.ts:    内嵌 runner（OpenClaw 自研的 agent 引擎）
  - pi-embedded-subscribe.ts:  订阅 + 块回复处理
  - pi-embedded-payloads.ts:   payload 处理
  - pi-embedded-helpers/:     工具函数
  - pi-embedded-runner-extraparams: 模型特殊参数解析
  - pi-compaction-constants:   compact 常量
  - pi-mcp-style.cache:        MCP-style cache
  - pi-tools-*:                工具定义（read/write/edit/bash/process/sandbox）
  - pi-bundle-*:               bundle loader
  - pi-hooks/:                 hook 系统
  - pi-auth-*:                 auth/credential 管理

  Agent 模式: 一切围绕"embedded runner" — 每次 agent run 启动一个 runner 实例，
  runner 内部用 subscribe 处理流式事件。

3.4 Subagent 体系（src/agents/subagent-*）
  这是 OpenClaw 真正有深度的部分:
  - subagent-registry.ts:           注册表（持久化）
  - subagent-registry-store.ts:     存储后端
  - subagent-registry-runtime.ts:   运行时
  - subagent-announce-*:            公告系统（子 agent 完成时通知父）
  - subagent-spawn-*:               spawn 规划/执行
  - subagent-depth.ts:              深度限制
  - subagent-lifecycle-events.ts:   生命周期事件
  - subagent-target-policy.ts:      目标策略（限定调用方）
  - subagent-capabilities.ts:       能力声明
  - subagent-recovery-state.ts:     恢复状态
  - subagent-session-*:             session key 派生
  - subagent-registry-lifecycle:    完整生命周期
  - subagent-orphan-recovery:       孤立 subagent 恢复
  - subagent-announce-timeout:      公告超时
  - subagent-requester-store-key:   requester 隔离
  **PilotDeck 只有 4 个 hardcoded subagent，OpenClaw 是一套完整 subagent OS**

3.5 Channel 系统（src/channels/）
  - src/channels/plugins/types.plugin.ts:  插件定义
  - src/channels/plugins/types.core.ts:    core 类型
  - src/channels/plugins/types.adapters.ts: adapter 类型
  - src/plugin-sdk/channel-contract.ts:    SDK 契约
  - 每个 bundled channel（extensions/<id>/）有 channel.ts / setup.ts / gateway.ts / outbound.ts
  - AGENTS.md 强制：extension 不直接 import src/channels/**，必须走 openclaw/plugin-sdk/*

3.6 Plugin 系统（src/plugins/）
  这是 OpenClaw 投入最大的工程:
  - manifest 校验（openclaw.plugin.json）
  - 加载器（jiti-loader-cache, source-loader）
  - 注册表（plugin-registry, registry-lifecycle）
  - 激活规划（activation-planner）
  - 安装/卸载/更新（install.ts / uninstall.ts / update.ts）
  - 依赖去重（dependency-denylist, package-entry-resolution）
  - 隔离（externalized-bundled-plugins）
  - 控制面 vs 运行时面分离
  - 缓存层（loader-cache-state, plugin-metadata-snapshot）
  - manifest-first 行为：discovery / setup / config 全部从 manifest 出发

3.7 ACP（Agent Client Protocol）
  src/acp/ — 让 OpenClaw 嵌入 IDE（VS Code / Zed / JetBrains）
  ACP binding architecture 是显式设计点
  acp-spawn.ts / acp-spawn-parent-stream.ts: 通过 ACP 派发 subagent
  这层 PilotDeck 和 Hermes 都没有

3.8 几个值得抄的设计点
  - pi-embedded-runner 的 subscribe 模型（事件驱动 + 块回复）
  - subagent-registry 持久化 + announce + orphan-recovery（比 PilotDeck 4 个 hardcoded 健壮得多）
  - manifest-first 插件系统（控制面 / 运行时面分离）
  - provider-family shared helpers（避免每个 provider 重复写 compat logic）
  - compaction-real-conversation 测试（用真实对话做 regression）
  - prompt-cache-stability.ts：保证 map/set/registry 序列化顺序稳定以利 prompt cache

═══════════════════════════════════════════════
4. Hermes Agent 源码深读
═══════════════════════════════════════════════

4.1 项目体量
  - 40.7 万行 Python
  - 17k tests across 900 files（项目自报）
  - 单文件最大：run_agent.py ~12k LOC（核心 agent loop）
  - cli.py ~11k LOC（CLI orchestrator）

4.2 目录结构
  run_agent.py          # AIAgent 类 — 核心对话循环（~12k LOC）
  model_tools.py        # 工具编排，discover_builtin_tools()
  toolsets.py           # toolset 定义
  cli.py                # HermesCLI 类 — 交互式 CLI（~11k LOC）
  hermes_state.py       # SessionDB — SQLite session store (FTS5 搜索)
  hermes_constants.py   # get_hermes_home() — profile-aware 路径
  hermes_logging.py     # 三个 log：agent.log / errors.log / gateway.log
  batch_runner.py       # 并行批处理
  agent/                # agent 内部（provider / memory / caching / compression）
  hermes_cli/           # CLI 子命令
  tools/                # 工具实现（auto-discovered via tools/registry.py）
  tools/environments/   # 终端后端：local / docker / ssh / modal / daytona / singularity
  gateway/              # IM gateway
  gateway/platforms/    # 30+ 平台适配器
  plugins/              # 插件
    memory/             # honcho / mem0 / supermemory
    context_engine/     # context 引擎
    model-providers/    # openrouter / anthropic / gmi
    kanban/             # 多 agent 看板
  optional-skills/      # 大型 / niche skills（不默认激活）
  skills/               # 内置 skills
  ui-tui/               # Ink (React) TUI — hermes --tui
  tui_gateway/          # Python JSON-RPC backend for TUI
  acp_adapter/          # ACP server
  cron/                 # 调度器
  scripts/              # 测试 / 发布

4.3 AIAgent 主循环（run_agent.py）
  class AIAgent:
    def __init__(self, base_url, api_key, provider, api_mode, model, ...):
      # 60+ 参数！包括 credentials / routing / callbacks / session / budget / pool
    def chat(self, message): ...
    def run_conversation(self, user_message, system_message, ...): ...

  while (api_call_count < max_iterations and budget.remaining > 0) or _budget_grace_call:
    if _interrupt_requested: break
    response = client.chat.completions.create(model, messages, tools)
    if response.tool_calls:
      for tc in response.tool_calls:
        result = handle_function_call(tc.name, tc.args, task_id)
        messages.append(tool_result_message(result))
      api_call_count += 1
    else:
      return response.content

  关键设计点：
  - 同步循环 + 主动 interrupt 检测
  - 预算追踪（iteration_budget + grace call）
  - 工具自动发现（registry pattern）
  - max_iterations 默认 90
  - 整个 agent 单文件实现 — 简单暴力

4.4 工具发现（tools/registry.py）
  - tools/*.py 每个 import 时调 registry.register()
  - model_tools.py 触发自动发现
  - 加载顺序：tools/registry.py → tools/*.py → model_tools.py → run_agent.py
  **PilotDeck 和 OpenClaw 都是显式 builtin 列表，Hermes 是 import-time 自动注册**

4.5 Plugin 系统（plugins/）
  - model-providers/:  每个 provider 一个插件（openrouter / anthropic / gmi / ...）
  - memory/:  第三方 memory 后端（honcho / mem0 / supermemory）
  - context_engine/:  context 引擎
  - kanban/:  多 agent 看板
  - observability/:  metrics / traces / logs
  - image_gen/:  图像生成
  - hermes-achievements/:  成就系统
  - disk-cleanup / google_meet / spotify / ...

4.6 通道系统（gateway/platforms/）
  30+ 平台：
    telegram / discord / slack / whatsapp / homeassistant / signal
    matrix / mattermost / email / sms / dingtalk / wecom / weixin
    feishu / qqbot / bluebubbles / yuanbao / webhook / api_server
  每个平台一个文件，有自己的 send/receive/typing/auth 逻辑

4.7 终端后端（tools/environments/）
  - local: 本机 shell
  - docker: 容器化
  - ssh: 远程
  - modal: Modal 云
  - daytona: Daytona 云
  - singularity: Singularity 容器
  **沙箱化执行**，PilotDeck 没有这个抽象

4.8 TUI（ui-tui/）
  - Ink (React) 写的 TypeScript TUI
  - Python JSON-RPC backend (tui_gateway/) 提供数据
  - 双进程架构：Python 跑 agent + TS 跑 UI
  - 这是 PilotDeck 不具备的设计 — PilotDeck 的 Ink TUI 是单进程

4.9 Kanban（多 agent）
  - plugins/kanban/: 多 agent 看板
  - kanban.py / kanban_db.py / kanban_decompose.py / kanban_specify.py / kanban_swarm.py
  - 把任务分解成多个 subagent 并行执行
  **PilotDeck 没有这个**，是 Hermes 独家

═══════════════════════════════════════════════
5. 三方共同的设计模式
═══════════════════════════════════════════════

5.1 三层模型
  - 协议层（OpenAI / Anthropic / 自研 Canonical）— 抽象 LLM 差异
  - Agent 层（loop / subagent / context）— 控制流
  - 通道层（CLI / TUI / IM）— 接入端

5.2 工具协议
  - 全部用 JSON Schema 描述工具
  - 全部支持多 provider（OpenAI tool_calls / Anthropic tools）
  - 全部有工具白名单 / 权限层

5.3 Channel Adapter
  - 全部有 channel adapter 抽象
  - session key 派生规则（thread binding / DM history limit）
  - allowlist / pairing 机制

5.4 持久化
  - PilotDeck: 文件 + WorkSpace 隔离
  - OpenClaw: session_file_repair + write_lock
  - Hermes: SQLite (FTS5 搜索)

5.5 Skill / Hook / Plugin
  - 三个项目都有 skill 系统（SKILL.md + frontmatter）
  - 三个项目都有 hook 系统
  - 只有 OpenClaw 有完整 plugin SDK（manifest + contract）
  - PilotDeck / Hermes 是 builtin + 简化 plugin loader

═══════════════════════════════════════════════
6. 三个项目各自独有 / 特别强的点
═══════════════════════════════════════════════

PilotDeck:
  - 任务难度路由（router tokenSaver）
  - 白盒 memory + dream mode + rollback
  - WorkSpace 隔离（per-project 独立 files/memory/skills）
  - 粘性 tier（sticky）
  - 紧凑代码（7.7 万行完成 80% 功能）

OpenClaw:
  - ACP（IDE 集成）
  - 完整 plugin SDK（manifest + 激活规划 + 控制面/运行时面分离）
  - Subagent OS（registry + announce + orphan-recovery）
  - 132 个 bundled extensions
  - prompt-cache-stability 优化
  - compaction-real-conversation regression 测试

Hermes:
  - SQLite session store + FTS5 搜索
  - 终端后端抽象（local/docker/ssh/modal/daytona/singularity）
  - Python + Ink TUI 双进程
  - Kanban（多 agent 看板）
  - 单文件极简（12k LOC 跑通核心 agent）

═══════════════════════════════════════════════
7. 三个项目都没解决 / 解决得不好的点
═══════════════════════════════════════════════

7.1 Agent 输出确定性
  - 三个项目都用事后兜底（retry / circuit breaker / LargeFileRepair）
  - 没有任何一个在结构层面解决"输出格式不稳定"
  - 这正是 pure-agent 要切入的点

7.2 工具调用的中间状态
  - 三个项目都把 tool_call 视为"瞬时事件"，无中间状态
  - 长任务（>10 分钟）没有 progress checkpoint
  - 用户中途进来无法"插话"

7.3 知识可移植性
  - memory 全部锁在项目内
  - 没有"agent 学到的通用知识"层
  - skill 复制粘贴是手动操作

7.4 Agent 间的协议
  - 多 agent 通信全部走 message passing
  - 没有标准 protocol（类似 FIPA ACL / KQML）
  - OpenClaw 的 subagent announce 算半成品

═══════════════════════════════════════════════
8. 调研结论（架构借鉴表）
═══════════════════════════════════════════════

pure-agent 借鉴的层次（自上而下）:

| 组件             | 借鉴自       | 备注                                  |
|------------------|--------------|---------------------------------------|
| 协议抽象         | PilotDeck     | Canonical message + 多 provider      |
| Agent loop       | PilotDeck     | while(true) + router + 容错           |
| Router           | PilotDeck     | tier-based 路由                       |
| Subagent         | OpenClaw      | registry + lifecycle，比 hardcoded 强 |
| Plugin system    | OpenClaw      | manifest + contract                    |
| Channel adapter  | Hermes+OpenClaw| 30+ 平台任选                          |
| Sandbox          | Hermes        | 终端后端抽象                          |
| TUI              | Hermes        | Ink + Python/TS 双进程                |
| 持久化           | Hermes        | SQLite + FTS5                         |
| 上下文压缩       | PilotDeck     | auto-compact + post-routing recompact|
| 工具协议         | 三方融合      | JSON Schema + 白名单 + 权限           |
| 输出确定性       | 自研          | 三个项目都没解决，pure-agent 的切入点 |
| ACP              | OpenClaw      | IDE 集成（可选）                      |

下一份文档：02-architecture-decisions.md — 给出 pure-agent 的具体架构选型。
