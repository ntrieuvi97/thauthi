"""Workspace path-containment helpers shared by every recursive scanner/writer.

Recursive scans must never follow symlinks or touch files whose canonical path
escapes the authorized workspace root (symlink-escape / cross-repo write).
"""

from pathlib import Path
from typing import Iterable, List, Optional


def is_within(path: Path, root: Path) -> bool:
    """True if `path` resolves to a location inside `root` (both canonicalized)."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def iter_python_files(root: Path, ignore_dirs: Optional[Iterable[str]] = None) -> List[Path]:
    """Sorted *.py files under `root`, skipping ignored dirs, symlinks and anything resolving outside `root`."""
    ignore = set(ignore_dirs or ())
    root_resolved = root.resolve()
    out: List[Path] = []
    for p in sorted(root_resolved.rglob("*.py")):
        try:
            rel_parts = p.relative_to(root_resolved).parts
        except ValueError:
            continue
        if any(part in ignore for part in rel_parts):
            continue
        if p.is_symlink() or any((root_resolved.joinpath(*rel_parts[: i + 1])).is_symlink() for i in range(len(rel_parts))):
            continue
        if not is_within(p, root_resolved):
            continue
        out.append(p)
    return out
