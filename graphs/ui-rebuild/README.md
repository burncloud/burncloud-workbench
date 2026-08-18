# BurnCloud UI Rebuild Graph v0.1

这是 BurnCloud UI 全量重建的可执行 LangGraph Harness。

## v0.1 目标

- 读取 `docs/ui/` 中已批准的 Product / IA / Page Contracts。
- 检查当前 `burncloud/burncloud` 的 Route、Auth、Role 和 Server API 权限边界。
- 硬性保证所有管理 UI 都位于 `/console/*`。
- 把 Buyer、Supplier、Admin 定义为 Workspace Role。
- 普通账号可以同时拥有 `buyer + supplier`，并在两者之间切换。
- 系统记住 `last_workspace`，但记忆永远不能绕过当前权限。
- 自动生成并遍历 25 个目标页面任务。
- 每页执行 Builder → Verifier → Reviewer → Fix Loop。
- 最后使用 LangGraph `interrupt()` 等待人工批准。
- v0.1 默认 `dry_run`，不会修改 `burncloud/burncloud`。

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

## 本地运行

建议两个仓库作为同级目录：

```text
workspace/
├── burncloud/
└── burncloud-workbench/
```

### 1. 创建虚拟环境并安装

Windows CMD：

```bat
cd burncloud-workbench\graphs\ui-rebuild
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

### 3. 运行测试和 LangGraph

```bat
pytest
langgraph dev
```

也可以直接运行当前 dry-run：

```bat
burncloud-ui-rebuild dry-run
```

也可以通过环境变量指定仓库位置：

```bash
export BURNCLOUD_SOURCE_ROOT=/path/to/burncloud
export BURNCLOUD_WORKBENCH_ROOT=/path/to/burncloud-workbench
```

v0.1 先验证 Graph、权限不变量、25 页任务队列和 Human Gate。真正的代码写入、commit、push、PR 权限会在下一阶段单独接入，避免一开始就把高权限工具交给 Builder。
