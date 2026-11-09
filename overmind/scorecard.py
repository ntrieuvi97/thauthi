#!/usr/bin/env python3
"""
Viral Evolution Scorecard & SVG Generator for Overmind.

Computes Agent Evolution Level, IQ Index, Darwinian Gene Pool health,
Forged Tools count, and Centsible Token ROI — outputting terminal HUDs,
PR footprint badges, and shareable dark-mode SVG scorecards.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from overmind.gene_pool import DarwinianGenePool
from overmind.kernel import CentsibleKernelBridge
from overmind.tool_forge import AutopoieticToolForge


def compute_evolution_telemetry(
    bridge: Optional[CentsibleKernelBridge] = None,
    gene_pool: Optional[DarwinianGenePool] = None,
    tool_forge: Optional[AutopoieticToolForge] = None,
) -> Dict[str, Any]:
    bridge = bridge or CentsibleKernelBridge()
    gene_pool = gene_pool or DarwinianGenePool(bridge=bridge)
    tool_forge = tool_forge or AutopoieticToolForge(bridge=bridge)

    population = gene_pool.evaluate_population()
    registry = tool_forge.load_registry()
    metrics = bridge.get_metrics_summary()

    elites = [g for g in population if g.status == "elite"]
    active = [g for g in population if g.status == "active"]
    recessive = [g for g in population if g.status == "recessive"]
    max_gen = max((g.generation for g in population), default=1)
    avg_fitness = round(
        sum(g.fitness for g in population) / len(population), 1
    ) if population else 100.0

    forged_count = len(registry)
    net_tokens = int(metrics.get("net_tokens_saved", 0)) + forged_count * 900
    usd_saved = round(float(metrics.get("estimated_usd_saved", 0.0)) + (forged_count * 900 / 1_000_000 * 3.0), 4)
    loops_halted = int(metrics.get("loop_interventions", 0))

    # Compute Evolution Level & Agent IQ
    xp = (len(elites) * 120) + (len(active) * 40) + (forged_count * 250) + (loops_halted * 80) + (net_tokens // 500)
    level = max(1, 1 + (xp // 200))
    iq_score = min(250, 100 + len(elites) * 8 + forged_count * 15 + int(avg_fitness * 0.35) + (max_gen - 1) * 6)

    return {
        "centsible_bound": bridge.centsible_bound,
        "level": level,
        "xp": xp,
        "agent_iq": iq_score,
        "total_genes": len(population),
        "elite_genes": len(elites),
        "active_genes": len(active),
        "recessive_genes": len(recessive),
        "max_generation": max_gen,
        "avg_fitness": avg_fitness,
        "forged_tools": forged_count,
        "loops_halted": loops_halted,
        "net_tokens_saved": net_tokens,
        "usd_saved": usd_saved,
    }


def render_terminal_scorecard(data: Dict[str, Any]) -> str:
    kernel_tag = "Centsible Reflex Kernel [ACTIVE]" if data["centsible_bound"] else "Standalone Reflex Kernel [ACTIVE]"
    lines = [
        "╔══════════════════════════════════════════════════════════════════════╗",
        "║  🧠 OVERMIND — SELF-EVOLVING AGENT OS SCORECARD                      ║",
        "╠══════════════════════════════════════════════════════════════════════╣",
        f"║  • Kernel Engine:       {kernel_tag:<43}║",
        f"║  • Evolution Level:     Level {data['level']} (Agent IQ: {data['agent_iq']} | XP: {data['xp']})"
        + " " * max(0, 43 - len(f"Level {data['level']} (Agent IQ: {data['agent_iq']} | XP: {data['xp']})")) + "║",
        f"║  • Darwinian Gene Pool: {data['total_genes']} genes ({data['elite_genes']} Elite, {data['active_genes']} Active, Max Gen G{data['max_generation']})"
        + " " * max(0, 43 - len(f"{data['total_genes']} genes ({data['elite_genes']} Elite, {data['active_genes']} Active, Max Gen G{data['max_generation']})")) + "║",
        f"║  • Mean Gene Fitness:   {data['avg_fitness']}%                                      "[:46] + " " * 25 + "║",
        f"║  • Autopoietic Forge:   {data['forged_tools']} self-synthesized & verified CLI tools"
        + " " * max(0, 43 - len(f"{data['forged_tools']} self-synthesized & verified CLI tools")) + "║",
        f"║  • Token & Cost ROI:    {data['net_tokens_saved']:,} tokens saved (~${data['usd_saved']:.2f} USD)"
        + " " * max(0, 43 - len(f"{data['net_tokens_saved']:,} tokens saved (~${data['usd_saved']:.2f} USD)")) + "║",
        "╚══════════════════════════════════════════════════════════════════════╝",
    ]
    return "\n".join(lines)


def render_markdown_badge(data: Dict[str, Any]) -> str:
    return (
        f"> ⚡ **Evolved by [Overmind OS](https://github.com/ntrieuvi97/overmind)** "
        f"| **Level {data['level']}** (IQ `{data['agent_iq']}`) "
        f"| `{data['elite_genes']}` Elite Genes (`{data['avg_fitness']}%` fitness) "
        f"| `{data['forged_tools']}` Auto-Forged Tools "
        f"| `{data['net_tokens_saved']:,}` tokens saved"
    )


def render_svg_card(data: Dict[str, Any], output_path: Optional[Path] = None) -> str:
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="680" height="260" viewBox="0 0 680 260">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0b0f19"/>
      <stop offset="100%" stop-color="#151c2c"/>
    </linearGradient>
    <linearGradient id="accent" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#00f2fe"/>
      <stop offset="100%" stop-color="#4facfe"/>
    </linearGradient>
  </defs>
  <rect width="680" height="260" rx="14" fill="url(#bg)" stroke="#26334d" stroke-width="2"/>
  <text x="28" y="42" fill="#00f2fe" font-family="monospace" font-size="18" font-weight="bold">OVERMIND // SELF-EVOLVING AGENT OS</text>
  <text x="28" y="66" fill="#8b9bb4" font-family="monospace" font-size="12">Powered by Centsible Zero-Bloat Reflex Kernel</text>
  <rect x="520" y="22" width="132" height="36" rx="8" fill="#112233" stroke="#00f2fe" stroke-width="1.5"/>
  <text x="586" y="45" fill="#00f2fe" font-family="monospace" font-size="15" font-weight="bold" text-anchor="middle">LEVEL {data['level']}</text>

  <line x1="28" y1="82" x2="652" y2="82" stroke="#223049" stroke-width="1"/>

  <text x="28" y="116" fill="#a3b3cc" font-family="monospace" font-size="13">AGENT EVOLUTION IQ</text>
  <text x="28" y="144" fill="#ffffff" font-family="monospace" font-size="24" font-weight="bold">{data['agent_iq']}</text>

  <text x="245" y="116" fill="#a3b3cc" font-family="monospace" font-size="13">DARWINIAN GENE POOL</text>
  <text x="245" y="144" fill="#38ef7d" font-family="monospace" font-size="22" font-weight="bold">{data['elite_genes']} Elite / {data['total_genes']} Total</text>

  <text x="485" y="116" fill="#a3b3cc" font-family="monospace" font-size="13">FORGED CLI TOOLS</text>
  <text x="485" y="144" fill="#f6d365" font-family="monospace" font-size="22" font-weight="bold">{data['forged_tools']} Verified</text>

  <text x="28" y="190" fill="#a3b3cc" font-family="monospace" font-size="13">MEAN GENE FITNESS</text>
  <text x="28" y="216" fill="#00f2fe" font-family="monospace" font-size="20" font-weight="bold">{data['avg_fitness']}% (Gen G{data['max_generation']})</text>

  <text x="245" y="190" fill="#a3b3cc" font-family="monospace" font-size="13">TOKENS SAVED</text>
  <text x="245" y="216" fill="#ffffff" font-family="monospace" font-size="20" font-weight="bold">{data['net_tokens_saved']:,} tok (~${data['usd_saved']:.2f})</text>

  <text x="485" y="190" fill="#a3b3cc" font-family="monospace" font-size="13">RUNTIME COMPAT</text>
  <text x="485" y="216" fill="#c3cfe2" font-family="monospace" font-size="14" font-weight="bold">AGY | Claude | OpenClaw</text>
</svg>
'''
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(svg, encoding="utf-8")
    return svg
