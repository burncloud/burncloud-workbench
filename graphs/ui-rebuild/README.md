# BurnCloud Graph Engineering Harness v1

BurnCloud Buyer / Supplier / Admin Console 的可执行 LangGraph 软件交付 Harness。

核心原则：**Agent 负责判断，Python/Graph 负责权限、状态、预算、验证、恢复和发布。人只处理真正的例外。**

## 固定产品边界

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

Buyer / Supplier / Admin 是独立 Workspace Role；一个普通账号可以同时拥有 Buyer + Supplier。

## Stabilization：Task 不再等于 Run

旧模型：

```text
一次 Run
→ 必须完成页面
→ 失败/预算耗尽
→ 人重新开 Thread
→ Scout/Planner 重跑
```

现在：

```text
Task = 一个持续存在的工程任务 + 一个 Agent branch

Task
├ Run 1（最多 5M Token）
├ compact checkpoint
├ Run 2（从安全阶段继续）
├ compact checkpoint
└ Run N
   ↓
PASS
↓
Draft PR
```

单 Run 用完只代表这一棒结束，不代表 Task 失败。

默认治理：

```text
page_limit                         1
plan rounds                       2
fix rounds                        3
write files / Agent               8
plan files                         8
restore files / Fixer             128
page wall-clock budget            2400s
run wall-clock budget             7200s
page token budget                 5,000,000
run token budget                  5,000,000
task token budget                 15,000,000
continuation runs                 4
Agent invocations / page          12

Scout       model/tool calls      90 / 240
Planner     model/tool calls      60 / 150
Builder     model/tool calls      120 / 300
Reviewer    model/tool calls      60 / 150
Fixer       model/tool calls      90 / 240
```

Task 达到 15M Token 或 continuation 上限后才升级为人工例外。

## Task Store + Context Compaction

write 模式会把当前 Agent branch 的紧凑任务状态保存在：

```text
graphs/ui-rebuild/.runtime/tasks/<agent-branch>.json
```

`.runtime/` 已被 Git 忽略。

保存的是恢复所需事实，不保存完整 Agent 对话：

```text
current page
safe node
Scout facts
Implementation Plan
allowed_files
plan/fix round
verification/review findings
page diff/checkpoint
completed pages
Task cumulative tokens
continuation count
```

长文本会截断压缩；新的 Run 不会把旧工具输出和完整会话重新塞回模型。

每个正常 Graph/Page 节点成功结束后都会原子更新 Task snapshot。节点抛异常时不会覆盖最后一个安全 snapshot。

## Safe Resume

新的 Thread/Run 在 Agent branch 上启动后先读取 Task Store。

```text
只有 Scout 已完成
→ 从 Planner 继续

Plan 已完成但 Builder 未完成
→ 从 Plan Guard → Builder 继续

Builder/后续阶段已完成过
→ 从 Scope Guard → deterministic format → compile/test → Reviewer 继续
```

因此 continuation 不再默认从 Scout 重跑。

启动阶段在 `恢复任务状态` 之前禁止写 Task Store，防止新 Run 的空 State 覆盖旧 snapshot。

## 页面工程子图

```text
页面恢复入口
├ fresh    → 页面上下文 → Scout
├ plan     → Planner
├ build    → Plan Guard → Builder
└ validate → Scope Guard

Scout
→ Planner
→ Plan Guard
→ Builder
→ Scope Guard
→ 确定性格式化
→ 格式化后 Scope Guard
→ cargo check
→ Reality Anchor
→ Reviewer
```

失败治理：

```text
Scope Guard: UNPLANNED_FILES
→ Replan（有额度时）

pre-existing dirty / 普通代码错误
→ Fixer

Reviewer 引用 Plan 外 client 文件
→ Replan

Fixer BLOCKED 且还有 Plan 额度
→ Replan

Run budget exhausted
→ compact snapshot
→ continuation
```

普通 rustfmt 排版由 Python 确定性节点处理，不浪费 Fixer Token；只有 rustfmt 因真实语法问题失败才交给 Fixer。

## Deterministic validation / Reality Anchor

页面范围：

```text
rustfmt apply（Plan 批准的 dirty .rs）
Scope Guard again
rustfmt --check（同一页面范围）
```

集成范围仍然是 crate/workspace 级：

```text
cargo check -p burncloud-client
cargo test -p burncloud-client
cargo check -p burncloud-client --no-default-features --features liveview
cargo check -p burncloud
```

没有 Browser E2E 时不会伪造 PASS；会明确记录 capability missing。

## Scenario Simulator

真实 LLM Run 不再承担 Graph 路由测试职责。

