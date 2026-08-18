from __future__ import annotations

import os
from pathlib import Path


def workbench_root() -> Path:
    configured = os.environ.get("BURNCLOUD_WORKBENCH_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[4]


def source_root() -> Path:
    configured = os.environ.get("BURNCLOUD_SOURCE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (workbench_root().parent / "burncloud").resolve()
