# Phase 2 — Goal/Plan 系统 详细设计

═══════════════════════════════════════════════
0. 目标
═══════════════════════════════════════════════

把 pure-agent 升级到能跑**长程任务**（5+ 分钟，多步骤，跨 step 上下文）：

  - 用户给一个 Goal（自由文本 + 约束）
  - 内部 PlanAgent 拆 Plan（typed pydantic，不是 free-form text）
  - Plan 持久化到 SQLite
  - 逐步执行：每 step 触发 AIAgentLoop
  - 失败重试 / 升级 / 跳过
  - Ctrl-C 中断后能 resume
  - Plan 可中途编辑
  - M2 达成：长程任务能跑 5 分钟不出错

═══════════════════════════════════════════════
1. 数据模型
═══════════════════════════════════════════════

  Goal:
    id: str  (uuid)
    project_id: str
    text: str  (用户原话)
    constraints: GoalConstraints  (可选 deadline / budget / scope)
    status: pending | planning | running | done | failed | abandoned
    created_at / updated_at

  GoalConstraints:
    deadline: datetime | None
    max_token_budget: int | None
    scope_paths: list[str]   # 文件白名单

  Plan:
    id: str
    goal_id: str
    version: int  (1 起步, 每次 edit +1)
    status: pending | in_progress | done | failed | abandoned
    created_at / updated_at

  PlanStep (typed, pydantic 强校验):
    id: str
    plan_id: str
    idx: int  (顺序)
    kind: StepKind  (read | code | search | verify | deliver | plan)
    action: str  (自由文本 action 描述)
    deps: list[str]  (依赖的 step ids)
    status: pending | in_progress | done | failed | blocked | skipped
    assigned_subagent: str | None  (Phase 5 用)
    attempts: int
    max_attempts: int = 3
    last_error: str | None
    started_at / completed_at: datetime | None
    step_report: StepReport | None

  StepReport (typed, subagent 返回的结构化结果):
    verdict: Literal["pass", "fail", "needs_fix", "skipped"]
    summary: str
    files_changed: list[str]
    notes: str | None
    artifacts: dict[str, Any]  # 任意附加结构化数据

  StepKind:
    read       (grep / read / glob)
    code       (write / edit)
    search     (web_search)
    verify     (跑测试 / 检查)
    deliver    (commit / 报告)
    plan       (子 plan 拆解)

═══════════════════════════════════════════════
2. 流程
═══════════════════════════════════════════════

  用户:  pure-agent plan "给项目加 X 功能"
       │
       ▼
  GoalManager.create(text)
       │  持久化 goal
       ▼
  PlanAgent.decompose(goal, project_context)
       │  LLM 调一次, 拿到 JSON
       │  validate via Plan pydantic
       │  retry 3x if invalid
       ▼
  PlanManager.save(plan)  → SQLite
       │
       ▼
  Runner.execute_plan(plan)
       │  for step in topological_order:
       │    1. check deps 都 done → 否则 mark blocked
       │    2. step.status = in_progress
       │    3. build system_prompt: "You are working on step s2 of plan p1, goal g1"
       │    4. inject context: plan summary + done step reports
       │    5. AIAgentLoop.run(step.action)
       │    6. parse loop result → StepReport
       │    7. step.status = done / failed
       │    8. emit events
       ▼
  Plan status = done / failed

═══════════════════════════════════════════════
3. 关键模块
═══════════════════════════════════════════════

3.1 plan/models.py
  - pydantic models: Goal / GoalConstraints / Plan / PlanStep / StepKind /
    StepReport
  - 状态机合法转换检查
  - DAG 校验:  deps 必须存在, 不能有环

3.2 plan/storage.py
  - SQLite CRUD: goals / plans / plan_steps 表
  - 用 Phase 0 schema（已预留表）
  - transaction 安全

3.3 plan/manager.py
  - GoalManager: create / list / get / abandon
  - PlanManager: create / get / save / version / edit
  - StateMachine: 合法状态转换
  - DAG 校验

