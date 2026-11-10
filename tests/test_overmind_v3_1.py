#!/usr/bin/env python3
"""
End-to-End Verification Suite for Overmind 3.1 (Roadmap Phase 1):
1. Zero-Dependency MCP Server (`overmind mcp`) over JSONL and LSP Content-Length stdio
2. 100+ Property-Based Fuzzing Prover + Alchemist Duplicate-Preserving Frequency Join
3. Hybrid Neural-Symbolic CEGIS Synthesizer (multi-statement + candidate validation)
4. Policy-Gated Immune System (`mode="shadow-pr"` raising loudly with cryo-test + unified `.diff`)
5. Multi-File Scope-Aware CallGraphHive (import aliases, module calls, class methods, kw-only args)
6. Async & Production I/O Virtualization in Chronos (`async def`, `sqlite3`, `subprocess`, `datetime`, `uuid`, `open`)
7. `overmind init` MCP config generation without clobbering existing MCP entries
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

from overmind.alchemist import AlgorithmicAlchemist
from overmind.chronos import ChronosTimeMachine
from overmind.cli import build_parser, main as cli_main
from overmind.hive import CallGraphHive
from overmind.immune import AutopoieticImmuneSystem
from overmind.mcp_server import OvermindMCPServer
from overmind.prover import BehavioralInvariantMiner, DifferentialShadowProver
from overmind.synthesizer import ContractSynthesizer


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

    server = OvermindMCPServer(workspace_dir=tmp_path)

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
                "name": "overmind_trace",
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
                "name": "overmind_prove",
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
                "name": "overmind_synthesize",
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
    assert lines[0]["result"]["serverInfo"]["name"] == "overmind"
    tool_names = {t["name"] for t in lines[1]["result"]["tools"]}
    assert {"overmind_trace", "overmind_prove", "overmind_optimize", "overmind_synthesize", "overmind_hive", "overmind_chronos", "overmind_verify"} <= tool_names

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
            "params": {"name": "overmind_verify", "arguments": {"workspace_path": str(tmp_path)}},
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
    alchemist = AlgorithmicAlchemist(storage_dir=tmp_path / ".overmind")
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
    immune_sys = AutopoieticImmuneSystem(storage_dir=tmp_path / ".overmind")

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

    # Verify CLI `overmind heal --mode shadow-pr` exits 1 while emitting surgery + diff JSON
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

    chronos = ChronosTimeMachine(storage_dir=tmp_path / ".overmind" / "cassettes")
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
    assert root_mcp["mcpServers"]["overmind"] == {"command": "overmind", "args": ["mcp", "--root", "."]}
    assert cursor_mcp["mcpServers"]["overmind"] == {"command": "overmind", "args": ["mcp", "--root", "."]}
