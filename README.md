# BurnCloud Workbench

`burncloud-workbench` 是 BurnCloud 的产品、UI、Graph/Agent 和架构设计工作台。

这里允许存放尚未进入生产仓库的目标规范、Page Contract、RFC、实验和设计决策。**Workbench 文档不是 `burncloud/burncloud` 当前运行事实。**

## Current Work

### UI / Product

从 [`docs/ui/README.md`](docs/ui/README.md) 开始。

当前已经建立：

- Product & UI Standard
- Information Architecture
- Buyer / Supplier / Admin Page Contracts
- Design System
- Interaction Rules
- Content Standard
- UI Review Checklist
- UI Agent / Graph Execution Protocol

## Truth Labels

文档应明确 truth 类型，例如：

- `truth: target` — 已批准或拟议的目标产品行为，不代表当前已实现
- `truth: source-derived` — 基于当前真实源码/可执行 Gate 验证的文档（更适合生产仓库）

不要因为 Workbench 写了目标，就在实现报告里声称功能已经存在。

## Promotion Rule

当某项设计满足以下条件时，才考虑迁移到 `burncloud/burncloud`：

1. 产品决策已批准；
2. 对应实现进入开发/已经实现；
3. 与当前 source/runtime truth 完成核对；
4. 可执行测试或 Gate 已覆盖关键不变量；
5. 文档 truth/status 标签不会误导 Agent。

## Repository Direction

建议后续目录按主题扩展：

```text
docs/
├── ui/
├── product/
├── architecture/
├── graphs/
├── rfcs/
└── research/
```

确认后的长期真相最终应回到主仓库；Workbench 负责让想法在进入主仓库之前先变得清楚、可审查、可执行。