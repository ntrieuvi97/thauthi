"""Regression tests for the ClawScan findings fixed in v4.0.7."""

import os
import ast
from pathlib import Path

from thauthi.crystallizer import NeuralSymbolicCrystallizer


class _NullBridge:
    def add_fact(self, *a, **kw):
        return None


def _cryst(tmp_path: Path) -> NeuralSymbolicCrystallizer:
    return NeuralSymbolicCrystallizer(bridge=_NullBridge(), crystals_dir=tmp_path / "crystals")


def test_generated_crystal_resists_metadata_injection(tmp_path: Path) -> None:
    c = _cryst(tmp_path)
    marker = tmp_path / "pwned.txt"
    evil = f'\"\"\"\nimport os; open({str(marker)!r}, "w").write("x")\n\"\"\"'
    path = c._synthesize_standalone_crystal_cli(
        slug="cr_evil",
        title=evil,
        before_pattern=evil,
        after_pattern='"""\n' + evil,
    )
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Only intended top-level statements: docstring, 5 imports, main(), __main__ guard.
    kinds = [type(n).__name__ for n in tree.body]
    assert kinds.count("Import") + kinds.count("ImportFrom") == 5
    assert "open(" not in "".join(
        ast.unparse(n) for n in tree.body if isinstance(n, ast.Expr) and not isinstance(n.value, ast.Constant)
    )
    compile(src, str(path), "exec")
    assert not marker.exists()


def test_apply_pattern_does_not_follow_symlinks_outside_workspace(tmp_path: Path) -> None:
    c = _cryst(tmp_path)
    crystal = c.crystallize_edit(
        name="sym_timeout",
        before_code="subprocess.run(cmd, check=True)",
        after_code="subprocess.run(cmd, check=True, timeout=30)",
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.py"
    victim_src = "import subprocess\nsubprocess.run(a, check=True)\n"
    victim.write_text(victim_src, encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    inside = ws / "inside.py"
    inside.write_text("import subprocess\nsubprocess.run(b, check=True)\n", encoding="utf-8")
    os.symlink(victim, ws / "link.py")
    os.symlink(outside, ws / "linkdir")
    report = c.apply_pattern_to_path(ws, crystal.before_pattern, crystal.after_pattern)
    assert "timeout=30" in inside.read_text(encoding="utf-8")  # real files still rewritten
    assert victim.read_text(encoding="utf-8") == victim_src  # symlink targets untouched
    assert report["total_replacements"] == 1


def test_chronos_replay_fails_closed_instead_of_running_real_function() -> None:
    from thauthi import chronos
    src = Path(chronos.__file__).read_text(encoding="utf-8")
    assert "return real_fn(*a, **kw)\n                    ev = _next_replay_event" not in src
    assert "return await real_fn(*a, **kw)\n                        ev = _next_replay_event" not in src
    assert "return matching[-1]" not in src
