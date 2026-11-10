#!/usr/bin/env python3
"""
End-to-End Verification Suite for Thauthi 3.1 (Roadmap Phase 1):
1. Zero-Dependency MCP Server (`thauthi mcp`) over JSONL and LSP Content-Length stdio
2. 100+ Property-Based Fuzzing Prover + Alchemist Duplicate-Preserving Frequency Join
3. Hybrid Neural-Symbolic CEGIS Synthesizer (multi-statement + candidate validation)
4. Policy-Gated Immune System (`mode="shadow-pr"` raising loudly with cryo-test + unified `.diff`)
5. Multi-File Scope-Aware CallGraphHive (import aliases, module calls, class methods, kw-only args)
6. Async & Production I/O Virtualization in Chronos (`async def`, `sqlite3`, `subprocess`, `datetime`, `uuid`, `open`)
7. `thauthi init` MCP config generation without clobbering existing MCP entries
"""

import asyncio
import io
import json
import runpy
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List

import pytest

from thauthi.alchemist import AlgorithmicAlchemist
from thauthi.chronos import ChronosTimeMachine
from thauthi.cli import build_parser, main as cli_main
from thauthi.hive import CallGraphHive
from thauthi.immune import AutopoieticImmuneSystem
from thauthi.mcp_server import ThauthiMCPServer
from thauthi.prover import BehavioralInvariantMiner, DifferentialShadowProver
from thauthi.synthesizer import ContractSynthesizer


def _parse_lsp_frames(raw_bytes: bytes) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    pos = 0
    while pos < len(raw_bytes):
        sep = raw_bytes.find(b"\r\n\r\n", pos)
        assert sep != -1, f"Malformed LSP header at offset {pos}"
        header = raw_bytes[pos:sep].decode("ascii")
        length = 0
        for line in header.splitlines():
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
        body_start = sep + 4
        body_end = body_start + length
        messages.append(json.loads(raw_bytes[body_start:body_end].decode("utf-8")))
        pos = body_end
    return messages


def test_phase1_mcp_server_jsonl_and_lsp_framing_and_tool_dispatch(tmp_path: Path) -> None:
    # Ensure CLI parser accepts `mcp --root`
    parsed = build_parser().parse_args(["mcp", "--root", str(tmp_path)])
    assert parsed.subcommand == "mcp"
    assert parsed.root == tmp_path

    server = ThauthiMCPServer(workspace_dir=tmp_path)

    # 1. Newline-delimited JSON-RPC (initialize, notification, tools/list, tools/call with noisy user stdout)
    jsonl_requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "thauthi_trace",
                "arguments": {
                    "source_code": textwrap.dedent(
                        """\
                        def buggy_ratio(x: int) -> float:
                            print("noisy user stdout")
                            denom = x - 5
                            return 100.0 / denom
                        buggy_ratio(5)
                        """
                    )
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "thauthi_prove",
                "arguments": {
                    "before_source": "def f(nums: list) -> int:\n    return sum(x for x in nums if x > 0)\n",
                    "after_source": "def f(nums: list) -> int:\n    t = 0\n    for n in nums:\n        if n > 0:\n            t += n\n    return t\n",
                },
            },
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {
                "name": "thauthi_synthesize",
                "arguments": {
                    "source_code": textwrap.dedent(
                        """\
                        def clamp_pos(x: int) -> int:
                            \"\"\"
                            >>> clamp_pos(-5)
                            0
                            >>> clamp_pos(7)
                            7
                            \"\"\"
                            pass
                        """
                    ),
                    "candidate_sources": ["max(0, x)"],
                },
            },
        },
    ]

    in_buf = io.BytesIO(("\n".join(json.dumps(r) for r in jsonl_requests) + "\n").encode("utf-8"))
    out_buf = io.BytesIO()
    assert server.serve_streams(in_buf, out_buf) == 0

    lines = [json.loads(line) for line in out_buf.getvalue().decode("utf-8").splitlines() if line.strip()]
    # Notification produces no response -> 5 responses for IDs 1..5
    assert [m["id"] for m in lines] == [1, 2, 3, 4, 5]
    assert lines[0]["result"]["serverInfo"]["name"] == "thauthi"
    tool_names = {t["name"] for t in lines[1]["result"]["tools"]}
    assert {"thauthi_trace", "thauthi_prove", "thauthi_optimize", "thauthi_synthesize", "thauthi_hive", "thauthi_chronos", "thauthi_verify"} <= tool_names

    # Verify user stdout didn't corrupt JSON-RPC wire and was captured cleanly inside tool payload
    trace_payload = json.loads(lines[2]["result"]["content"][0]["text"])
    assert trace_payload["crashed"] is True
    assert trace_payload["_captured_stdout"] == "noisy user stdout"
    assert len(trace_payload["fault_reports"]) >= 1

    prove_payload = json.loads(lines[3]["result"]["content"][0]["text"])
    assert prove_payload["passed"] is True

    synth_payload = json.loads(lines[4]["result"]["content"][0]["text"])
    assert synth_payload["synthesized_count"] == 1
    assert synth_payload["results"][0]["source_kind"] == "hybrid_neural_cegis"

    # 2. Content-Length LSP-framed transport check
    (tmp_path / "clean.py").write_text("def ok() -> int:\n    return 1\n", encoding="utf-8")
    lsp_msgs = [
        {"jsonrpc": "2.0", "id": 10, "method": "ping"},
        {
            "jsonrpc": "2.0",
            "id": 11,
            "method": "tools/call",
            "params": {"name": "thauthi_verify", "arguments": {"workspace_path": str(tmp_path)}},
        },
    ]
    lsp_raw = b""
    for m in lsp_msgs:
        body = json.dumps(m).encode("utf-8")
        lsp_raw += f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

    lsp_out = io.BytesIO()
    assert server.serve_streams(io.BytesIO(lsp_raw), lsp_out) == 0
    decoded_lsp = _parse_lsp_frames(lsp_out.getvalue())
    assert [m["id"] for m in decoded_lsp] == [10, 11]
    verify_payload = json.loads(decoded_lsp[1]["result"]["content"][0]["text"])
    assert verify_payload["passed"] is True


