from __future__ import annotations

import subprocess
from pathlib import Path

from burncloud_ui_rebuild.coding_tools import build_coding_tools
from burncloud_ui_rebuild.policy import DEFAULT_POLICY


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def test_restore_cleanup_does_not_consume_eight_file_edit_budget(tmp_path: Path):
    source = tmp_path / "burncloud"
    workbench = tmp_path / "burncloud-workbench"
    source.mkdir()
    workbench.mkdir()

    _git(source, "init", "-b", "main")
    _git(source, "config", "user.email", "agent-test@example.com")
    _git(source, "config", "user.name", "Agent Test")

    restore_files: list[str] = []
    for index in range(12):
        relative = f"src/old_{index}.rs"
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"// base {index}\n", encoding="utf-8")
        restore_files.append(relative)
    _git(source, "add", ".")
    _git(source, "commit", "-m", "base")
    _git(source, "switch", "-c", "agent/ui-rebuild/restore-budget")

    for index, relative in enumerate(restore_files):
        (source / relative).write_text(f"// dirty {index}\n", encoding="utf-8")

    tools = {
        item.name: item
        for item in build_coding_tools(
            source_root=source,
            workbench_root=workbench,
            allow_write=True,
            expected_branch="agent/ui-rebuild/restore-budget",
            allowed_restore_files=restore_files,
        )
    }

    assert DEFAULT_POLICY.max_write_files_per_agent == 8
    assert DEFAULT_POLICY.max_restore_files_per_agent == 128

    for relative in restore_files:
        result = tools["restore_source_file"].invoke({"path": relative})
        assert result.startswith("RESTORED")

    # Restores are cleanup, not creative edits. The independent eight-file edit
    # budget must still be fully available after restoring more than eight files.
    for index in range(8):
        result = tools["create_source_file"].invoke({
            "path": f"src/new_{index}.rs",
            "content": f"// new {index}\n",
        })
        assert result.startswith("CREATED")

    refused = tools["create_source_file"].invoke({
        "path": "src/new_8.rs",
        "content": "// ninth edit\n",
    })
    assert "WRITE_BUDGET_REFUSED" in refused
