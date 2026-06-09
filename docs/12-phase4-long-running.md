# Phase 4 — 长时间运行 详细设计

═══════════════════════════════════════════════
0. 目标
═══════════════════════════════════════════════

把 pure-agent 升级到能跑 30+ 分钟的长程任务：

  - auto-compact 集成到 AIAgentLoop
  - FileTracker 集成到 read_file (diff 优化)
  - checkpoint / resume 机制
  - watchdog (超时保护 + 死循环检测)
  - 用户 steer (运行中插话)
  - token budget 集成 (Phase 3 已实现)

承诺:
  - 30+ 分钟 plan 跑通不出错
  - 5 步 plan token 比 Phase 2 减少 30-60% (实测数字)
  - 中断后能 resume
  - 用户能插话 (在 turn 之间)

═══════════════════════════════════════════════
1. AIAgentLoop 改造
═══════════════════════════════════════════════

1.1 turn 流程 (改造后)
    for turn in 1..max_turns:
        a. 检查 abort_signal → break
        b. 检查 token budget (per step)
        c. 检查 context size → 触发 Compactor
        d. 注入 memory layers (procedural + semantic + episodic)
        e. build request
        f. provider.stream() → consume events
        g. 收集 tool calls
        h. execute tool (含 FileTracker 集成)
        i. 检查 watchdog (single tool 耗时)
        j. emit events
        k. token 累计 → budget check

1.2 Compactor 触发
    - 每个 turn 前: estimate_tokens(messages)
    - if > threshold (默认 80% of model max):
        result = compactor.compact(messages, keep_last=4)
        messages = result.new_messages
        token_usage += result.usage
        emit("compacted", original_tokens, compacted_tokens)

1.3 Watchdog
    - 每个 tool 执行开始 → record t0
    - 结束 → record t1
    - if (t1 - t0) > TOOL_TIMEOUT (默认 120s):
        → kill tool execution (asyncio.CancelledError)
        → inject "tool timed out" message
        → continue

1.4 Checkpoint
    - 每个 turn 完成 → save messages + step state to DB
    - 用 checkpoints 表 (Phase 0 已建)
    - checkpointer.save(session_id, turn_id, messages, ...)

1.5 Steer
    - background task 监听 stdin (REPL mode) 或 file (headless)
    - 用户输入 → asyncio.Queue
    - 每个 turn 开始检查 queue → 注入到 system prompt
    - 用户能:
      - 修改 plan (新增 step)
      - 改变 step action
      - 中止当前 step

═══════════════════════════════════════════════
2. FileTracker 集成到 read_file
═══════════════════════════════════════════════

2.1 改 ReadFileTool
    - 接收 FileTracker 实例
    - execute():
        1. tracker.lookup(path) → FileState
        2. if cached: include "unchanged since last read" hint
        3. read content (含 line numbers)
        4. return content + cached flag

2.2 改 EditFileTool / WriteFileTool
    - 写入后: tracker.invalidate(path)
    - 下次 read → 走 full read (mtime 变)

═══════════════════════════════════════════════
3. Checkpoint 机制
═══════════════════════════════════════════════

3.1 数据 (schema 已建)
    checkpoints:
      id, session_id, turn_id, messages_json,
      step_state_json, plan_state_json, created_at

3.2 save()
    - 每个 turn end → save current messages + state
    - 用 JSON 序列化 messages (TypedDict-friendly)

3.3 load()
    - session 启动时: load latest checkpoint
    - resume from there

3.4 clean()
    - plan 完成 → 删除 checkpoints

═══════════════════════════════════════════════
4. Watchdog
═══════════════════════════════════════════════

4.1 监测对象
    - 单 tool 执行时间
    - 单 turn 时间
    - 连续 N 轮没进展 (no_progress)
    - LLM 总耗时

4.2 触发动作
    - tool 超时 → kill + inject error + retry
    - turn 超时 → abort step
    - no_progress N 轮 → 提示用户
    - LLM 超时 → cancel stream

4.3 配置
    - tool_timeout_s = 120 (per tool)
    - turn_timeout_s = 600 (per turn)
    - no_progress_threshold = 5 (consecutive)

═══════════════════════════════════════════════
5. Steer (运行中插话)
═══════════════════════════════════════════════

5.1 接口
    - runner.steer(text: str) → 立即插入到下一个 turn
    - 或 runner.inject_user_message(CanonicalMessage)
    - 用 asyncio.Queue 异步

5.2 use case
    - "暂停"
    - "把 step 3 改成 X"
    - "增加一个新 step: Y"
    - "改用 deepseek 模型"

5.3 实现
    - PlanRunner.steer_queue: list[CanonicalMessage]
    - 每个 step start → 检查 queue → 注入 message
    - step 修改 → 直接改 storage.plan.steps

═══════════════════════════════════════════════
6. 关键模块
═══════════════════════════════════════════════

  agent/loop.py            改造
  agent/checkpoint.py      新建
  agent/watchdog.py        新建
  harness/timeout.py       新建 (asyncio 工具)

  tools/filesystem.py      改造 (read_file 用 FileTracker)

  cli/chat.py              改造 (REPL 加 /steer 命令)
  cli/plan_cli.py          改造 (启动后台 task 监听)

  server/ 留待 Phase 7

═══════════════════════════════════════════════
7. 验收清单
═══════════════════════════════════════════════

  7.1 单元
    □ Compactor 在 AIAgentLoop 集成后调用
    □ FileTracker.lookup 减少 read_file 输出
    □ Checkpoint.save / load round-trip
    □ Watchdog 触发 tool 超时
    □ Steer 注入 message 到下一个 turn

  7.2 集成
    □ 5 步 plan token 减少 30-60% (实测)
    □ 10 步 plan 不爆 context (auto-compact 至少触发 1 次)
    □ Ctrl-C 中断后 plan-resume 恢复
    □ Steer: 运行时增加 step 被采纳

  7.3 工程
    □ pytest 180+ tests
    □ CLI 加 /steer
    □ 文档完整

═══════════════════════════════════════════════
8. 风险
═══════════════════════════════════════════════

  风险 1: auto-compact 频繁触发导致 LLM 失忆
    缓解: 保留 system + 最近 2 turn raw, max 3 compactions
    缓解: summary 里强制要求 file:line 引用

  风险 2: checkpoint 写太频繁 I/O 重
    缓解: 每 turn end 写一次, 不每个 token
    缓解: SQLite WAL 模式

  风险 3: steer 注入到 system 错位
    缓解: steer 注入到 user message (符合 LLM 协议)

═══════════════════════════════════════════════
9. 里程碑
═══════════════════════════════════════════════

  M3: Phase 3 验收通过 ✓
  M4: Phase 4 验收 (30 分钟 plan 跑通 + token 减少 30-60%)
  时间: ~1 周单人
