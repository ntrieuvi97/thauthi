"""Workspace path-containment helpers shared by every recursive scanner/writer.

Recursive scans must never follow symlinks or touch files whose canonical path
escapes the authorized workspace root (symlink-escape / cross-repo write).
"""

import os
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


def check_storage_dir(storage_dir: Path, workspace_root: Optional[Path]) -> Path:
    """Validate an internal storage directory (e.g. `<ws>/.thauthi/crystals`) before creating/using it.

    Fails closed if any existing component between `workspace_root` and `storage_dir` is a symlink,
    or if the resolved directory is not inside `workspace_root` (when one is given).
    Returns the directory path to use.
    """
    storage_dir = Path(storage_dir)
    if workspace_root is not None:
        root = Path(workspace_root).resolve()
        try:
            rel = Path(os.path.abspath(storage_dir)).relative_to(root)
        except ValueError:
            raise ValueError(f"Storage directory {storage_dir} is outside workspace {root}")
        cur = root
        for part in rel.parts:
            cur = cur / part
            if cur.is_symlink():
                raise ValueError(f"Refusing symlinked storage path component: {cur}")
        if cur.exists() and not is_within(cur, root):
            raise ValueError(f"Storage directory resolves outside workspace {root}: {cur}")
    elif storage_dir.is_symlink():
        raise ValueError(f"Refusing symlinked storage directory: {storage_dir}")
    return storage_dir


def write_text_nofollow(path: Path, text: str, mode: int = 0o644, atomic: bool = True) -> None:
    """Write `text` to `path` without following a symlink at `path`.

    Uses a securely created temp file in the same directory + os.replace (atomic), or O_NOFOLLOW|O_EXCL.
    Permissions are applied on the open file descriptor, never via a path-based chmod.
    """
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Refusing to write through symlink: {path}")
    data = text.encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(str(tmp), flags, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fchmod(fh.fileno(), mode)
        if path.is_symlink():
            raise ValueError(f"Refusing to write through symlink: {path}")
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise
