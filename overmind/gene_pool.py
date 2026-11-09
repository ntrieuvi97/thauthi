#!/usr/bin/env python3
"""
Darwinian Gene Pool for Overmind.

Treats every Centsible micro-fact and forged tool skill as a living evolutionary gene:
- Computes Bayesian-smoothed Fitness Scores (0..100) from real agent execution telemetry.
- Mutates underperforming ("recessive") genes by tightening noisy regex triggers and
  sharpening vague directives into executable imperative contracts.
- Drives natural selection: promotes high-fitness genes to Elite DNA (.overmind/dna.jsonl)
  and banishes multi-generation failures to the Graveyard (~/.overmind/graveyard.jsonl).
"""

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from overmind.kernel import CentsibleKernelBridge, get_overmind_home

DEFAULT_MIN_TRIALS = 5
ELITE_FITNESS_THRESHOLD = 72.0
RECESSIVE_FITNESS_THRESHOLD = 38.0
EXTINCT_GENERATION_THRESHOLD = 2


@dataclass
class Gene:
    gene_id: str
    pattern: str
    directive: str
    category: str
    generation: int
    parent_id: Optional[str]
    shown: int
    applied: int
    post_failures: int
    saved_tokens: int
    fitness: float
    status: str  # "elite" | "active" | "recessive" | "extinct"
    mutation_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def compute_darwinian_fitness(
    shown: int,
    applied: int,
    post_failures: int = 0,
    saved_tokens: int = 0,
    min_trials: int = DEFAULT_MIN_TRIALS,
) -> float:
    """
    Calculate evolutionary fitness in [0.0, 100.0].

    Uses Laplace-smoothed adoption rate, post-injection reliability, and token ROI:
      Adoption  = (applied + 1) / (shown + 2)
      Success   = (applied + 1) / (applied + post_failures + 2)
      TokenROI  = min(1.25, avg_saved_tokens / 300.0)
    When shown >= min_trials and applied == 0, applies a non-adoption penalty so chronically
    ignored hints become recessive and trigger mutation.
    """
    shown = max(0, int(shown))
    applied = max(0, min(shown, int(applied)))
    post_failures = max(0, int(post_failures))
    saved_tokens = max(0, int(saved_tokens))

    adoption_rate = (applied + 1.0) / (shown + 2.0)
    success_rate = (applied + 1.0) / (applied + post_failures + 2.0)
    avg_saved = (saved_tokens / max(1, shown)) if shown > 0 else 150.0
    token_roi = min(1.25, avg_saved / 300.0)

    raw_score = 100.0 * (0.50 * adoption_rate + 0.35 * success_rate + 0.15 * token_roi)

    if shown >= min_trials and applied == 0:
        # Decay score progressively as unapplied exposures accumulate
        decay = max(0.30, 1.0 - 0.08 * (shown - min_trials + 1))
        raw_score *= decay

    return round(max(0.0, min(100.0, raw_score)), 1)


def classify_gene_status(
    fitness: float,
    shown: int,
    applied: int,
    generation: int,
    min_trials: int = DEFAULT_MIN_TRIALS,
) -> str:
    """Classify a gene into elite, active, recessive, or extinct."""
    if shown >= min_trials * 2 and applied == 0 and generation >= EXTINCT_GENERATION_THRESHOLD:
        return "extinct"
    if shown >= min_trials and (fitness < RECESSIVE_FITNESS_THRESHOLD or applied == 0):
        return "recessive"
    if fitness >= ELITE_FITNESS_THRESHOLD and applied >= 2:
        return "elite"
    return "active"


