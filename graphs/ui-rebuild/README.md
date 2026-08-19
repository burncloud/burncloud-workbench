# BurnCloud UI Rebuild Graph v0.2

这是 BurnCloud UI 全量重建的可执行 LangGraph Harness。

## v0.2 目标

- 读取 `docs/ui/` 中已批准的 Product / IA / Page Contracts。
- 检查当前 `burncloud/burncloud` 的 Route、Auth、Role 和 Server API 权限边界。
- 硬性保证所有管理 UI 都位于 `/console/*`。
- 把 Buyer、Supplier、Admin 定义为 Workspace Role。
- 普通账号可以同时拥有 `buyer + supplier`，并在两者之间切换。
- 系统记住 `last_workspace`，但记忆永远不能绕过当前权限。
- 自动生成并遍历 25 个目标页面任务，Golden Pages 优先。
- live write 模式使用真实 `create_agent()` Builder / Reviewer / Fixer。
- 每页执行 Builder → deterministic Verifier → independent Reviewer → bounded Fix Loop。
- 最后使用 LangGraph `interrupt()` 等待人工批准。
- Release Agent 当前不会自动 commit、push 或 merge。

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
├── git diff/status (read-only)
└── allowlisted validation

Reviewer
├── read source/workbench
├── search/list
├── git diff/status
└── allowlisted validation

Fixer
├── same limited write tools as Builder
└── only fixes structured Reviewer findings

No Agent can:
- access .git internals
- delete source files
- execute arbitrary shell commands
- git commit
- git push
- merge
- publish
```

## 本地运行

建议两个仓库作为同级目录：

```text
C:\Users\huang\Work\
├── burncloud\
└── burncloud-workbench\
```

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

- `API_KEY`：Agent 模型端点使用的 Key。
- `BASE_URL`：OpenAI-compatible 模型端点。
- `LANGSMITH_API_KEY`：LangGraph Studio / LangSmith 使用的 Key。

`model_factory.py` 统一读取这些变量。Agent 的模型名称由调用方传入，不增加第四个环境变量。

### 3. 验证 create_agent + Tool Calling

在允许 Builder 修改 BurnCloud 之前，先验证模型端点真的支持 Tool Calling：

```bat
burncloud-ui-rebuild agent-check --model gpt-5.6-terra
```

成功结果：

```json
{
  "status": "PASS",
  "model": "gpt-5.6-terra",
  "tool_called": true,
  "final_text": "AGENT_READY"
}
```

这个检查不会读取或修改 `burncloud/burncloud`。

### 4. 运行单元测试

```bat
pytest
```

### 5. 启动 LangGraph Studio

```bat
langgraph dev
```

Studio 的 Graph 在进入 `bootstrap` 后会自动解析同级 `burncloud` 和 `burncloud-workbench` 路径。

第一次 live Studio Run 推荐输入：

```json
{
  "execution_mode": "write",
  "model_name": "gpt-5.6-terra",
  "page_limit": 1
}
```

write preflight 会先检查 `burncloud` working tree 必须是 clean；否则在任何 Agent 写文件之前停止。

### 6. 第一次真实 Agent 开发（CLI）

先确认源码没有未提交修改：

```bat
cd C:\Users\huang\Work\burncloud
git status --short
```

输出必须为空。

然后：

```bat
cd C:\Users\huang\Work\burncloud-workbench\graphs\ui-rebuild
.venv\Scripts\activate.bat
burncloud-ui-rebuild rebuild --model gpt-5.6-terra --limit 1 --write
```

`--limit 1` 对应第一张 Golden Page：`Buyer Overview`。

流程：

```text
bootstrap
→ spec_agent
→ repo_scout
→ permission_guardian
→ write_preflight
→ architecture_agent
→ select_next_page
→ Builder(create_agent + limited write tools)
→ Verifier(cargo fmt/client check)
→ Reviewer(create_agent + read-only tools)
→ FAIL: Fixer(create_agent + bounded write tools) → Verify → Review
→ PASS: mark_page_complete
→ final_permission_check
→ Human Gate
```

到 Human Gate 时，源码修改已经留在本地 working tree，但还没有 commit/push/merge。检查：

```bat
cd C:\Users\huang\Work\burncloud
git status --short
git diff
```

只有确认结果后才进入后续 Git Release 阶段。

### 7. 保留 dry-run

完整 25 页流程仍可不调用真实写 Agent：

```bat
burncloud-ui-rebuild dry-run
```

也可以通过环境变量覆盖仓库位置：

```bash
export BURNCLOUD_SOURCE_ROOT=/path/to/burncloud
export BURNCLOUD_WORKBENCH_ROOT=/path/to/burncloud-workbench
```
