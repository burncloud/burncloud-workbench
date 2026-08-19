# BurnCloud UI Rebuild Graph v0.3

这是 BurnCloud UI 全量重建的可执行 LangGraph Harness。

## v0.3 目标

- 读取 `docs/ui/` 中已批准的 Product / IA / Page Contracts。
- 检查当前 `burncloud/burncloud` 的 Route、Auth、Role 和 Server API 权限边界。
- 硬性保证所有管理 UI 都位于 `/console/*`。
- 把 Buyer、Supplier、Admin 定义为 Workspace Role。
- 普通账号可以同时拥有 `buyer + supplier`，并在两者之间切换。
- 系统记住 `last_workspace`，但记忆永远不能绕过当前权限。
- 自动生成并遍历 25 个目标页面任务，Golden Pages 优先。
- live write 模式使用真实 `create_agent()` Builder / Reviewer / Fixer。
- 每页执行 Builder → deterministic Verifier → independent Reviewer → bounded Fix Loop。
- 第一次 live write 从干净的 `burncloud/main` 创建独立 Agent branch + Git worktree；后续 Run 自动复用同一施工分支/worktree。
- 默认模型是 `gpt-5.6-sol`，Studio/CLI 都可以省略模型名；需要时仍可显式覆盖。
- 最后使用 LangGraph `interrupt()` 等待人工批准。
- Release Agent 当前不会自动 commit、push 或 merge。

## Git 隔离规则

第一次 write Run 自动创建：

```text
C:\Users\huang\Work\
├── burncloud\                         # main，只做基线，不写
├── burncloud-workbench\
└── burncloud-worktrees\
    └── ui-rebuild-<timestamp>-<id>\  # Agent 实际施工目录
```

对应分支：

```text
agent/ui-rebuild/<timestamp>-<id>
```

后续再次启动 LangGraph、重新创建 Studio Thread 或重新提交 Run 时：

```text
扫描已有 agent/ui-rebuild/* worktree
→ 选择最新仍存在的 UI rebuild worktree
→ 复用原 agent_branch
→ 保留原来的未提交 Agent diff
→ 从上次施工现场继续
```

只有当没有任何可复用的 UI rebuild worktree 时，才会创建新的 Agent branch/worktree。

硬规则：

- `burncloud` 主 checkout 必须位于 `main`。
- `burncloud/main` 必须 clean，否则拒绝开始 Agent 写入。
- 新建 Agent worktree 时必须 clean。
- 复用旧 Agent worktree 时允许保留上次未提交的 Agent 修改，并继续开发。
- Builder/Fixer 的写工具会再次检查当前 branch 必须等于当前 `agent_branch`。
- `main` / `master` 上的写操作会被工具层硬拒绝。
- 不自动 stash、不自动 restore main、不自动删除用户已有修改。

## 路径硬规则

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

## Agent 权限模型

```text
Builder
├── read source/workbench
├── search/list
├── exact source text replacement
├── create new source file
├── format one explicitly changed .rs file
├── git diff/status (read-only)
└── allowlisted validation

Reviewer
├── read source/workbench
├── search/list
├── git diff/status
└── allowlisted validation

Fixer
├── same bounded write tools as Builder
└── only fixes structured Reviewer findings

No Agent can:
- write main/master
- access .git internals
- delete source files
- execute arbitrary shell commands
- git commit
- git push
- merge
- publish
```

每个 Builder/Fixer invocation 最多触碰 8 个不同文件。

## 本地运行

基础目录：

```text
C:\Users\huang\Work\
├── burncloud\
└── burncloud-workbench\
```

`burncloud-worktrees\` 会在第一次 live write 时自动创建；后续 Run 默认复用已有 UI rebuild worktree。

### 1. 创建虚拟环境并安装

Windows CMD：

```bat
cd C:\Users\huang\Work\burncloud-workbench\graphs\ui-rebuild
py -3.13 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install -e ".[dev]"
python -m pip install -U "langgraph-cli[inmem]"
```

### 2. 配置本地环境变量

真实密钥只放本机 `.env`，不要提交到 Git。

```bat
copy .env.example .env
```

`.env` 只使用三个参数：

```env
API_KEY=xxxxxxxx
BASE_URL=http://127.0.0.1:8080/v1
LANGSMITH_API_KEY=xxxxxxxx
```

模型名不是 secret，不放 `.env`。默认：

```text
gpt-5.6-sol
```

### 3. 验证 create_agent + Tool Calling

默认模型：

```bat
burncloud-ui-rebuild agent-check
```

临时覆盖模型：

```bat
burncloud-ui-rebuild agent-check --model gpt-5.6-terra
```

### 4. 运行单元测试

```bat
pytest
```

### 5. 启动 LangGraph Studio

```bat
langgraph dev
```

默认执行模式已经是 `write`。限制到第一张 Golden Page 时只需要：

```json
{
  "page_limit": 1
}
```

`bootstrap` 会自动使用 `gpt-5.6-sol`；`prepare_worktree` 会优先复用已有 UI rebuild Agent branch/worktree，没有时才创建新的。

完整主流程：

```text
默认执行模式
→ 初始化
→ 读取规范
→ 代码侦察
→ 权限守卫
→ 创建开发分支（优先复用旧 worktree）
→ 写入预检
→ 架构规划
→ 选择下一页
→ 页面重建
→ 最终权限检查
→ 人工审批
```

### 6. 第一次真实 Agent 开发（CLI）

只需要确认主 checkout 是 clean main：

```bat
cd C:\Users\huang\Work\burncloud
git status
```

然后：

```bat
cd C:\Users\huang\Work\burncloud-workbench\graphs\ui-rebuild
.venv\Scripts\activate.bat
burncloud-ui-rebuild rebuild --limit 1 --write
```

默认使用 `gpt-5.6-sol`。

命令输出和 Human Gate 会显示：

```text
agent_branch
worktree_root
worktree_reused
base_commit
changed_files
validation_results
```

检查 Agent 修改时，应进入输出中的 `worktree_root`，不是 `C:\Users\huang\Work\burncloud`：

```bat
cd <worktree_root>
git status --short
git diff
```

主 checkout 应继续保持：

```bat
cd C:\Users\huang\Work\burncloud
git status --short
```

无输出。

### 7. 保留 dry-run

```bat
burncloud-ui-rebuild dry-run
```

也可以通过环境变量覆盖仓库位置：

```bash
export BURNCLOUD_SOURCE_ROOT=/path/to/burncloud
export BURNCLOUD_WORKBENCH_ROOT=/path/to/burncloud-workbench
```
