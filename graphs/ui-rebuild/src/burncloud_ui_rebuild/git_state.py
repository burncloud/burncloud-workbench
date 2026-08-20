from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable

from .coding_tools import ToolSafetyError, git_branch, head_commit, normalize_repo_path


def _git(root: Path, argv: list[str], *, timeout: int = 60) -> str:
    completed = subprocess.run(
        ["git", *argv],
        cwd=root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        output = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        raise RuntimeError(output.strip() or f"git {' '.join(argv)} failed ({completed.returncode})")
    return completed.stdout


def porcelain_status_entries(source_root: str | Path) -> list[tuple[str, str]]:
    """Return (XY, path) from porcelain-v1 without destroying leading status spaces.

    `git status --porcelain=v1 -z` is deliberately parsed directly instead of going
    through the generic clipped/stripped command helper. The leading space in an
    entry such as ` M crates/client/src/app.rs` is data, not formatting.
    """
    root = Path(source_root).resolve()
    raw = _git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"], timeout=30)
    records = raw.split("\0")
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise RuntimeError(f"Unexpected git porcelain-v1 record: {record!r}")
        status = record[:2]
        path = normalize_repo_path(record[3:])
        if not path:
            raise RuntimeError(f"Git porcelain-v1 record has empty path: {record!r}")
        entries.append((status, path))

        # With -z, rename/copy records are emitted as `XY destination\0source\0`.
        # The second path has no XY prefix and must not be treated as another entry.
        if "R" in status or "C" in status:
            if index < len(records) and records[index]:
                index += 1
    return entries


def changed_source_files(source_root: str | Path) -> list[str]:
    return list(dict.fromkeys(path for _, path in porcelain_status_entries(source_root)))


def _fingerprint_path(root: Path, status: str, path: str) -> str:
    target = root / PurePosixPath(path)
    digest = hashlib.sha256()
    digest.update(status.encode("utf-8", errors="replace"))
    digest.update(b"\0")
    if target.is_symlink():
        digest.update(b"SYMLINK\0")
        digest.update(os.readlink(target).encode("utf-8", errors="replace"))
    elif target.exists() and target.is_file():
        digest.update(b"FILE\0")
        digest.update(target.read_bytes())
    elif target.exists():
        digest.update(b"NON_FILE\0")
    else:
        digest.update(b"MISSING\0")
    return digest.hexdigest()


def dirty_fingerprints(source_root: str | Path) -> dict[str, str]:
    root = Path(source_root).resolve()
    return {
        path: _fingerprint_path(root, status, path)
        for status, path in porcelain_status_entries(root)
    }


def page_changed_files_since_baseline(
    source_root: str | Path,
    baseline_fingerprints: dict[str, str] | None,
) -> list[str]:
    """Files whose dirty state/content changed relative to the page-start snapshot.

    This detects new files, further edits to a file that was already dirty when the
    retry started, and restoration/removal of a pre-existing dirty file.
    """
    baseline = dict(baseline_fingerprints or {})
    current = dirty_fingerprints(source_root)
    paths = sorted(set(baseline) | set(current))
    return [path for path in paths if baseline.get(path) != current.get(path)]


def create_scoped_page_checkpoint(
    source_root: str | Path,
    page_id: str,
    paths: Iterable[str],
) -> dict[str, object]:
    """Commit only the page-approved dirty paths, never unrelated carry-over state."""
    root = Path(source_root).resolve()
    branch = git_branch(root)
    if branch in {"main", "master"} or not branch.startswith("agent/ui-rebuild/"):
        raise ToolSafetyError(f"Refusing checkpoint on non-Agent branch: {branch!r}")

    selected: list[str] = []
    for raw_path in paths:
        normalized = normalize_repo_path(str(raw_path))
        candidate = PurePosixPath(normalized)
        if not normalized or candidate.is_absolute() or any(part in {"..", ".git"} for part in candidate.parts):
            raise ToolSafetyError(f"Unsafe scoped checkpoint path: {raw_path!r}")
        if normalized not in selected:
            selected.append(normalized)

    current_dirty = set(changed_source_files(root))
    selected_dirty = [path for path in selected if path in current_dirty]
    unrelated_dirty = sorted(current_dirty - set(selected_dirty))
    if unrelated_dirty:
        raise ToolSafetyError(
            "Refusing page checkpoint while unrelated dirty files remain after Scope Guard: "
            f"{unrelated_dirty}"
        )

    before = head_commit(root)
    if not selected_dirty:
        return {
            "status": "no_changes",
            "page_id": page_id,
            "branch": branch,
            "commit": before,
            "paths": [],
        }

    _git(root, ["add", "-A", "--", *selected_dirty], timeout=60)
    # --only prevents already-staged unrelated paths from leaking into this commit.
    _git(
        root,
        [
            "-c", "user.name=BurnCloud UI Rebuild",
            "-c", "user.email=agent@burncloud.local",
            "commit", "--only", "-m", f"agent(ui): checkpoint {page_id}", "--", *selected_dirty,
        ],
        timeout=120,
    )
    after = head_commit(root)
    return {
        "status": "committed",
        "page_id": page_id,
        "branch": branch,
        "previous_commit": before,
        "commit": after,
        "paths": selected_dirty,
    }
