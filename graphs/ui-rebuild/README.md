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

然后：

```bash
cd burncloud-workbench/graphs/ui-rebuild
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
burncloud-ui-rebuild dry-run
```

也可以通过环境变量指定仓库位置：

```bash
export BURNCLOUD_SOURCE_ROOT=/path/to/burncloud
export BURNCLOUD_WORKBENCH_ROOT=/path/to/burncloud-workbench
```

v0.1 先验证 Graph、权限不变量、25 页任务队列和 Human Gate。真正的代码写入、commit、push、PR 权限会在下一阶段单独接入，避免一开始就把高权限工具交给 Builder。
