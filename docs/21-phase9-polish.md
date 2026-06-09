# Phase 9 — 打磨 详细设计

═══════════════════════════════════════════════
0. 目标
═══════════════════════════════════════════════

把 pure-agent 升级到生产级：

  - 真实流式 WebSocket (text_delta 实时)
  - 端口可配 (避免 18789 冲突)
  - SessionManager 持久化
  - Auth middleware
  - Tool manifest
  - Sandbox 完善
  - 三方 benchmark (compare with PilotDeck)
  - 完整 README

承诺:
  - 流式 WS 真实工作
  - 端口冲突可解决
  - 重启 gateway 不丢 session
  - benchmark 跑过
  - README + docs 完整

═══════════════════════════════════════════════
1. 流式 WebSocket
═══════════════════════════════════════════════

1.1 Provider 改造
    - 加 stream_events 模式
    - 每个 ModelEvent 触发 callback
    - callback 收到 event 后 emit to WS

1.2 Gateway 改造
    - ws_session 在 chat 模式下订阅 provider events
    - 每个 text_delta 立即 send_json

1.3 UI 改造
    - 用 WebSocket 代替 REST /chat
    - 接收 text_delta 累加到 assistant message
    - 接收 message_end 标记完成

═══════════════════════════════════════════════
2. 端口配置
═══════════════════════════════════════════════

2.1 默认 18789 (跟 PilotDeck 冲突)
    - 改默认 18790
    - env var PURE_AGENT_PORT 优先

2.2 UI 改造
    - 读 ?gateway=port query string
    - 默认 18790

═══════════════════════════════════════════════
3. SessionManager 持久化
═══════════════════════════════════════════════

3.1 当前问题
    - SessionManager 内存 dict
    - 重启 gateway 丢 session

3.2 改造
    - 把 session metadata 存 sessions 表 (Phase 0 已有)
    - 重启时从 sessions 表 load
    - in-memory L1 仍不持久化 (OK)

3.3 Phase 9 目标
    - 实现但简化版
    - SessionManager.persist() / .load()

═══════════════════════════════════════════════
4. Auth Middleware
═══════════════════════════════════════════════

4.1 简单 API key 校验
    - header: X-API-Key: <key>
    - 或 query: ?api_key=<key>
    - key 来自 config

4.2 没 key 时
    - localhost 默认放过
    - 远端必须有 key

═══════════════════════════════════════════════
5. Tool Manifest
═══════════════════════════════════════════════

5.1 列出所有 tool + schema
    - GET /tools
    - 返回 [{name, description, parameters, read_only}]

5.2 用于
    - 文档生成
    - UI 显示可用工具
    - benchmark 报告

═══════════════════════════════════════════════
6. Sandbox 完善
═══════════════════════════════════════════════

6.1 当前
    - Sandbox(root=...) 强制所有 path 在 root 下

6.2 Phase 9
    - 加 deny list (e.g. .env, *.key)
    - 加 audit log (记录每次 read/write)
    - 加 max file size 限制

═══════════════════════════════════════════════
7. 三方 Benchmark
═══════════════════════════════════════════════

7.1 任务
    - 5 个 benchmark task:
      1. Read & summarize file
      2. Fix typo in code
      3. Generate plan for refactor
      4. Search web + write summary
      5. Multi-step plan (read 2 files, add function)

7.2 度量
    - 端到端时间
    - token 消耗
    - 成功率
    - 工具调用数

7.3 对比
    - pure-agent
    - PilotDeck
    - raw OpenAI API (no tools)

7.4 输出
    - docs/22-benchmark.md
    - 表格 + 结论

═══════════════════════════════════════════════
8. README
═══════════════════════════════════════════════

8.1 /Users/wenxin/work/pure-agent/README.md
    - 项目简介
    - 跟 PilotDeck / OpenClaw / Hermes 对比
    - 安装
    - 用法 (CLI / Gateway / UI)
    - 架构图
    - 测试运行
    - 已知问题
    - 路线图

═══════════════════════════════════════════════
9. 验收清单
═══════════════════════════════════════════════

  9.1 单元
    □ 流式 WS text_delta
    □ SessionManager.persist() / .load()
    □ Auth middleware
    □ /tools endpoint
    □ Sandbox deny list
    □ Benchmark runner

  9.2 集成
    □ 真实流式 WS 端到端
    □ 重启 gateway session 还在
    □ API key 校验
    □ 5 个 benchmark task

  9.3 工程
    □ pytest 280+ tests
    □ README + benchmark + 8 docs
    □ 全 9 阶段完成

═══════════════════════════════════════════════
10. 里程碑
═══════════════════════════════════════════════

  M9: 全部完成
  时间: ~1 周单人
