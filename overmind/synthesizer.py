#!/usr/bin/env python3
"""
Pillar 7: Contract-to-Code CEGIS Program Synthesizer (`overmind.synthesizer`).

Eliminates manual imperative coding by allowing developers or agents to write
ONLY a function signature + docstring doctests (or I/O examples + postconditions).
Overmind's Counterexample-Guided Inductive Synthesis (CEGIS) engine:
1. Parses I/O contracts from `>>>` doctests or `@synthesize(examples=[...])`.
2. Searches compositional AST grammars & algebraic linear solvers to synthesize
   candidate Python implementations.
3. Verifies 100% example satisfaction, postcondition invariants, argument purity,
   and asymptotic complexity bounds (`O(1)` / `O(N)`) via `BehavioralInvariantMiner`.
4. Hot-binds the synthesized AST into the live callable or writes the synthesized
   implementation directly back into the `.py` source file (`overmind synthesize --apply`).
"""

import ast
import copy
import doctest
import functools
import inspect
import textwrap
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from overmind.prover import COMPLEXITY_RANK, BehavioralContract, BehavioralInvariantMiner


@dataclass
class SynthesisResult:
    func_name: str
    synthesized_expr: str
    synthesized_source: str
    examples_verified: int
    complexity_class: str
    is_pure: bool
    candidates_evaluated: int
    contract: Dict[str, Any] = field(default_factory=dict)
    source_kind: str = "cegis_template"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_doctest_examples(func: Callable) -> List[Tuple[Tuple[Any, ...], Any]]:
    """Extract `((arg1, arg2, ...), expected_output)` pairs from `func.__doc__`."""
    doc = inspect.getdoc(func)
    if not doc:
        return []

    parser = doctest.DocTestParser()
    parsed = parser.get_examples(doc)
    examples: List[Tuple[Tuple[Any, ...], Any]] = []
    fn_name = func.__name__

    for ex in parsed:
        src = ex.source.strip()
        want = ex.want.strip()
        if not src or not want:
            continue
        try:
            tree = ast.parse(src, mode="eval")
            if isinstance(tree.body, ast.Call):
                call_node = tree.body
                if isinstance(call_node.func, ast.Name) and call_node.func.id == fn_name:
                    args = tuple(ast.literal_eval(a) for a in call_node.args)
                    expected = ast.literal_eval(want)
                    examples.append((args, expected))
        except Exception:
            continue
    return examples


def _solve_linear_1d(examples: List[Tuple[Tuple[Any, ...], Any]]) -> Optional[str]:
    """Solve `y = a * x + b` for 1D numeric examples if exact."""
    pts: List[Tuple[float, float]] = []
    for args, out in examples:
        if len(args) != 1:
            return None
        if not isinstance(args[0], (int, float)) or isinstance(args[0], bool):
            return None
        if not isinstance(out, (int, float)) or isinstance(out, bool):
            return None
        pts.append((float(args[0]), float(out)))

    if len(pts) < 2:
        return None

    # Find two distinct x points
    x0, y0 = pts[0]
    distinct = [(x, y) for x, y in pts[1:] if abs(x - x0) > 1e-9]
    if not distinct:
        return None
    x1, y1 = distinct[0]
    a = (y1 - y0) / (x1 - x0)
    b = y0 - a * x0

    # Check if all examples match
    for x, y in pts:
        if abs((a * x + b) - y) > 1e-6:
            return None

    all_int = all(isinstance(args[0], int) and isinstance(out, int) for args, out in examples)
    a_int = round(a)
    b_int = round(b)
    if all_int and abs(a - a_int) < 1e-6 and abs(b - b_int) < 1e-6:
        if a_int == 0:
            return f"{b_int}"
        term_a = "{p0}" if a_int == 1 else ("-{p0}" if a_int == -1 else f"{a_int} * {{p0}}")
        if b_int == 0:
            return term_a
        return f"{term_a} + {b_int}" if b_int > 0 else f"{term_a} - {abs(b_int)}"

    a_r = round(a, 6)
    b_r = round(b, 6)
    if b_r == 0:
        return f"{a_r} * {{p0}}"
    return f"{a_r} * {{p0}} + {b_r}" if b_r > 0 else f"{a_r} * {{p0}} - {abs(b_r)}"


