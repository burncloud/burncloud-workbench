from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable

from langchain.tools import tool

from .policy import DEFAULT_POLICY


IGNORED_DIRS = {
    ".git", ".venv", "node_modules", "target", "dist", "build", ".next", ".pytest_cache", "__pycache__",
}
TEXT_SUFFIXES = {
    ".rs", ".toml", ".md", ".json", ".yaml", ".yml", ".ts", ".tsx", ".js", ".jsx", ".css", ".scss", ".html", ".py", ".sh",
}
MAX_TOOL_OUTPUT = DEFAULT_POLICY.max_tool_output_chars
MAX_WRITE_FILES_PER_AGENT = DEFAULT_POLICY.max_write_files_per_agent


class ToolSafetyError(RuntimeError):
    pass


def normalize_repo_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _clip(text: str, limit: int = MAX_TOOL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def _safe_path(root: Path, relative: str, *, allow_missing: bool = False) -> Path:
    if not relative or relative.strip() in {".", "./"}:
        candidate = root.resolve()
    else:
        raw = Path(relative.strip())
        if raw.is_absolute():
            raise ToolSafetyError("Absolute paths are not allowed.")
        if any(part in {"..", ".git"} for part in raw.parts):
            raise ToolSafetyError("Parent traversal and .git access are not allowed.")
        candidate = (root / raw).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ToolSafetyError("Path escaped the configured repository root.")
    if not allow_missing and not candidate.exists():
        raise FileNotFoundError(str(candidate))
    return candidate


def _recoverable_path_error(kind: str, relative: str) -> str:
    return (
        f"{kind}: {relative}\n"
        "This is a recoverable discovery miss. Use list_source_directory or search_source to locate the real path, then retry."
    )


def _read_lines(path: Path, start_line: int, end_line: int) -> str:
    if start_line < 1 or end_line < start_line:
        raise ValueError("Require 1 <= start_line <= end_line.")
    if end_line - start_line + 1 > DEFAULT_POLICY.max_read_lines:
        raise ValueError(f"A single read is limited to {DEFAULT_POLICY.max_read_lines} lines.")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[start_line - 1:end_line]
    return _clip("\n".join(f"{idx}: {line}" for idx, line in enumerate(selected, start=start_line)))


def _iter_text_files(root: Path) -> Iterable[Path]:
    seen = 0
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in IGNORED_DIRS]
        for name in files:
            path = Path(current) / name
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            yield path
            seen += 1
            if seen >= 5000:
                return


