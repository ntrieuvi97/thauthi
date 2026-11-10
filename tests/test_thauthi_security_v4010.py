"""Regression tests for ClawScan findings fixed in v4.0.10."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from thauthi.alchemist import AlgorithmicAlchemist
from thauthi.crystallizer import NeuralSymbolicCrystallizer
from thauthi.hive import CallGraphHive
from thauthi.prover import DifferentialShadowProver
from thauthi.verifier import ShadowVerifier


class _NullBridge:
    def add_fact(self, *a, **kw):
        return None


SLOW = "def common(a, b):\n    out = []\n    for x in a:\n        for y in b:\n            if x == y:\n                out.append(x)\n    return out\n"


def _symlinked_ws(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.py"
    victim.write_text(SLOW, encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "real.py").write_text(SLOW, encoding="utf-8")
    os.symlink(victim, ws / "link.py")
    os.symlink(outside, ws / "linkdir")
    return ws, victim


def test_alchemist_directory_scan_ignores_symlinks(tmp_path: Path) -> None:
    ws, victim = _symlinked_ws(tmp_path)
    AlgorithmicAlchemist(storage_dir=tmp_path / "st").optimize_directory(ws, apply=True)
    assert victim.read_text(encoding="utf-8") == SLOW
    assert "_om_freq_b" in (ws / "real.py").read_text(encoding="utf-8")


def test_alchemist_refuses_explicit_symlink_write(tmp_path: Path) -> None:
    ws, victim = _symlinked_ws(tmp_path)
    with pytest.raises(ValueError):
        AlgorithmicAlchemist(storage_dir=tmp_path / "st").optimize_file(ws / "link.py", apply=True)
    assert victim.read_text(encoding="utf-8") == SLOW


def test_hive_and_verifier_ignore_symlinks(tmp_path: Path) -> None:
    ws, victim = _symlinked_ws(tmp_path)
    names = [p.name for p in CallGraphHive(workspace_dir=ws)._iter_python_files()]
    assert names == ["real.py"]
    assert [p.name for p in ShadowVerifier(workspace_dir=ws)._collect_python_files()] == ["real.py"]


def test_crystals_are_workspace_scoped(tmp_path: Path) -> None:
    ws_a, ws_b = tmp_path / "a", tmp_path / "b"
    ws_a.mkdir()
    ws_b.mkdir()
    ca = NeuralSymbolicCrystallizer(bridge=_NullBridge(), workspace_dir=ws_a)
    crystal = ca.crystallize_edit(
        name="scoped",
        before_code="subprocess.run(c, check=True)",
        after_code="subprocess.run(c, check=True, timeout=30)",
    )
    assert str(ws_a / ".thauthi" / "crystals") in crystal.tool_path  # stored inside workspace, not ~
    victim = ws_b / "t.py"
    src = "import subprocess\nsubprocess.run(x, check=True)\n"
    victim.write_text(src, encoding="utf-8")
    # A rule created in workspace A cannot rewrite workspace B.
    with pytest.raises(ValueError):
        ca.apply_all_crystals(victim)
    cb = NeuralSymbolicCrystallizer(bridge=_NullBridge(), workspace_dir=ws_b)
    # Workspace B received a registry file copied from A (e.g. committed to a shared repo): its entry
    # carries A's workspace id and must be skipped.
    cb.crystals_dir.mkdir(parents=True)
    cb.registry_file.write_text(ca.registry_file.read_text(encoding="utf-8"), encoding="utf-8")
    rep = cb.apply_all_crystals(ws_b)
    assert rep["crystals_skipped_other_workspace"] == ["cr_scoped"]
    assert victim.read_text(encoding="utf-8") == src


def test_prover_fails_closed_on_empty_or_missing_functions() -> None:
    old = "def add(a, b):\n    return a + b\n"
    gone = "def other(a, b):\n    return a + b\n"
    rep = DifferentialShadowProver.prove_equivalence(old, gone)
    assert rep.passed is False and rep.functions_compared == 0
    assert rep.inconclusive_reason and "add" in rep.inconclusive_reason
    empty = DifferentialShadowProver.prove_equivalence("x = 1\n", "y = 2\n")
    assert empty.passed is False and empty.inconclusive_reason == "no functions were compared"
    ok = DifferentialShadowProver.prove_equivalence(old, "def add(a, b):\n    return b + a\n")
    assert ok.passed is True and ok.functions_compared == 1


def test_heal_reports_module_level_crash(tmp_path: Path) -> None:
    script = tmp_path / "boom.py"
    script.write_text("print(1 / 0)\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "thauthi", "heal", str(script)],
        capture_output=True, text=True, cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path), "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    assert proc.returncode == 1
    assert "crashed at module level" in proc.stderr and "Traceback" not in proc.stderr


def test_precommit_hook_is_review_only() -> None:
    from thauthi.initializer import PRE_COMMIT_SCRIPT
    assert "crystallize --apply . --dry-run" in PRE_COMMIT_SCRIPT
