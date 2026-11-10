"""Comment-preserving source rewrite and write-safety helpers shared by every `--apply` code path."""

import ast
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

_BODY_FIELDS = ("body", "orelse", "finalbody", "handlers")
_MATCH_TYPES: tuple = tuple(t for t in (getattr(ast, "Match", None),) if t is not None)  # py>=3.10 only
_RUN_STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
_LINE_RE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")


def backup_root() -> Path:
    """Directory holding pre-write copies (override with THAUTHI_BACKUP_DIR)."""
    env = os.environ.get("THAUTHI_BACKUP_DIR")
    return Path(env) if env else Path.home() / ".thauthi" / "backups"


def backup_before_write(path: Path) -> Optional[Path]:
    """Copy `path` byte-for-byte into `<backup_root>/<run stamp>/<absolute path>` before it is overwritten.

    Idempotent within one process: the first copy of a file (the true original) wins, so repeated
    edits in the same run never clobber it. Returns the backup path, or None when `path` is absent.
    """
    src = Path(path).resolve()
    if not src.is_file():
        return None
    dest = backup_root() / _RUN_STAMP / Path(*src.parts[1:])
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def _split_lines(text: str) -> List[str]:
    # Matches ast's notion of a line (\n, \r\n, \r only), unlike str.splitlines.
    return _LINE_RE.findall(text)


def _dump(node: ast.AST) -> str:
    return ast.dump(node)


def _body_free_equal(old: ast.AST, new: ast.AST) -> bool:
    for name in old._fields:
        if name in _BODY_FIELDS:
            continue
        a, b = getattr(old, name, None), getattr(new, name, None)
        if isinstance(a, list):
            if not isinstance(b, list) or len(a) != len(b):
                return False
            if any(_dump(x) != _dump(y) if isinstance(x, ast.AST) and isinstance(y, ast.AST) else x != y for x, y in zip(a, b)):
                return False
        elif isinstance(a, ast.AST):
            if not isinstance(b, ast.AST) or _dump(a) != _dump(b):
                return False
        elif a != b:
            return False
    return True


def _splice_fine(old_src: str, old_tree: ast.Module, new_tree: ast.Module) -> Optional[str]:
    """Replace only the innermost differing *statements*, recursing through compound statements.

    Returns None when the trees cannot be matched statement-for-statement on their own lines.
    """
    lines = _split_lines(old_src)
    edits: List[Tuple[int, int, List[str]]] = []

    def first_line(node: ast.AST) -> int:
        decos = getattr(node, "decorator_list", [])
        return min([node.lineno] + [d.lineno for d in decos])

    def alone(node: ast.AST) -> bool:
        fl, el = first_line(node), node.end_lineno
        prefix = lines[fl - 1].encode("utf-8")
        if prefix[: node.col_offset].strip():
            return False
        # only the decorated/plain first line's own indent is checked above; trailing text must be a comment
        tail = lines[el - 1].encode("utf-8")[node.end_col_offset :].decode("utf-8").strip()
        return tail == "" or tail.startswith("#")

    def replace(old: ast.stmt, new: ast.stmt) -> bool:
        if not alone(old):
            return False
        fl = first_line(old)
        indent = lines[fl - 1][: len(lines[fl - 1]) - len(lines[fl - 1].lstrip())]
        text = ast.unparse(new).splitlines()
        eol = "\r\n" if lines[old.end_lineno - 1].endswith("\r\n") else "\n"
        edits.append((fl, old.end_lineno, [indent + t + eol for t in text]))
        return True

    def descend(old: ast.AST, new: ast.AST) -> bool:
        if type(old) is not type(new) or isinstance(old, _MATCH_TYPES):
            return False
        if not any(hasattr(old, f) for f in _BODY_FIELDS) or not _body_free_equal(old, new):
            return False
        for name in _BODY_FIELDS:
            a, b = getattr(old, name, None), getattr(new, name, None)
            if a is None and b is None:
                continue
            if not isinstance(a, list) or not isinstance(b, list):
                return False
            if not walk(a, b, replaceable=name != "handlers"):
                return False
        return True

    def walk(olist: List[ast.AST], nlist: List[ast.AST], replaceable: bool = True) -> bool:
        if len(olist) != len(nlist):
            return False
        for o, n in zip(olist, nlist):
            if _dump(o) == _dump(n):
                continue
            if descend(o, n):
                continue
            if not replaceable or not isinstance(o, ast.stmt) or not replace(o, n):  # type: ignore[arg-type]
                return False
        return True

    if not walk(old_tree.body, new_tree.body):
        return None
    for first, last, repl in sorted(edits, key=lambda e: -e[0]):
        lines[first - 1 : last] = repl
    return "".join(lines)


def _splice_coarse(old_src: str, old: ast.Module, new_tree: ast.Module) -> str:
    share_a_line = any(a.end_lineno >= b.lineno for a, b in zip(old.body, old.body[1:]))
    if len(old.body) != len(new_tree.body) or share_a_line:
        return ast.unparse(new_tree) + "\n"

    lines = old_src.splitlines(keepends=True)
    for old_node, new_node in reversed(list(zip(old.body, new_tree.body))):
        if ast.dump(old_node) != ast.dump(new_node):
            first = min([old_node.lineno] + [d.lineno for d in getattr(old_node, "decorator_list", [])])
            lines[first - 1 : old_node.end_lineno] = [ast.unparse(new_node) + "\n"]
    return "".join(lines)


def splice_source(old_src: str, new_tree: ast.Module) -> str:
    """
    Return `old_src` with only the innermost statements that differ in `new_tree` replaced by their
    unparsed form, so comments and formatting everywhere else survive (even inside the edited function).

    The fine-grained result is verified to parse back to exactly `new_tree`; otherwise this degrades to
    replacing whole top-level statements, and finally to a full `ast.unparse` when statements cannot be
    matched 1:1 by line span. Comments inside a rewritten statement are still lost (ast has no comments).
    """
    old = ast.parse(old_src)
    try:
        fine = _splice_fine(old_src, old, new_tree)
        if fine is not None and ast.dump(ast.parse(fine)) == ast.dump(new_tree):
            return fine
    except Exception:
        pass
    return _splice_coarse(old_src, old, new_tree)