def test_phase1_property_fuzzing_prover_and_alchemist_frequency_map(tmp_path: Path) -> None:
    # Function preserving duplicates from `right_items` (NO `break`!)
    slow_dups = textwrap.dedent(
        """\
        def intersect_with_dups(left_items: list, right_items: list) -> list:
            out = []
            for x in left_items:
                for y in right_items:
                    if x == y:
                        out.append(x)
            return out
        """
    )
    broken_set_version = textwrap.dedent(
        """\
        def intersect_with_dups(left_items: list, right_items: list) -> list:
            _set_r = set(right_items)
            out = []
            for x in left_items:
                if x in _set_r:
                    out.append(x)
            return out
        """
    )

    # 1. Verify 100+ randomized property fuzz inputs catch the duplicate-multiplicity bug!
    rep = DifferentialShadowProver.prove_equivalence(slow_dups, broken_set_version)
    assert rep.passed is False
    assert rep.results[0].verdict == "SEMANTIC_DIVERGENCE"
    assert rep.results[0].old_contract.samples_tested >= 100
    assert len(rep.results[0].counterexamples) >= 1

    # 2. Verify AlgorithmicAlchemist rejects the broken set version and picks `nested_equality_to_frequency_map`!
    alchemist = AlgorithmicAlchemist(storage_dir=tmp_path / ".thauthi")
    opt_src, reports = alchemist.optimize_source(slow_dups)
    assert len(reports) == 1
    assert reports[0].strategy == "nested_equality_to_frequency_map"
    assert reports[0].new_complexity == "O(N)"
    assert reports[0].proven_equivalent is True

    ns: Dict[str, Any] = {}
    exec(opt_src, ns)
    assert ns["intersect_with_dups"]([1, 2], [1, 1, 2, 1]) == [1, 1, 1, 2]


