import ast
import textwrap
from pathlib import Path

from thauthi.srcedit import backup_before_write, backup_root, splice_source


def _swap(src: str, old: str, new: str) -> str:
    tree = ast.parse(src)

    class T(ast.NodeTransformer):
        def visit_Constant(self, node: ast.Constant):
            return ast.Constant(value=new) if node.value == old else node

    return splice_source(src, ast.fix_missing_locations(T().visit(tree)))


def test_splice_preserves_comments_inside_edited_function() -> None:
    src = textwrap.dedent(
        '''\
        import os


        def f(x):
            """Doc   string."""
            # leading comment
            a   =  1   # keep spacing
            if x:
                b  = "OLD"   # changed line
                c  =  2      # untouched sibling
            return a,   x
        '''
    )
    out = _swap(src, "OLD", "NEW")
    assert out == src.replace('b  = "OLD"   # changed line', "b = 'NEW'")
    ast.parse(out)


def test_splice_handles_try_handlers_and_else() -> None:
    src = textwrap.dedent(
        """\
        def f():
            try:
                x  =  1   # keep
            except ValueError:
                y = "OLD"
            else:
                z  = 3  # keep too
            return 0
        """
    )
    out = _swap(src, "OLD", "NEW")
    assert "x  =  1   # keep" in out and "z  = 3  # keep too" in out
    assert "y = 'NEW'" in out


def test_splice_one_liner_compound_falls_back_safely() -> None:
    src = "def f(x):\n    if x: return 'OLD'\n    return 1  # tail\n"
    out = _swap(src, "OLD", "NEW")
    ast.parse(out)
    assert "'NEW'" in out and "# tail" in out


def test_splice_semicolon_line_still_correct() -> None:
    src = "a = 'OLD'; b = 2\nc = 3  # keep\n"
    out = _swap(src, "OLD", "NEW")
    assert ast.dump(ast.parse(out)) == ast.dump(ast.parse("a = 'NEW'; b = 2\nc = 3\n"))


def test_backup_before_write_copies_original_once_per_run(tmp_path: Path) -> None:
    f = tmp_path / "proj" / "mod.py"
    f.parent.mkdir()
    f.write_bytes(b"x = 1\r\n# caf\xc3\xa9\r\n")
    first = backup_before_write(f)
    assert first is not None and first.read_bytes() == b"x = 1\r\n# caf\xc3\xa9\r\n"
    assert backup_root() in first.parents
    f.write_text("x = 2\n", encoding="utf-8")
    again = backup_before_write(f)
    assert again == first and again.read_bytes() == b"x = 1\r\n# caf\xc3\xa9\r\n"
    assert backup_before_write(tmp_path / "missing.py") is None
