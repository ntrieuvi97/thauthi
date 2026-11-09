---
name: overmind
description: >-
  Overmind 2.0: The Omniscient Autopoietic Kernel for AI coding agents (Antigravity & Claude Code) —
  Omniscient Runtime Tracer, In-Flight Self-Healing AST Surgery, Differential Shadow Prover,
  Neural-to-Symbolic AST Crystallizer, Darwinian Gene Pool & Autopoietic Tool Forge.
---

# Overmind 2.0 Agent Skill 🧠⚡

Overmind 2.0 moves AI coding agents beyond the "Static Text Guessing Era" into the **Omniscient Runtime & Symbolic Compiler Era**:
1. **Omniscient Runtime Tracer (`overmind trace`):** Hooks directly into `sys.settrace` to record live variable mutation timelines (`FrameDNA`) and Backward Dynamic Slicing (`CausalFaultReport`) when crashes occur.
2. **In-Flight Self-Healing Immune System (`overmind heal` / `@overmind.immune`):** Intercepts unhandled exceptions in-flight, cryo-freezes call frames into `pytest` regression tests, synthesizes & verifies Counterfactual AST Patches in-memory against historical healthy frames, and hot-swaps the healed code without crashing.
3. **Behavioral Invariant Miner & Differential Shadow Prover (`overmind prove`):** Mines Daikon-style runtime contracts and empirical $O(N)$ step complexity (`O(1)`, `O(log N)`, `O(N)`, `O(N^2)+`), running Old-AST vs. New-AST Differential Shadow Execution to mathematically replace manual human code review.
4. **Neural-to-Symbolic AST Crystallizer (`overmind crystallize`):** Abstracts neural code edits (`before -> after`) into metavariable AST patterns (`__OM_V1__`) and compiles them into deterministic `<2ms`, `0-token` Symbolic AST Codemods.

## Core Commands

### 0. Onboard Any Repository (`overmind init`)
```bash
overmind init
overmind init /path/to/repo --no-ci
```

### 1. Omniscient Runtime Tracer & Causal Fault Graph
```bash
overmind trace path/to/script.py
overmind trace path/to/script.py --json
```

### 2. In-Flight Self-Healing Immune System & AST Surgery
```bash
# Run script under Autopoietic Immune System (generates regression test + hot-heals crashes)
overmind heal path/to/script.py
overmind heal path/to/script.py --apply   # Also write verified AST patch back to source file
```
Or in Python code:
```python
from overmind import immune

@immune(auto_heal=True, persist_test=True)
def compute_ratio(revenue: float, users: int) -> float:
    return revenue / users
```

### 3. Behavioral Invariant Miner & Differential Shadow Prover
```bash
# Mine behavioral contracts & empirical O(N) complexity from a file
overmind prove --mine app.py

# Mathematically prove equivalence, purity & complexity between Old AST and New AST
overmind prove --before old_version.py --after new_version.py
```

### 4. Neural-to-Symbolic AST Crystallizer (0-Token Codemods)
```bash
# Compile a recurring code edit into a deterministic Symbolic AST Crystal
overmind crystallize --name safe_dict_get \
  --before "data[key]" \
  --after "data.get(key, None)"

# Apply all registered Symbolic AST Crystals across a file or repository in <2ms (0 tokens)
overmind crystallize --apply .
overmind crystallize --list
```

### 5. Darwinian Gene Pool, Tool Forge & Scorecard
```bash
overmind status
overmind evolve
overmind forge --list
overmind verify --run-tests
overmind scorecard --svg overmind-scorecard.svg
```
