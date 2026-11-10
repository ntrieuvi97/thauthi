#!/usr/bin/env python3
"""
End-to-End Verification Suite for Overmind 3.0 (The Post-Developer Singularity Pillars):
5. Algorithmic Alchemist: Autonomous O(N^2)->O(N) & O(2^N)->O(N) Optimizer (`overmind.alchemist`)
6. Chronos: Deterministic I/O Virtualization & Time-Travel Replay Kernel (`overmind.chronos`)
7. Contract-to-Code CEGIS Program Synthesizer (`overmind.synthesizer`)
8. Whole-Repo Call-Graph Ripple Propagator (`overmind.hive`)
"""

import runpy
import textwrap
import time
from pathlib import Path
from typing import Any, Dict, List

from overmind.alchemist import AlgorithmicAlchemist
from overmind.chronos import ChronosTimeMachine, time_travel
from overmind.cli import main as cli_main
from overmind.hive import CallGraphHive
from overmind.synthesizer import ContractSynthesizer, synthesize


def test_pillar5_algorithmic_alchemist_optimizes_o_n2_and_recursion(tmp_path: Path) -> None:
    slow_file = tmp_path / "analytics_slow.py"
    slow_file.write_text(
        textwrap.dedent(
            """\
            def filter_whitelisted(events: list, whitelist: list) -> list:
                matched = []
                for ev in events:
                    if ev in whitelist:
                        matched.append(ev)
                return matched

            def intersect_unique(left_items: list, right_items: list) -> list:
                out = []
                for x in left_items:
                    for y in right_items:
                        if x == y:
                            out.append(x)
                            break
                return out

            def climb_ways(n: int) -> int:
                if n <= 1:
                    return 1
                return climb_ways(n - 1) + climb_ways(n - 2)
            """
        ),
        encoding="utf-8",
    )

    rc = cli_main(["optimize", str(slow_file), "--apply", "--json"])
    assert rc == 0

    optimized_code = slow_file.read_text(encoding="utf-8")
    # 1. Verify O(1) hash set pre-computation was injected
    assert "_om_set_whitelist = set(whitelist)" in optimized_code
    assert "_om_set_right_items = set(right_items)" in optimized_code
    # 2. Verify inline recursion memoization was injected
    assert "_om_memo" in optimized_code

    # 3. Execute optimized functions and verify exact outputs
    ns = runpy.run_path(str(slow_file))
    assert ns["filter_whitelisted"]([1, 2, 3, 4, 5], [2, 5, 9]) == [2, 5]
    assert ns["intersect_unique"]([10, 20, 30], [20, 40, 10]) == [10, 20]
    assert ns["climb_ways"](10) == 89


def test_pillar6_chronos_time_travel_cassette_and_stateful_io_healing(tmp_path: Path) -> None:
    chronos = ChronosTimeMachine(storage_dir=tmp_path / ".overmind" / "cassettes")
    network_call_counter = {"count": 0}

    def fetch_gateway_settlement(merchant_id: int) -> Dict[str, Any]:
        network_call_counter["count"] += 1
        if merchant_id == 999:
            # Edge case payload from remote API where batch_count is 0
            return {"gross_cents": 5000, "batch_count": 0}
        return {"gross_cents": 5000, "batch_count": 10}

    def compute_merchant_payout(merchant_id: int) -> float:
        payload = fetch_gateway_settlement(merchant_id)
        _ts = time.time()
        return payload["gross_cents"] / payload["batch_count"]

    # Make `fetch_gateway_settlement` visible in `compute_merchant_payout.__globals__`
    compute_merchant_payout.__globals__["fetch_gateway_settlement"] = fetch_gateway_settlement

    protected_payout = chronos.protect(compute_merchant_payout, auto_heal=True)

    # 1. Healthy execution records I/O cassette (1 real call to `fetch_gateway_settlement`)
    assert protected_payout(101) == 500.0
    assert network_call_counter["count"] == 1

    # 2. Crashing payload (merchant_id=999 -> batch_count=0 -> ZeroDivisionError):
    # Chronos records the crashing cassette (call #2), then replays both cassettes hermetically
    # in memory across multiple candidate AST surgeries WITHOUT calling `fetch_gateway_settlement` again!
    healed_res = protected_payout(999)
    assert healed_res == 0.0
    assert network_call_counter["count"] == 2  # Zero extra network calls during counterfactual healing!

    # 3. Verify cassettes were persisted to disk and can be replayed offline
    saved_cassettes = chronos.load_cassettes("compute_merchant_payout")
    assert len(saved_cassettes) == 2
    replayed_val, err = chronos.replay_cassette(compute_merchant_payout, saved_cassettes[0])
    assert err is None
    assert replayed_val == 500.0
    assert network_call_counter["count"] == 2  # Still 2! Offline replay made 0 network calls!