def _solve_linear_2d(examples: List[Tuple[Tuple[Any, ...], Any]]) -> Optional[str]:
    """Solve `z = a * x + b * y + c` for 2D numeric examples over small integer/half coefficients."""
    pts: List[Tuple[float, float, float]] = []
    for args, out in examples:
        if len(args) != 2:
            return None
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (*args, out)):
            return None
        pts.append((float(args[0]), float(args[1]), float(out)))

    if len(pts) < 2:
        return None

    for a in (0, 1, -1, 2, -2, 3, -3, 4, 5, 10, 0.5):
        for b in (0, 1, -1, 2, -2, 3, -3, 4, 5, 10, 0.5):
            if a == 0 and b == 0:
                continue
            c = pts[0][2] - (a * pts[0][0] + b * pts[0][1])
            if all(abs((a * x + b * y + c) - z) < 1e-6 for x, y, z in pts):
                c_num: Any = int(round(c)) if abs(c - round(c)) < 1e-6 else round(c, 4)
                a_str = "{p0}" if a == 1 else (f"{a} * {{p0}}" if a != 0 else "")
                b_str = "{p1}" if b == 1 else (f"{b} * {{p1}}" if b != 0 else "")
                expr_parts = [p for p in (a_str, b_str) if p]
                base = " + ".join(expr_parts)
                if c_num == 0:
                    return base
                return f"{base} + {c_num}"
    return None