3.4 plan/agent.py
  - PlanAgent: 用 LLM 拆 plan
  - 提示词: "Given goal X, decompose into Plan { steps: [...] }"
  - 强制 JSON 输出, 失败重试 3x
  - 返回: validated Plan

3.5 plan/runner.py
  - PlanRunner: 执行 plan
  - 拓扑排序
  - 调度 step → AIAgentLoop
  - 失败处理: 重试 N 次 / 升级到用户
  - 中断: abort_event + checkpoint
  - 恢复: 读 SQLite, 跳过 done step

3.6 plan/cli.py
  - pure-agent plan <goal>
  - pure-agent plan resume
  - pure-agent plan list
  - pure-agent plan show <plan_id>
  - pure-agent plan edit <plan_id>
  - pure-agent plan run <plan_id>

═══════════════════════════════════════════════
4. PlanAgent 详细设计
═══════════════════════════════════════════════

  PlanAgent.decompose(goal: Goal, project_context: str) -> Plan:
    """
    1. 构造 system_prompt: "You are a planning agent. Given a user goal,
       produce a structured Plan with steps. Each step has kind/action/deps.
       Return ONLY valid JSON matching the Plan schema."
    2. 构造 user_prompt: goal.text + project_context
    3. LLM 调用（不传 tool_calls，避免它调 tool）
    4. 解析 LLM 输出为 JSON
    5. 用 pydantic 校验
    6. 失败重试 3x，每次把错误反馈给 LLM
    7. 返回 Plan

    project_context 包括:
      - 项目 root 路径
      - 已存在的关键文件列表（read_file 风格的 dir listing）
      - 已有 memory 的 top-K 事实（Phase 6 实现）
    """

  JSON 提取: 用 ```json ... ``` 围栏或 [START_JSON] 标记
  容错: 修复 common JSON 错误（用 jsonrepair 库）

═══════════════════════════════════════════════
5. PlanRunner 详细设计
═══════════════════════════════════════════════

  class PlanRunner:
      def __init__(self, db, llm_provider, tool_registry, ...):
          ...

      async def execute(
          self,
          plan_id: str,
          *,
          on_step_event: Callable | None = None,
          abort_signal: asyncio.Event | None = None,
      ) -> PlanRunResult:
          """
          1. 加载 plan from DB
          2. 拓扑排序
          3. for step in topo_order:
              a. if step.status == done: skip
              b. check deps 都 done；否则 mark blocked, skip
              c. if abort_signal: save partial state, return
              d. step.status = in_progress
              e. build context: prior step reports + plan summary
              f. loop = AIAgentLoop(provider, tools, system_prompt=...)
              g. result = await loop.run(step.action, abort_signal=...)
              h. step.step_report = parse_report(result)
              i. step.status = done | failed
              j. emit events
          4. plan.status = done (if all steps done) | failed
          """

      async def resume(self, plan_id: str) -> PlanRunResult:
          """读取持久化状态，跳过 done step，继续。"""

      def get_state(self, plan_id: str) -> PlanRunState:
          """返回当前 plan 状态（用于 GUI / CLI 展示）。"""

═══════════════════════════════════════════════
6. 状态机
═══════════════════════════════════════════════

  Goal:
    pending → planning → running → done
                            → failed
                            → abandoned
    * → abandoned 任何时候

  Plan:
    pending → in_progress → done
                         → failed
                         → abandoned

  PlanStep:
    pending → in_progress → done
                         → failed → (retry: in_progress) → done
                                 → (max attempts) → failed (终态)
    pending → blocked (deps not met)
    * → skipped (manual edit)

  PlanRunner 负责状态转换合法性检查

