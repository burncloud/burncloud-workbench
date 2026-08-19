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
    assert "format_source_file" not in tools


def test_discovery_tools_return_recoverable_not_found(tmp_path):
    source = tmp_path / "burncloud"
    workbench = tmp_path / "burncloud-workbench"
    source.mkdir()
    workbench.mkdir()

    tools = _tool_map(source, workbench, allow_write=False)

    read_result = tools["read_source_file"].invoke({"path": "missing/file.rs"})
    list_result = tools["list_source_directory"].invoke({"path": "missing-dir"})
    search_result = tools["search_source"].invoke({"query": "billing", "path": "missing-dir"})

    assert "NOT_FOUND: missing/file.rs" in read_result
    assert "recoverable discovery miss" in read_result
    assert "NOT_FOUND: missing-dir" in list_result
    assert "NOT_FOUND: missing-dir" in search_result


def test_discovery_tools_keep_security_errors_hard(tmp_path):
    source = tmp_path / "burncloud"
    workbench = tmp_path / "burncloud-workbench"
    source.mkdir()
    workbench.mkdir()

    tools = _tool_map(source, workbench, allow_write=False)

    with pytest.raises(ToolSafetyError):
        tools["read_source_file"].invoke({"path": "../secret.txt"})


def test_write_tools_are_confined_to_source_root(tmp_path):
    source = tmp_path / "burncloud"
    workbench = tmp_path / "burncloud-workbench"
    source.mkdir()
    workbench.mkdir()
    (source / "src").mkdir()
    target = source / "src" / "app.rs"
    target.write_text("old value\n", encoding="utf-8")

    tools = _tool_map(source, workbench, allow_write=True)
    assert "format_source_file" in tools
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


def test_routine_write_refusals_are_recoverable(tmp_path):
    source = tmp_path / "burncloud"
    workbench = tmp_path / "burncloud-workbench"
    source.mkdir()
    workbench.mkdir()
    existing = source / "existing.rs"
    existing.write_text("keep me", encoding="utf-8")

    tools = _tool_map(source, workbench, allow_write=True)

    create_result = tools["create_source_file"].invoke({
        "path": "existing.rs",
        "content": "overwrite attempt",
    })
    replace_result = tools["replace_source_text"].invoke({
        "path": "existing.rs",
        "old": "does not exist",
        "new": "replacement",
        "expected_occurrences": 1,
    })

    assert "CREATE_REFUSED" in create_result
    assert "REPLACEMENT_REFUSED" in replace_result
    assert existing.read_text(encoding="utf-8") == "keep me"


def test_agent_write_budget_limits_distinct_files(tmp_path):
    source = tmp_path / "burncloud"
    workbench = tmp_path / "burncloud-workbench"
    source.mkdir()
    workbench.mkdir()

    tools = _tool_map(source, workbench, allow_write=True)

    for index in range(8):
        result = tools["create_source_file"].invoke({
            "path": f"src/file_{index}.rs",
            "content": f"// {index}\n",
        })
        assert result.startswith("CREATED")

    refused = tools["create_source_file"].invoke({
        "path": "src/file_8.rs",
        "content": "// ninth\n",
    })

    assert "WRITE_BUDGET_REFUSED" in refused
    assert not (source / "src" / "file_8.rs").exists()