def test_phase1_hybrid_neural_cegis_synthesizer_and_multi_statement_file(tmp_path: Path) -> None:
    specs_file = tmp_path / "hybrid_specs.py"
    specs_file.write_text(
        textwrap.dedent(
            """\
            def running_max(items: list) -> list:
                \"\"\"
                >>> running_max([3, 1, 5, 2])
                [3, 3, 5, 5]
                >>> running_max([-2, -5, -1])
                [-2, -2, -1]
                \"\"\"
                pass
            """
        ),
        encoding="utf-8",
    )

    # First candidate is intentionally wrong (`sorted(items)`), second is a valid multi-statement O(N) def
    candidates = [
        "sorted(items)",
        textwrap.dedent(
            """\
            def running_max(items: list) -> list:
                if not items:
                    return []
                out = []
                cur = items[0]
                for val in items:
                    if val > cur:
                        cur = val
                    out.append(cur)
                return out
            """
        ),
    ]

    new_src, results = ContractSynthesizer.synthesize_file(
        specs_file,
        apply=True,
        candidate_sources=candidates,
    )
    assert len(results) == 1
    assert results[0].source_kind == "hybrid_neural_cegis"
    assert results[0].is_pure is True
    assert results[0].contract["samples_tested"] >= 100

    ns = runpy.run_path(str(specs_file))
    assert ns["running_max"]([1, 4, 2, 9, 3]) == [1, 4, 4, 9, 9]
    assert ns["running_max"]([]) == []


