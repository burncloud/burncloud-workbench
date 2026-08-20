from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from burncloud_ui_rebuild.coding_tools import ToolSafetyError
from burncloud_ui_rebuild.engineering_nodes import scope_guard_node
from burncloud_ui_rebuild.git_state import (
    changed_source_files,
    create_scoped_page_checkpoint,
    dirty_fingerprints,
    page_changed_files_since_baseline,
)
from burncloud_ui_rebuild.quality_nodes import _restore_scope


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )
    return completed.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    src = repo / "crates/client/src"
    src.mkdir(parents=True)
    for index in range(12):
        (src / f"file_{index}.rs").write_text(f"fn file_{index}() {{}}\n", encoding="utf-8")
    (src / "app.rs").write_text("fn app() {}\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")
    _git(repo, "switch", "-c", "agent/ui-rebuild/retry-test")
    return repo


def test_porcelain_parser_preserves_first_path_character(tmp_path: Path):
    repo = _repo(tmp_path)
    app = repo / "crates/client/src/app.rs"
    app.write_text("fn app() { println!(\"dirty\"); }\n", encoding="utf-8")

    assert changed_source_files(repo) == ["crates/client/src/app.rs"]


def test_page_delta_detects_further_edit_to_preexisting_dirty_file(tmp_path: Path):
    repo = _repo(tmp_path)
    app = repo / "crates/client/src/app.rs"
    app.write_text("fn app() { println!(\"old retry\"); }\n", encoding="utf-8")
    baseline = dirty_fingerprints(repo)

    assert page_changed_files_since_baseline(repo, baseline) == []
    app.write_text("fn app() { println!(\"new retry work\"); }\n", encoding="utf-8")
    assert page_changed_files_since_baseline(repo, baseline) == ["crates/client/src/app.rs"]


def test_scope_guard_does_not_count_unchanged_retry_carryover_as_builder_diff(tmp_path: Path):
    repo = _repo(tmp_path)
    dirty_paths: list[str] = []
    for index in range(10):
        path = repo / f"crates/client/src/file_{index}.rs"
        path.write_text(f"fn file_{index}() {{ println!(\"old\"); }}\n", encoding="utf-8")
        dirty_paths.append(f"crates/client/src/file_{index}.rs")

    baseline = dirty_fingerprints(repo)
    # Planner intentionally preserves one carry-over file as relevant to this page.
    allowed = [dirty_paths[0]]
    state = {
        "execution_mode": "write",
        "source_repo_root": str(repo),
        "implementation_plan": {"allowed_files": allowed},
        "page_context": {
            "baseline_dirty_files": dirty_paths,
            "baseline_dirty_fingerprints": baseline,
        },
        "verification_findings": [],
    }

    result = scope_guard_node(state)
    codes = {item["code"] for item in result["verification_findings"]}
    assert "SCOPE_GUARD_PREEXISTING_DIRTY" in codes
    assert "SCOPE_GUARD_FILE_BUDGET" not in codes
    assert "SCOPE_GUARD_UNPLANNED_FILES" not in codes
    assert "SCOPE_GUARD_PROTECTED_DOMAIN" not in codes
    assert result["changed_files"] == allowed
    assert result["page_checkpoint_files"] == allowed

    restore_scope = _restore_scope(state)
    assert restore_scope == dirty_paths[1:]
    assert dirty_paths[0] not in restore_scope


def test_scope_guard_passes_after_unplanned_retry_carryover_is_cleaned(tmp_path: Path):
    repo = _repo(tmp_path)
    keep = "crates/client/src/file_0.rs"
    stale = [f"crates/client/src/file_{index}.rs" for index in range(1, 10)]
    for path in [keep, *stale]:
        target = repo / path
        target.write_text(target.read_text(encoding="utf-8").replace("{}", "{ println!(\"old\"); }"), encoding="utf-8")
    baseline_files = changed_source_files(repo)
    baseline = dirty_fingerprints(repo)

    _git(repo, "restore", "--", *stale)
    state = {
        "execution_mode": "write",
        "source_repo_root": str(repo),
        "implementation_plan": {"allowed_files": [keep]},
        "page_context": {
            "baseline_dirty_files": baseline_files,
            "baseline_dirty_fingerprints": baseline,
        },
        "verification_findings": [],
    }
    result = scope_guard_node(state)
    assert result["verification_findings"] == []
    assert result["current_page_status"] == "scope_passed"
    assert result["changed_files"] == [keep]


def test_scoped_checkpoint_refuses_unrelated_dirty_and_then_commits_only_page_files(tmp_path: Path):
    repo = _repo(tmp_path)
    app = repo / "crates/client/src/app.rs"
    unrelated = repo / "crates/client/src/file_1.rs"
    app.write_text("fn app() { println!(\"page\"); }\n", encoding="utf-8")
    unrelated.write_text("fn file_1() { println!(\"old\"); }\n", encoding="utf-8")

    with pytest.raises(ToolSafetyError, match="unrelated dirty files remain"):
        create_scoped_page_checkpoint(repo, "buyer-overview", ["crates/client/src/app.rs"])

    _git(repo, "restore", "--", "crates/client/src/file_1.rs")
    checkpoint = create_scoped_page_checkpoint(repo, "buyer-overview", ["crates/client/src/app.rs"])
    assert checkpoint["status"] == "committed"
    assert checkpoint["paths"] == ["crates/client/src/app.rs"]
    assert changed_source_files(repo) == []
    assert "agent(ui): checkpoint buyer-overview" in _git(repo, "log", "-1", "--format=%s")
