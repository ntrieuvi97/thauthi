#!/usr/bin/env python3
"""
Overmind 2.0 CLI — Unified command-line interface for the Omniscient Autopoietic Kernel.
"""

import argparse
import inspect
import json
import runpy
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from overmind.crystallizer import NeuralSymbolicCrystallizer
from overmind.gene_pool import DarwinianGenePool
from overmind.immune import AutopoieticImmuneSystem
from overmind.initializer import RepoInitializer
from overmind.kernel import CentsibleKernelBridge
from overmind.omniscient import OmniscientTracer
from overmind.prover import BehavioralInvariantMiner, DifferentialShadowProver
from overmind.scorecard import (
    compute_evolution_telemetry,
    render_markdown_badge,
    render_svg_card,
    render_terminal_scorecard,
)
from overmind.tool_forge import AutopoieticToolForge
from overmind.verifier import ShadowVerifier


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="overmind",
        description="Overmind 2.0: The Omniscient Autopoietic Kernel for AI Coding Agents.",
    )
    parser.add_argument("--facts-path", type=Path, default=None, help="Custom path to facts.jsonl")
    sub = parser.add_subparsers(dest="subcommand")

    # status
    p_status = sub.add_parser("status", help="Show Overmind kernel, gene pool, tool forge & AST crystal status")
    p_status.add_argument("--all", action="store_true", help="Aggregate metrics and genes across all repositories")
    p_status.add_argument("--json", action="store_true", help="Output JSON")

    # init (World-Scale Zero-Touch Repo Onboarding)
    p_init = sub.add_parser("init", help="Bootstrap Overmind 2.0 into any repository (Git hook, GitHub Actions CI, Claude/Cursor/AGY configs, Team DNA)")
    p_init.add_argument("path", nargs="?", type=Path, default=Path("."), help="Target repository directory (default: current directory)")
    p_init.add_argument("--no-ci", action="store_true", help="Skip generating .github/workflows/overmind.yml")
    p_init.add_argument("--no-git-hook", action="store_true", help="Skip installing .git/hooks/pre-commit")
    p_init.add_argument("--no-agents", action="store_true", help="Skip generating .claude, .cursor, and .agent configs")
    p_init.add_argument("--json", action="store_true", help="Output JSON")

    # trace (Pillar 1: Omniscient Runtime Tracer)
    p_trace = sub.add_parser("trace", help="Run a Python script under Omniscient Runtime Tracer & Causal Fault Graph")
    p_trace.add_argument("script", type=Path, help="Python file to execute under live frame tracing")
    p_trace.add_argument("--json", action="store_true", help="Output JSON")

    # heal (Pillar 2: In-Flight Self-Healing Immune System)
    p_heal = sub.add_parser("heal", help="Execute a Python script with In-Flight Self-Healing & Counterfactual AST Surgery")
    p_heal.add_argument("script", type=Path, help="Python file to protect and auto-heal on crash")
    p_heal.add_argument("--apply", action="store_true", help="Write verified AST surgery patches back to source file")
    p_heal.add_argument("--json", action="store_true", help="Output JSON")

    # prove (Pillar 3: Behavioral Invariant Miner & Differential Shadow Prover)
    p_prove = sub.add_parser("prove", help="Mine behavioral contracts or run Differential Shadow Execution (Old vs New AST)")
    p_prove.add_argument("--before", type=Path, default=None, help="Original Python file (Old AST)")
    p_prove.add_argument("--after", type=Path, default=None, help="Modified Python file (New AST)")
    p_prove.add_argument("--mine", type=Path, default=None, help="Mine behavioral invariants & O(N) complexity from a single file")
    p_prove.add_argument("--json", action="store_true", help="Output JSON")

    # crystallize (Pillar 4: Neural-to-Symbolic AST Crystallizer)
    p_crys = sub.add_parser("crystallize", help="Compile code edits into 0-token Symbolic AST Crystals or apply them")
    p_crys.add_argument("--name", type=str, default=None, help="Slug name for the new AST Crystal")
    p_crys.add_argument("--before", type=str, default=None, help="Before Python code snippet or file path")
    p_crys.add_argument("--after", type=str, default=None, help="After Python code snippet or file path")
    p_crys.add_argument("--trigger", type=str, default=None, help="Optional regex trigger for Centsible")
    p_crys.add_argument("--apply", type=Path, default=None, help="Apply all registered AST Crystals to target file/dir")
    p_crys.add_argument("--dry-run", action="store_true", help="Preview AST rewrites without modifying files")
    p_crys.add_argument("--list", action="store_true", help="List all compiled Symbolic AST Crystals")
    p_crys.add_argument("--json", action="store_true", help="Output JSON")

    # fitness
    p_fit = sub.add_parser("fitness", help="Rank all agent skills/genes by Darwinian Fitness Score")
    p_fit.add_argument("--min-trials", type=int, default=5, help="Minimum exposures before natural selection")
    p_fit.add_argument("--json", action="store_true", help="Output JSON")

    # evolve
    p_evo = sub.add_parser("evolve", help="Run Darwinian evolution cycle (mutate recessive genes, prune extinct)")
    p_evo.add_argument("--dry-run", action="store_true", help="Preview mutations and extinctions without writing")
    p_evo.add_argument("--min-trials", type=int, default=5, help="Minimum exposures before mutation")
    p_evo.add_argument("--json", action="store_true", help="Output JSON")

    # forge
    p_forge = sub.add_parser("forge", help="Autopoietic Tool Forge: synthesize & verify CLI tools from transcripts")
    p_forge.add_argument("--transcript", type=Path, default=None, help="JSONL transcript to mine for recurring pipelines")
    p_forge.add_argument("--name", type=str, default=None, help="Custom tool name to forge directly")
    p_forge.add_argument("--steps", nargs="+", default=None, help="Command steps for custom tool forging")
    p_forge.add_argument("--trigger", type=str, default=None, help="Optional regex trigger for Centsible auto-injection")
    p_forge.add_argument("--min-occurrences", type=int, default=2, help="Min occurrences in transcript to forge")
    p_forge.add_argument("--list", action="store_true", help="List all forged and verified tools")
    p_forge.add_argument("--prune", action="store_true", help="Prune noisy, obsolete, or dead forged tools")
    p_forge.add_argument("--slug", type=str, default=None, help="Specific tool slug to prune")
    p_forge.add_argument("--dead", action="store_true", help="Prune unapplied or unverified tools")
    p_forge.add_argument("--all", action="store_true", help="Prune all forged tools in registry")
    p_forge.add_argument("--dry-run", action="store_true", help="Preview pruning actions without deleting")
    p_forge.add_argument("--json", action="store_true", help="Output JSON")

    # verify
    p_ver = sub.add_parser("verify", help="Run Adversarial Shadow Verifier on workspace")
    p_ver.add_argument("--path", type=Path, default=None, help="Workspace directory to verify")
    p_ver.add_argument("--run-tests", action="store_true", help="Also execute pytest suite")
    p_ver.add_argument("--json", action="store_true", help="Output JSON")

    # scorecard
    p_score = sub.add_parser("scorecard", help="Generate viral Evolution Scorecard (Terminal, Markdown, or SVG)")
    p_score.add_argument("--svg", type=Path, default=None, help="Write shareable SVG scorecard to path")
    p_score.add_argument("--markdown", action="store_true", help="Print Markdown badge for PRs/READMEs")
    p_score.add_argument("--json", action="store_true", help="Output JSON")

    # dna
    p_dna = sub.add_parser("dna", help="Export or import cross-repo Elite DNA (.overmind/dna.jsonl)")
    p_dna.add_argument("action", choices=["export", "import"], help="Export or import DNA")
    p_dna.add_argument("--path", type=Path, default=Path(".overmind/dna.jsonl"), help="DNA JSONL file path")
    p_dna.add_argument("--include-active", action="store_true", help="Include active genes alongside elite genes")

    # hook (Antigravity & Claude Code lifecycle integration)
    p_hook = sub.add_parser("hook", help="Lifecycle hook handler for Antigravity, Claude Code & OpenClaw")
    p_hook.add_argument("event", choices=["stop", "post-session"], help="Hook event name")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        args.subcommand = "status"
        args.json = False

    bridge = CentsibleKernelBridge(facts_path=args.facts_path)

    if args.subcommand == "status":
        all_proj = getattr(args, "all", None)
        data = compute_evolution_telemetry(bridge=bridge, all_projects=True if all_proj else None)
        if getattr(args, "json", False):
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(render_terminal_scorecard(data))
        return 0

    if args.subcommand == "init":
        initializer = RepoInitializer(repo_dir=args.path)
        rep = initializer.initialize_repository(
            with_ci=not args.no_ci,
            with_git_hook=not args.no_git_hook,
            with_agents=not args.no_agents,
        )
        if args.json:
            print(json.dumps(rep, ensure_ascii=False, indent=2))
        else:
            print(f"🚀 Initialized Overmind 2.0 in repository: {rep['repository']}")
            for f_rel in rep["files_configured"]:
                print(f"  ✓ Configured: {f_rel}")
        return 0

    if args.subcommand == "trace":
        script_path = args.script.resolve()
        tracer = OmniscientTracer(target_files={str(script_path)})
        _, exc, frames = tracer.run_callable(runpy.run_path, str(script_path), run_name="__main__")
        payload = {
            "script": str(script_path),
            "crashed": exc is not None,
            "frames_recorded": len(frames),
            "frames": [f.to_dict() for f in frames],
            "fault_reports": [r.to_dict() for r in tracer.fault_reports],
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"=== Omniscient Runtime Trace: {script_path.name} ({len(frames)} frames) ===")
            for f in frames:
                status = f"❌ {f.exception_type}" if f.exception_type else f"-> {f.return_value!r}"
                print(f"  • {f.func_name}({f.args}) {status} [{f.steps_executed} steps, {f.duration_us}us]")
            for rep in tracer.fault_reports:
                print("\n" + rep.render_compact_diagnosis())
        return 1 if exc is not None else 0

    if args.subcommand == "heal":
        script_path = args.script.resolve()
        immune_sys = AutopoieticImmuneSystem(storage_dir=script_path.parent / ".overmind")
        mod_globals = runpy.run_path(str(script_path), run_name="__overmind_heal__")
        # Wrap all user functions in module and invoke `main()` if present
        for name, obj in list(mod_globals.items()):
            if inspect.isfunction(obj) and not name.startswith("_"):
                mod_globals[name] = immune_sys.protect(obj, auto_heal=True, persist_test=True, apply_patch=args.apply)
                if hasattr(obj, "__globals__"):
                    obj.__globals__[name] = mod_globals[name]
        if "main" in mod_globals and callable(mod_globals["main"]):
            mod_globals["main"]()
        out = {
            "script": str(script_path),
            "surgeries_performed": len(immune_sys.surgeries),
            "surgeries": [s.to_dict() for s in immune_sys.surgeries],
        }
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"=== Overmind Autopoietic Immune System ({len(immune_sys.surgeries)} surgery/surgeries) ===")
            for s in immune_sys.surgeries:
                print(
                    f"  🩹 Healed {s.func_name}() on {s.exception_type} via [{s.strategy_applied}] "
                    f"-> returned {s.healed_return_value!r} (Test: {s.regression_test_path})"
                )
        return 0

    if args.subcommand == "prove":
        if args.mine:
            src = args.mine.read_text(encoding="utf-8")
            funcs, asts = DifferentialShadowProver._compile_functions(src, "mine", source_path=args.mine)
            contracts = [
                BehavioralInvariantMiner.mine_contract(fn, asts[name]).to_dict()
                for name, fn in funcs.items()
            ]
            if args.json:
                print(json.dumps(contracts, ensure_ascii=False, indent=2))
            else:
                print(f"=== Behavioral Invariant Contracts ({len(contracts)} functions) ===")
                for c in contracts:
                    print(f"  • {c['func_name']}(): {' | '.join(c['invariants_summary'])}")
            return 0

        if args.before and args.after:
            old_src = args.before.read_text(encoding="utf-8")
            new_src = args.after.read_text(encoding="utf-8")
            report = DifferentialShadowProver.prove_equivalence(old_src, new_src)
            if args.json:
                print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(report.render_summary())
            return 0 if report.passed else 1

        parser.error("Provide --before OLD.py --after NEW.py, or --mine FILE.py")

    if args.subcommand == "crystallize":
        crys = NeuralSymbolicCrystallizer(bridge=bridge)
        if args.list:
            reg = crys.load_registry()
            if args.json:
                print(json.dumps(reg, ensure_ascii=False, indent=2))
            else:
                print(f"=== Symbolic AST Crystals ({len(reg)}) ===")
                for slug, info in reg.items():
                    print(f"  💎 {slug}: `{info['before_pattern']}` -> `{info['after_pattern']}` ({info['tool_path']})")
            return 0

        if args.apply:
            rep = crys.apply_all_crystals(target_path=args.apply, dry_run=args.dry_run)
            if args.json:
                print(json.dumps(rep, ensure_ascii=False, indent=2))
            else:
                print(
                    f"💎 Applied {rep['crystals_evaluated']} Symbolic AST Crystal(s): "
                    f"{rep['total_rewrites']} rewrite(s) performed."
                )
            return 0

        if args.name and args.before and args.after:
            b_path = Path(args.before)
            a_path = Path(args.after)
            b_code = b_path.read_text(encoding="utf-8") if b_path.exists() and b_path.is_file() else args.before
            a_code = a_path.read_text(encoding="utf-8") if a_path.exists() and a_path.is_file() else args.after
            entry = crys.crystallize_edit(
                name=args.name,
                before_code=b_code,
                after_code=a_code,
                trigger_pattern=args.trigger,
            )
            if args.json:
                print(json.dumps(entry.to_dict(), ensure_ascii=False, indent=2))
            else:
                print(f"💎 Crystallized 0-Token Symbolic AST Codemod: {entry.slug}")
                print(f"   Pattern: `{entry.before_pattern}` -> `{entry.after_pattern}`")
                print(f"   Executable: {entry.tool_path}")
            return 0

        parser.error("Provide --name NAME --before ... --after ..., --apply PATH, or --list")

    if args.subcommand == "fitness":
        pool = DarwinianGenePool(bridge=bridge, min_trials=args.min_trials)
        genes = pool.evaluate_population()
        if args.json:
            print(json.dumps([g.to_dict() for g in genes], ensure_ascii=False, indent=2))
        else:
            print(f"=== Darwinian Gene Pool ({len(genes)} genes) ===")
            for g in genes:
                badge = {"elite": "👑 ELITE", "active": "🟢 ACTIVE", "recessive": "🧬 RECESSIVE", "extinct": "💀 EXTINCT"}.get(g.status, g.status)
                print(
                    f"[{badge}] {g.gene_id} (Gen G{g.generation}) | Fitness: {g.fitness}% "
                    f"(applied {g.applied}/{g.shown}) | Trigger: /{g.pattern}/"
                )
        return 0

    if args.subcommand == "evolve":
        pool = DarwinianGenePool(bridge=bridge, min_trials=args.min_trials)
        report = pool.evolve_generation(dry_run=args.dry_run)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            mode_str = "DRY-RUN" if args.dry_run else "LIVE"
            print(
                f"=== Overmind Evolution Cycle [{mode_str}] ===\n"
                f"• Evaluated: {report['total_evaluated']} genes\n"
                f"• Promoted to Elite: {report['elites_count']}\n"
                f"• Auto-Mutated (Recessive -> Next Gen): {report['mutated_count']}\n"
                f"• Pruned to Graveyard (Extinct): {report['extinct_count']}"
            )
            for m in report["mutations"]:
                print(f"  🧬 Mutated {m['parent_id']} -> {m['gene_id']} (G{m['generation']}): {m['mutation_note']}")
        return 0

    if args.subcommand == "forge":
        forge = AutopoieticToolForge(bridge=bridge)
        if args.list:
            reg = forge.load_registry()
            if args.json:
                print(json.dumps(reg, ensure_ascii=False, indent=2))
            else:
                print(f"=== Autopoietic Forged Tools ({len(reg)}) ===")
                for slug, info in reg.items():
                    print(f"• {slug}: {info.get('title')} -> {info.get('tool_path')}")
            return 0

        if getattr(args, "prune", False) or getattr(args, "slug", None) or (getattr(args, "all", False) and args.subcommand == "forge"):
            pruned = forge.prune_tools(
                slug=args.slug,
                only_dead=getattr(args, "dead", False),
                all_tools=getattr(args, "all", False),
                dry_run=getattr(args, "dry_run", False),
            )
            if args.json:
                print(json.dumps(pruned, ensure_ascii=False, indent=2))
            else:
                mode_str = "PREVIEW (dry-run)" if getattr(args, "dry_run", False) else "PRUNED"
                print(f"=== Autopoietic Forge Pruning [{mode_str}] ===")
                if not pruned:
                    print("• No noisy or dead tools matched criteria.")
                for item in pruned:
                    print(f"• Pruned {item.get('slug')}: {item.get('title')} ({item.get('tool_path')})")
            return 0

        if args.name and args.steps:
            entry = forge.forge_custom_pipeline(
                name=args.name,
                steps=args.steps,
                trigger_pattern=args.trigger,
            )
            if args.json:
                print(json.dumps(entry, ensure_ascii=False, indent=2))
            else:
                print(f"🔨 Forged & Verified CLI Tool: {entry['slug']} -> {entry['tool_path']}")
                print(f"🔗 Auto-bound to Centsible fact [{entry['fact_id']}] (trigger: /{entry['trigger_pattern']}/)")
            return 0

        if args.transcript:
            forged = forge.forge_from_transcript(
                transcript_path=args.transcript,
                min_occurrences=args.min_occurrences,
            )
            if args.json:
                print(json.dumps(forged, ensure_ascii=False, indent=2))
            else:
                print(f"🔨 Mined transcript & forged {len(forged)} verified CLI tools:")
                for item in forged:
                    print(f"  • {item['slug']} ({ len(item['steps']) } steps, seen {item['occurrences']}x) -> {item['tool_path']}")
            return 0

        parser.error("Provide --transcript PATH, --name NAME --steps ..., or --list")

    if args.subcommand == "verify":
        verifier = ShadowVerifier(workspace_dir=args.path)
        report = verifier.verify_workspace(run_tests=args.run_tests)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            verdict = "✅ PASSED" if report["passed"] else "❌ BLOCKED"
            print(
                f"=== Overmind Adversarial Shadow Verifier: {verdict} ===\n"
                f"• Files Checked: {report['files_checked']} | Integrity Score: {report['integrity_score']}/100"
            )
            for iss in report["issues"]:
                print(f"  [!] {iss['kind']} in {iss['file']}:{iss['line']} — {iss['detail']}")
        return 0 if report["passed"] else 1

    if args.subcommand == "scorecard":
        data = compute_evolution_telemetry(bridge=bridge)
        if args.svg:
            render_svg_card(data, output_path=args.svg)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif args.markdown:
            print(render_markdown_badge(data))
        else:
            print(render_terminal_scorecard(data))
            if args.svg:
                print(f"\n✨ Saved viral SVG scorecard to: {args.svg}")
        return 0

    if args.subcommand == "dna":
        pool = DarwinianGenePool(bridge=bridge)
        if args.action == "export":
            out = pool.export_dna(output_path=args.path, include_active=args.include_active)
            print(f"🧬 Exported Overmind DNA to {out}")
            return 0
        count = pool.import_dna(dna_path=args.path)
        print(f"🧬 Imported {count} genes from {args.path} into Centsible Reflex Kernel")
        return 0

    if args.subcommand == "hook":
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        payload = {}
        if raw.strip():
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = {}

        # 1. Auto-evolve Darwinian Gene Pool on session stop
        pool = DarwinianGenePool(bridge=bridge)
        pool.evolve_generation(dry_run=False)

        # 2. If transcript_path is provided in hook payload, auto-forge recurring CLI tools
        t_path_str = payload.get("transcript_path") or payload.get("transcriptPath")
        if t_path_str:
            t_path = Path(str(t_path_str)).expanduser()
            if t_path.exists():
                forge = AutopoieticToolForge(bridge=bridge)
                forge.forge_from_transcript(t_path, min_occurrences=2)

        sys.stdout.write("{}\n")
        sys.stdout.flush()
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