def test_phase1_immune_shadow_pr_mode_raises_loudly_with_diff_and_cryo_test(tmp_path: Path) -> None:
    immune_sys = AutopoieticImmuneSystem(storage_dir=tmp_path / ".thauthi")

    @immune_sys.protect(auto_heal=True, persist_test=True, apply_patch=False, mode="shadow-pr")
    def charge_split(amount: float, split: int) -> float:
        return amount / split

    # Healthy frame succeeds
    assert charge_split(100.0, 2) == 50.0

    # Crash frame MUST raise loudly in shadow-pr mode (never swallow or return 0.0!)
    with pytest.raises(ZeroDivisionError):
        charge_split(100.0, 0)

    assert len(immune_sys.surgeries) == 1
    surgery = immune_sys.surgeries[0]
    assert surgery.mode == "shadow-pr"
    assert surgery.source_file_patched is False
    assert surgery.regression_test_path is not None
    assert surgery.patch_diff_path is not None

    diff_file = Path(surgery.patch_diff_path)
    assert diff_file.exists()
    diff_text = diff_file.read_text(encoding="utf-8")
    assert "--- a/" in diff_text and "+++ b/" in diff_text

    reg_file = Path(surgery.regression_test_path)
    assert reg_file.exists()
    proc = subprocess.run([sys.executable, "-m", "pytest", str(reg_file), "-q"], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0

    # Verify CLI `thauthi heal --mode shadow-pr` exits 1 while emitting surgery + diff JSON
    strict_script = tmp_path / "strict_ledger.py"
    strict_script.write_text(
        textwrap.dedent(
            """\
            def settle(gross: float, count: int) -> float:
                return gross / count

            def main():
                settle(100.0, 4)
                settle(100.0, 0)
            """
        ),
        encoding="utf-8",
    )
    rc = cli_main(["heal", str(strict_script), "--mode", "shadow-pr", "--json"])
    assert rc == 1
    # Disk file must NOT be silently modified in shadow-pr mode
    assert "if count != 0" not in strict_script.read_text(encoding="utf-8")


def test_phase1_hive_multi_file_imports_aliases_classes_and_kwonly(tmp_path: Path) -> None:
    repo = tmp_path / "poly_service_repo"
    repo.mkdir(parents=True)

    # Module A & Module B both define `compute_fee` with DIFFERENT signatures!
    (repo / "modern_billing.py").write_text(
        textwrap.dedent(
            """\
            def compute_fee(base: float, tax_rate: float, tier: str) -> float:
                return round(base * (1.0 + tax_rate), 2)
            """
        ),
        encoding="utf-8",
    )
    (repo / "legacy_billing.py").write_text(
        textwrap.dedent(
            """\
            def compute_fee(amount: float) -> float:
                return float(amount)

            class InvoiceService:
                def issue(self, amount: float, account_id: int, *, currency: str) -> dict:
                    return {"amount": amount, "account_id": account_id, "currency": currency}
            """
        ),
        encoding="utf-8",
    )

    # Caller uses: import alias (`calc_fee`), module alias (`lb.compute_fee`), and class method (`svc.issue`)
    (repo / "checkout.py").write_text(
        textwrap.dedent(
            """\
            import legacy_billing as lb
            from legacy_billing import InvoiceService
            from modern_billing import compute_fee as calc_fee

            def run_all():
                f1 = calc_fee(base=100.0, old_rate=0.2)
                f2 = lb.compute_fee(50.0, "deprecated_arg")
                svc = InvoiceService()
                inv = svc.issue(amount=25.0, user_id=9)
                return f1, f2, inv
            """
        ),
        encoding="utf-8",
    )

    hive = CallGraphHive(workspace_dir=repo)
    report = hive.heal_workspace(apply=True)
    assert report.drifts_detected == 3
    assert "checkout.py" in report.files_healed

    healed = (repo / "checkout.py").read_text(encoding="utf-8")
    assert "tax_rate=0.2" in healed
    assert "tier=''" in healed
    assert "deprecated_arg" not in healed
    assert "account_id=9" in healed
    assert "currency=''" in healed

    # Execute healed module to guarantee zero TypeErrors at runtime
    orig_path = list(sys.path)
    sys.path.insert(0, str(repo))
    try:
        ns = runpy.run_path(str(repo / "checkout.py"))
        f1, f2, inv = ns["run_all"]()
        assert f1 == 120.0
        assert f2 == 50.0
        assert inv == {"amount": 25.0, "account_id": 9, "currency": ""}
    finally:
        sys.path[:] = orig_path


def test_phase1_chronos_async_sqlite_subprocess_datetime_uuid_open_virtualization(tmp_path: Path) -> None:
    db_path = tmp_path / "orders.db"
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({"multiplier": 2}), encoding="utf-8")

    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, price INTEGER)")
    conn.execute("INSERT INTO items (id, price) VALUES (1, 50), (2, 0)")
    conn.commit()
    conn.close()

    ext_counter = {"calls": 0}

    async def fetch_bonus(order_id: int) -> int:
        ext_counter["calls"] += 1
        return 10 if order_id == 1 else 0

    from datetime import datetime
    import uuid

    async def process_order_async(db_file: str, cfg_file: str, order_id: int) -> Dict[str, Any]:
        with open(cfg_file, "r", encoding="utf-8") as fh:
            cfg = json.loads(fh.read())
        with sqlite3.connect(db_file) as c:
            row = c.execute("SELECT price FROM items WHERE id = ?", (order_id,)).fetchone()
        price = row[0] if row else 0
        proc = subprocess.run([sys.executable, "-c", "print('sub_ok')"], capture_output=True, text=True)
        bonus = await fetch_bonus(order_id)
        ts = datetime.now().isoformat()
        uid = str(uuid.uuid4())
        # Intentional division by `price` so order_id=2 crashes and heals via Chronos!
        unit_ratio = 100.0 / price
        return {
            "ratio": unit_ratio * cfg["multiplier"] + bonus,
            "sub": proc.stdout.strip(),
            "ts": ts,
            "uid": uid,
        }

    process_order_async.__globals__["fetch_bonus"] = fetch_bonus
    process_order_async.__globals__["datetime"] = datetime

    chronos = ChronosTimeMachine(storage_dir=tmp_path / ".thauthi" / "cassettes")
    protected = chronos.protect(process_order_async, auto_heal=True)

    async def _run() -> None:
        # 1. Healthy async execution records sqlite3, open, subprocess, datetime, uuid, async global helper
        res1 = await protected(str(db_path), str(cfg_path), 1)
        assert res1["ratio"] == 14.0  # (100/50)*2 + 10
        assert res1["sub"] == "sub_ok"
        assert ext_counter["calls"] == 1

        # 2. Delete DB & config file from disk, then replay cassette #1 hermetically!
        db_path_backup = tmp_path / "orders.db.bak"
        db_path.rename(db_path_backup)
        cfg_path.unlink()

        cas1 = chronos.load_cassettes("process_order_async")[0]
        replayed, err = await chronos.replay_cassette_async(process_order_async, cas1)
        assert err is None
        assert replayed == res1  # Exact identical UUID, ISO timestamp, DB row, file config, subprocess output!
        assert ext_counter["calls"] == 1  # Zero real async helper re-execution!

        # 3. Restore files, trigger crash on order_id=2 (price=0), verify async in-flight healing
        # without making duplicate async helper calls during candidate evaluation!
        db_path_backup.rename(db_path)
        cfg_path.write_text(json.dumps({"multiplier": 2}), encoding="utf-8")
        healed_res = await protected(str(db_path), str(cfg_path), 2)
        assert healed_res["ratio"] == 0.0
        assert ext_counter["calls"] == 2  # Exactly 1 call for crash recording, 0 during replay healing!

    asyncio.run(_run())


