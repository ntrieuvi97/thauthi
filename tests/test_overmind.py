#!/usr/bin/env python3
"""
Comprehensive end-to-end test suite for Overmind:
- Centsible Reflex Kernel Bridge
- Darwinian Gene Pool (Fitness, Auto-Mutation, Extinction, Portable DNA)
- Autopoietic Tool Forge (Transcript Mining, Tool Synthesis, 3-Gate Verification, Centsible Auto-Binding)
- Adversarial Shadow Verifier & Viral Evolution Scorecard
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from overmind.cli import main as cli_main
from overmind.gene_pool import (
    DarwinianGenePool,
    classify_gene_status,
    compute_darwinian_fitness,
    mutate_pattern_and_directive,
)
from overmind.kernel import CentsibleKernelBridge
from overmind.scorecard import compute_evolution_telemetry, render_markdown_badge, render_svg_card
from overmind.tool_forge import (
    AutopoieticToolForge,
    CommandStep,
    ToolCandidate,
    _generalize_step_paths,
    _subsume_candidates,
    extract_commands_from_transcript,
    is_trivial_or_meta_step,
    mine_candidates,
)
from overmind.verifier import ShadowVerifier


def test_centsible_kernel_binding_and_fact_crud(tmp_path: Path) -> None:
    facts_file = tmp_path / "facts.jsonl"
    bridge = CentsibleKernelBridge(facts_path=facts_file)
    assert isinstance(bridge.centsible_bound, bool)

    added = bridge.add_fact(
        pattern=r"pytest.{0,20}(fail|error)",
        fact="MUST RUN: 'pytest tests/ -q --tb=short'",
        category="gotcha",
        fact_id="fact_pytest_01",
    )
    assert added["id"] == "fact_pytest_01"
    facts = bridge.list_facts()
    assert len(facts) == 1
    assert facts[0]["id"] == "fact_pytest_01"


def test_darwinian_fitness_and_evolution_cycle(tmp_path: Path) -> None:
    facts_file = tmp_path / "facts.jsonl"
    events_file = tmp_path / "events.jsonl"
    home_dir = tmp_path / "overmind_home"

    # Seed 3 genes:
    # 1) Elite gene: shown 10, applied 9
    # 2) Recessive gene (Gen 1): shown 8, applied 0, overly broad wildcard .{0,45}
    # 3) Extinct gene (Gen 2): shown 12, applied 0
    raw_facts = [
        {
            "id": "gene_elite",
            "pattern": r"db_migrate.{0,15}error",
            "fact": "MUST RUN: 'python3 manage.py migrate --fake-initial'",
            "category": "gotcha",
            "hits": 10,
            "attributed_hits": 9,
            "saved_tokens": 4500,
        },
        {
            "id": "gene_recessive",
            "pattern": r"pytest.{0,45} suite",
            "fact": "run pytest tests/ -v for unit tests",
            "category": "general",
            "hits": 8,
            "attributed_hits": 0,
            "saved_tokens": 0,
            "generation": 1,
        },
        {
            "id": "gene_extinct",
            "pattern": r"legacy_build.{0,15}fail",
            "fact": "MUST RUN: 'make legacy'",
            "category": "general",
            "hits": 12,
            "attributed_hits": 0,
            "saved_tokens": 0,
            "generation": 2,
        },
    ]
    facts_file.write_text("\n".join(json.dumps(r) for r in raw_facts) + "\n", encoding="utf-8")

    bridge = CentsibleKernelBridge(facts_path=facts_file, events_path=events_file)
    pool = DarwinianGenePool(bridge=bridge, min_trials=5, home_dir=home_dir)

    pop = {g.gene_id: g for g in pool.evaluate_population()}
    assert pop["gene_elite"].status == "elite"
    assert pop["gene_elite"].fitness >= 75.0
    assert pop["gene_recessive"].status == "recessive"
    assert pop["gene_extinct"].status == "extinct"

    # Run evolution cycle
    report = pool.evolve_generation(dry_run=False)
    assert report["elites_count"] == 1
    assert report["mutated_count"] == 1
    assert report["extinct_count"] == 1

    # Verify extinct gene was moved to graveyard.jsonl and removed from active facts
    assert (home_dir / "graveyard.jsonl").exists()
    graveyard_lines = (home_dir / "graveyard.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(graveyard_lines) == 1
    assert json.loads(graveyard_lines[0])["gene_id"] == "gene_extinct"

    # Verify recessive gene mutated into a Generation 2 gene with tightened pattern & quoted command
    mut_info = report["mutations"][0]
    assert mut_info["parent_id"] == "gene_recessive"
    assert mut_info["generation"] == 2
    assert ".{0,18}" in mut_info["new_pattern"]
    assert "MUST RUN:" in mut_info["new_directive"]
    assert "'pytest tests/ -v'" in mut_info["new_directive"]

    # Verify DNA export & import round-trip
    dna_path = tmp_path / ".overmind" / "dna.jsonl"
    pool.export_dna(output_path=dna_path, include_active=False)
    assert dna_path.exists()
    dna_records = [json.loads(line) for line in dna_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(dna_records) == 1
    assert dna_records[0]["gene_id"] == "gene_elite"


def test_autopoietic_tool_forge_from_transcript_and_execution(tmp_path: Path) -> None:
    facts_file = tmp_path / "facts.jsonl"
    tools_dir = tmp_path / "forged_tools"
    transcript_file = tmp_path / "session.jsonl"

    # Create a realistic hybrid transcript with a recurring 2-step verification pipeline
    steps = [
        {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "python3 -c \"print('compile_ok')\""}}]},
        {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "python3 -c \"print('tests_ok')\""}}]},
        {"type": "USER_INPUT", "content": "Next task"},
        {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "python3 -c \"print('compile_ok')\""}}]},
        {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "python3 -c \"print('tests_ok')\""}}]},
    ]
    transcript_file.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")

    bridge = CentsibleKernelBridge(facts_path=facts_file)
    forge = AutopoieticToolForge(bridge=bridge, tools_dir=tools_dir)

    forged_list = forge.forge_from_transcript(transcript_file, min_occurrences=2)
    assert len(forged_list) >= 1
    forged = forged_list[0]
    assert forged["verified"] is True

    tool_path = Path(forged["tool_path"])
    assert tool_path.exists()

    # Execute the newly forged tool in real mode (--json) and verify it runs the 2 steps
    proc = subprocess.run(
        [sys.executable, str(tool_path), "--json"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["status"] == "ok"
    assert payload["steps_executed"] == 2
    assert payload["results"][0]["stdout"] == "compile_ok"
    assert payload["results"][1]["stdout"] == "tests_ok"

    # Confirm the forged tool was auto-bound into Centsible's FactsEngine
    facts = bridge.list_facts()
    assert any(f.get("id") == forged["fact_id"] and f.get("category") == "forged-tool" for f in facts)


def test_shadow_verifier_catches_traps_and_passes_clean_code(tmp_path: Path) -> None:
    clean_dir = tmp_path / "clean_repo"
    clean_dir.mkdir()
    (clean_dir / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

    verifier = ShadowVerifier(workspace_dir=clean_dir)
    res_clean = verifier.verify_workspace()
    assert res_clean["passed"] is True
    assert res_clean["integrity_score"] == 100

    # Inject a debug trap
    (clean_dir / "bad.py").write_text("def debug():\n    breakpoint()\n", encoding="utf-8")
    res_bad = verifier.verify_workspace()
    assert res_bad["passed"] is False
    assert any(iss["kind"] == "debug_trap" for iss in res_bad["issues"])


def test_scorecard_and_cli_end_to_end(tmp_path: Path) -> None:
    facts_file = tmp_path / "facts.jsonl"
    svg_file = tmp_path / "card.svg"

    rc = cli_main([
        "--facts-path", str(facts_file),
        "scorecard",
        "--svg", str(svg_file),
    ])
    assert rc == 0
    assert svg_file.exists()
    assert "OVERMIND // SELF-EVOLVING AGENT OS" in svg_file.read_text(encoding="utf-8")


def test_noise_shield_and_path_generalization(tmp_path: Path) -> None:
    # 1. Verify trivial and exploratory inspection commands are filtered out
    assert is_trivial_or_meta_step("ls -la /Users/test/repo") is True
    assert is_trivial_or_meta_step("which python3") is True
    assert is_trivial_or_meta_step("git status && git log -n 5") is True
    assert is_trivial_or_meta_step("python3 checkpoint.py --help") is True
    assert is_trivial_or_meta_step("python3 om_test_tool.py") is True

    # 2. Verify non-trivial compilation, test, and build commands pass the filter
    assert is_trivial_or_meta_step("pytest tests/ -v") is False
    assert is_trivial_or_meta_step("python3 -m unittest discover tests/") is False
    assert is_trivial_or_meta_step("npm run build && npm test") is False
    assert is_trivial_or_meta_step("cargo test --all") is False

    # 3. Verify path de-hardcoding converts workspace paths to relative
    ws = tmp_path / "my_project"
    ws.mkdir()
    raw_cmd = f"python3 {ws}/scripts/build.py --out {ws}/dist"
    generalized = _generalize_step_paths(raw_cmd, workspace_dir=ws)
    assert str(ws) not in generalized
    assert generalized == "python3 scripts/build.py --out dist"


def test_candidate_subsumption() -> None:
    # If a 3-step pipeline (A, B, C) occurs 2 times and (A, B) occurs 2 times,
    # the 2-step sub-slice should be subsumed into the 3-step pipeline.
    candidates = {
        ("step_a", "step_b", "step_c"): ToolCandidate(
            slug="om_long_pipe",
            title="Long Pipe",
            kind="pipeline",
            steps=["step_a", "step_b", "step_c"],
            occurrences=2,
            trigger_pattern="long_pipe",
            estimated_token_savings=1350,
        ),
        ("step_a", "step_b"): ToolCandidate(
            slug="om_short_pipe",
            title="Short Pipe",
            kind="pipeline",
            steps=["step_a", "step_b"],
            occurrences=2,
            trigger_pattern="short_pipe",
            estimated_token_savings=900,
        ),
    }

    subsumed = _subsume_candidates(candidates)
    assert len(subsumed) == 1
    assert subsumed[0].slug == "om_long_pipe"


def test_parameterized_tool_execution_and_forge_prune(tmp_path: Path) -> None:
    facts_file = tmp_path / "facts.jsonl"
    tools_dir = tmp_path / "forged_tools"

    bridge = CentsibleKernelBridge(facts_path=facts_file)
    forge = AutopoieticToolForge(bridge=bridge, tools_dir=tools_dir)

    # Forge a parameterized tool that uses sys.argv in the final step
    entry = forge.forge_custom_pipeline(
        name="param_runner",
        steps=[
            "python3 -c \"print('step_one_done')\"",
            "python3 -c \"import sys; print('extra_args: ' + ' '.join(sys.argv[1:]))\"",
        ],
        trigger_pattern="param_runner",
    )
    tool_path = Path(entry["tool_path"])
    assert tool_path.exists()

    # 1. Execute with extra arguments and verify they are passed to the final command step
    proc = subprocess.run(
        [sys.executable, str(tool_path), "argA", "argB"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0
    assert "step_one_done" in proc.stdout
    assert "extra_args: argA argB" in proc.stdout

    # 2. Prune the forged tool and verify deletion from disk, registry, and facts
    pruned = forge.prune_tools(slug="param_runner")
    assert len(pruned) == 1
    assert not tool_path.exists()
    assert "om_param_runner" not in forge.load_registry()
    assert not any(f.get("id") == entry["fact_id"] for f in bridge.list_facts())


def test_centsible_metrics_aliases_and_verified_scorecard_roi(tmp_path: Path) -> None:
    facts_file = tmp_path / "facts.jsonl"
    metrics_file = tmp_path / "metrics.json"
    tools_dir = tmp_path / "forged_tools"
    crystals_dir = tmp_path / "crystals"

    # Simulate a Centsible store with high unattributed hits (1000 hits -> ~1.5M est)
    # but modest attributed hits (20 attributed_hits -> 30,000 verified tokens)
    facts_file.write_text(
        json.dumps(
            {
                "id": "fact_verify_roi",
                "pattern": r"pytest.{0,20}error",
                "fact": "MUST RUN: 'python3 -m pytest tests/ -q'",
                "category": "gotcha",
                "hits": 1000,
                "attributed_hits": 20,
                "injected_tokens": 5000,
                "saved_tokens": 1500000,
                "est_savings": 1500,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    metrics_file.write_text(
        json.dumps(
            {
                "fact_hits": 1000,
                "attributed_hits": 20,
                "injected_tokens": 5000,
                "saved_discovery_tokens": 1500000,
                "command_calls_total": 100,
                "first_try_cmd_successes": 96,
            }
        ),
        encoding="utf-8",
    )

    bridge = CentsibleKernelBridge(facts_path=facts_file)
    summary = bridge.get_metrics_summary()

    # Verify alias normalization between Centsible and Overmind keys
    assert summary["net_tokens_saved"] == summary["net_saved_tokens"]
    assert summary["net_tokens_saved"] >= 1495000
    assert summary["estimated_usd_saved"] == summary["saved_usd"]
    assert summary["estimated_usd_saved"] > 4.0

    # Verify attributed/verified tokens are much lower than unverified hit estimate
    assert 20000 <= summary["verified_tokens_saved"] <= 35000

    # Verify Scorecard separates verified vs estimated tokens and does not inflate Level on unattributed hits
    from overmind.crystallizer import NeuralSymbolicCrystallizer

    pool = DarwinianGenePool(bridge=bridge, home_dir=tmp_path / "om_home")
    forge = AutopoieticToolForge(bridge=bridge, tools_dir=tools_dir)
    cryst = NeuralSymbolicCrystallizer(bridge=bridge, crystals_dir=crystals_dir)

    telemetry = compute_evolution_telemetry(
        bridge=bridge,
        gene_pool=pool,
        tool_forge=forge,
        crystallizer=cryst,
        all_projects=False,
    )
    assert telemetry["verified_tokens_saved"] == summary["verified_tokens_saved"]
    assert telemetry["net_tokens_saved"] == summary["net_tokens_saved"]
    # Level must be computed from verified tokens (XP < 200 -> Level 1), NOT inflated to Level 15+ by 1.5M unattributed tokens
    assert telemetry["level"] <= 2

