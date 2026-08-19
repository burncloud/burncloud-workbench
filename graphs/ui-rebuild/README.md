# BurnCloud UI Rebuild Graph v0.4

这是 BurnCloud UI 全量重建的可执行 LangGraph Harness。

## v0.4 Graph Engineering 目标

- 读取 `docs/ui/` 中已批准的 Product / IA / Page Contracts。
- 检查当前 `burncloud/burncloud` 的 Route、Auth、Role 和 Server API 权限边界。
- 硬性保证所有管理 UI 都位于 `/console/*`。
- Buyer、Supplier、Admin 是 Workspace Role；普通账号可同时拥有 `buyer + supplier`。
- 默认模型 `gpt-5.6-sol`，默认 `write`，默认一次只处理 1 个页面。
- 第一次 live write 创建独立 Agent branch + Git worktree；后续 Run 复用同一施工分支/worktree。
- Builder / Reviewer / Fixer 是独立 `create_agent()`；确定性代码掌握路由、验证、权限和 Git 生命周期。
- `HarnessPolicy` 统一治理调用预算、Fix Loop、写文件上限、验证标准和 Reviewer 阻塞等级。
- Builder / Reviewer / Fixer 分别限制模型调用和 Tool 调用，避免节点内部 Loop 无限增长。
- 页面质量链拆成：构建 → 代码验证 → 现实验证 → 独立审查 → 有界修复。
- `cargo fmt` + `cargo check` 是代码事实；`cargo test -p burncloud-client` 是独立 Reality Anchor。
- Reviewer 只有 `major/blocker` 才触发 Fixer；`minor/info` 允许带警告通过。
- 页面全部质量门通过后，Harness 在 Agent branch 创建本地 Git checkpoint；不 push、不 merge main。
- 最后通过 LangGraph `interrupt()` 等待人工批准。

## v0.4 核心拓扑

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
    ├─ 构建（LLM Agent）
    ├─ 代码验证（Python: fmt + check）
    ├─ 现实验证（Python: client_test）
    ├─ 审查（独立 LLM Agent）
    ├─ 保存失败上下文
    ├─ 修复（LLM Agent）
    └─ 整理修复结果
→ 页面检查点（本地 Git commit，仅 Agent branch）
→ 标记页面完成
→ 最终权限检查
→ 人工审批
→ 发布状态
```

## HarnessPolicy

默认策略集中在 `src/burncloud_ui_rebuild/policy.py`：

```text
page_limit                1
fix_rounds                3
write files / Agent       8
Builder model calls       18
Builder tool calls        40
Reviewer model calls      10
Reviewer tool calls       24
Fixer model calls         12
Fixer tool calls          28
blocking review levels    blocker, major
code validations          cargo_fmt_check, client_check
reality validations       client_test
```

模型可以在这些边界内自主推理，但不能修改这些边界。

## Git 隔离与恢复

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

后续 Run：

```text
扫描已有 agent/ui-rebuild/* worktree
→ 选择最新仍存在的 UI rebuild worktree
→ 复用原 agent_branch
→ 从上次施工现场继续
```

页面通过所有质量门后：

```text
页面 PASS
→ git add -A（Harness deterministic node）
→ local commit: agent(ui): checkpoint <page-id>
→ 记录 checkpoint SHA
→ 下一页从干净工作区开始
```

注意：LLM Agent 本身仍然没有 `git commit/push/merge` Tool；checkpoint 是 Harness 的确定性生命周期动作。

硬规则：

- `burncloud` 主 checkout 必须位于 `main` 且保持 clean。
- Builder/Fixer 写工具会检查当前 branch 必须等于 `agent_branch`。
- `main` / `master` 上写操作硬拒绝。
- 不自动 stash、不自动修改 main、不自动 push、不自动 merge。

## Agent 工具边界

Builder / Fixer 可用：

```text
read_source_file
read_workbench_file
list_source_directory
search_source
git_diff
git_worktree_status
replace_source_text
create_source_file
format_source_file
restore_source_file
```

Reviewer 只有只读子集。

确定性验证不再作为 Agent Tool 暴露；验证由 Graph 节点执行，减少 Tool Overload 和自我验证偏差。

`restore_source_file` 只能把一个 tracked 文件恢复到当前 Agent branch HEAD，用于 Reviewer 明确指出的 scope/regression 清理；不能访问 `.git`、不能切分支、不能 push。

## 本地运行

### 1. 更新环境

```bat
cd C:\Users\huang\Work\burncloud-workbench
git pull

cd graphs\ui-rebuild
.venv\Scripts\activate.bat
python -m pip install -e ".[dev]"
pytest
```

### 2. `.env`

真实密钥只放本机 `.env`：

```env
API_KEY=xxxxxxxx
BASE_URL=http://127.0.0.1:8080/v1
LANGSMITH_API_KEY=xxxxxxxx
```

### 3. Agent 连通性

```bat
burncloud-ui-rebuild agent-check
```

默认模型：

```text
gpt-5.6-sol
```

### 4. Studio

```bat
langgraph dev
```

Studio Input 默认直接：

```json
{}
```

等价于：

```json
{
  "execution_mode": "write",
  "page_limit": 1,
  "model_name": "gpt-5.6-sol"
}
```

### 5. CLI

```bat
burncloud-ui-rebuild rebuild --limit 1 --write
```

### 6. 查看 Agent 施工现场

```bat
cd C:\Users\huang\Work\burncloud
git worktree list
```

进入 `agent/ui-rebuild/...` 对应的 worktree：

```bat
git status --short
git diff
git log --oneline -10
```

通过页面会留下 `agent(ui): checkpoint <page-id>` 本地提交。

### 7. Dry run

```bat
burncloud-ui-rebuild dry-run
```

需要一次 dry-run 覆盖全部 25 页时，通过 CLI/State 显式设置 `page_limit=25`。
