from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable

from .coding_tools import ToolSafetyError, normalize_repo_path
from .policy import DEFAULT_POLICY


def _safe_rust_paths(source_root: str | Path, paths: Iterable[str]) -> list[tuple[str, Path]]:
    root = Path(source_root).resolve()
    selected: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for raw_path in paths:
        normalized = normalize_repo_path(str(raw_path))
        candidate = PurePosixPath(normalized)
        if not normalized or candidate.is_absolute() or any(part in {"..", ".git"} for part in candidate.parts):
            raise ToolSafetyError(f"Unsafe page format path: {raw_path!r}")
        if not normalized.endswith(".rs") or normalized in seen:
            continue
        target = (root / Path(*candidate.parts)).resolve()
        if root not in target.parents:
            raise ToolSafetyError(f"Page format path escaped source root: {raw_path!r}")
        if not target.is_file():
            raise ToolSafetyError(f"Page format path is not an existing Rust file: {normalized}")
        selected.append((normalized, target))
        seen.add(normalized)
    return selected


def run_page_rustfmt_check(source_root: str | Path, paths: Iterable[str]) -> dict[str, object]:
    """Check formatting only for Rust files owned by the current page scope.

    The BurnCloud Agent branch may contain unrelated clean files that do not satisfy
    a repository-wide cargo fmt baseline. Page engineering must not enter a repair
    loop for files the Planner did not approve. Compile/test validations remain
    crate/workspace-wide; only formatting is scoped to the page-owned Rust diff.
    """
    root = Path(source_root).resolve()
    selected = _safe_rust_paths(root, paths)
    if not selected:
        return {
            "command": "page_rustfmt_check",
            "returncode": 0,
            "output": "NO_PAGE_RUST_FILES",
            "checked_files": [],
        }

    outputs: list[str] = []
    failed = False
    for relative, target in selected:
        completed = subprocess.run(
            ["rustfmt", "--edition", "2021", "--check", str(target)],
            cwd=root,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=180,
            check=False,
        )
        if completed.returncode != 0:
            failed = True
        body = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
        if body:
            outputs.append(f"[{relative}]\n{body}")

    output = "\n\n".join(outputs)
    if len(output) > DEFAULT_POLICY.max_tool_output_chars:
        output = output[: DEFAULT_POLICY.max_tool_output_chars] + "\n... [truncated]"
    return {
        "command": "page_rustfmt_check " + " ".join(relative for relative, _ in selected),
        "returncode": 1 if failed else 0,
        "output": output or "OK",
        "checked_files": [relative for relative, _ in selected],
    }
