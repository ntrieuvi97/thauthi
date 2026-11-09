#!/usr/bin/env python3
"""
End-to-End Verification Suite for Overmind 2.0 (The 4 Omniscient Pillars):
1. Omniscient Runtime Tracer & Backward Dynamic Causal Fault Localization
2. In-Flight Self-Healing Immune System & Counterfactual AST Surgery
3. Behavioral Invariant Miner & Differential Shadow Prover (Semantics, Purity, O(N) Complexity)
4. Neural-to-Symbolic AST Crystallizer (0-Token Metavariable AST Codemods)
"""

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List

from overmind.cli import main as cli_main
from overmind.crystallizer import NeuralSymbolicCrystallizer, abstract_edit_pair
from overmind.immune import AutopoieticImmuneSystem
from overmind.kernel import CentsibleKernelBridge
from overmind.omniscient import OmniscientTracer
from overmind.prover import BehavioralInvariantMiner, DifferentialShadowProver


def test_pillar1_omniscient_tracer_and_causal_fault_graph(tmp_path: Path) -> None:
    def buggy_pricing_pipeline(base_price: int, discount_code: int) -> float:
        adjusted = base_price - discount_code
        weight = adjusted * 2
        return 1000.0 / weight

    tracer = OmniscientTracer(target_funcs={"buggy_pricing_pipeline"})

    # 1. Trace healthy execution
    res, exc, frames = tracer.run_callable(buggy_pricing_pipeline, 50, 10)
    assert exc is None
    assert res == 12.5
    assert len(frames) == 1
    healthy_dna = frames[0]
    assert healthy_dna.args == {"base_price": 50, "discount_code": 10}
    assert any(m.variable == "adjusted" and m.new_value == 40 for m in healthy_dna.mutations)
    assert any(m.variable == "weight" and m.new_value == 80 for m in healthy_dna.mutations)

    # 2. Trace crashing execution (base_price=25, discount_code=25 -> adjusted=0 -> weight=0 -> ZeroDivisionError)
    res2, exc2, frames2 = tracer.run_callable(buggy_pricing_pipeline, 25, 25)
    assert isinstance(exc2, ZeroDivisionError)
    assert len(frames2) == 1
    assert len(tracer.fault_reports) == 1

    fault = tracer.fault_reports[0]
    assert fault.exception_type == "ZeroDivisionError"
    assert "weight" in fault.suspect_variables
    # Backward Dynamic Slicing must trace `weight` back to `adjusted`!
    sliced_vars = [step["variable"] for step in fault.causal_chain]
    assert "weight" in sliced_vars
    assert "adjusted" in sliced_vars
    assert fault.root_cause_mutation is not None
    assert fault.root_cause_mutation["variable"] == "adjusted"
    assert fault.root_cause_mutation["new_value"] == 0

    diag_text = fault.render_compact_diagnosis()
    assert "OMNISCIENT CAUSAL FAULT GRAPH" in diag_text
    assert "Root Cause Origin" in diag_text


def test_pillar2_in_flight_self_healing_immune_system_and_ast_surgery(tmp_path: Path) -> None:
    immune_sys = AutopoieticImmuneSystem(storage_dir=tmp_path / ".overmind")

    @immune_sys.protect(auto_heal=True, persist_test=True, apply_patch=False)
    def compute_roi(gain: float, cost: float) -> float:
        return gain / cost

    # 1. Record healthy frames so Immune System learns ground-truth behavior
    assert compute_roi(100.0, 20.0) == 5.0
    assert compute_roi(50.0, 10.0) == 5.0

    # 2. Trigger unhandled ZeroDivisionError in-flight — must auto-heal and NOT raise!
    healed_val = compute_roi(100.0, 0.0)
    assert healed_val == 0.0

    # 3. Verify historical healthy behavior is 100% preserved after hot-swap
    assert compute_roi(200.0, 25.0) == 8.0

    # 4. Verify SurgeryReport & auto-generated pytest regression test file
    assert len(immune_sys.surgeries) == 1
    surgery = immune_sys.surgeries[0]
    assert surgery.exception_type == "ZeroDivisionError"
    assert "ast_zero_div_guard" in surgery.strategy_applied
    assert surgery.healthy_frames_verified == 2
    assert surgery.regression_test_path is not None

    reg_test_file = Path(surgery.regression_test_path)
    assert reg_test_file.exists()

    # Execute the auto-generated pytest regression file to prove it passes standalone!
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", str(reg_test_file), "-q"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, f"Auto-generated regression test failed: {proc.stdout}\n{proc.stderr}"


def test_pillar2_immune_heals_keyerror_and_patches_source_file(tmp_path: Path) -> None:
    script_file = tmp_path / "user_service.py"
    script_file.write_text(
        textwrap.dedent(
            """\
            def extract_tier(profile: dict) -> str:
                return profile["tier"]

            def main():
                a = extract_tier({"tier": "enterprise"})
                b = extract_tier({})
                return a, b
            """
        ),
        encoding="utf-8",
    )

    rc = cli_main(["heal", str(script_file), "--apply", "--json"])
    assert rc == 0

    patched_code = script_file.read_text(encoding="utf-8")
    assert ".get(" in patched_code


