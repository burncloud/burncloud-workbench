# BurnCloud Graph Engineering Harness v1

这是 BurnCloud Buyer / Supplier / Admin Console 的可执行 LangGraph 软件交付 Harness。

它的核心不是“多放几个 Agent”，而是把 AI 的判断力放进受控节点，把可靠性、权限、预算、验证、Git 生命周期放进确定性代码和边。

## v1 核心原则

```text
Prompt/Context 负责告诉 Agent 应该理解什么
Agent           负责局部判断
Graph           负责谁先做、失败去哪里
Policy          负责哪些事情绝对不能越界
Reality Anchor  负责现实事实
Git Checkpoint  负责外部副作用恢复
Human Gate      负责最终高风险批准
Notification    负责异常、人工审核和成功完成的外部提醒
```

固定产品边界：

```text
Public UI              /, /home, /login, /register
Management UI          /console/*
Buyer workspace        /console/buyer/*
Supplier workspace     /console/supplier/*
Admin workspace        /console/admin/*
Management API         /console/api/*
Internal control       /console/internal/*
Inference data plane   /v1/*
```

Buyer / Supplier / Admin 是独立 Workspace Role；普通账号可以同时拥有 `buyer + supplier`。

## v1 主图

```text
默认执行模式
→ 初始化
→ 读取规范
→ 仓库侦察
→ 权限守卫
→ 准备开发分支
→ 写入预检
→ 运行上下文
→ 恢复通知（仅需要恢复审批时）
→ 恢复审批
→ 恢复检查
→ 架构规划
→ 选择下一页
→ 页面工程
→ 页面检查点
→ 标记页面完成
→ 最终质量检查
→ 人工审核通知
→ 人工审批
→ 发布状态
→ 完成通知（仅成功完成时）
```

## 页面工程子图

原来的“大 Builder Loop”已经展开为真正的 Graph：

```text
页面上下文                     Python
    ↓
代码侦察                       Scout Agent / read-only
    ↓
修改计划                       Planner Agent / read-only
    ↓
计划守卫                       Python
    ├─ 越界 → 重新规划（最多 2 轮）
    └─ 通过
         ↓
实施修改                       Builder Agent / bounded write
    ↓
范围守卫                       Python
    ├─ 计划外文件 → Fixer
    └─ 通过
         ↓
代码验证                       Python
    ├─ cargo fmt --check
    └─ cargo check -p burncloud-client
         ↓
现实验证                       Python
    ├─ cargo test -p burncloud-client
    ├─ LiveView client compile check
    └─ BurnCloud application integration compile check
         ↓
独立审查                       Reviewer Agent / read-only
    ├─ minor/info → 带警告通过
    └─ major/blocker → Fixer
                         ↓
                       修复 Agent
                         ↓
                       范围守卫
```

真正调用 LLM 的核心岗位只有：

```text
Scout
Planner
Builder
Reviewer
Fixer
```

其余节点均由确定性 Python 控制。

所有普通主图节点和页面子图节点都有错误通知边界：节点真正抛异常时，Harness 会先尝试发送 Telegram 错误通知，再把原异常继续抛给 LangGraph。`interrupt()` 属于正常控制流，不会被误报成错误。

## HarnessPolicy

所有治理规则集中在 `src/burncloud_ui_rebuild/policy.py`。

默认：

```text
page_limit                         1
plan rounds                       2
fix rounds                        3
write files / Agent               8
plan files                         8
page wall-clock budget            2400s
run wall-clock budget             7200s
page token budget                 1000000
run token budget                  1000000
Agent invocations / page          12

Scout       model/tool calls      8 / 20
Planner     model/tool calls      8 / 20
Builder     model/tool calls      18 / 40
Reviewer    model/tool calls      10 / 24
Fixer       model/tool calls      12 / 28

blocking review levels            blocker, major
advisory review levels            minor, info
page writable domain              crates/client/*
```

默认 `{}` 只处理 1 页，因此一页可以使用完整的 100 万 Token Graph 预算。如果以后一次运行多页，100 万是整个 Graph Run 的总预算。

如果上游兼容接口没有返回 token usage metadata，Harness 不会伪造 Token/Cost；Invocation 数、model/tool call 限制和 wall-clock 预算仍然有效。

## Plan 是真正权限，不是建议

Planner 必须提前输出：

```text
allowed_files
steps
backend_gaps
risks
```

Builder/Fixer 的 Tool 层会检查 `allowed_files`：

```text
计划内文件       → 可以修改
计划外文件       → PLAN_SCOPE_REFUSED
../ / .git       → 硬拒绝
非 crates/client → Plan Guard 拒绝，作为 BackendGap/独立任务升级
```