def test_pillar7_cegis_program_synthesizer_decorator_and_cli(tmp_path: Path) -> None:
    # 1. Test in-memory `@synthesize` decorator with doctest examples
    @synthesize
    def celsius_to_fahrenheit(c: float) -> float:
        """
        >>> celsius_to_fahrenheit(0)
        32
        >>> celsius_to_fahrenheit(100)
        212
        """
        pass

    assert celsius_to_fahrenheit(0) == 32
    assert celsius_to_fahrenheit(100) == 212
    assert celsius_to_fahrenheit(50) == 122

    @synthesize
    def deduplicate_ordered(items: list) -> list:
        """
        >>> deduplicate_ordered([3, 1, 3, 2, 1])
        [3, 1, 2]
        >>> deduplicate_ordered([10, 10, 20])
        [10, 20]
        """
        ...

    assert deduplicate_ordered([5, 4, 5, 1, 4]) == [5, 4, 1]

    # 2. Test CLI `overmind synthesize <file> --apply` on stub file
    specs_file = tmp_path / "specs.py"
    specs_file.write_text(
        textwrap.dedent(
            """\
            def common_elements(a: list, b: list) -> list:
                \"\"\"
                >>> common_elements([1, 2, 3, 4], [2, 4, 6])
                [2, 4]
                >>> common_elements([10, 20], [30, 10])
                [10]
                \"\"\"
                pass

            def is_palindrome(text: str) -> bool:
                \"\"\"
                >>> is_palindrome("radar")
                True
                >>> is_palindrome("overmind")
                False
                \"\"\"
                ...
            """
        ),
        encoding="utf-8",
    )

    rc = cli_main(["synthesize", str(specs_file), "--apply", "--json"])
    assert rc == 0

    synthesized_file_src = specs_file.read_text(encoding="utf-8")
    assert "return " in synthesized_file_src
    ns = runpy.run_path(str(specs_file))
    assert ns["common_elements"]([7, 8, 9], [9, 7, 0]) == [7, 9]
    assert ns["is_palindrome"]("level") is True
    assert ns["is_palindrome"]("hello") is False


def test_pillar8_whole_repo_call_graph_hive_ripple_propagator(tmp_path: Path) -> None:
    repo_dir = tmp_path / "microservice_repo"
    repo_dir.mkdir(parents=True)

    # `billing.py` evolved its signature:
    # - `user_id` was renamed to `account_id`
    # - `tax_rate: float` was added as a required parameter
    (repo_dir / "billing.py").write_text(
        textwrap.dedent(
            """\
            def issue_invoice(amount: float, account_id: int, tax_rate: float) -> dict:
                return {
                    "amount": amount,
                    "account_id": account_id,
                    "tax_rate": tax_rate,
                    "total": amount * (1.0 + tax_rate),
                }
            """
        ),
        encoding="utf-8",
    )

    # `checkout.py` still calls `issue_invoice` with the old keyword `user_id=` and misses `tax_rate`!
    # Also passes an outdated 4th positional arg in `legacy_batch()`.
    (repo_dir / "checkout.py").write_text(
        textwrap.dedent(
            """\
            from billing import issue_invoice

            def run_checkout():
                inv1 = issue_invoice(amount=200.0, user_id=42)
                inv2 = issue_invoice(100.0, 99, 0.1, "deprecated_extra_arg")
                return inv1, inv2
            """
        ),
        encoding="utf-8",
    )

    rc = cli_main(["hive", str(repo_dir), "--apply", "--json"])
    assert rc == 0

    healed_checkout = (repo_dir / "checkout.py").read_text(encoding="utf-8")
    assert "account_id=42" in healed_checkout
    assert "tax_rate=0.0" in healed_checkout
    assert "deprecated_extra_arg" not in healed_checkout

    # Execute the healed `checkout.py` to prove it now runs with zero TypeError!
    sys_path_orig = list(__import__("sys").path)
    __import__("sys").path.insert(0, str(repo_dir))
    try:
        ns = runpy.run_path(str(repo_dir / "checkout.py"))
        inv1, inv2 = ns["run_checkout"]()
        assert inv1["account_id"] == 42
        assert inv1["tax_rate"] == 0.0
        assert inv2["account_id"] == 99
        assert round(inv2["total"], 2) == 110.0
    finally:
        __import__("sys").path[:] = sys_path_orig