def test_pillar3_behavioral_invariant_miner_and_differential_prover() -> None:
    # 1. Prove Equivalence on a valid O(N) optimization
    old_valid = textwrap.dedent(
        """\
        def sum_positives(items: list) -> int:
            total = 0
            for x in items:
                if x > 0:
                    total = total + x
            return total
        """
    )
    new_valid = textwrap.dedent(
        """\
        def sum_positives(items: list) -> int:
            acc = 0
            for val in items:
                if val > 0:
                    acc += val
            return acc
        """
    )
    rep_ok = DifferentialShadowProver.prove_equivalence(old_valid, new_valid)
    assert rep_ok.passed is True
    assert rep_ok.results[0].verdict == "PROVEN_EQUIVALENT"
    assert rep_ok.results[0].old_contract.complexity_class == "O(N)"
    assert rep_ok.results[0].new_contract.complexity_class == "O(N)"
    assert rep_ok.results[0].new_contract.is_pure_on_args is True

    # 2. Catch Algorithmic Complexity Degradation: O(N) -> O(N^2)+
    new_quadratic = textwrap.dedent(
        """\
        def sum_positives(items: list) -> int:
            total = 0
            for i in range(len(items)):
                for j in range(len(items)):
                    if i == j and items[i] > 0:
                        total += items[i]
            return total
        """
    )
    rep_slow = DifferentialShadowProver.prove_equivalence(old_valid, new_quadratic)
    assert rep_slow.passed is False
    assert rep_slow.results[0].verdict == "COMPLEXITY_REGRESSION"
    assert rep_slow.results[0].new_contract.complexity_class == "O(N^2)+"

    # 3. Catch In-Place Argument Mutation (Purity Violation)
    new_impure = textwrap.dedent(
        """\
        def sum_positives(items: list) -> int:
            total = sum(x for x in items if x > 0)
            items.append(999)
            return total
        """
    )
    rep_impure = DifferentialShadowProver.prove_equivalence(old_valid, new_impure)
    assert rep_impure.passed is False
    assert rep_impure.results[0].verdict == "CONTRACT_VIOLATION"
    assert any("Purity violation" in v for v in rep_impure.results[0].violations)

    # 4. Catch Semantic Divergence with Counterexample
    new_buggy = textwrap.dedent(
        """\
        def sum_positives(items: list) -> int:
            return sum(x for x in items if x >= 10)
        """
    )
    rep_bug = DifferentialShadowProver.prove_equivalence(old_valid, new_buggy)
    assert rep_bug.passed is False
    assert rep_bug.results[0].verdict == "SEMANTIC_DIVERGENCE"
    assert len(rep_bug.results[0].counterexamples) >= 1


def test_pillar4_neural_symbolic_ast_crystallizer_zero_token_codemod(tmp_path: Path) -> None:
    facts_file = tmp_path / "facts.jsonl"
    crystals_dir = tmp_path / "crystals"
    bridge = CentsibleKernelBridge(facts_path=facts_file)
    crystallizer = NeuralSymbolicCrystallizer(bridge=bridge, crystals_dir=crystals_dir)

    # 1. Crystallize a neural edit: `subprocess.run(cmd, check=True)` -> `subprocess.run(cmd, check=True, timeout=30)`
    crystal = crystallizer.crystallize_edit(
        name="enforce_subprocess_timeout",
        before_code="subprocess.run(user_cmd, check=True)",
        after_code="subprocess.run(user_cmd, check=True, timeout=30)",
    )
    assert "__OM_V1__" in crystal.before_pattern
    assert "timeout=30" in crystal.after_pattern
    assert Path(crystal.tool_path).exists()

    # 2. Apply the crystal to a target file with completely different variable/expression arguments
    target_py = tmp_path / "worker.py"
    target_py.write_text(
        textwrap.dedent(
            """\
            import subprocess

            def deploy(build_args, deploy_cmd):
                subprocess.run(build_args, check=True)
                subprocess.run(deploy_cmd, check=True)
            """
        ),
        encoding="utf-8",
    )

    report = crystallizer.apply_all_crystals(target_path=target_py, dry_run=False)
    assert report["total_rewrites"] == 2

    transformed = target_py.read_text(encoding="utf-8")
    assert "subprocess.run(build_args, check=True, timeout=30)" in transformed
    assert "subprocess.run(deploy_cmd, check=True, timeout=30)" in transformed

    # 3. Verify idempotency: running a second time performs 0 rewrites
    report_second = crystallizer.apply_all_crystals(target_path=target_py, dry_run=False)
    assert report_second["total_rewrites"] == 0

    # 4. Verify Centsible Reflex Kernel auto-binding
    facts = bridge.list_facts()
    assert any(f.get("id") == crystal.fact_id and f.get("category") == "symbolic-crystal" for f in facts)


def test_world_scale_repo_init_scaffolds_ci_hooks_and_agents(tmp_path: Path) -> None:
    repo = tmp_path / "global_startup_repo"
    (repo / ".git" / "hooks").mkdir(parents=True)

    rc = cli_main(["init", str(repo), "--json"])
    assert rc == 0

    assert (repo / ".overmind" / "dna.jsonl").exists()
    assert (repo / ".git" / "hooks" / "pre-commit").exists()
    assert (repo / ".github" / "workflows" / "overmind.yml").exists()
    assert (repo / ".claude" / "settings.json").exists()
    assert (repo / ".cursor" / "rules" / "overmind.mdc").exists()
    assert (repo / ".agent" / "rules" / "overmind.md").exists()

    claude_cfg = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert "Stop" in claude_cfg.get("hooks", {})

