from pathlib import Path
from types import SimpleNamespace

import pytest

from burncloud_ui_rebuild.coding_tools import ToolSafetyError
from burncloud_ui_rebuild.format_validation import run_page_rustfmt_apply, run_page_rustfmt_check
from burncloud_ui_rebuild import format_validation, quality_nodes


def test_page_rustfmt_apply_only_formats_page_rust_files(tmp_path: Path, monkeypatch):
    root = tmp_path / "burncloud"
    rust = root / "crates/client/src/page.rs"
    css = root / "crates/client/src/page.css"
    rust.parent.mkdir(parents=True)
    rust.write_text("fn page() {}\n", encoding="utf-8")
    css.write_text(".page {}\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(format_validation.subprocess, "run", fake_run)
    result = run_page_rustfmt_apply(
        root,
        ["crates/client/src/page.rs", "crates/client/src/page.css"],
    )

    assert result["returncode"] == 0
    assert result["formatted_files"] == ["crates/client/src/page.rs"]
    assert len(calls) == 1
    assert calls[0][0:3] == ["rustfmt", "--edition", "2021"]
    assert "--check" not in calls[0]
    assert calls[0][-1].endswith("page.rs")


def test_page_rustfmt_check_only_checks_page_rust_files(tmp_path: Path, monkeypatch):
    root = tmp_path / "burncloud"
    rust = root / "crates/client/src/page.rs"
    css = root / "crates/client/src/page.css"
    rust.parent.mkdir(parents=True)
    rust.write_text("fn page() {}\n", encoding="utf-8")
    css.write_text(".page {}\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(format_validation.subprocess, "run", fake_run)
    result = run_page_rustfmt_check(
        root,
        ["crates/client/src/page.rs", "crates/client/src/page.css"],
    )

    assert result["returncode"] == 0
    assert result["checked_files"] == ["crates/client/src/page.rs"]
    assert len(calls) == 1
    assert calls[0][0:4] == ["rustfmt", "--edition", "2021", "--check"]
    assert calls[0][-1].endswith("page.rs")


def test_page_rustfmt_check_rejects_unsafe_paths(tmp_path: Path):
    with pytest.raises(ToolSafetyError, match="Unsafe page format path"):
        run_page_rustfmt_check(tmp_path, ["../outside.rs"])


def test_page_formatter_applies_rustfmt_before_verifier(tmp_path: Path, monkeypatch):
    source = tmp_path / "burncloud"
    source.mkdir()

    monkeypatch.setattr(
        quality_nodes,
        "_changed_files",
        lambda state: [
            "crates/client/src/app.rs",
            "crates/client/src/product_ui.css",
        ],
    )

    calls: list[list[str]] = []

    def fake_apply(root, paths):
        calls.append(list(paths))
        return {
            "command": "page_rustfmt_apply crates/client/src/app.rs",
            "returncode": 0,
            "output": "OK",
            "formatted_files": ["crates/client/src/app.rs"],
        }

    monkeypatch.setattr(quality_nodes, "run_page_rustfmt_apply", fake_apply)
    result = quality_nodes.page_formatter({
        "execution_mode": "write",
        "source_repo_root": str(source),
        "verification_findings": [],
        "validation_results": [],
        "budget_usage": {},
    })

    assert calls == [[
        "crates/client/src/app.rs",
        "crates/client/src/product_ui.css",
    ]]
    assert result["current_page_status"] == "formatted"
    assert result["verification_findings"] == []


def test_page_formatter_turns_rustfmt_parse_failure_into_blocker(tmp_path: Path, monkeypatch):
    source = tmp_path / "burncloud"
    source.mkdir()

    monkeypatch.setattr(quality_nodes, "_changed_files", lambda state: ["crates/client/src/app.rs"])
    monkeypatch.setattr(
        quality_nodes,
        "run_page_rustfmt_apply",
        lambda root, paths: {
            "command": "page_rustfmt_apply crates/client/src/app.rs",
            "returncode": 1,
            "output": "error: mismatched closing delimiter",
            "formatted_files": [],
        },
    )

    result = quality_nodes.page_formatter({
        "execution_mode": "write",
        "source_repo_root": str(source),
        "verification_findings": [],
        "validation_results": [],
        "budget_usage": {},
    })

    assert result["current_page_status"] == "format_failed"
    assert result["verification_findings"][0]["code"] == "VALIDATION_PAGE_RUSTFMT_APPLY"


def test_code_verifier_uses_scoped_format_check_not_crate_wide_cargo_fmt(tmp_path: Path, monkeypatch):
    source = tmp_path / "burncloud"
    workbench = tmp_path / "burncloud-workbench"
    source.mkdir()
    contract = workbench / "docs/ui/page-contracts/buyer-overview.md"
    contract.parent.mkdir(parents=True)
    contract.write_text("# Buyer Overview\n", encoding="utf-8")

    scoped_calls: list[list[str]] = []
    named_calls: list[str] = []

    monkeypatch.setattr(
        quality_nodes,
        "_changed_files",
        lambda state: [
            "crates/client/src/app.rs",
            "crates/client/src/product_ui.css",
        ],
    )

    def fake_scoped(root, paths):
        scoped_calls.append(list(paths))
        return {
            "command": "page_rustfmt_check crates/client/src/app.rs",
            "returncode": 0,
            "output": "OK",
            "checked_files": ["crates/client/src/app.rs"],
        }

    def fake_named(root, name):
        named_calls.append(name)
        return {"command": name, "returncode": 0, "output": "OK"}

    monkeypatch.setattr(quality_nodes, "run_page_rustfmt_check", fake_scoped)
    monkeypatch.setattr(quality_nodes, "run_named_validation", fake_named)

    state = {
        "execution_mode": "write",
        "source_repo_root": str(source),
        "workbench_root": str(workbench),
        "current_page": {
            "id": "buyer-overview",
            "role": "buyer",
            "page": "overview",
            "route": "/console/buyer",
            "contract_path": "docs/ui/page-contracts/buyer-overview.md",
            "phase": "golden",
        },
        "verification_findings": [],
        "budget_usage": {},
    }

    result = quality_nodes.code_verifier(state)

    assert scoped_calls == [[
        "crates/client/src/app.rs",
        "crates/client/src/product_ui.css",
    ]]
    assert "cargo_fmt_check" not in named_calls
    assert named_calls == ["client_check"]
    assert result["current_page_status"] == "code_verified"
