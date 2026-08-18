from __future__ import annotations

from typing import cast

from .state import PageTask, WorkspaceRole


def _task(role: WorkspaceRole, page: str, route: str, phase: str) -> PageTask:
    return PageTask(
        id=f"{role}-{page}",
        role=role,
        page=page,
        route=route,
        contract_path=f"docs/ui/page-contracts/{role}-{page}.md",
        phase=phase,
    )


TARGET_PAGES: list[PageTask] = [
    _task("buyer", "overview", "/console/buyer", "golden"),
    _task("supplier", "overview", "/console/supplier", "golden"),
    _task("admin", "overview", "/console/admin", "golden"),
    _task("buyer", "marketplace", "/console/buyer/marketplace", "golden"),
    _task("supplier", "resources", "/console/supplier/resources", "golden"),
    _task("admin", "capacity", "/console/admin/capacity", "golden"),

    _task("buyer", "playground", "/console/buyer/playground", "buyer"),
    _task("buyer", "api-keys", "/console/buyer/keys", "buyer"),
    _task("buyer", "usage", "/console/buyer/usage", "buyer"),
    _task("buyer", "billing", "/console/buyer/billing", "buyer"),
    _task("buyer", "logs", "/console/buyer/logs", "buyer"),

    _task("supplier", "deployments", "/console/supplier/deployments", "supplier"),
    _task("supplier", "earnings", "/console/supplier/earnings", "supplier"),
    _task("supplier", "settlements", "/console/supplier/settlements", "supplier"),
    _task("supplier", "reliability", "/console/supplier/reliability", "supplier"),
    _task("supplier", "settings", "/console/supplier/settings", "supplier"),

    _task("admin", "supply", "/console/admin/supply", "admin"),
    _task("admin", "demand", "/console/admin/demand", "admin"),
    _task("admin", "models", "/console/admin/models", "admin"),
    _task("admin", "revenue", "/console/admin/revenue", "admin"),
    _task("admin", "settlements", "/console/admin/settlements", "admin"),
    _task("admin", "suppliers", "/console/admin/suppliers", "admin"),
    _task("admin", "customers", "/console/admin/customers", "admin"),
    _task("admin", "operations", "/console/admin/operations", "admin"),
    _task("admin", "settings", "/console/admin/settings", "admin"),
]

assert len(TARGET_PAGES) == 25
