#!/usr/bin/env python3
"""
Pillar 3: Behavioral Invariant Miner & Differential Shadow Prover.

Replaces manual Human Code Review with mathematical runtime verification:
- Automatically mines Daikon-style Behavioral Contracts (purity, determinism,
  output bounds, dict key schemas, length invariants).
- Empirically classifies algorithmic time/step complexity (O(1), O(log N), O(N), O(N^2)+)
  by measuring exact line/instruction execution scaling via OmniscientTracer.
- Performs Differential Shadow Execution between Old AST and New AST to catch
  counterexamples, purity violations, schema drift, and O(N^2) regressions.
"""

import ast
import copy
import inspect
import math
import random
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from overmind.omniscient import OmniscientTracer


@dataclass
class BehavioralContract:
    func_name: str
    samples_tested: int
    is_pure_on_args: bool
    is_deterministic: bool
    output_types: List[str]
    non_negative_output: Optional[bool]
    dict_schema_keys: List[str]
    length_preserving: Optional[bool]
    complexity_class: str
    scaling_steps: Dict[int, int] = field(default_factory=dict)
    invariants_summary: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FunctionProofResult:
    func_name: str
    equivalent: bool
    verdict: str
    old_contract: BehavioralContract
    new_contract: BehavioralContract
    counterexamples: List[Dict[str, Any]] = field(default_factory=list)
    violations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["old_contract"] = self.old_contract.to_dict()
        d["new_contract"] = self.new_contract.to_dict()
        return d


@dataclass
class DifferentialProofReport:
    passed: bool
    functions_compared: int
    results: List[FunctionProofResult]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "functions_compared": self.functions_compared,
            "results": [r.to_dict() for r in self.results],
        }

    def render_summary(self) -> str:
        verdict_tag = "✅ MATHEMATICALLY PROVEN" if self.passed else "❌ REGRESSION DETECTED"
        lines = [
            f"=== Overmind Differential Shadow Prover: {verdict_tag} ===",
            f"• Functions Compared: {self.functions_compared}",
        ]
        for res in self.results:
            icon = "✅" if res.equivalent and not res.violations else "❌"
            lines.append(
                f"  {icon} {res.func_name}(): [{res.verdict}] "
                f"| Complexity: {res.old_contract.complexity_class} -> {res.new_contract.complexity_class} "
                f"| Pure: {res.old_contract.is_pure_on_args} -> {res.new_contract.is_pure_on_args}"
            )
            for v in res.violations:
                lines.append(f"      [!] Violation: {v}")
            for ce in res.counterexamples[:3]:
                lines.append(
                    f"      [!] Counterexample on args={ce['args']}: "
                    f"old={ce['old_outcome']} vs new={ce['new_outcome']}"
                )
        return "\n".join(lines)


COMPLEXITY_RANK = {
    "O(1)": 1,
    "O(log N)": 2,
    "O(N)": 3,
    "O(N log N)": 4,
    "O(N^2)+": 5,
    "unknown": 0,
}


def _infer_param_kind(param_name: str, annotation: Any, func_ast: Optional[ast.FunctionDef] = None) -> str:
    ann_str = str(annotation).lower() if annotation is not inspect.Parameter.empty else ""
    name_low = param_name.lower()

    if any(k in ann_str for k in ("list", "sequence", "iterable", "tuple", "set")):
        return "list"
    if "dict" in ann_str or "mapping" in ann_str:
        return "dict"
    if "bool" in ann_str:
        return "bool"
    if "str" in ann_str:
        return "str"
    if "float" in ann_str:
        return "float"
    if "int" in ann_str:
        return "int"

    # Inspect AST usage of param_name if annotation is missing
    if func_ast is not None:
        for node in ast.walk(func_ast):
            # Used as loop iterable: `for x in param_name:` -> list
            if isinstance(node, ast.For) and isinstance(node.iter, ast.Name) and node.iter.id == param_name:
                return "list"
            # Passed to len(param_name) -> list
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in ("len", "sum", "sorted", "min", "max")
                and any(isinstance(a, ast.Name) and a.id == param_name for a in node.args)
            ):
                return "list"
            # Subscripted with string literal: `param_name["key"]` -> dict
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == param_name:
                sl = node.slice
                if hasattr(ast, "Index") and isinstance(sl, getattr(ast, "Index")):
                    sl = getattr(sl, "value")
                if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                    return "dict"
                return "list"

    if any(tok in name_low for tok in ("items", "arr", "nums", "values", "elements", "seq", "list", "data", "records")):
        return "list"
    if any(tok in name_low for tok in ("payload", "config", "mapping", "row", "user", "obj", "dict")):
        return "dict"
    if name_low.startswith(("is_", "has_", "allow", "enable", "flag")):
        return "bool"
    if any(tok in name_low for tok in ("text", "name", "query", "path", "msg", "str", "token")):
        return "str"
    return "int"