```bat
burncloud-ui-rebuild scenarios
```

当前至少覆盖 12 个过去真实遇到的失败场景，包括：

```text
Scope plan mismatch
pre-existing dirty
Fixer blocked/exhausted
plan round exhausted
Reviewer requires replan
run budget continuation
task budget escalation
deterministic format routing
validation → Fixer
persisted Plan → Builder resume
clean Autopilot → auto approve
```

这个命令不调用模型，应该在真实 Run 前先 PASS。

## Human-by-exception

Studio 模式仍保留最终 Human Gate，适合开发/观察。

Autopilot 模式：

```text
最终 Gate 无 blocker/major
→ 自动批准
→ push Agent branch
→ 创建/复用 Draft PR
→ Telegram PR URL

存在 blocker/major
→ Telegram
→ Human Gate
```

不会自动 merge PR，也不会直接写 main。

## Autopilot

日常推荐：

```bat
burncloud-ui-rebuild autopilot
```

它会：

```text
检测/继续当前 Agent branch
→ Restore Task snapshot
→ 跑一个 bounded Run
→ 5M Run budget 用完则自动接下一棒
→ clean final gate 自动批准
→ page checkpoint commit
→ push
→ Draft PR
→ Telegram
```

明确开始独立新任务：

```bat
burncloud-ui-rebuild autopilot --new-task
```

查看当前 Task：

```bat
burncloud-ui-rebuild task-status
```

Studio 主要用于可视化调试：

```bat
burncloud-ui-rebuild studio
```

Studio 默认 Input：

```json
{}
```

## Git Branch 生命周期

只使用一个 BurnCloud checkout：

```text
C:\Users\huang\Work\burncloud
├ target/                 # Cargo 增量缓存一直复用
└ current branch
   ├ main
   └ agent/ui-rebuild/...
```

不再创建 Git worktree。

```text
ACTIVE / BLOCKED / ERROR
→ 留在原 Agent branch
→ continuation 继续

PASS
→ page checkpoint commit
→ final policy gate
→ git push Agent branch
→ Draft PR

PR 未 merge
→ 后续 Run 复用同一 PR

main 已包含 Agent branch
→ 新任务才从 main 创建新 Agent branch
```

旧 worktree 一次性迁移：

```bat
burncloud-ui-rebuild migrate-legacy-worktree --confirm
```

## Release

Release Graph 可以：

```text
普通 push Agent branch
创建/复用一个 Draft PR → main
Telegram PR URL
```

不会：

```text
force-push
自动 merge PR
直接写 main
```

成功任务必须在执行模型工作前通过 GitHub 发布预检：

```text
gh exists
git origin is GitHub
gh auth status PASS
```

## Telegram

```text
Graph/Agent Server 真异常        → 🚨
真正 Human Exception             → 🟡
Draft PR 创建/复用完成           → ✅ + PR URL
```

Autopilot 的 clean final gate 不发送假的“需要人工审核”消息。

## Secrets

本地 `.env`：

```env
API_KEY=xxxxxxxx
BASE_URL=http://127.0.0.1:8080/v1
LANGSMITH_API_KEY=xxxxxxxx
TELEGRAM_BOT_TOKEN=xxxxxxxx
TELEGRAM_CHAT_ID=xxxxxxxx
```

真实 secrets 不进入 Python/Markdown/YAML/Test/Prompt/Git。

默认模型：

```text
gpt-5.6-sol
```

## 本地更新与稳定性检查

```bat
cd C:\Users\huang\Work\burncloud-workbench
git pull

cd graphs\ui-rebuild
.venv\Scripts\activate.bat
python -m pip install -e ".[dev]"

pytest
burncloud-ui-rebuild scenarios
burncloud-ui-rebuild telegram-check
```

全部 PASS 后再运行真实工程：

```bat
burncloud-ui-rebuild autopilot
```

需要观察拓扑时才运行：

```bat
burncloud-ui-rebuild studio
```

## 其它 CLI

单个 bounded Run：

```bat
burncloud-ui-rebuild rebuild --write --limit 1
```

Dry run：

```bat
burncloud-ui-rebuild dry-run --limit 1
```

模型 Tool Calling：

```bat
burncloud-ui-rebuild agent-check
```

Git page checkpoints：

```bat
burncloud-ui-rebuild checkpoints
```

恢复 checkpoint：

```bat
burncloud-ui-rebuild recover --commit <SHA> --confirm
```

目标状态不是“Graph 永远不失败”，而是：**Run 可以失败或耗尽，但 Task 能从最后安全状态继续；只有真正无法由系统决定的问题才需要人。**
