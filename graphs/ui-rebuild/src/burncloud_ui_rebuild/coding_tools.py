from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterable

from langchain.tools import tool


IGNORED_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "target",
    "dist",
    "build",
    ".next",
    ".pytest_cache",
    "__pycache__",
}
TEXT_SUFFIXES = {
    ".rs",
    ".toml",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".css",
    ".scss",
    ".html",
    ".py",
    ".sh",
}
MAX_TOOL_OUTPUT = 40_000


class ToolSafetyError(RuntimeError):
    pass


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
        "This is a recoverable discovery miss. Use list_source_directory or search_source "
        "to locate the real path, then retry."
    )


def _read_lines(path: Path, start_line: int, end_line: int) -> str:
    if start_line < 1 or end_line < start_line:
        raise ValueError("Require 1 <= start_line <= end_line.")
    if end_line - start_line + 1 > 500:
        raise ValueError("A single read is limited to 500 lines.")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[start_line - 1 : end_line]
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
    return {
        "command": " ".join(argv),
        "returncode": completed.returncode,
        "output": _clip(combined),
    }


def git_status(source_root: str | Path) -> str:
    root = Path(source_root).resolve()
    result = _run(root, ["git", "status", "--porcelain"], timeout=30)
    if result["returncode"] != 0:
        raise RuntimeError(str(result["output"]))
    return str(result["output"])


def changed_source_files(source_root: str | Path) -> list[str]:
    status = git_status(source_root)
    changed: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        changed.append(path.replace("\\", "/"))
    return changed


def run_named_validation(source_root: str | Path, name: str) -> dict[str, object]:
    root = Path(source_root).resolve()
    commands: dict[str, tuple[list[str], int]] = {
        "cargo_fmt_check": (["cargo", "fmt", "-p", "burncloud-client", "--", "--check"], 180),
        "client_check": (["cargo", "check", "-p", "burncloud-client"], 900),
        "client_test": (["cargo", "test", "-p", "burncloud-client"], 900),
    }
    if name not in commands:
        raise ToolSafetyError(f"Validation command is not allowlisted: {name}")
    argv, timeout = commands[name]
    return _run(root, argv, timeout=timeout)


def build_coding_tools(
    *,
    source_root: str | Path,
    workbench_root: str | Path,
    allow_write: bool,
):
    source = Path(source_root).resolve()
    workbench = Path(workbench_root).resolve()

    @tool("read_source_file")
    def read_source_file(path: str, start_line: int = 1, end_line: int = 250) -> str:
        """Read a UTF-8 source file inside burncloud/burncloud with line numbers. Max 500 lines per call. Missing paths are recoverable discovery results."""
        target = _safe_path(source, path, allow_missing=True)
        if not target.exists():
            return _recoverable_path_error("NOT_FOUND", path)
        if not target.is_file():
            return _recoverable_path_error("NOT_A_FILE", path)
        return _read_lines(target, start_line, end_line)

    @tool("read_workbench_file")
    def read_workbench_file(path: str, start_line: int = 1, end_line: int = 250) -> str:
        """Read an approved target-truth file inside burncloud-workbench. This tool is always read-only. Missing paths are recoverable discovery results."""
        target = _safe_path(workbench, path, allow_missing=True)
        if not target.exists():
            return _recoverable_path_error("NOT_FOUND", path)
        if not target.is_file():
            return _recoverable_path_error("NOT_A_FILE", path)
        return _read_lines(target, start_line, end_line)

    @tool("list_source_directory")
    def list_source_directory(path: str = ".") -> str:
        """List one directory inside the BurnCloud source repository; hidden Git internals are never exposed. Missing paths are recoverable discovery results."""
        target = _safe_path(source, path, allow_missing=True)
        if not target.exists():
            return _recoverable_path_error("NOT_FOUND", path)
        if not target.is_dir():
            return _recoverable_path_error("NOT_A_DIRECTORY", path)
        items: list[str] = []
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if child.name in IGNORED_DIRS or child.name == ".git":
                continue
            kind = "dir" if child.is_dir() else "file"
            items.append(f"{kind}\t{child.relative_to(source).as_posix()}")
        return _clip("\n".join(items))

    @tool("search_source")
    def search_source(query: str, path: str = ".", max_results: int = 80) -> str:
        """Case-insensitive literal search across text source files. Returns path, line number, and matching line. Missing search roots are recoverable discovery results."""
        if not query.strip():
            raise ValueError("query must not be empty")
        if max_results < 1 or max_results > 200:
            raise ValueError("max_results must be between 1 and 200")
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
                    relative = file_path.relative_to(source).as_posix()
                    matches.append(f"{relative}:{line_number}: {line.strip()}")
                    if len(matches) >= max_results:
                        return _clip("\n".join(matches))
        return _clip("\n".join(matches) if matches else "NO_MATCHES")

    @tool("git_diff")
    def git_diff() -> str:
        """Show the current uncommitted BurnCloud source diff. Never stages, commits, pushes, or modifies Git."""
        result = _run(source, ["git", "diff", "--no-ext-diff", "--unified=3", "--", "."], timeout=30)
        if result["returncode"] != 0:
            raise RuntimeError(str(result["output"]))
        return str(result["output"]) or "NO_DIFF"

    @tool("git_worktree_status")
    def git_worktree_status() -> str:
        """Show porcelain Git status for the BurnCloud source tree without changing Git state."""
        return git_status(source) or "CLEAN"

    @tool("run_validation")
    def run_validation(name: str) -> str:
        """Run one allowlisted validation: cargo_fmt_check, client_check, or client_test. Arbitrary shell commands are forbidden."""
        return str(run_named_validation(source, name))

    tools = [
        read_source_file,
        read_workbench_file,
        list_source_directory,
        search_source,
        git_diff,
        git_worktree_status,
        run_validation,
    ]

    if allow_write:
        @tool("replace_source_text")
        def replace_source_text(path: str, old: str, new: str, expected_occurrences: int = 1) -> str:
            """Safely edit an existing BurnCloud source file by exact text replacement. Use small, targeted replacements only."""
            if not old:
                raise ValueError("old must not be empty")
            if expected_occurrences < 1 or expected_occurrences > 20:
                raise ValueError("expected_occurrences must be between 1 and 20")
            target = _safe_path(source, path)
            if not target.is_file():
                raise ToolSafetyError("replace_source_text requires an existing file.")
            text = target.read_text(encoding="utf-8")
            actual = text.count(old)
            if actual != expected_occurrences:
                raise ToolSafetyError(
                    f"Replacement refused: expected {expected_occurrences} exact occurrence(s), found {actual}."
                )
            target.write_text(text.replace(old, new, expected_occurrences), encoding="utf-8")
            return f"UPDATED {target.relative_to(source).as_posix()} ({expected_occurrences} replacement(s))"

        @tool("create_source_file")
        def create_source_file(path: str, content: str) -> str:
            """Create a new UTF-8 file inside BurnCloud source. Existing files cannot be overwritten with this tool."""
            target = _safe_path(source, path, allow_missing=True)
            if target.exists():
                raise ToolSafetyError("create_source_file refuses to overwrite an existing path.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"CREATED {target.relative_to(source).as_posix()}"

        @tool("format_client")
        def format_client() -> str:
            """Run the fixed safe formatter `cargo fmt -p burncloud-client`. No arbitrary command arguments are accepted."""
            return str(_run(source, ["cargo", "fmt", "-p", "burncloud-client"], timeout=180))

        tools.extend([replace_source_text, create_source_file, format_client])

    return tools