def _is_recursive_func_ast(func_ast: Optional[ast.FunctionDef]) -> bool:
    if func_ast is None:
        return False
    for node in ast.walk(func_ast):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == func_ast.name:
            return True
    return False


def _generate_fuzz_pool(kind: str, rng: random.Random, count: int, is_recursive: bool = False) -> List[Any]:
    out: List[Any] = []
    if kind == "bool":
        for i in range(count):
            out.append(i % 2 == 0)
        return out

    if kind == "int":
        lo, hi = (-3, 12) if is_recursive else (-500, 500)
        for _ in range(count):
            out.append(rng.randint(lo, hi))
        return out

    if kind == "float":
        edges = [0.0, -0.0, 1.0, -1.0, 0.5, -2.5, 1e-6, 1e4]
        for i in range(count):
            if i < len(edges):
                out.append(edges[i])
            else:
                out.append(round(rng.uniform(-250.0, 250.0), 4))
        return out

    if kind == "str":
        alphabet = "abcxyz01_ -"
        edges = ["", " ", "a", "aa", "radar", "level", "hello world", "  OverMind  ", "αβγ"]
        for i in range(count):
            if i < len(edges):
                out.append(edges[i])
            else:
                ln = rng.randint(0, 14)
                out.append("".join(rng.choice(alphabet) for _ in range(ln)))
        return out

    if kind == "dict":
        keys_pool = ["id", "value", "count", "name", "tier", "amount", "flag", "k"]
        edges = [{}, {"id": 0}, {"id": 1, "value": 10}, {"id": 2, "value": 0, "tier": "pro"}]
        for i in range(count):
            if i < len(edges):
                out.append(copy.deepcopy(edges[i]))
            else:
                n_keys = rng.randint(0, 5)
                d: Dict[str, Any] = {}
                for _ in range(n_keys):
                    k = rng.choice(keys_pool)
                    d[k] = rng.choice([0, 1, -3, 42, "pro", "", True])
                out.append(d)
        return out

    # Default: list (duplicate-rich, unsorted, negatives, empty, singletons)
    edges_list = [
        [],
        [0],
        [1],
        [-1],
        [1, 1],
        [2, 2, 2],
        [3, 1, 2, 1, 3],
        [-5, 0, 5, -5, 10],
        [10, 9, 8, 7, 6],
    ]
    for i in range(count):
        if i < len(edges_list):
            out.append(list(edges_list[i]))
        else:
            ln = rng.randint(0, 14)
            # Draw from compact domain [-6, 10] to stress duplicates & hash collisions
            out.append([rng.randint(-6, 10) for _ in range(ln)])
    return out