def test_phase1_init_scaffolds_mcp_configs_without_clobbering(tmp_path: Path) -> None:
    repo = tmp_path / "mcp_ready_repo"
    (repo / ".git" / "hooks").mkdir(parents=True)
    existing_mcp = {
        "mcpServers": {
            "custom_server": {"command": "node", "args": ["server.js"]},
        }
    }
    (repo / ".mcp.json").write_text(json.dumps(existing_mcp), encoding="utf-8")

    rc = cli_main(["init", str(repo), "--json"])
    assert rc == 0

    root_mcp = json.loads((repo / ".mcp.json").read_text(encoding="utf-8"))
    cursor_mcp = json.loads((repo / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert "custom_server" in root_mcp["mcpServers"]
    assert root_mcp["mcpServers"]["thauthi"] == {"command": "thauthi", "args": ["mcp", "--root", "."]}
    assert cursor_mcp["mcpServers"]["thauthi"] == {"command": "thauthi", "args": ["mcp", "--root", "."]}


def test_alchemist_preserves_future_annotations_module_imports_helpers_and_docstrings(tmp_path: Path) -> None:
    # Sibling helper module imported by target module
    (tmp_path / "math_helpers.py").write_text(
        "SCALE_FACTOR = 2\ndef scale_val(x: int) -> int:\n    return x * SCALE_FACTOR\n",
        encoding="utf-8",
    )

    target_mod = tmp_path / "annotated_service.py"
    target_mod.write_text(
        textwrap.dedent(
            """\
            \"\"\"Module docstring before __future__ import.\"\"\"
            from __future__ import annotations

            import math
            from typing import List, Optional, Sequence
            from math_helpers import scale_val

            OFFSET = 1

            def _local_helper(v: int) -> int:
                return scale_val(v) + int(math.floor(OFFSET))

            class DummyContainer:
                def filter_shared(self, a: List[int], b: List[int]) -> List[int]:
                    # Class method with the same name must NOT be clobbered by top-level replacer
                    return []

            def filter_shared(
                candidates: List[int],
                allowed: Sequence[int],
            ) -> list[int] | None:
                \"\"\"Keep candidates present in allowed and transform via module helpers.\"\"\"
                out: List[int] = []
                for item in candidates:
                    if item in allowed:
                        out.append(_local_helper(item))
                return out

            def count_query_hits(queries: List[int], events: List[int]) -> List[int]:
                \"\"\"Count occurrences of each query in events.\"\"\"
                return [events.count(q) for q in queries]

            def climb_ways(n: int) -> Optional[int]:
                \"\"\"Recursive staircase count with typing annotation.\"\"\"
                if n <= 1:
                    return 1
                return climb_ways(n - 1) + climb_ways(n - 2)
            """
        ),
        encoding="utf-8",
    )

    alchemist = AlgorithmicAlchemist(storage_dir=tmp_path / ".thauthi")
    new_src, reports = alchemist.optimize_file(target_mod, apply=True)
    by_name = {r.func_name: r for r in reports}

    assert "filter_shared" in by_name
    assert by_name["filter_shared"].strategy == "nested_membership_to_hash_set"
    assert by_name["filter_shared"].proven_equivalent is True

    assert "count_query_hits" in by_name
    assert by_name["count_query_hits"].strategy == "loop_count_to_frequency_map"
    assert by_name["count_query_hits"].proven_equivalent is True

    assert "climb_ways" in by_name
    assert by_name["climb_ways"].strategy == "recursive_state_memoization"
    assert by_name["climb_ways"].proven_equivalent is True

    # Execute the optimized file to verify imports, __future__ annotations, docstrings, and class methods remain intact
    orig_sys_path = list(sys.path)
    sys.path.insert(0, str(tmp_path))
    try:
        ns = runpy.run_path(str(target_mod))
        assert ns["filter_shared"]([1, 2, 3, 4], [2, 4]) == [5, 9]
        assert ns["filter_shared"].__doc__ == "Keep candidates present in allowed and transform via module helpers."
        assert ns["count_query_hits"]([1, 2, 9], [2, 1, 2, 2, 5]) == [1, 3, 0]
        assert ns["count_query_hits"].__doc__ == "Count occurrences of each query in events."
        assert ns["climb_ways"](10) == 89
        assert ns["climb_ways"].__doc__ == "Recursive staircase count with typing annotation."
        # Class method with colliding name must remain untouched
        assert ns["DummyContainer"]().filter_shared([1, 2], [1, 2]) == []
    finally:
        sys.path[:] = orig_sys_path


def test_alchemist_directory_scanning_cli_and_mcp(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pkg_dir = tmp_path / "analytics_pkg"
    sub_dir = pkg_dir / "workers"
    venv_dir = pkg_dir / ".venv"
    sub_dir.mkdir(parents=True)
    venv_dir.mkdir(parents=True)

    # Ignored directory file must never be touched
    (venv_dir / "ignored_slow.py").write_text(
        "def ignored_fn(a: list, b: list) -> list:\n    return [x for x in a if x in b]\n",
        encoding="utf-8",
    )

    f1 = pkg_dir / "matcher.py"
    f1.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations
            from typing import List

            def match_ids(left: List[int], right: List[int]) -> List[int]:
                res = []
                for x in left:
                    if x in right:
                        res.append(x)
                return res
            """
        ),
        encoding="utf-8",
    )

    f2 = sub_dir / "scorer.py"
    f2.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations
            from typing import List

            def normalize_above_max(items: List[int], baseline: List[int]) -> List[int]:
                out = []
                for x in items:
                    if x >= max(baseline):
                        out.append(x)
                return out
            """
        ),
        encoding="utf-8",
    )

    # 1. Test CLI `thauthi optimize <dir> --json` (preview mode)
    rc = cli_main(["optimize", str(pkg_dir), "--json"])
    assert rc == 0
    cli_out = json.loads(capsys.readouterr().out)
    assert cli_out["files_scanned"] == 2
    assert cli_out["transmutations_count"] == 2
    assert cli_out["applied"] is False
    assert "_om_set_" not in f1.read_text(encoding="utf-8")

    # 2. Test MCP `thauthi_optimize` on directory with `apply=True`
    server = ThauthiMCPServer(workspace_dir=tmp_path)
    mcp_res = server.call_tool("thauthi_optimize", {"file_path": str(pkg_dir), "apply": True})
    assert mcp_res["isError"] is False
    mcp_payload = json.loads(mcp_res["content"][0]["text"])
    assert mcp_payload["files_scanned"] == 2
    assert mcp_payload["transmutations_count"] == 2
    assert len(mcp_payload["files_optimized"]) == 2
    assert "_om_set_right" in f1.read_text(encoding="utf-8")
    assert "_om_hoist_max_baseline" in f2.read_text(encoding="utf-8")
    assert "_om_set_" not in (venv_dir / "ignored_slow.py").read_text(encoding="utf-8")


def test_alchemist_resolves_relative_and_with_guarded_imports_inside_packages(tmp_path: Path) -> None:
    sub = tmp_path / "om_relpkg" / "sub"
    sub.mkdir(parents=True)
    (tmp_path / "om_relpkg" / "__init__.py").write_text("", encoding="utf-8")
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "om_relpkg" / "utils.py").write_text("def norm(x):\n    return x * 2\n", encoding="utf-8")
    (sub / "consts.py").write_text("K = 1\n", encoding="utf-8")
    target = sub / "matcher.py"
    target.write_text(
        textwrap.dedent(
            """\
            from __future__ import annotations
            import contextlib
            from ..utils import norm
            from . import consts
            with contextlib.suppress(ImportError):
                import json as fastjson

            def match_rel(left: list[int], right: list[int] | None) -> list[int]:
                res = []
                for x in left:
                    if x in right:
                        res.append(norm(x) + consts.K + len(fastjson.dumps(x)))
                return res
            """
        ),
        encoding="utf-8",
    )

    funcs, _ = DifferentialShadowProver._compile_functions(
        target.read_text(encoding="utf-8"), "relpkg", source_path=target
    )
    assert funcs["match_rel"]([1, 2], [2]) == [6]

    new_src, reports = AlgorithmicAlchemist(storage_dir=tmp_path / ".thauthi").optimize_file(target)
    assert [r.func_name for r in reports] == ["match_rel"]
    assert "from ..utils import norm" in new_src and "with contextlib.suppress(ImportError):" in new_src