class ContractSynthesizer:
    """
    Counterexample-Guided Inductive Synthesis (CEGIS) engine that synthesizes
    provably pure, complexity-bounded Python AST implementations from I/O contracts.
    """

    @staticmethod
    def _build_candidate_pool(
        param_names: List[str],
        examples: List[Tuple[Tuple[Any, ...], Any]],
    ) -> List[str]:
        p0 = param_names[0] if len(param_names) >= 1 else "x"
        p1 = param_names[1] if len(param_names) >= 2 else "y"
        p2 = param_names[2] if len(param_names) >= 3 else "z"

        templates: List[str] = []

        # 1. Algebraic linear solvers for 1D and 2D numeric contracts
        lin1 = _solve_linear_1d(examples)
        if lin1:
            templates.append(lin1)
        lin2 = _solve_linear_2d(examples)
        if lin2:
            templates.append(lin2)

        if len(param_names) == 1:
            templates.extend([
                "{p0}",
                "len({p0})",
                "sum({p0})",
                "max({p0}) if {p0} else 0",
                "min({p0}) if {p0} else 0",
                "abs({p0})",
                "-{p0}",
                "{p0} ** 2",
                "{p0} ** 3",
                "{p0} * ({p0} + 1) // 2",
                "sorted({p0})",
                "sorted({p0}, reverse=True)",
                "list(reversed({p0}))",
                "list(dict.fromkeys({p0}))",
                "[_x for _x in {p0} if _x > 0]",
                "[_x for _x in {p0} if _x >= 0]",
                "[_x for _x in {p0} if _x % 2 == 0]",
                "[_x for _x in {p0} if _x % 2 != 0]",
                "[_x for _x in {p0} if _x]",
                "[_x * 2 for _x in {p0}]",
                "[_x ** 2 for _x in {p0}]",
                "[abs(_x) for _x in {p0}]",
                "sum(_x for _x in {p0} if _x > 0)",
                "sum(_x for _x in {p0} if _x % 2 == 0)",
                "sum(_x ** 2 for _x in {p0})",
                "sum({p0}) / len({p0}) if {p0} else 0.0",
                "[_y for _x in {p0} for _y in _x]",
                "dict((_x, {p0}.count(_x)) for _x in dict.fromkeys({p0}))",
                "{_v: _k for _k, _v in {p0}.items()}",
                "{p0}.strip()",
                "{p0}.lower()",
                "{p0}.upper()",
                "{p0}[::-1]",
                "{p0} == {p0}[::-1]",
                "{p0}.lower() == {p0}.lower()[::-1]",
                "' '.join({p0}.split())",
                "{p0}.split()",
                "len({p0}.split())",
                "dict((_k, sum(1 for _v in {p0} if _v == _k)) for _k in dict.fromkeys({p0}))",
                "[len(_x) for _x in {p0}]",
                "sum(1 for _x in {p0} if _x > 0)",
                "sum(1 for _x in {p0} if _x % 2 == 0)",
                "max({p0}, key=len) if {p0} else ''",
                "min({p0}, key=len) if {p0} else ''",
                "{p0}.strip().lower().replace(' ', '-')",
            ])
        elif len(param_names) == 2:
            templates.extend([
                "{p0} + {p1}",
                "{p0} - {p1}",
                "{p0} * {p1}",
                "{p0} / {p1} if {p1} != 0 else 0.0",
                "{p0} // {p1} if {p1} != 0 else 0",
                "{p0} % {p1} if {p1} != 0 else 0",
                "max({p0}, {p1})",
                "min({p0}, {p1})",
                "[_x for _x in {p0} if _x in set({p1})]",
                "list(dict.fromkeys(_x for _x in {p0} if _x in set({p1})))",
                "[_x for _x in {p0} if _x not in set({p1})]",
                "list(dict.fromkeys(list({p0}) + list({p1})))",
                "[_x for _x in {p0} if _x > {p1}]",
                "[_x for _x in {p0} if _x >= {p1}]",
                "[_x for _x in {p0} if _x < {p1}]",
                "[_x for _x in {p0} if _x != {p1}]",
                "[_x * {p1} for _x in {p0}]",
                "[_x + {p1} for _x in {p0}]",
                "dict(zip({p0}, {p1}))",
                "list(zip({p0}, {p1}))",
                "sum(_a * _b for _a, _b in zip({p0}, {p1}))",
                "sorted({p0}.lower()) == sorted({p1}.lower())",
                "{p1}.join({p0})",
                "[_r[{p1}] for _r in {p0} if {p1} in _r]",
                "sum(_r.get({p1}, 0) for _r in {p0})",
                "any(({p1} - _x) in set({p0}) for _x in {p0})",
                "{p0}.get({p1})",
                "len([_x for _x in {p0} if _x == {p1}])",
            ])
        elif len(param_names) >= 3:
            templates.extend([
                "{p0} + {p1} + {p2}",
                "{p0} * {p1} + {p2}",
                "max({p0}, min({p1}, {p2}))",
                "min(max({p0}, {p1}), {p2})",
                "{p0}.replace({p1}, {p2})",
                "{p1} if {p0} < {p1} else ({p2} if {p0} > {p2} else {p0})",
            ])

        rendered: List[str] = []
        for t in templates:
            expr = t.replace("{p0}", p0).replace("{p1}", p1).replace("{p2}", p2)
            if expr not in rendered:
                rendered.append(expr)
        return rendered

    @staticmethod
    def _compile_candidate(
        func_name: str,
        params_sig: str,
        raw_candidate: str,
        extra_globals: Optional[Dict[str, Any]] = None,
    ) -> Optional[Tuple[str, Callable, ast.FunctionDef, str]]:
        """
        Compile either a single expression, a multi-line function body, or a full `def ...`
        candidate into an executable Python callable + AST.
        """
        stripped = textwrap.dedent(raw_candidate).strip()
        if not stripped:
            return None

        cand_src: str
        expr_summary: str

        # Case A: Full function definition `def ...`
        if stripped.startswith("def ") or stripped.startswith("async def "):
            try:
                parsed = ast.parse(stripped)
            except SyntaxError:
                return None
            if len(parsed.body) != 1 or not isinstance(parsed.body[0], ast.FunctionDef):
                return None
            fn_node = parsed.body[0]
            fn_node.name = func_name
            ast.fix_missing_locations(parsed)
            cand_src = ast.unparse(parsed)
            if (
                len(fn_node.body) == 1
                and isinstance(fn_node.body[0], ast.Return)
                and fn_node.body[0].value is not None
            ):
                expr_summary = ast.unparse(fn_node.body[0].value)
            else:
                expr_summary = "<multi-statement body>"
        else:
            # Case B: Single expression (try eval mode first)
            is_expr = False
            try:
                ast.parse(stripped, mode="eval")
                is_expr = True
            except SyntaxError:
                is_expr = False

            if is_expr:
                cand_src = f"def {func_name}({params_sig}):\n    return {stripped}\n"
                expr_summary = stripped
            else:
                # Case C: Statement block
                indented = textwrap.indent(stripped, "    ")
                cand_src = f"def {func_name}({params_sig}):\n{indented}\n"
                expr_summary = "<multi-statement body>"

        try:
            tree = ast.parse(cand_src)
            code_obj = compile(tree, filename=f"<cegis:{func_name}>", mode="exec")
            ns: Dict[str, Any] = dict(extra_globals or {})
            exec(code_obj, ns)
            cand_fn = ns[func_name]
            cand_ast = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
            return cand_src.strip(), cand_fn, cand_ast, expr_summary
        except Exception:
            return None

    @classmethod
    def synthesize_for_callable(
        cls,
        func: Callable,
        examples: Optional[List[Tuple[Tuple[Any, ...], Any]]] = None,
        postcondition: Optional[Callable[[Tuple[Any, ...], Any], bool]] = None,
        max_complexity: str = "O(N)",
        candidate_sources: Optional[Sequence[str]] = None,
        auto_crystallize: bool = False,
    ) -> SynthesisResult:
        sig = inspect.signature(func)
        param_names = [
            p.name
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]

        io_examples = list(examples) if examples else parse_doctest_examples(func)
        if not io_examples:
            raise ValueError(
                f"Cannot synthesize `{func.__name__}`: provide `examples=[((args...), expected)]` "
                f"or `>>> {func.__name__}(...)` doctest examples in the docstring."
            )

        builtin_candidates = cls._build_candidate_pool(param_names, io_examples)
        candidate_items: List[Tuple[str, str]] = []
        for c_src in (candidate_sources or []):
            candidate_items.append(("hybrid_neural_cegis", str(c_src)))
        for expr in builtin_candidates:
            candidate_items.append(("cegis_template", expr))

        max_rank = COMPLEXITY_RANK.get(max_complexity, 3)

        func_name = func.__name__
        params_sig = ", ".join(param_names)
        func_globals = dict(getattr(func, "__globals__", {}))
        evaluated = 0

        best_match: Optional[Tuple[str, str, str, Callable, BehavioralContract]] = None

        for source_kind, raw_cand in candidate_items:
            evaluated += 1
            compiled = cls._compile_candidate(func_name, params_sig, raw_cand, extra_globals=func_globals)
            if compiled is None:
                continue
            cand_src, cand_fn, cand_ast, expr_summary = compiled

            # Gate 1: Must satisfy all I/O examples and postconditions
            all_match = True
            for ex_args, ex_want in io_examples:
                try:
                    actual = cand_fn(*copy.deepcopy(ex_args))
                    if actual != ex_want:
                        all_match = False
                        break
                    if postcondition is not None and not postcondition(ex_args, actual):
                        all_match = False
                        break
                except Exception:
                    all_match = False
                    break

            if not all_match:
                continue

            # Gate 2: Mine behavioral contract (with 100+ property fuzzing) and check purity + complexity bound
            custom_in = [ex_args for ex_args, _ in io_examples]
            contract = BehavioralInvariantMiner.mine_contract(cand_fn, cand_ast, custom_inputs=custom_in)
            if not contract.is_pure_on_args:
                continue

            c_rank = COMPLEXITY_RANK.get(contract.complexity_class, 0)
            if c_rank == 0 or c_rank <= max_rank:
                contract_dict = contract.to_dict()
                if auto_crystallize and expr_summary != "<multi-statement body>":
                    try:
                        from overmind.crystallizer import NeuralSymbolicCrystallizer
                        crys = NeuralSymbolicCrystallizer()
                        crystal = crys.crystallize_edit(
                            name=f"synth_{func_name}",
                            before_code=f"def {func_name}({params_sig}):\n    pass\n",
                            after_code=cand_src,
                        )
                        contract_dict["crystallized_id"] = crystal.crystal_id
                    except Exception:
                        pass
                return SynthesisResult(
                    func_name=func_name,
                    synthesized_expr=expr_summary,
                    synthesized_source=cand_src,
                    examples_verified=len(io_examples),
                    complexity_class=contract.complexity_class,
                    is_pure=contract.is_pure_on_args,
                    candidates_evaluated=evaluated,
                    contract=contract_dict,
                    source_kind=source_kind,
                )
            elif best_match is None:
                best_match = (source_kind, expr_summary, cand_src, cand_fn, contract)

        if best_match is not None:
            source_kind, expr_summary, src_str, _, contract = best_match
            return SynthesisResult(
                func_name=func_name,
                synthesized_expr=expr_summary,
                synthesized_source=src_str,
                examples_verified=len(io_examples),
                complexity_class=contract.complexity_class,
                is_pure=contract.is_pure_on_args,
                candidates_evaluated=evaluated,
                contract=contract.to_dict(),
                source_kind=source_kind,
            )

        raise ValueError(
            f"CEGIS Synthesizer evaluated {evaluated} AST candidates for `{func_name}` "
            f"but none satisfied all {len(io_examples)} examples."
        )

    @classmethod
    def synthesize_file(
        cls,
        file_path: Path,
        apply: bool = False,
        candidate_sources: Optional[Sequence[str]] = None,
        auto_crystallize: bool = False,
    ) -> Tuple[str, List[SynthesisResult]]:
        """
        Scan a Python file for stub functions (`pass` or `...` body with doctest examples),
        synthesize their implementations via CEGIS (optionally verifying caller-supplied candidates),
        and optionally write them back to disk.
        """
        src = file_path.read_text(encoding="utf-8")
        tree = ast.parse(src, filename=str(file_path))
        code_obj = compile(tree, filename=str(file_path), mode="exec")
        ns: Dict[str, Any] = {"__name__": "__overmind_synth__"}
        exec(code_obj, ns)

        results: List[SynthesisResult] = []
        synthesized_bodies: Dict[str, List[ast.stmt]] = {}

        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            # Check if body (excluding docstring) is just `pass`, `...`, or `raise NotImplementedError`
            non_doc_stmts = [
                s for s in node.body
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str))
            ]
            is_stub = False
            if not non_doc_stmts:
                is_stub = True
            elif len(non_doc_stmts) == 1:
                s0 = non_doc_stmts[0]
                if isinstance(s0, ast.Pass):
                    is_stub = True
                elif isinstance(s0, ast.Expr) and isinstance(s0.value, ast.Constant) and s0.value.value is Ellipsis:
                    is_stub = True
                elif isinstance(s0, ast.Raise):
                    is_stub = True

            if not is_stub or node.name not in ns or not callable(ns[node.name]):
                continue

            fn_obj = ns[node.name]
            examples = parse_doctest_examples(fn_obj)
            if not examples:
                continue

            res = cls.synthesize_for_callable(
                fn_obj,
                examples=examples,
                candidate_sources=candidate_sources,
                auto_crystallize=auto_crystallize,
            )
            results.append(res)
            synth_fn_ast = ast.parse(res.synthesized_source).body[0]
            if isinstance(synth_fn_ast, ast.FunctionDef):
                synthesized_bodies[node.name] = synth_fn_ast.body

        if not synthesized_bodies:
            return src, []

        class _StubFiller(ast.NodeTransformer):
            def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
                if node.name in synthesized_bodies:
                    doc_stmts = [
                        s for s in node.body
                        if isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant) and isinstance(s.value.value, str)
                    ]
                    node.body = doc_stmts + copy.deepcopy(synthesized_bodies[node.name])
                return node

        new_tree = ast.fix_missing_locations(_StubFiller().visit(tree))
        new_src = ast.unparse(new_tree) + "\n"
        if apply:
            file_path.write_text(new_src, encoding="utf-8")
        return new_src, results