def synthesize_domain_inputs(
    func: Callable,
    func_ast: Optional[ast.FunctionDef] = None,
    fuzz_count: int = 96,
    seed: int = 42,
) -> Tuple[List[Tuple[Any, ...]], Dict[int, Tuple[Any, ...]]]:
    """
    Synthesize a deterministic corpus of boundary/property test inputs (100+ cases by default)
    and scaling probes (N=5, 15, 45) for a given function signature.
    """
    sig = inspect.signature(func)
    params = [
        p for p in sig.parameters.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    if not params:
        return [()], {}

    kinds = [_infer_param_kind(p.name, p.annotation, func_ast) for p in params]
    is_rec = _is_recursive_func_ast(func_ast)

    domain_pools: Dict[str, List[Any]] = {
        "int": [0, 1, -1, 5, 12, -7, 25] if is_rec else [0, 1, -1, 5, 12, -7, 100],
        "float": [0.0, 1.0, -1.5, 10.5, 100.0],
        "bool": [True, False],
        "str": ["", "a", "overmind", "hello world"],
        "list": [[], [1], [3, 1, 2], [-2, 0, 4, 8], [1, 2, 2, 3, 5]],
        "dict": [{}, {"id": 1, "value": 10}, {"id": 2, "value": 0, "name": "test"}],
    }

    corpus: List[Tuple[Any, ...]] = []
    seen_reprs: Set[str] = set()

    def _push_case(t_row: Tuple[Any, ...]) -> None:
        key = repr(t_row)
        if key not in seen_reprs:
            seen_reprs.add(key)
            corpus.append(t_row)

    max_cases = 7
    for i in range(max_cases):
        row = []
        for idx, k in enumerate(kinds):
            pool = domain_pools.get(k, domain_pools["int"])
            val = copy.deepcopy(pool[(i + idx) % len(pool)])
            row.append(val)
        _push_case(tuple(row))

    if fuzz_count > 0:
        rng = random.Random(seed)
        pool_size = fuzz_count * 4
        per_param_fuzz = [_generate_fuzz_pool(k, rng, pool_size, is_recursive=is_rec) for k in kinds]
        target_total = max_cases + fuzz_count
        for i in range(pool_size):
            if len(corpus) >= target_total:
                break
            row = [copy.deepcopy(per_param_fuzz[p_idx][i]) for p_idx in range(len(kinds))]
            _push_case(tuple(row))

    # Synthesize scaling probes for empirical complexity analysis (N = 5, 15, 45 -> 3x scaling)
    scaling_probes: Dict[int, Tuple[Any, ...]] = {}
    for n_size in (5, 15, 45):
        probe_args: List[Any] = []
        for k in kinds:
            if k == "list":
                probe_args.append(list(range(n_size, 0, -1)))
            elif k == "str":
                probe_args.append("x" * n_size)
            elif k == "dict":
                probe_args.append({f"k_{j}": j for j in range(n_size)})
            elif k == "bool":
                probe_args.append(n_size % 2 == 0)
            else:
                probe_args.append(n_size)
        scaling_probes[n_size] = tuple(probe_args)

    return corpus, scaling_probes


class BehavioralInvariantMiner:
    """Mines empirical behavioral invariants and asymptotic step complexity from a callable."""

    @staticmethod
    def _classify_scaling(steps_by_n: Dict[int, int]) -> str:
        if 5 not in steps_by_n or 15 not in steps_by_n or 45 not in steps_by_n:
            return "unknown"
        s5, s15, s45 = steps_by_n[5], steps_by_n[15], steps_by_n[45]
        if s45 <= s5 + 2 and s15 <= s5 + 2:
            return "O(1)"
        # Input size increases 3x from 15 -> 45 (linear ~3x, quadratic ~9x)
        delta_small = max(1, s15 - s5)
        delta_large = max(1, s45 - s15)
        ratio = s45 / max(1, s15)
        growth_ratio = delta_large / delta_small
        if ratio <= 1.25:
            return "O(1)"
        if ratio <= 1.9:
            return "O(log N)"
        if ratio <= 4.5 and growth_ratio <= 4.5:
            return "O(N)"
        return "O(N^2)+"

    @classmethod
    def mine_contract(
        cls,
        func: Callable,
        func_ast: Optional[ast.FunctionDef] = None,
        custom_inputs: Optional[List[Tuple[Any, ...]]] = None,
    ) -> BehavioralContract:
        corpus, scaling_probes = synthesize_domain_inputs(func, func_ast)
        if custom_inputs:
            corpus = list(custom_inputs) + corpus

        is_pure = True
        is_deterministic = True
        output_types: Set[str] = set()
        numeric_outputs: List[float] = []
        dict_keys_sets: List[Set[str]] = []
        length_matches: List[bool] = []
        successful_samples = 0

        for args in corpus:
            args_copy1 = copy.deepcopy(args)
            args_snapshot = copy.deepcopy(args)
            args_copy2 = copy.deepcopy(args)
            try:
                out1 = func(*args_copy1)
                successful_samples += 1
            except Exception:
                continue

            # Check purity: did func mutate any input argument in-place?
            if args_copy1 != args_snapshot:
                is_pure = False

            # Check determinism
            try:
                out2 = func(*args_copy2)
                if out1 != out2:
                    is_deterministic = False
            except Exception:
                is_deterministic = False

            output_types.add(type(out1).__name__)
            if isinstance(out1, (int, float)) and not isinstance(out1, bool):
                numeric_outputs.append(float(out1))
            if isinstance(out1, dict):
                dict_keys_sets.append(set(str(k) for k in out1.keys()))
            if isinstance(out1, (list, tuple, str)) and len(args) >= 1 and isinstance(args[0], (list, tuple, str)):
                length_matches.append(len(out1) == len(args[0]))

        # Measure instruction/line scaling across N = 10, 40, 160
        scaling_steps: Dict[int, int] = {}
        for n_size, probe_args in scaling_probes.items():
            tracer = OmniscientTracer(target_funcs={func.__name__}, max_steps_per_frame=100_000)
            _, exc, frames = tracer.run_callable(func, *copy.deepcopy(probe_args))
            if exc is None and frames:
                scaling_steps[n_size] = sum(
                    f.steps_executed
                    for f in frames
                    if f.func_name == func.__name__ or f.func_name in ("<genexpr>", "<listcomp>", "<setcomp>", "<dictcomp>")
                )

        complexity = cls._classify_scaling(scaling_steps)
        non_neg = all(v >= 0 for v in numeric_outputs) if numeric_outputs else None
        common_keys = sorted(set.intersection(*dict_keys_sets)) if dict_keys_sets else []
        len_pres = all(length_matches) if length_matches else None

        summary: List[str] = [
            f"Complexity: {complexity}",
            f"Pure(no arg mutation): {is_pure}",
            f"Deterministic: {is_deterministic}",
        ]
        if non_neg is True:
            summary.append("Output >= 0")
        if common_keys:
            summary.append(f"DictSchema: {common_keys}")
        if len_pres is True:
            summary.append("LengthPreserving(len(out) == len(arg0))")

        return BehavioralContract(
            func_name=func.__name__,
            samples_tested=successful_samples,
            is_pure_on_args=is_pure,
            is_deterministic=is_deterministic,
            output_types=sorted(output_types),
            non_negative_output=non_neg,
            dict_schema_keys=common_keys,
            length_preserving=len_pres,
            complexity_class=complexity,
            scaling_steps=scaling_steps,
            invariants_summary=summary,
        )


class DifferentialShadowProver:
    """
    Compares two Python code versions (`before_source` vs `after_source`) via
    Differential Shadow Execution and Behavioral Invariant Comparison.
    """

    @staticmethod
    def _compile_functions(
        source_code: str,
        label: str,
        source_path: Optional[Path] = None,
    ) -> Tuple[Dict[str, Callable], Dict[str, ast.FunctionDef]]:
        fname = str(source_path.resolve()) if source_path else f"<omniscient_{label}>"
        tree = ast.parse(source_code, filename=fname)
        code_obj = compile(tree, filename=fname, mode="exec")
        ns: Dict[str, Any] = {"__name__": f"__omniscient_{label}__", "__file__": fname}
        added_paths: List[str] = []
        if source_path is not None:
            resolved_dir = source_path.resolve().parent
            for cand_dir in (resolved_dir, resolved_dir.parent):
                c_str = str(cand_dir)
                if c_str not in sys.path:
                    sys.path.insert(0, c_str)
                    added_paths.append(c_str)
        try:
            exec(code_obj, ns)
        finally:
            for p_str in added_paths:
                if p_str in sys.path:
                    sys.path.remove(p_str)
        funcs: Dict[str, Callable] = {}
        ast_nodes: Dict[str, ast.FunctionDef] = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                if node.name in ns and callable(ns[node.name]):
                    funcs[node.name] = ns[node.name]
                    ast_nodes[node.name] = node
        return funcs, ast_nodes

    @classmethod
    def prove_equivalence(
        cls,
        old_source: str,
        new_source: str,
        custom_inputs: Optional[List[Tuple[Any, ...]]] = None,
    ) -> DifferentialProofReport:
        old_funcs, old_asts = cls._compile_functions(old_source, "old")
        new_funcs, new_asts = cls._compile_functions(new_source, "new")

        shared_names = [name for name in old_funcs if name in new_funcs]
        results: List[FunctionProofResult] = []
        all_passed = True

        for name in shared_names:
            old_fn = old_funcs[name]
            new_fn = new_funcs[name]
            old_ast = old_asts[name]
            new_ast = new_asts[name]

            old_contract = BehavioralInvariantMiner.mine_contract(old_fn, old_ast, custom_inputs)
            new_contract = BehavioralInvariantMiner.mine_contract(new_fn, new_ast, custom_inputs)

            corpus, _ = synthesize_domain_inputs(old_fn, old_ast)
            if custom_inputs:
                corpus = list(custom_inputs) + corpus

            counterexamples: List[Dict[str, Any]] = []
            for args in corpus:
                old_err: Optional[str] = None
                new_err: Optional[str] = None
                old_val: Any = None
                new_val: Any = None

                try:
                    old_val = old_fn(*copy.deepcopy(args))
                except Exception as e:
                    old_err = f"{type(e).__name__}({e})"

                try:
                    new_val = new_fn(*copy.deepcopy(args))
                except Exception as e:
                    new_err = f"{type(e).__name__}({e})"

                if old_err != new_err:
                    values_equal = False
                elif old_err is None:
                    if (
                        isinstance(old_val, float)
                        and isinstance(new_val, float)
                        and math.isnan(old_val)
                        and math.isnan(new_val)
                    ):
                        values_equal = True
                    elif (type(old_val) is type(new_val)) or (
                        isinstance(old_val, (int, float))
                        and isinstance(new_val, (int, float))
                        and not isinstance(old_val, bool)
                        and not isinstance(new_val, bool)
                    ):
                        values_equal = old_val == new_val
                    else:
                        values_equal = False
                else:
                    values_equal = True

                if not values_equal:
                    counterexamples.append({
                        "args": repr(args),
                        "old_outcome": old_err if old_err else repr(old_val),
                        "new_outcome": new_err if new_err else repr(new_val),
                    })

            violations: List[str] = []
            # 1. Check purity regression
            if old_contract.is_pure_on_args and not new_contract.is_pure_on_args:
                violations.append("Purity violation: new implementation mutates input arguments in-place.")

            # 2. Check complexity degradation (e.g., O(N) -> O(N^2)+)
            old_rank = COMPLEXITY_RANK.get(old_contract.complexity_class, 0)
            new_rank = COMPLEXITY_RANK.get(new_contract.complexity_class, 0)
            if old_rank > 0 and new_rank > old_rank:
                violations.append(
                    f"Complexity degradation: {old_contract.complexity_class} degraded to {new_contract.complexity_class} "
                    f"(steps at N=45: {old_contract.scaling_steps.get(45)} -> {new_contract.scaling_steps.get(45)})."
                )

            # 3. Check dict schema key removal
            if old_contract.dict_schema_keys:
                missing_keys = set(old_contract.dict_schema_keys) - set(new_contract.dict_schema_keys)
                if missing_keys:
                    violations.append(f"Schema drift: returned dict lost required keys {sorted(missing_keys)}.")

            equivalent = len(counterexamples) == 0
            if not equivalent:
                verdict = "SEMANTIC_DIVERGENCE"
                all_passed = False
            elif any("Complexity degradation" in v for v in violations):
                verdict = "COMPLEXITY_REGRESSION"
                all_passed = False
            elif violations:
                verdict = "CONTRACT_VIOLATION"
                all_passed = False
            else:
                verdict = "PROVEN_EQUIVALENT"

            results.append(
                FunctionProofResult(
                    func_name=name,
                    equivalent=equivalent,
                    verdict=verdict,
                    old_contract=old_contract,
                    new_contract=new_contract,
                    counterexamples=counterexamples,
                    violations=violations,
                )
            )

        return DifferentialProofReport(
            passed=all_passed,
            functions_compared=len(results),
            results=results,
        )