def mutate_pattern_and_directive(
    pattern: str,
    directive: str,
    generation: int,
) -> Tuple[str, str, str]:
    """
    Deterministically mutate a recessive gene's regex trigger and imperative directive.
    Returns (mutated_pattern, mutated_directive, mutation_reason).
    """
    reasons: List[str] = []
    new_pattern = pattern.strip()
    new_directive = directive.strip()

    # 1. Tighten overly broad wildcard spans (.{0,40} -> .{0,18}, .* -> .{0,20})
    if re.search(r"\.\{0,(?:3\d|4\d|5\d|[6-9]\d|\d{3,})\}", new_pattern):
        new_pattern = re.sub(r"\.\{0,(?:3\d|4\d|5\d|[6-9]\d|\d{3,})\}", ".{0,18}", new_pattern)
        reasons.append("tightened wildcard span to .{0,18}")
    elif ".*" in new_pattern and new_pattern != "*":
        new_pattern = new_pattern.replace(".*", ".{0,20}")
        reasons.append("bounded unbounded '.*' wildcard to .{0,20}")

    # 2. If pattern is a single bare word/path without context, anchor on action/error context
    if (
        not re.search(r"\.\{0,\d+\}", new_pattern)
        and "|" not in new_pattern
        and new_pattern != "*"
        and len(new_pattern) < 35
    ):
        new_pattern = f"{new_pattern}.{{0,24}}(run|test|fail|error|check|build)"
        reasons.append("anchored bare trigger with action/symptom context")

    # 3. Sharpen directive into an explicit quoted command prescription so Centsible's
    # attribution detector and the agent immediately recognize the executable action.
    quoted_cmd = re.search(r"`([^`]+)`|'([^']+)'", new_directive)
    if not quoted_cmd:
        # Look for common CLI command tokens inside the directive, stopping before prose connectors
        cmd_match = re.search(
            r"\b((?:pytest|python3?|npm|pnpm|yarn|cargo|go|git|uv|poetry|make|overmind|centsible)"
            r"(?:\s+(?!(?:for|to|when|if|in|on|with|before|after|because|so)\b)[A-Za-z0-9_./:=-]+)+)",
            new_directive,
        )
        if cmd_match:
            cmd_str = cmd_match.group(1).strip()
            new_directive = new_directive.replace(cmd_str, f"'{cmd_str}'", 1)
            reasons.append(f"quoted executable prescription '{cmd_str}'")

    if not new_directive.upper().startswith("MUST RUN:"):
        new_directive = re.sub(r"^run\s+", "", new_directive, flags=re.IGNORECASE)
        new_directive = f"MUST RUN: {new_directive}"
        reasons.append("prefixed strict imperative header")

    if len(new_directive) > 235:
        new_directive = new_directive[:232].rstrip() + "..."

    reason_str = "; ".join(reasons) if reasons else f"generation {generation + 1} adaptive reinforcement"
    return new_pattern, new_directive, reason_str


