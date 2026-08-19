from pathlib import Path

import pytest

from burncloud_ui_rebuild.coding_tools import ToolSafetyError, build_coding_tools


def _tool_map(source: Path, workbench: Path, *, allow_write: bool):
    return {
        item.name: item
        for item in build_coding_tools(
            source_root=source,
            workbench_root=workbench,
            allow_write=allow_write,
        )
    }


def test_read_only_toolset_has_no_source_write_tools(tmp_path):
    source = tmp_path / "burncloud"
    workbench = tmp_path / "burncloud-workbench"
    source.mkdir()
    workbench.mkdir()

    tools = _tool_map(source, workbench, allow_write=False)

    assert "read_source_file" in tools
    assert "read_workbench_file" in tools
    assert "replace_source_text" not in tools
    assert "create_source_file" not in tools


def test_write_tools_are_confined_to_source_root(tmp_path):
    source = tmp_path / "burncloud"
    workbench = tmp_path / "burncloud-workbench"
    source.mkdir()
    workbench.mkdir()
    (source / "src").mkdir()
    target = source / "src" / "app.rs"
    target.write_text("old value\n", encoding="utf-8")

    tools = _tool_map(source, workbench, allow_write=True)
    tools["replace_source_text"].invoke({
        "path": "src/app.rs",
        "old": "old value",
        "new": "new value",
        "expected_occurrences": 1,
    })

    assert target.read_text(encoding="utf-8") == "new value\n"

    with pytest.raises(ToolSafetyError):
        tools["create_source_file"].invoke({
            "path": "../escaped.txt",
            "content": "must not escape",
        })


def test_create_tool_never_overwrites_existing_file(tmp_path):
    source = tmp_path / "burncloud"
    workbench = tmp_path / "burncloud-workbench"
    source.mkdir()
    workbench.mkdir()
    existing = source / "existing.rs"
    existing.write_text("keep me", encoding="utf-8")

    tools = _tool_map(source, workbench, allow_write=True)

    with pytest.raises(ToolSafetyError):
        tools["create_source_file"].invoke({
            "path": "existing.rs",
            "content": "overwrite attempt",
        })

    assert existing.read_text(encoding="utf-8") == "keep me"