所以 Agent 不能在执行中自己扩大 scope。

## Reality Anchor

v1 把验证从 Agent 私有 Loop 中移出，统一交给 Graph：

### Code facts

```text
cargo fmt -p burncloud-client -- --check
cargo check -p burncloud-client
```

### Integration reality

```text
cargo test -p burncloud-client
cargo check -p burncloud-client --no-default-features --features liveview
cargo check -p burncloud
```

BurnCloud 当前仓库没有可直接被 Harness 调用的浏览器 E2E 套件，因此 Human Gate 会明确显示：

```text
browser_e2e = capability_missing_not_silently_passed
```

不会把“没有 Browser E2E”伪装成 PASS。后续仓库增加 Playwright/WebDriver 等确定性套件后，只需要把它加入 Reality Anchor 白名单。

## State 分层

LangGraph 顶层仍保持兼容的 `UIRebuildState`，但 v1 已明确分出：

```text
RunContext
├ run id
├ base branch / commit
├ agent branch
├ source repo root
├ branch reused
├ model
└ page limit

PageContext
├ page id / role / route / contract
├ baseline commit / dirty files
├ Scout report
├ Implementation Plan
├ allowed files
└ page checkpoint

BudgetUsage
├ Agent invocation count
├ model/tool calls
├ input/output/total tokens
├ page budget counters
└ run budget counters

NotificationHistory
├ event
├ sent / failed / disabled / deduplicated
└ non-secret delivery metadata
```

Agent 节点只接收完成自身职责所需的最小 Context，不把整个历史聊天塞进每一个模型调用。

Studio 使用 `{}` 新建运行时，如果没有显式提供 `thread_id`，Harness 会生成唯一 Run ID，避免不同运行的 Telegram 去重键互相碰撞。

## Git Branch 生命周期：单 checkout，不创建 worktree

Harness 现在只使用一个 BurnCloud checkout：

```text
C:\Users\huang\Work\burncloud
├ target/                         # 始终留在同一个目录，复用 Cargo 增量编译缓存
└ 当前 Git branch
   ├ main                         # 新任务基线
   └ agent/ui-rebuild/...         # Agent 施工分支
```

**新版本不会调用 `git worktree add`。** `target/` 属于被 Git 忽略的构建目录，切换 branch 时不会被删除，因此失败重试、重新启动 LangGraph、以及后续新 Agent branch 都可以继续复用同一个 `target/` 缓存。代码变化、feature/profile/依赖变化仍可能导致 Cargo 对受影响部分重新编译，这是正常增量编译行为。

分支生命周期由 Harness 确定性控制：

```text
main + clean
→ 创建 agent/ui-rebuild/<id>
→ ACTIVE

ACTIVE / BLOCKED / ERROR
→ 不创建新 branch
→ 不回 main
→ 重启 LangGraph / 新 Studio Thread / 503 后重跑
→ 继续当前 agent/ui-rebuild/<id>
→ 保留 dirty diff + target cache

SUCCESS + Human Gate 通过
→ 当前 Agent branch 标记 completed
→ 当前 Run 结束时不切 branch

下一次新 Graph Run
→ 发现当前 branch 已 completed 且 clean
→ git switch main
→ 从当前本地 main 创建新的 agent/ui-rebuild/<new-id>
→ target cache 仍在同一 checkout 中
```

另外支持显式新任务：

```json
{
  "start_new_task": true
}
```

或 CLI：

```bat
burncloud-ui-rebuild rebuild --write --new-task
```

如果当前 Agent branch 还有未提交修改，显式新任务会直接拒绝，防止把失败现场丢掉。要继续修就直接重新运行 Graph；要真正放弃则先由人明确清理/恢复当前 Agent branch。

Builder/Fixer Tool 每次写入都会重新验证：

```text
current branch == expected agent_branch
```

`main/master` 写操作硬拒绝。

### 从旧 worktree 版本迁移一次

如果本机还存在旧的：

```text
C:\Users\huang\Work\burncloud-worktrees\ui-rebuild-...
```

新 Harness 不会偷偷忽略旧失败现场再开 branch，而会要求先迁移一次：

```bat
burncloud-ui-rebuild migrate-legacy-worktree --confirm
```

迁移逻辑：

```text
旧 Agent worktree 有 dirty 修改
→ 临时 stash tracked + untracked
→ remove 旧 linked worktree
→ C:\Users\huang\Work\burncloud 切到原 Agent branch
→ stash pop 恢复原修改
→ 从此所有修复都在主 checkout 的原 branch 上继续
```

旧 worktree 中的 ignored `target/` 不迁移，因为目标就是从此统一使用主 checkout 的 `C:\Users\huang\Work\burncloud\target` 缓存。

