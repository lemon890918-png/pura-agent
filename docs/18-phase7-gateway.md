# Phase 7 — Gateway + WebSocket 详细设计

═══════════════════════════════════════════════
0. 目标
═══════════════════════════════════════════════

为 pure-agent 提供 HTTP/WS gateway：

  - Python HTTP server (端口 18789, 类似 PilotDeck)
  - WebSocket 流式输出
  - 多 session 并发
  - REST API: /health, /sessions, /sessions/{id}, /chat, /plan
  - CLI → Gateway 通讯
  - ContextBuilder 自动注入 system prompt
  - JSON log

承诺:
  - HTTP server 起来能 curl /health 返回 200
  - WebSocket 流式接收到 LLM 输出
  - 多 session 并发不冲突
  - ContextBuilder 真的注入到 AIAgentLoop

═══════════════════════════════════════════════
1. 架构
═══════════════════════════════════════════════

  ┌────────────────┐
  │   CLI / GUI    │
  │  (HTTP/WS)     │
  └────────┬───────┘
           │ JSON over HTTP/WS
           ▼
  ┌────────────────┐
  │   Gateway      │  端口 18789
  │   (FastAPI)    │
  │   - /health    │
  │   - /sessions  │
  │   - /chat      │
  │   - /plan      │
  │   - /ws        │
  └────────┬───────┘
           │
  ┌────────▼────────┐
  │  SessionManager │
  │  - 多 session  │
  │  - L1/L2 cache │
  └────────┬────────┘
           │
  ┌────────▼────────┐
  │  AIAgentLoop    │
  │  + ContextBuild │
  │  + Memory       │
  └─────────────────┘

═══════════════════════════════════════════════
2. 关键模块
═══════════════════════════════════════════════

  server/gateway.py     FastAPI app + 路由
  server/sessions.py    SessionManager + Session state
  server/loop_runner.py 把 AIAgentLoop 包成可调用的 async function
  server/plan_runner.py 把 PlanRunner 包成可调用的 async function
  server/ws.py          WebSocket 路由
  server/auth.py        API key 校验

═══════════════════════════════════════════════
3. REST API
═══════════════════════════════════════════════

3.1 GET /health
    - 返回 {"status": "ok", "version": "0.1.0", "uptime_s": 1234}

3.2 GET /sessions
    - 返回 {"sessions": [{"id": "s1", "title": "...", "updated_at": "..."}]}

3.3 POST /sessions
    - body: {"title": "..."}
    - 返回 {"id": "s1"}

3.4 GET /sessions/{id}
    - 返回 {"id": "s1", "messages": [...], "summary": "..."}

3.5 POST /sessions/{id}/chat
    - body: {"message": "..."}
    - 返回 {"session_id": "s1", "response": "..."}

3.6 POST /sessions/{id}/plan
    - body: {"goal": "..."}
    - 返回 {"plan_id": "p1", "status": "running"}

3.7 GET /sessions/{id}/plan/{plan_id}
    - 返回 {"plan_id": "p1", "status": "done", "steps": [...]}

3.8 WS /ws/sessions/{id}
    - send: {"type": "chat", "message": "..."}
    - recv: {"type": "text_delta", "text": "..."}
    - recv: {"type": "tool_call", "name": "read_file", "args": {...}}
    - recv: {"type": "message_end", "finish_reason": "stop"}

═══════════════════════════════════════════════
4. SessionManager
═══════════════════════════════════════════════

4.1 Session state
    - id: str
    - title: str
    - messages: list[CanonicalMessage]
    - context: ContextBuilder 实例
    - memory: MemoryLayers
    - last_used_at: float
    - lock: asyncio.Lock (避免并发)

4.2 创建
    - 自动建 default project
    - 自动建 default session
    - 自动建 L1/L2/L3/L4 memory

4.3 Chat
    - 取 session lock
    - 用 ContextBuilder 注入 system prompt
    - 调 AIAgentLoop.run()
    - 流式 emit 到 WS
    - 累加 usage

═══════════════════════════════════════════════
5. WebSocket
═══════════════════════════════════════════════

  5.1 连接
    - ws://localhost:18789/ws/sessions/{id}
    - 双向 JSON

  5.2 Server → Client events:
    - {"type": "text_delta", "text": "..."}
    - {"type": "tool_call", "name": "read_file", "args": {...}}
    - {"type": "tool_result", "name": "...", "output": "..."}
    - {"type": "message_end", "finish_reason": "stop"}
    - {"type": "error", "message": "..."}
    - {"type": "usage", "prompt": 100, "completion": 50}
    - {"type": "checkpoint_saved", "id": "ckpt_xxx"}

  5.3 Client → Server commands:
    - {"type": "chat", "message": "..."}
    - {"type": "steer", "message": "..."}
    - {"type": "abort"}
    - {"type": "ping"}

═══════════════════════════════════════════════
6. ContextBuilder 集成
═══════════════════════════════════════════════

  AIAgentLoop.system_prompt = build_system_prompt(
      base="...agent instructions...",
      context=context_builder.build(),  # 4 层 memory 注入
      project_instructions="...project-specific..."
  )

═══════════════════════════════════════════════
7. 关键依赖
═══════════════════════════════════════════════

  - fastapi
  - uvicorn
  - websockets (FastAPI 内置)
  - pydantic v2 (已有)
  - httpx (已有)

═══════════════════════════════════════════════
8. 验收清单
═══════════════════════════════════════════════

  8.1 单元
    □ FastAPI app 启动
    □ /health 返回 200
    □ /sessions CRUD
    □ /chat 返回响应
    □ /plan 创建并执行
    □ WS 接通
    □ 流式 text_delta 收到
    □ ContextBuilder 注入 system prompt

  8.2 集成
    □ 真实 LLM 经 gateway 跑通
    □ 多 session 并发
    □ checkpoint 经 gateway 触发
    □ steer 经 WS 注入

  8.3 工程
    □ pytest 250+ tests
    □ 文档完整
    □ CLI 调用 gateway 跑通

═══════════════════════════════════════════════
9. 里程碑
═══════════════════════════════════════════════

  M7: Gateway + WS + ContextBuilder 集成
  时间: ~1 周单人