═══════════════════════════════════════════════
7. Step → AIAgentLoop 集成
═══════════════════════════════════════════════

  每个 step 启动一个 AIAgentLoop 实例。System prompt 注入:
    "You are working on step s{idx} of plan {plan_id}, goal {goal_id}.
     Goal: {goal.text}
     Plan summary: {plan summary}
     Completed steps:
       s1: PASS — {summary}
       s2: PASS — {summary}
     Your task: {step.action}
     When done, return a StepReport with verdict (pass/fail/needs_fix),
     summary, files_changed, and notes."

  Output parsing: LLM 最终回复 → 解析成 StepReport（pydantic）
  容错: 解析失败 → 标记 needs_fix 让 LLM 修正

═══════════════════════════════════════════════
8. 中断与恢复
═══════════════════════════════════════════════

  中断: Ctrl-C → asyncio.run 抛 KeyboardInterrupt
       → PlanRunner catch → 标记 in_progress step 为 interrupted
       → 持久化所有状态
       → 用户跑 pure-agent plan resume

  恢复: PlanRunner.resume(plan_id)
       → 读 SQLite
       → 跳过 status=done 的 step
       → 重启 in_progress step（重置 attempts 计数 +1）
       → 继续

  进程崩溃: SQLite WAL 模式保证持久化数据不丢
            启动时检查 in_progress step → 给用户恢复选项

═══════════════════════════════════════════════
9. 文件结构
═══════════════════════════════════════════════

  src/pure_agent/plan/
  ├── __init__.py
  ├── models.py            # pydantic 数据模型 + 状态机
  ├── storage.py           # SQLite CRUD
  ├── manager.py           # GoalManager / PlanManager
  ├── agent.py             # PlanAgent (LLM 拆 plan)
  ├── runner.py            # PlanRunner
  ├── parser.py            # LLM JSON 输出解析
  └── cli.py               # CLI 子命令

  src/pure_agent/cli/plan.py  # 入口

  tests/
  ├── test_plan_models.py       # 12 tests
  ├── test_plan_storage.py      # 8 tests
  ├── test_plan_manager.py      # 8 tests
  ├── test_plan_agent.py        # 6 tests (含 1 E2E with mock LLM)
  ├── test_plan_runner.py       # 10 tests
  └── test_plan_cli.py          # 6 tests

═══════════════════════════════════════════════
10. 验收清单（M2）
═══════════════════════════════════════════════

  10.1 单元
    □ Goal / Plan / PlanStep / StepReport pydantic 校验
    □ DAG 校验: 环检测, 缺失 dep 检测
    □ 状态机合法转换检查
    □ SQLite CRUD (insert/get/list/update)
    □ PlanAgent 解析 LLM 输出 (含修复 JSON)
    □ PlanRunner 拓扑排序
    □ Step → AIAgentLoop 集成 (mock LLM)

  10.2 集成 (真实 LLM)
    □ "给项目加 X 功能" 跑通 5 步 plan
    □ Ctrl-C 后 resume 完整恢复
    □ Plan 可中途编辑 (加 step / 删 step / 改 action)
    □ 失败 step 重试 N 次 escalate

  10.3 工程
    □ pytest 50+ tests
    □ CLI 命令全部跑通
    □ 端到端真实任务 < 5 分钟
    □ 文档完整

═══════════════════════════════════════════════
11. 风险
═══════════════════════════════════════════════

  风险 1: LLM 拆 plan 不稳定
    缓解: 3x 重试 + 错误反馈 + pydantic 强校验

  风险 2: 真实长程任务超过 token 预算
    缓解: 跨 step 不重复传完整 history，只传 step_report 摘要
          (Phase 3 流量优化做更激进的事)

  风险 3: 中断恢复丢失 step in-progress 状态
    缓解: SQLite transaction + WAL, 每次 step.status 变化都 flush

  风险 4: 用户编辑 plan 时产生环
    缓解: Plan edit 后重新做 DAG 校验, 有环就拒绝

═══════════════════════════════════════════════
12. 里程碑
═══════════════════════════════════════════════

  M2: 上述验收清单全部通过
  时间: ~1.5 周单人
  关键 demo: "用 Plan 改一处代码" 5 步 plan 全跑通
