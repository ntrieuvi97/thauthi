"""Regression tests for ClawScan findings fixed in v4.0.11."""

import ast
from pathlib import Path

import pytest

from thauthi.immune import AutopoieticImmuneSystem, RecordedCallFrame


def _frame(msg: str, args=(1,)) -> RecordedCallFrame:
    return RecordedCallFrame(
        args=tuple(args), kwargs={}, result=None,
        exception_type="ValueError", exception_message=msg,
    )


@pytest.mark.parametrize(
    "msg",
    [
        "boom\nimport os; open('{m}', 'w').write('x')",
        "boom\r\nimport os; open('{m}', 'w').write('x')",
        "boom\rimport os; open('{m}', 'w').write('x')",
        "x\n\"\"\"\nimport os; open('{m}', 'w').write('x')\n\"\"\"",
    ],
)
def test_regression_test_generation_resists_exception_message_injection(tmp_path: Path, msg: str) -> None:
    marker = tmp_path / "pwned.txt"
    immune = AutopoieticImmuneSystem(storage_dir=tmp_path / ".thauthi")
    def f(x):
        return x
    path = immune.synthesize_regression_test(
        func=f,
        patched_source="def f(x):\n    return x\n",
        crash_frame=_frame(msg.format(m=marker)),
        healed_result=1,
        healthy_frames=[],
    )
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Only the generated test function may contain statements beyond the patched def + imports.
    for node in tree.body:
        assert isinstance(node, (ast.Expr, ast.ImportFrom, ast.FunctionDef)), ast.dump(node)
    test_fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")][0]
    assert all(isinstance(n, (ast.Assert, ast.Pass)) for n in test_fn.body)
    assert "open(" not in "".join(ast.unparse(n) for n in test_fn.body)
    exec(compile(src, str(path), "exec"), {})  # must run without side effects
    assert not marker.exists()


def test_chronos_replay_warns_not_a_sandbox(tmp_path: Path) -> None:
    from thauthi.chronos import ChronosTimeMachine, ExecutionCassette
    tm = ChronosTimeMachine(storage_dir=tmp_path / "c")
    def g():
        return 1
    cas = ExecutionCassette(cassette_id="c1", func_name="g", module_name="m", args=[], kwargs={}, syscalls=[])
    with pytest.warns(RuntimeWarning, match="NOT a sandbox"):
        tm.replay_cassette(g, cas)


def test_docs_no_longer_claim_zero_side_effects() -> None:
    root = Path(__file__).resolve().parents[1]
    for rel in ("README.md", "skills/thauthi/SKILL.md", "thauthi/initializer.py"):
        text = (root / rel).read_text(encoding="utf-8").lower()
        assert "zero side effects" not in text and "0 external network calls" not in text, rel