class DarwinianGenePool:
    """
    Manages the evolutionary lifecycle of agent skills/facts inside Centsible.
    """

    def __init__(
        self,
        bridge: Optional[CentsibleKernelBridge] = None,
        min_trials: int = DEFAULT_MIN_TRIALS,
        home_dir: Optional[Path] = None,
    ):
        self.bridge = bridge or CentsibleKernelBridge()
        self.min_trials = min_trials
        self.home_dir = home_dir or get_overmind_home()
        self.lineage_path = self.home_dir / "lineage.jsonl"
        self.graveyard_path = self.home_dir / "graveyard.jsonl"

    def _load_lineage_map(self) -> Dict[str, Dict[str, Any]]:
        """Map gene_id -> lineage metadata (generation, parent_id, mutation_note)."""
        if not self.lineage_path.exists():
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for line in self.lineage_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                gid = rec.get("gene_id")
                if gid:
                    out[gid] = rec
            except ValueError:
                continue
        return out

    def _record_lineage(self, record: Dict[str, Any]) -> None:
        self.lineage_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lineage_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _record_graveyard(self, gene: Gene, reason: str) -> None:
        self.graveyard_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            **gene.to_dict(),
            "extinct_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "extinction_reason": reason,
        }
        with open(self.graveyard_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def evaluate_population(self) -> List[Gene]:
        """
        Inspect all facts in the Centsible kernel + event logs, returning ranked Gene objects.
        """
        raw_facts = self.bridge.list_facts(scope="project")
        events = self.bridge.read_events()
        lineage_map = self._load_lineage_map()

        # Aggregate event-level telemetry per fact_id
        ev_shown: Dict[str, int] = {}
        ev_applied: Dict[str, int] = {}
        ev_failures: Dict[str, int] = {}

        last_shown_ids: List[str] = []
        for ev in events:
            kind = ev.get("event", "")
            fids = ev.get("fact_ids") or []
            if kind in ("prompt_inject", "hint"):
                for fid in fids:
                    ev_shown[fid] = ev_shown.get(fid, 0) + 1
                last_shown_ids = list(fids)
            elif kind == "applied":
                for fid in fids:
                    ev_applied[fid] = ev_applied.get(fid, 0) + 1
            elif kind in ("loop_block", "error", "tool_error"):
                for fid in last_shown_ids:
                    ev_failures[fid] = ev_failures.get(fid, 0) + 1

        population: List[Gene] = []
        for f in raw_facts:
            gid = str(f.get("id") or "")
            if not gid:
                continue
            lin = lineage_map.get(gid, {})
            generation = int(f.get("generation") or lin.get("generation") or 1)
            parent_id = f.get("parent_id") or lin.get("parent_id")

            shown = max(int(f.get("hits", 0)), ev_shown.get(gid, 0))
            applied = max(int(f.get("attributed_hits", 0)), ev_applied.get(gid, 0))
            failures = int(f.get("post_failures", 0)) + ev_failures.get(gid, 0)
            saved_tokens = int(f.get("saved_tokens", shown * int(f.get("est_savings", 350))))

            fitness = compute_darwinian_fitness(
                shown=shown,
                applied=applied,
                post_failures=failures,
                saved_tokens=saved_tokens,
                min_trials=self.min_trials,
            )
            status = classify_gene_status(
                fitness=fitness,
                shown=shown,
                applied=applied,
                generation=generation,
                min_trials=self.min_trials,
            )
            population.append(
                Gene(
                    gene_id=gid,
                    pattern=str(f.get("pattern", "")),
                    directive=str(f.get("fact", "")),
                    category=str(f.get("category", "general")),
                    generation=generation,
                    parent_id=parent_id,
                    shown=shown,
                    applied=applied,
                    post_failures=failures,
                    saved_tokens=saved_tokens,
                    fitness=fitness,
                    status=status,
                    mutation_note=str(lin.get("mutation_note", "")),
                )
            )

        population.sort(key=lambda g: (g.fitness, g.applied, g.saved_tokens), reverse=True)
        return population

    def evolve_generation(self, dry_run: bool = False) -> Dict[str, Any]:
        """
        Execute one full Darwinian evolution cycle:
        1. Mutate `recessive` genes into next-generation stronger variants.
        2. Prune `extinct` multi-generation failures into the Graveyard.
        3. Identify `elite` genes ready for cross-repo DNA propagation.
        """
        population = self.evaluate_population()
        mutated: List[Dict[str, Any]] = []
        extinct: List[Dict[str, Any]] = []
        elites: List[Dict[str, Any]] = []

        for gene in population:
            if gene.status == "elite":
                elites.append(gene.to_dict())
            elif gene.status == "extinct":
                extinct.append(gene.to_dict())
                if not dry_run:
                    self.bridge.remove_fact(gene.gene_id)
                    self._record_graveyard(
                        gene,
                        reason=f"Gen {gene.generation} unapplied after {gene.shown} exposures (fitness {gene.fitness})",
                    )
            elif gene.status == "recessive":
                new_pat, new_dir, reason = mutate_pattern_and_directive(
                    gene.pattern, gene.directive, gene.generation
                )
                new_gen = gene.generation + 1
                digest = hashlib.sha1(f"{new_pat}:{new_dir}:{new_gen}".encode("utf-8")).hexdigest()[:8]
                new_id = f"mut_g{new_gen}_{digest}"

                mutation_event = {
                    "parent_id": gene.gene_id,
                    "gene_id": new_id,
                    "generation": new_gen,
                    "old_pattern": gene.pattern,
                    "new_pattern": new_pat,
                    "old_directive": gene.directive,
                    "new_directive": new_dir,
                    "old_fitness": gene.fitness,
                    "mutation_note": reason,
                    "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                }
                mutated.append(mutation_event)

                if not dry_run:
                    self.bridge.remove_fact(gene.gene_id)
                    self.bridge.add_fact(
                        pattern=new_pat,
                        fact=new_dir,
                        category=gene.category,
                        fact_id=new_id,
                        force=True,
                    )
                    self._record_lineage(mutation_event)

        return {
            "total_evaluated": len(population),
            "elites_count": len(elites),
            "mutated_count": len(mutated),
            "extinct_count": len(extinct),
            "elites": elites,
            "mutations": mutated,
            "extinct": extinct,
            "dry_run": dry_run,
        }

    def export_dna(self, output_path: Optional[Path] = None, include_active: bool = False) -> Path:
        """
        Export Elite (and optionally Active) genes to a portable `.overmind/dna.jsonl` file
        so any repo or teammate can inherit the evolved intelligence.
        """
        target = output_path or (Path.cwd() / ".overmind" / "dna.jsonl")
        target.parent.mkdir(parents=True, exist_ok=True)
        population = self.evaluate_population()
        allowed = {"elite", "active"} if include_active else {"elite"}
        selected = [g for g in population if g.status in allowed]

        lines = [json.dumps(g.to_dict(), ensure_ascii=False) for g in selected]
        target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return target

    def import_dna(self, dna_path: Path) -> int:
        """Import portable DNA genes into the local Centsible Reflex Kernel."""
        if not dna_path.exists():
            return 0
        imported = 0
        for line in dna_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                pattern = rec.get("pattern")
                directive = rec.get("directive") or rec.get("fact")
                if pattern and directive:
                    gid = rec.get("gene_id") or rec.get("id")
                    self.bridge.add_fact(
                        pattern=pattern,
                        fact=directive,
                        category=rec.get("category", "general"),
                        fact_id=gid,
                        force=True,
                    )
                    imported += 1
            except ValueError:
                continue
        return imported