## 页面 Git Checkpoint

页面只有在：

```text
Scope PASS
+ Code PASS
+ Reality PASS
+ Reviewer PASS/PASS_WITH_WARNINGS
```

以后才会由确定性 Harness 创建：

```text
agent(ui): checkpoint <page-id>
```

LLM Agent 本身没有 commit / push / merge 权限。

## Recovery

查看当前 Agent branch 已有页面锚点：

```bat
burncloud-ui-rebuild checkpoints
```

恢复到一个已知 checkpoint：

```bat
burncloud-ui-rebuild recover --commit <SHA> --confirm
```

Recovery 规则：

- 只允许当前 `agent/ui-rebuild/*` branch。
- 目标必须是 Harness 产生的 `agent(ui): checkpoint ...` commit。
- 目标必须是当前 HEAD 的 ancestor。
- 使用 `git reset --hard <checkpoint>` 恢复 tracked 文件。
- 不自动删除 untracked 文件。
- main 永远不参与恢复。
- Studio 中需要恢复确认时，会在真正 `interrupt()` 前先发送 Telegram 人工审核提醒。

## Telegram 通知

通知由 Harness 的确定性 Notification Layer 负责，不交给 LLM Agent。

触发规则：

```text
任何普通 Graph/Page Node 抛异常
→ 🚨 图错误通知

进入 Git Recovery 人工确认
→ 🟠 需要人工审核通知

进入最终 Human Gate
→ 🟡 需要人工审核通知

write Run 通过 Human Gate 并达到 approved_agent_branch_no_git_publish
→ ✅ 任务完成通知
```

通知内容会包含节点、页面、状态、Agent branch、Thread/Run ID、首个阻塞原因或最新 checkpoint 等必要信息，但不会主动发送 API Key、Bot Token 等 Secret。

Telegram 投递原则：

- 最多 3 次短重试处理临时网络错误、429、5xx。
- 同一个 Run/Event 在单进程内去重，避免重试节点造成重复轰炸。
- Telegram 最终发送失败只记为 `failed`，不能让 Harness 主任务失败。
- `notification_history` 可在 Studio/CLI 里查看通知状态。
- dry-run 不发送“人工审核/完成”通知；真正 Node 异常仍可通知。

## Secrets

模型连接仍使用原来的三个本地参数；启用 Telegram 再增加两个本地参数：

```env
API_KEY=xxxxxxxx
BASE_URL=http://127.0.0.1:8080/v1
LANGSMITH_API_KEY=xxxxxxxx
TELEGRAM_BOT_TOKEN=xxxxxxxx
TELEGRAM_CHAT_ID=xxxxxxxx
```

`.env` 不提交 Git；`.env.example` 只保留占位符。真实 Key/Token 不得进入 Python、Markdown、YAML、测试、Prompt、日志或 Git commit。

默认模型不是 secret：

```text
gpt-5.6-sol
```

配置好 Telegram 后先测试：

```bat
burncloud-ui-rebuild telegram-check
```

## 本地更新

```bat
cd C:\Users\huang\Work\burncloud-workbench
git pull

cd graphs\ui-rebuild
.venv\Scripts\activate.bat
python -m pip install -e ".[dev]"
pytest
```

## LangGraph Studio

推荐通过 Telegram-aware Supervisor 启动：

```bat
burncloud-ui-rebuild studio
```

它内部运行 `langgraph dev`，并额外覆盖 Graph import / Agent Server 启动阶段的异常通知。

默认 Input：

```json
{}
```

等价于：

```json
{
  "execution_mode": "write",
  "model_name": "gpt-5.6-sol",
  "page_limit": 1,
  "start_new_task": false
}
```

## CLI

继续当前任务（失败/重试默认就是这个）：

```bat
burncloud-ui-rebuild rebuild --write --limit 1
```

明确新任务：

```bat
burncloud-ui-rebuild rebuild --write --limit 1 --new-task
```

Dry run：

```bat
burncloud-ui-rebuild dry-run --limit 1
```

模型 Tool Calling 健康检查：

```bat
burncloud-ui-rebuild agent-check
```

Telegram 健康检查：

```bat
burncloud-ui-rebuild telegram-check
```

## Release 边界

当前 Harness 会：

```text
创建/续用 Agent branch（单 checkout）
复用同一个 Cargo target/
修改当前 Agent branch
确定性验证
本地 page checkpoint commit
Human Gate
Telegram 生命周期通知
```

当前 Harness 不会自动：

```text
push
创建 PR
merge
写 main
```

这些动作仍属于后续 Release Graph，并且必须在 Human Gate 后执行。
