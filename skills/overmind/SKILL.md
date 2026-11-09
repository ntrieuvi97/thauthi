---
name: overmind
description: >-
  Self-evolving meta-kernel for AI coding agents (Antigravity & Claude Code) —
  Autopoietic Tool Forge, Darwinian Gene Pool, Adversarial Verification & Viral Scorecard.
---

# Overmind Agent Skill 🧠⚡

Overmind is the self-evolving meta-kernel for AI agents. It continuously optimizes agent performance by mining recurring multi-step workflows into verified CLI tools, scoring skills via Darwinian natural selection, and verifying diffs with an Adversarial Shadow Verifier.

## When to Use
- User asks about agent evolution, IQ, or scorecard (`overmind status`, `overmind scorecard`).
- User wants to synthesize a multi-step verification/build pipeline (`overmind forge`).
- User wants to prune noisy or dead tools (`overmind forge --prune`).
- User wants to run natural selection on agent habits/skills (`overmind evolve`, `overmind fitness`).
- Inspecting workspace integrity, syntax errors, debug traps (`breakpoint()`, `pdb`), or secrets before completing a task (`overmind verify`).
- Sharing or importing team-wide Elite DNA (`overmind dna export`, `overmind dna import`).

## Core Commands

### 1. Inspect Evolution Scorecard & Agent IQ
```bash
overmind status
overmind status --all   # Swarm mode across all repositories
overmind scorecard --markdown
overmind scorecard --svg overmind-scorecard.svg
```

### 2. Autopoietic Tool Forge
Synthesize standalone, self-verifying, parameterized Python CLI tools:
```bash
# Forge custom multi-step pipeline tool with trigger
overmind forge --name fast_check \
  --steps "python3 -m py_compile overmind/*.py" "pytest tests/ -q --tb=short" \
  --trigger "verify.{0,20}(code|suite|tests)"

# List all forged tools
overmind forge --list

# Prune noisy inspection tools (ls, which, cat, etc.) or dead tools
overmind forge --prune
overmind forge --prune --dead
overmind forge --prune --slug <tool_slug>
```

### 3. Darwinian Gene Pool & Natural Selection
```bash
# Rank all skills/facts by Darwinian Fitness (0..100%)
overmind fitness

# Trigger evolution cycle (mutates recessive genes, banishes extinct)
overmind evolve
overmind evolve --dry-run
```

### 4. Adversarial Shadow Verifier
```bash
# Check syntax compilation, debug traps (breakpoint, pdb), and secret leaks
overmind verify

# Also run test suite
overmind verify --run-tests
```

### 5. Swarm Intelligence & Team DNA
```bash
# Export Elite DNA (Fitness >= 72%) to share with team
overmind dna export --path .overmind/dna.jsonl

# Import DNA into local reflex kernel
overmind dna import --path .overmind/dna.jsonl
```
