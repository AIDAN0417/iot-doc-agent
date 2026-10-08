"""Atomic UTF-8 storage for resumable local pipelines."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def write_json(path: Path, value: Any) -> None:
    """Replace JSON atomically so an interrupted write cannot corrupt checkpoints."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def read_json(path: Path) -> Any:
    """Read a complete local JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))
