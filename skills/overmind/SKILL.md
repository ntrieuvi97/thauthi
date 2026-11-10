---
name: overmind
description: >-
  Overmind 3.0: The Post-Developer Singularity Kernel for AI coding agents (Antigravity & Claude Code) —
  Omniscient Runtime Tracer, In-Flight Self-Healing AST Surgery, Differential Shadow Prover,
  Symbolic AST Crystallizer, Autonomous O(N^2)->O(N) Alchemist, Chronos I/O Time-Travel,
  Contract-to-Code CEGIS Synthesizer & Whole-Repo Call-Graph Hive.
---

# Overmind 3.0 Agent Skill 🧠⚡

Overmind 3.0 moves AI coding agents beyond the "Static Text Guessing Era" into the **Post-Developer Runtime & Symbolic Compiler Singularity** via **8 Zero-Dependency Pillars**:
1. **Omniscient Runtime Tracer (`overmind trace`):** Hooks `sys.settrace` to record live variable mutation timelines (`FrameDNA`) and Backward Dynamic Slicing (`CausalFaultReport`) when crashes occur.
2. **In-Flight Self-Healing Immune System (`overmind heal` / `@overmind.immune`):** Intercepts unhandled exceptions in-flight, cryo-freezes call frames into `pytest` regression tests, synthesizes & verifies Counterfactual AST Patches in-memory against historical healthy frames, and hot-swaps the healed code without crashing.
3. **Behavioral Invariant Miner & Differential Shadow Prover (`overmind prove`):** Mines Daikon-style runtime contracts and empirical $O(N)$ step complexity (`O(1)`, `O(log N)`, `O(N)`, `O(N^2)+`), running Old-AST vs. New-AST Differential Shadow Execution to mathematically replace manual human code review.
4. **Neural-to-Symbolic AST Crystallizer (`overmind crystallize`):** Abstracts neural code edits (`before -> after`) into metavariable AST patterns (`__OM_V1__`) and compiles them into deterministic `<2ms`, `0-token` Symbolic AST Codemods.
5. **Algorithmic Alchemist (`overmind optimize`):** Autonomously rewrites $O(N^2)$ nested membership/equality loops into $O(N)$ hash-set lookups and injects inline memoization into $O(2^N)$ recursive functions, mathematically proving 100% equivalence + empirical speedup via `DifferentialShadowProver`.
6. **Chronos I/O Time-Travel Kernel (`overmind chronos` / `@overmind.time_travel`):** Intercepts network (`urllib`), clock (`time.time`), RNG (`random`), env, and external database/API helper boundaries into hermetic `ExecutionCassette`s (`.overmind/cassettes/`), replaying and healing stateful/impure functions in `<1ms` with zero side effects.
7. **Contract-to-Code CEGIS Synthesizer (`overmind synthesize` / `@overmind.synthesize`):** Write only a function signature + docstring `>>>` doctests (with `pass` or `...` as the body) and Overmind's Counterexample-Guided Inductive Synthesis engine synthesizes, proves, and writes the pure $O(N)$ / $O(1)$ implementation automatically.
8. **Whole-Repo Call-Graph Hive (`overmind hive`):** Indexes all function signatures and call sites across the repository, automatically repairing broken callers when parameter names are renamed, required arguments are added, or deprecated positional arguments are removed.

## Core Commands

```bash
# 0. Onboard Any Repository (Hooks, CI, Rules, and `.mcp.json`)
overmind init

# 0b. Start Zero-Dependency MCP Server for Native Agent Tool Calling
overmind mcp --root .

# 1. Omniscient Runtime Tracer & Causal Fault Graph
overmind trace path/to/script.py

# 2. In-Flight Self-Healing Immune System & AST Surgery (`safe-fallback` or strict `shadow-pr` diff mode)
overmind heal path/to/script.py --apply
overmind heal path/to/payment.py --mode shadow-pr --json

# 3. Behavioral Invariant Miner & Differential Shadow Prover
overmind prove --mine app.py
overmind prove --before old_version.py --after new_version.py

# 4. Neural-to-Symbolic AST Crystallizer (0-Token Codemods)
overmind crystallize --name safe_dict_get --before "data[key]" --after "data.get(key, None)"
overmind crystallize --apply .

# 5. Algorithmic Alchemist (Autonomous O(N^2)->O(N) & O(2^N)->O(N) Optimizer)
overmind optimize slow_module.py --apply

# 6. Chronos I/O Virtualization & Time-Travel Replay
overmind chronos --script stateful_worker.py
overmind chronos --list

# 7. Contract-to-Code CEGIS Program Synthesizer
overmind synthesize specs.py --apply

# 8. Whole-Repo Call-Graph Ripple Propagator
overmind hive . --apply
```
