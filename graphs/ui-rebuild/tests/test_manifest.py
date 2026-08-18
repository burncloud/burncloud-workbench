from burncloud_ui_rebuild.manifest import TARGET_PAGES
from burncloud_ui_rebuild.permissions import validate_target_manifest


def test_target_manifest_has_25_unique_pages():
    assert len(TARGET_PAGES) == 25
    assert len({task["id"] for task in TARGET_PAGES}) == 25
    assert len({task["route"] for task in TARGET_PAGES}) == 25


def test_all_management_pages_live_under_console_and_role_namespace():
    assert validate_target_manifest(TARGET_PAGES) == []
    assert all(
        task["route"] == "/console" or task["route"].startswith("/console/")
        for task in TARGET_PAGES
    )