def _run(root: Path, argv: list[str], *, timeout: int) -> dict[str, object]:
    completed = subprocess.run(
        argv,
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    combined = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    return {"command": " ".join(argv), "returncode": completed.returncode, "output": _clip(combined)}


def git_status(source_root: str | Path) -> str:
    root = Path(source_root).resolve()
    result = _run(root, ["git", "status", "--porcelain"], timeout=30)
    if result["returncode"] != 0:
        raise RuntimeError(str(result["output"]))
    return str(result["output"])


def git_branch(source_root: str | Path) -> str:
    root = Path(source_root).resolve()
    result = _run(root, ["git", "branch", "--show-current"], timeout=30)
    if result["returncode"] != 0:
        raise RuntimeError(str(result["output"]))
    return str(result["output"]).strip()


def head_commit(source_root: str | Path) -> str:
    root = Path(source_root).resolve()
    result = _run(root, ["git", "rev-parse", "HEAD"], timeout=30)
    if result["returncode"] != 0:
        raise RuntimeError(str(result["output"]))
    return str(result["output"]).strip()


def changed_source_files(source_root: str | Path) -> list[str]:
    status = git_status(source_root)
    changed: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.append(normalize_repo_path(path))
    return changed


def run_named_validation(source_root: str | Path, name: str) -> dict[str, object]:
    root = Path(source_root).resolve()
    commands: dict[str, tuple[list[str], int]] = {
        "cargo_fmt_check": (["cargo", "fmt", "-p", "burncloud-client", "--", "--check"], 180),
        "client_check": (["cargo", "check", "-p", "burncloud-client"], 900),
        "client_test": (["cargo", "test", "-p", "burncloud-client"], 900),
        "client_liveview_check": (["cargo", "check", "-p", "burncloud-client", "--no-default-features", "--features", "liveview"], 900),
        "application_integration_check": (["cargo", "check", "-p", "burncloud"], 1200),
    }
    if name not in commands:
        raise ToolSafetyError(f"Validation command is not allowlisted: {name}")
    argv, timeout = commands[name]
    return _run(root, argv, timeout=timeout)


def checkpoint_history(source_root: str | Path) -> list[dict[str, str]]:
    root = Path(source_root).resolve()
    result = _run(root, ["git", "log", "--format=%H%x09%s", "--grep=^agent(ui): checkpoint "], timeout=30)
    if result["returncode"] != 0:
        raise RuntimeError(str(result["output"]))
    items: list[dict[str, str]] = []
    for line in str(result["output"]).splitlines():
        if "\t" not in line:
            continue
        commit, subject = line.split("\t", 1)
        prefix = "agent(ui): checkpoint "
        if subject.startswith(prefix):
            items.append({"commit": commit.strip(), "page_id": subject[len(prefix):].strip()})
    items.reverse()
    return items


def create_page_checkpoint(source_root: str | Path, page_id: str) -> dict[str, object]:
    root = Path(source_root).resolve()
    branch = git_branch(root)
    if branch in {"main", "master"} or not branch.startswith("agent/ui-rebuild/"):
        raise ToolSafetyError(f"Refusing checkpoint on non-Agent branch: {branch!r}")
    before = head_commit(root)
    if not git_status(root):
        return {"status": "no_changes", "page_id": page_id, "branch": branch, "commit": before}
    add_result = _run(root, ["git", "add", "-A", "--", "."], timeout=60)
    if add_result["returncode"] != 0:
        raise RuntimeError(str(add_result["output"]))
    commit_result = _run(
        root,
        ["git", "-c", "user.name=BurnCloud UI Rebuild", "-c", "user.email=agent@burncloud.local", "commit", "-m", f"agent(ui): checkpoint {page_id}"],
        timeout=120,
    )
    if commit_result["returncode"] != 0:
        raise RuntimeError(str(commit_result["output"]))
    after = head_commit(root)
    return {"status": "committed", "page_id": page_id, "branch": branch, "previous_commit": before, "commit": after}


def restore_page_checkpoint(source_root: str | Path, target_commit: str) -> dict[str, object]:
    root = Path(source_root).resolve()
    branch = git_branch(root)
    if branch in {"main", "master"} or not branch.startswith("agent/ui-rebuild/"):
        raise ToolSafetyError(f"Refusing recovery on non-Agent branch: {branch!r}")
    history = checkpoint_history(root)
    known = {item["commit"]: item["page_id"] for item in history}
    if target_commit not in known:
        raise ToolSafetyError("Recovery target is not a known BurnCloud page checkpoint commit.")
    ancestor = _run(root, ["git", "merge-base", "--is-ancestor", target_commit, "HEAD"], timeout=30)
    if ancestor["returncode"] != 0:
        raise ToolSafetyError("Recovery target is not an ancestor of current Agent HEAD.")
    untracked = [line for line in git_status(root).splitlines() if line.startswith("??")]
    reset = _run(root, ["git", "reset", "--hard", target_commit], timeout=60)
    if reset["returncode"] != 0:
        raise RuntimeError(str(reset["output"]))
    return {"status": "restored", "branch": branch, "commit": target_commit, "page_id": known[target_commit], "untracked_preserved": untracked}


def build_coding_tools(
    *,
    source_root: str | Path,
    workbench_root: str | Path,
    allow_write: bool,
    expected_branch: str | None = None,
    allowed_write_files: Iterable[str] | None = None,
    allowed_restore_files: Iterable[str] | None = None,
):
    source = Path(source_root).resolve()
    workbench = Path(workbench_root).resolve()
    touched_paths: set[str] = set()
    planned_files = {normalize_repo_path(path) for path in allowed_write_files} if allowed_write_files is not None else None
    restorable_files = {normalize_repo_path(path) for path in allowed_restore_files} if allowed_restore_files is not None else None

    def assert_write_branch() -> None:
        if not allow_write or not expected_branch:
            return
        actual = git_branch(source)
        if actual in {"main", "master"}:
            raise ToolSafetyError(f"Direct writes to protected branch {actual!r} are forbidden.")
        if actual != expected_branch:
            raise ToolSafetyError(f"Agent write branch mismatch: expected {expected_branch!r}, current branch is {actual!r}.")

    def claim_budget(relative: str) -> str | None:
        if relative in touched_paths:
            return None
        if len(touched_paths) >= MAX_WRITE_FILES_PER_AGENT:
            return (
                f"WRITE_BUDGET_REFUSED: this Agent is limited to {MAX_WRITE_FILES_PER_AGENT} distinct files per run. "
                f"Already touched: {sorted(touched_paths)}."
            )
        touched_paths.add(relative)
        return None

    def claim_write(target: Path) -> str | None:
        relative = normalize_repo_path(target.relative_to(source).as_posix())
        if planned_files is not None and relative not in planned_files:
            return f"PLAN_SCOPE_REFUSED: {relative} is not in approved allowed_files={sorted(planned_files)}."
        return claim_budget(relative)

    def claim_restore(target: Path) -> str | None:
        relative = normalize_repo_path(target.relative_to(source).as_posix())
        if restorable_files is not None and relative not in restorable_files:
            return f"RESTORE_SCOPE_REFUSED: {relative} is not in current dirty-file restore scope={sorted(restorable_files)}."
        return claim_budget(relative)

    @tool("read_source_file")
    def read_source_file(path: str, start_line: int = 1, end_line: int = 250) -> str:
        """Read source inside the Agent worktree. Missing paths are recoverable."""
        target = _safe_path(source, path, allow_missing=True)
        if not target.exists():
            return _recoverable_path_error("NOT_FOUND", path)
        if not target.is_file():
            return _recoverable_path_error("NOT_A_FILE", path)
        try:
            return _read_lines(target, start_line, end_line)
        except ValueError as exc:
            return f"INVALID_READ_RANGE: {exc}"

    @tool("read_workbench_file")
    def read_workbench_file(path: str, start_line: int = 1, end_line: int = 250) -> str:
        """Read approved target truth from burncloud-workbench. Always read-only."""
        target = _safe_path(workbench, path, allow_missing=True)
        if not target.exists():
            return _recoverable_path_error("NOT_FOUND", path)
        if not target.is_file():
            return _recoverable_path_error("NOT_A_FILE", path)
        try:
            return _read_lines(target, start_line, end_line)
        except ValueError as exc:
            return f"INVALID_READ_RANGE: {exc}"

    @tool("list_source_directory")
    def list_source_directory(path: str = ".") -> str:
        """List one source directory; Git internals are never exposed."""
        target = _safe_path(source, path, allow_missing=True)
        if not target.exists():
            return _recoverable_path_error("NOT_FOUND", path)
        if not target.is_dir():
            return _recoverable_path_error("NOT_A_DIRECTORY", path)
        items: list[str] = []
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if child.name in IGNORED_DIRS or child.name == ".git":
                continue
            items.append(f"{'dir' if child.is_dir() else 'file'}\t{child.relative_to(source).as_posix()}")
        return _clip("\n".join(items))

    @tool("search_source")
    def search_source(query: str, path: str = ".", max_results: int = 80) -> str:
        """Case-insensitive literal source search returning path, line and text."""
        if not query.strip():
            return "INVALID_ARGUMENT: query must not be empty"
        if max_results < 1 or max_results > 200:
            return "INVALID_ARGUMENT: max_results must be between 1 and 200"
        start = _safe_path(source, path, allow_missing=True)
        if not start.exists():
            return _recoverable_path_error("NOT_FOUND", path)
        search_root = start if start.is_dir() else start.parent
        needle = query.lower()
        matches: list[str] = []
        for file_path in _iter_text_files(search_root):
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for line_number, line in enumerate(lines, start=1):
                if needle in line.lower():
                    matches.append(f"{file_path.relative_to(source).as_posix()}:{line_number}: {line.strip()}")
                    if len(matches) >= max_results:
                        return _clip("\n".join(matches))
        return _clip("\n".join(matches) if matches else "NO_MATCHES")

    @tool("git_diff")
    def git_diff() -> str:
        """Show current uncommitted Agent-worktree diff. Read-only."""
        result = _run(source, ["git", "diff", "--no-ext-diff", "--unified=3", "--", "."], timeout=30)
        if result["returncode"] != 0:
            raise RuntimeError(str(result["output"]))
        return str(result["output"]) or "NO_DIFF"

    @tool("git_worktree_status")
    def git_worktree_status() -> str:
        """Show porcelain Git status for the Agent worktree. Read-only."""
        return git_status(source) or "CLEAN"

    tools = [read_source_file, read_workbench_file, list_source_directory, search_source, git_diff, git_worktree_status]

    if allow_write:
        @tool("replace_source_text")
        def replace_source_text(path: str, old: str, new: str, expected_occurrences: int = 1) -> str:
            """Edit one approved planned file by exact text replacement."""
            assert_write_branch()
            if not old:
                return "INVALID_ARGUMENT: old must not be empty"
            if expected_occurrences < 1 or expected_occurrences > 20:
                return "INVALID_ARGUMENT: expected_occurrences must be between 1 and 20"
            target = _safe_path(source, path, allow_missing=True)
            if not target.exists():
                return _recoverable_path_error("NOT_FOUND", path)
            if not target.is_file():
                return _recoverable_path_error("NOT_A_FILE", path)
            text = target.read_text(encoding="utf-8")
            actual = text.count(old)
            if actual != expected_occurrences:
                return f"REPLACEMENT_REFUSED: expected {expected_occurrences} exact occurrence(s), found {actual}. Re-read and retry."
            refused = claim_write(target)
            if refused:
                return refused
            target.write_text(text.replace(old, new, expected_occurrences), encoding="utf-8")
            return f"UPDATED {target.relative_to(source).as_posix()} ({expected_occurrences} replacement(s))"

        @tool("create_source_file")
        def create_source_file(path: str, content: str) -> str:
            """Create one approved planned UTF-8 file inside the Agent worktree."""
            assert_write_branch()
            target = _safe_path(source, path, allow_missing=True)
            if target.exists():
                return f"CREATE_REFUSED: {path} already exists."
            refused = claim_write(target)
            if refused:
                return refused
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"CREATED {target.relative_to(source).as_posix()}"

        @tool("format_source_file")
        def format_source_file(path: str) -> str:
            """Format exactly one approved planned Rust file; never a crate/workspace."""
            assert_write_branch()
            target = _safe_path(source, path, allow_missing=True)
            if not target.exists():
                return _recoverable_path_error("NOT_FOUND", path)
            if not target.is_file() or target.suffix.lower() != ".rs":
                return "FORMAT_REFUSED: format_source_file only accepts an existing .rs file"
            refused = claim_write(target)
            if refused:
                return refused
            return str(_run(source, ["rustfmt", "--edition", "2021", str(target)], timeout=180))

        @tool("restore_source_file")
        def restore_source_file(path: str) -> str:
            """Discard uncommitted changes in one current dirty tracked file without granting edit scope."""
            assert_write_branch()
            target = _safe_path(source, path, allow_missing=True)
            relative = normalize_repo_path(target.relative_to(source).as_posix())
            tracked = _run(source, ["git", "ls-files", "--error-unmatch", "--", relative], timeout=30)
            if tracked["returncode"] != 0:
                return f"RESTORE_REFUSED: {relative} is not a tracked file"
            refused = claim_restore(target)
            if refused:
                return refused
            result = _run(source, ["git", "restore", "--", relative], timeout=30)
            if result["returncode"] != 0:
                return f"RESTORE_REFUSED: {result['output']}"
            return f"RESTORED {relative} TO HEAD"

        tools.extend([replace_source_text, create_source_file, format_source_file, restore_source_file])

    return tools
