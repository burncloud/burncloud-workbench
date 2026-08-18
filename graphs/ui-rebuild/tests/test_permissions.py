from burncloud_ui_rebuild.permissions import (
    available_workspaces,
    can_access_workspace_route,
    resolve_workspace,
)


def test_normal_user_can_be_buyer_and_supplier():
    roles = ["buyer", "supplier"]
    assert available_workspaces(roles) == ("buyer", "supplier")
    assert can_access_workspace_route(roles, "/console/buyer/billing")
    assert can_access_workspace_route(roles, "/console/supplier/earnings")
    assert not can_access_workspace_route(roles, "/console/admin/revenue")


def test_last_workspace_is_remembered_only_while_authorized():
    assert resolve_workspace(["buyer", "supplier"], "supplier") == "supplier"

    # Supplier permission was revoked. Memory must never override authorization.
    assert resolve_workspace(["buyer"], "supplier") == "buyer"


def test_admin_does_not_implicitly_become_supplier():
    assert available_workspaces(["admin"]) == ("admin",)
    assert not can_access_workspace_route(["admin"], "/console/supplier/resources")
