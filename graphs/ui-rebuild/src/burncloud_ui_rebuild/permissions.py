from __future__ import annotations

from collections.abc import Iterable

from .state import Finding, PageTask, WorkspaceRole


CONSOLE_PREFIX = "/console"
MANAGEMENT_API_PREFIX = "/console/api"
INTERNAL_CONTROL_PREFIX = "/console/internal"
DATA_PLANE_PREFIX = "/v1"

WORKSPACE_PREFIXES: dict[WorkspaceRole, str] = {
    "buyer": "/console/buyer",
    "supplier": "/console/supplier",
    "admin": "/console/admin",
}

WORKSPACE_FALLBACK_ORDER: tuple[WorkspaceRole, ...] = (
    "buyer",
    "supplier",
    "admin",
)


def normalize_roles(roles: Iterable[str]) -> tuple[WorkspaceRole, ...]:
    owned = set(roles)
    return tuple(role for role in WORKSPACE_FALLBACK_ORDER if role in owned)


def available_workspaces(roles: Iterable[str]) -> tuple[WorkspaceRole, ...]:
    return normalize_roles(roles)


def resolve_workspace(
    roles: Iterable[str],
    last_workspace: str | None,
) -> WorkspaceRole | None:
    """Memory is useful, but current authorization always wins."""
    available = available_workspaces(roles)
    if last_workspace in available:
        return last_workspace  # type: ignore[return-value]
    return available[0] if available else None


def required_workspace_for_route(path: str) -> WorkspaceRole | None:
    for role, prefix in WORKSPACE_PREFIXES.items():
        if path == prefix or path.startswith(prefix + "/"):
            return role
    return None


def can_access_workspace_route(roles: Iterable[str], path: str) -> bool:
    required = required_workspace_for_route(path)
    return required is not None and required in available_workspaces(roles)


def validate_page_route(task: PageTask) -> list[Finding]:
    expected_prefix = WORKSPACE_PREFIXES[task["role"]]
    route = task["route"]
    if route == expected_prefix or route.startswith(expected_prefix + "/"):
        return []
    return [
        Finding(
            severity="blocker",
            code="ROLE_ROUTE_BOUNDARY",
            message=f'{task["id"]} is outside the {task["role"]} workspace namespace.',
            evidence=route,
            expected=f"{expected_prefix}/*",
        )
    ]


def validate_target_manifest(tasks: Iterable[PageTask]) -> list[Finding]:
    findings: list[Finding] = []
    seen_ids: set[str] = set()
    seen_routes: set[str] = set()

    for task in tasks:
        if task["id"] in seen_ids:
            findings.append(
                Finding(
                    severity="blocker",
                    code="DUPLICATE_PAGE_ID",
                    message=f'Duplicate page id: {task["id"]}',
                )
            )
        seen_ids.add(task["id"])

        if task["route"] in seen_routes:
            findings.append(
                Finding(
                    severity="blocker",
                    code="DUPLICATE_ROUTE",
                    message=f'Duplicate route: {task["route"]}',
                )
            )
        seen_routes.add(task["route"])
        findings.extend(validate_page_route(task))

    return findings
