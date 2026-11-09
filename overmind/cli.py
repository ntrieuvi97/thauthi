#!/usr/bin/env python3
"""
Overmind CLI — Unified command-line interface for the Self-Evolving Agent OS.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from overmind.gene_pool import DarwinianGenePool
from overmind.kernel import CentsibleKernelBridge
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
        description="Overmind: The Self-Evolving Meta-Kernel for AI Coding Agents (powered by Centsible).",
    )
    parser.add_argument("--facts-path", type=Path, default=None, help="Custom path to facts.jsonl")
    sub = parser.add_subparsers(dest="subcommand")

    # status
    p_status = sub.add_parser("status", help="Show Overmind kernel, gene pool, and tool forge status")
    p_status.add_argument("--all", action="store_true", help="Aggregate metrics and genes across all repositories")
    p_status.add_argument("--json", action="store_true", help="Output JSON")

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