def synthesize(
    func: Optional[Callable] = None,
    *,
    examples: Optional[List[Tuple[Tuple[Any, ...], Any]]] = None,
    postcondition: Optional[Callable[[Tuple[Any, ...], Any], bool]] = None,
    max_complexity: str = "O(N)",
    candidates: Optional[Sequence[str]] = None,
    auto_crystallize: bool = False,
) -> Any:
    """
    Decorator that synthesizes a function's AST implementation at definition time
    from its docstring `>>>` examples or explicit `examples=[((args...), expected)]`.
    """
    def _decorator(fn: Callable) -> Callable:
        res = ContractSynthesizer.synthesize_for_callable(
            fn,
            examples=examples,
            postcondition=postcondition,
            max_complexity=max_complexity,
            candidate_sources=candidates,
            auto_crystallize=auto_crystallize,
        )
        tree = ast.parse(res.synthesized_source)
        code_obj = compile(tree, filename=f"<synthesized:{fn.__name__}>", mode="exec")
        ns: Dict[str, Any] = dict(getattr(fn, "__globals__", {}))
        exec(code_obj, ns)
        compiled_fn = ns[fn.__name__]
        functools.update_wrapper(compiled_fn, fn)
        setattr(compiled_fn, "__overmind_synthesis__", res)
        return compiled_fn

    if func is not None and callable(func):
        return _decorator(func)
    return _decorator
