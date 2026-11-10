#!/usr/bin/env python3
"""
Pillar 2: In-Flight Self-Healing Immune System & Counterfactual AST Surgery.

Intercepts unhandled runtime exceptions in-flight, freezes the exact failing call
frame into an automated pytest regression test, synthesizes candidate AST patches,
verifies them in-memory against all historical healthy frames + the crashing frame,
and hot-swaps the healed code without crashing the process.
"""

import ast
import copy
import difflib
import functools
import hashlib
import inspect
import json
import textwrap
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from thauthi.srcedit import backup_before_write


@dataclass
class RecordedCallFrame:
    args: Tuple[Any, ...]
    kwargs: Dict[str, Any]
    result: Any = None
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None


@dataclass
class SurgeryReport:
    surgery_id: str
    func_name: str
    module_name: str
    filename: str
    exception_type: str
    exception_message: str
    crash_args: List[Any]
    crash_kwargs: Dict[str, Any]
    strategy_applied: str
    original_source: str
    patched_source: str
    healthy_frames_verified: int
    healed_return_value: Any
    regression_test_path: Optional[str] = None
    source_file_patched: bool = False
    mode: str = "safe-fallback"
    patch_diff_path: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _infer_safe_defaults(func: Callable, healthy_frames: List[RecordedCallFrame]) -> List[Any]:
    """Infer safe default fallback values from historical healthy returns or return annotations."""
    candidates: List[Any] = []

    # 1. Inspect historical healthy outputs
    for frm in healthy_frames:
        val = frm.result
        if isinstance(val, float) and 0.0 not in candidates:
            candidates.append(0.0)
        elif isinstance(val, int) and not isinstance(val, bool) and 0 not in candidates:
            candidates.append(0)
        elif isinstance(val, str) and "" not in candidates:
            candidates.append("")
        elif isinstance(val, list) and [] not in candidates:
            candidates.append([])
        elif isinstance(val, dict) and {} not in candidates:
            candidates.append({})
        elif isinstance(val, bool) and False not in candidates:
            candidates.append(False)

    # 2. Inspect return type annotation
    try:
        sig = inspect.signature(func)
        ret_ann = sig.return_annotation
        ann_str = str(ret_ann)
        if ret_ann is float or "float" in ann_str:
            if 0.0 not in candidates:
                candidates.append(0.0)
        elif ret_ann is int or "int" in ann_str:
            if 0 not in candidates:
                candidates.append(0)
        elif ret_ann is str or "str" in ann_str:
            if "" not in candidates:
                candidates.append("")
        elif ret_ann is list or "List" in ann_str or "list" in ann_str:
            if [] not in candidates:
                candidates.append([])
        elif ret_ann is dict or "Dict" in ann_str or "dict" in ann_str:
            if {} not in candidates:
                candidates.append({})
    except (ValueError, TypeError):
        pass

    for fallback in [0.0, 0, None, "", [], {}]:
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def _value_to_ast_constant(val: Any) -> ast.expr:
    if isinstance(val, list):
        return ast.List(elts=[], ctx=ast.Load())
    if isinstance(val, dict):
        return ast.Dict(keys=[], values=[])
    return ast.Constant(value=val)


class _ZeroDivisionSurgeon(ast.NodeTransformer):
    """Transforms `a / b`, `a // b`, `a % b` into `(a / b if b != 0 else default)`."""

    def __init__(self, default_val: Any):
        self.default_val = default_val
        self.modified = False

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            self.modified = True
            divisor = copy.deepcopy(node.right)
            cond = ast.Compare(
                left=divisor,
                ops=[ast.NotEq()],
                comparators=[ast.Constant(value=0)],
            )
            return ast.IfExp(
                test=cond,
                body=node,
                orelse=_value_to_ast_constant(self.default_val),
            )
        return node


class _KeyErrorSurgeon(ast.NodeTransformer):
    """Transforms dictionary subscript `d[k]` (Load) into `d.get(k, default)`."""

    def __init__(self, default_val: Any):
        self.default_val = default_val
        self.modified = False

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and not isinstance(node.slice, ast.Slice):
            self.modified = True
            key_expr = node.slice
            # Python 3.8 compatibility if ast.Index wrapper exists
            if hasattr(ast, "Index") and isinstance(key_expr, getattr(ast, "Index")):
                key_expr = getattr(key_expr, "value")
            return ast.Call(
                func=ast.Attribute(value=node.value, attr="get", ctx=ast.Load()),
                args=[key_expr, _value_to_ast_constant(self.default_val)],
                keywords=[],
            )
        return node


class _IndexErrorSurgeon(ast.NodeTransformer):
    """Transforms sequence subscript `seq[i]` into `(seq[i] if -len(seq) <= i < len(seq) else default)`."""

    def __init__(self, default_val: Any):
        self.default_val = default_val
        self.modified = False

    def visit_Subscript(self, node: ast.Subscript) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.ctx, ast.Load) and not isinstance(node.slice, ast.Slice):
            self.modified = True
            idx_expr = node.slice
            if hasattr(ast, "Index") and isinstance(idx_expr, getattr(ast, "Index")):
                idx_expr = getattr(idx_expr, "value")
            seq_copy_1 = copy.deepcopy(node.value)
            seq_copy_2 = copy.deepcopy(node.value)
            idx_copy = copy.deepcopy(idx_expr)
            len_call_1 = ast.Call(func=ast.Name(id="len", ctx=ast.Load()), args=[seq_copy_1], keywords=[])
            len_call_2 = ast.Call(func=ast.Name(id="len", ctx=ast.Load()), args=[seq_copy_2], keywords=[])
            neg_len = ast.UnaryOp(op=ast.USub(), operand=len_call_1)
            cond = ast.Compare(
                left=neg_len,
                ops=[ast.LtE(), ast.Lt()],
                comparators=[idx_copy, len_call_2],
            )
            return ast.IfExp(
                test=cond,
                body=node,
                orelse=_value_to_ast_constant(self.default_val),
            )
        return node


class CounterfactualSurgeon:
    """
    Synthesizes candidate AST mutations for a crashing function and verifies
    them in an isolated in-memory counterfactual sandbox.
    """

    @staticmethod
    def extract_clean_func_ast(func: Callable) -> Tuple[str, ast.Module, ast.FunctionDef]:
        raw_src = inspect.getsource(func)
        dedented = textwrap.dedent(raw_src)
        tree = ast.parse(dedented)
        func_def: Optional[Any] = None
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Strip @immune decorators so counterfactual execution runs the raw body directly
                node.decorator_list = [
                    d for d in node.decorator_list
                    if "immune" not in (ast.unparse(d) if hasattr(ast, "unparse") else "")
                ]
                func_def = node
                break
        if func_def is None:
            raise ValueError(f"Could not locate FunctionDef for {func}")
        clean_src = ast.unparse(tree) if hasattr(ast, "unparse") else dedented
        return clean_src, tree, func_def

    @classmethod
    def generate_candidates(
        cls,
        base_tree: ast.Module,
        exc_type_name: str,
        crash_frame: RecordedCallFrame,
        func: Callable,
        healthy_frames: List[RecordedCallFrame],
    ) -> List[Tuple[str, ast.Module]]:
        defaults = _infer_safe_defaults(func, healthy_frames)
        candidates: List[Tuple[str, ast.Module]] = []

        # 1. Targeted AST operator / expression surgeons based on exception class
        if exc_type_name == "ZeroDivisionError":
            for dval in defaults[:3]:
                t_copy = copy.deepcopy(base_tree)
                surgeon = _ZeroDivisionSurgeon(dval)
                t_mut = surgeon.visit(t_copy)
                if surgeon.modified:
                    ast.fix_missing_locations(t_mut)
                    candidates.append((f"ast_zero_div_guard(default={dval!r})", t_mut))

        elif exc_type_name == "KeyError":
            for dval in defaults[:3]:
                t_copy = copy.deepcopy(base_tree)
                surgeon = _KeyErrorSurgeon(dval)
                t_mut = surgeon.visit(t_copy)
                if surgeon.modified:
                    ast.fix_missing_locations(t_mut)
                    candidates.append((f"ast_dict_get_guard(default={dval!r})", t_mut))

        elif exc_type_name == "IndexError":
            for dval in defaults[:3]:
                t_copy = copy.deepcopy(base_tree)
                surgeon = _IndexErrorSurgeon(dval)
                t_mut = surgeon.visit(t_copy)
                if surgeon.modified:
                    ast.fix_missing_locations(t_mut)
                    candidates.append((f"ast_bounds_clamp_guard(default={dval!r})", t_mut))

        # 2. Null-argument entry guard if any positional/keyword arg was None
        sig_params: List[str] = []
        try:
            sig_params = list(inspect.signature(func).parameters.keys())
        except (ValueError, TypeError):
            pass

        none_params: List[str] = []
        for idx, val in enumerate(crash_frame.args):
            if val is None and idx < len(sig_params):
                none_params.append(sig_params[idx])
        for k, val in crash_frame.kwargs.items():
            if val is None and k not in none_params:
                none_params.append(k)

        if none_params and exc_type_name in ("TypeError", "AttributeError", "ValueError"):
            for dval in defaults[:3]:
                t_copy = copy.deepcopy(base_tree)
                f_def = next(n for n in t_copy.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
                checks = [
                    ast.Compare(
                        left=ast.Name(id=p, ctx=ast.Load()),
                        ops=[ast.Is()],
                        comparators=[ast.Constant(value=None)],
                    )
                    for p in none_params
                ]
                test_expr: ast.expr = checks[0] if len(checks) == 1 else ast.BoolOp(op=ast.Or(), values=checks)
                guard_if = ast.If(
                    test=test_expr,
                    body=[ast.Return(value=_value_to_ast_constant(dval))],
                    orelse=[],
                )
                f_def.body.insert(0, guard_if)
                ast.fix_missing_locations(t_copy)
                candidates.append((f"ast_null_arg_guard({','.join(none_params)} -> {dval!r})", t_copy))

        # 3. Structural Exception Boundary Fallback (universal last-resort guard)
        for dval in defaults[:2]:
            t_copy = copy.deepcopy(base_tree)
            f_def = next(n for n in t_copy.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
            orig_body = list(f_def.body)
            try_node = ast.Try(
                body=orig_body,
                handlers=[
                    ast.ExceptHandler(
                        type=ast.Name(id=exc_type_name, ctx=ast.Load()),
                        name=None,
                        body=[ast.Return(value=_value_to_ast_constant(dval))],
                    )
                ],
                orelse=[],
                finalbody=[],
            )
            f_def.body = [try_node]
            ast.fix_missing_locations(t_copy)
            candidates.append((f"ast_exception_boundary({exc_type_name} -> {dval!r})", t_copy))

        return candidates

    @classmethod
    def verify_and_select(
        cls,
        func: Callable,
        exc_type_name: str,
        crash_frame: RecordedCallFrame,
        healthy_frames: List[RecordedCallFrame],
    ) -> Optional[Tuple[str, str, Callable, Any]]:
        """
        Synthesize candidate AST patches and verify each in-memory against:
        1. All recorded healthy frames (must match exact historical outputs)
        2. The crashing frame (must succeed without raising)
        Returns (strategy_name, patched_source, compiled_callable, healed_output).
        """
        _, base_tree, func_def = cls.extract_clean_func_ast(func)
        func_name = func_def.name
        candidates = cls.generate_candidates(base_tree, exc_type_name, crash_frame, func, healthy_frames)

        for strategy, cand_tree in candidates:
            try:
                future_stmt = ast.ImportFrom(
                    module="__future__",
                    names=[ast.alias(name="annotations", asname=None)],
                    level=0,
                )
                compile_tree = ast.Module(
                    body=[future_stmt, *copy.deepcopy(cand_tree.body)],
                    type_ignores=[],
                )
                ast.fix_missing_locations(compile_tree)
                code_obj = compile(compile_tree, filename=f"<counterfactual:{func_name}>", mode="exec")
                sandbox_ns: Dict[str, Any] = dict(getattr(func, "__globals__", {}))
                exec(code_obj, sandbox_ns)
                cand_fn = sandbox_ns[func_name]
            except Exception:
                continue

            # Gate 1: Zero regression on all historical healthy frames
            healthy_ok = True
            for h_frame in healthy_frames:
                try:
                    out = cand_fn(*copy.deepcopy(h_frame.args), **copy.deepcopy(h_frame.kwargs))
                    if out != h_frame.result:
                        healthy_ok = False
                        break
                except Exception:
                    healthy_ok = False
                    break
            if not healthy_ok:
                continue

            # Gate 2: Must survive the crashing frame without raising
            try:
                healed_out = cand_fn(*copy.deepcopy(crash_frame.args), **copy.deepcopy(crash_frame.kwargs))
            except Exception:
                continue

            patched_src = ast.unparse(cand_tree) if hasattr(ast, "unparse") else ""
            return strategy, patched_src, cand_fn, healed_out

        return None


class AutopoieticImmuneSystem:
    """
    Runtime Immune Engine that monitors wrapped functions, synthesizes regression
    tests on crash, performs Counterfactual AST Surgery, and hot-swaps healed code.
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or (Path.cwd() / ".thauthi")
        self.tests_dir = self.storage_dir / "immune_tests"
        self.patches_dir = self.storage_dir / "patches"
        self.surgeries_file = self.storage_dir / "surgeries.jsonl"
        self._healthy_frames: Dict[str, List[RecordedCallFrame]] = {}
        self._healed_impls: Dict[str, Callable] = {}
        self.surgeries: List[SurgeryReport] = []

    def synthesize_patch_diff(
        self,
        func: Callable,
        orig_source: str,
        patched_source: str,
        surgery_id: str,
    ) -> Optional[Path]:
        """Generate a unified `.diff` patch file suitable for PR review / `git apply`."""
        self.patches_dir.mkdir(parents=True, exist_ok=True)
        diff_file = self.patches_dir / f"{surgery_id}_{func.__name__}.diff"

        src_file_str = inspect.getsourcefile(func) or ""
        src_path = Path(src_file_str).resolve() if src_file_str else None
        rel_label = func.__name__ + ".py"
        before_lines: List[str] = orig_source.strip().splitlines(keepends=True)
        after_lines: List[str] = patched_source.strip().splitlines(keepends=True)

        if src_path and src_path.exists():
            try:
                rel_label = src_path.name
                raw_func_src = inspect.getsource(func)
                file_text = src_path.read_text(encoding="utf-8")
                if raw_func_src in file_text:
                    first_line = raw_func_src.splitlines()[0]
                    indent = first_line[: len(first_line) - len(first_line.lstrip())]
                    indented_patch = textwrap.indent(patched_source.strip(), indent) + "\n"
                    new_file_text = file_text.replace(raw_func_src, indented_patch, 1)
                    before_lines = file_text.splitlines(keepends=True)
                    after_lines = new_file_text.splitlines(keepends=True)
            except Exception:
                pass

        diff_lines = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"a/{rel_label}",
                tofile=f"b/{rel_label}",
            )
        )
        diff_text = "".join(diff_lines)
        diff_file.write_text(diff_text, encoding="utf-8")
        return diff_file

    def synthesize_regression_test(
        self,
        func: Callable,
        patched_source: str,
        crash_frame: RecordedCallFrame,
        healed_result: Any,
        healthy_frames: List[RecordedCallFrame],
    ) -> Path:
        """Generate a standalone pytest regression file proving the crash is healed."""
        self.tests_dir.mkdir(parents=True, exist_ok=True)
        func_name = func.__name__
        test_file = self.tests_dir / f"test_immune_{func_name}.py"

        assertions: List[str] = []
        for idx, hf in enumerate(healthy_frames[:10]):
            args_repr = ", ".join([repr(a) for a in hf.args] + [f"{k}={v!r}" for k, v in hf.kwargs.items()])
            assertions.append(f"    assert {func_name}({args_repr}) == {hf.result!r}  # healthy frame #{idx + 1}")

        crash_args_repr = ", ".join(
            [repr(a) for a in crash_frame.args] + [f"{k}={v!r}" for k, v in crash_frame.kwargs.items()]
        )
        assertions.append(
            f"    # Cryo-frozen crash frame ({crash_frame.exception_type}: {crash_frame.exception_message})\n"
            f"    assert {func_name}({crash_args_repr}) == {healed_result!r}"
        )

        body_assertions = "\n".join(assertions)
        content = (
            "#!/usr/bin/env python3\n"
            '"""Auto-generated by Thauthi 2.0 Autopoietic Immune System."""\n'
            "from __future__ import annotations\n"
            "from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union\n\n"
            f"{patched_source}\n\n\n"
            f"def test_immune_{func_name}_regression() -> None:\n"
            f"{body_assertions}\n"
        )
        test_file.write_text(content, encoding="utf-8")
        return test_file

    def apply_patch_to_source_file(self, func: Callable, patched_source: str) -> bool:
        """Replace the crashing function's definition in its source file with the verified AST patch."""
        try:
            src_file = Path(inspect.getsourcefile(func) or "").resolve()
            if not src_file.exists():
                return False
            raw_func_src = inspect.getsource(func)
            file_text = src_file.read_text(encoding="utf-8")
            if raw_func_src not in file_text:
                return False
            # Preserve indentation of original function definition
            first_line = raw_func_src.splitlines()[0]
            indent = first_line[: len(first_line) - len(first_line.lstrip())]
            indented_patch = textwrap.indent(patched_source.strip(), indent) + "\n"
            new_file_text = file_text.replace(raw_func_src, indented_patch, 1)
            backup_before_write(src_file)
            src_file.write_text(new_file_text, encoding="utf-8")
            return True
        except Exception:
            return False

    def protect(
        self,
        func: Optional[Callable] = None,
        *,
        auto_heal: bool = True,
        persist_test: bool = True,
        apply_patch: bool = False,
        mode: str = "safe-fallback",
    ) -> Callable:
        """
        Decorator that equips any Python function with In-Flight Self-Healing.
        - `mode="safe-fallback"` (default): hot-swaps verified AST patch and returns healed output.
        - `mode="shadow-pr"`: generates cryo-test + unified `.diff` PR patch while re-raising loudly
          so strict transactional/financial code never returns silent fallback values.
        """
        if mode not in ("safe-fallback", "shadow-pr"):
            raise ValueError(f"Unsupported immune mode `{mode}`; expected 'safe-fallback' or 'shadow-pr'.")

        def decorator(target_fn: Callable) -> Callable:
            qual_key = f"{target_fn.__module__}.{target_fn.__qualname__}"
            self._healthy_frames.setdefault(qual_key, [])

            @functools.wraps(target_fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                active_fn = self._healed_impls.get(qual_key, target_fn) if mode == "safe-fallback" else target_fn
                try:
                    res = active_fn(*args, **kwargs)
                    history = self._healthy_frames[qual_key]
                    if len(history) < 25:
                        history.append(
                            RecordedCallFrame(
                                args=copy.deepcopy(args),
                                kwargs=copy.deepcopy(kwargs),
                                result=copy.deepcopy(res),
                            )
                        )
                    return res
                except Exception as exc:
                    if not auto_heal:
                        raise
                    exc_type_name = type(exc).__name__
                    crash_frame = RecordedCallFrame(
                        args=copy.deepcopy(args),
                        kwargs=copy.deepcopy(kwargs),
                        exception_type=exc_type_name,
                        exception_message=str(exc),
                    )
                    healthy = self._healthy_frames.get(qual_key, [])
                    orig_src, _, _ = CounterfactualSurgeon.extract_clean_func_ast(target_fn)
                    outcome = CounterfactualSurgeon.verify_and_select(
                        func=target_fn,
                        exc_type_name=exc_type_name,
                        crash_frame=crash_frame,
                        healthy_frames=healthy,
                    )
                    if outcome is None:
                        raise

                    strategy, patched_src, healed_fn, healed_out = outcome

                    if mode == "safe-fallback":
                        # Hot-swap in live memory so future calls execute healed_fn directly
                        self._healed_impls[qual_key] = healed_fn

                    test_path_str: Optional[str] = None
                    if persist_test:
                        t_path = self.synthesize_regression_test(
                            func=target_fn,
                            patched_source=patched_src,
                            crash_frame=crash_frame,
                            healed_result=healed_out,
                            healthy_frames=healthy,
                        )
                        test_path_str = str(t_path)

                    raw_hash = f"{qual_key}:{exc_type_name}:{strategy}:{time.time_ns()}"
                    surgery_id = "srg_" + hashlib.sha1(raw_hash.encode("utf-8")).hexdigest()[:10]
                    diff_path = self.synthesize_patch_diff(
                        func=target_fn,
                        orig_source=orig_src,
                        patched_source=patched_src,
                        surgery_id=surgery_id,
                    )

                    patched_disk = False
                    if apply_patch and mode == "safe-fallback":
                        patched_disk = self.apply_patch_to_source_file(target_fn, patched_src)

                    report = SurgeryReport(
                        surgery_id=surgery_id,
                        func_name=target_fn.__name__,
                        module_name=target_fn.__module__,
                        filename=inspect.getsourcefile(target_fn) or "<memory>",
                        exception_type=exc_type_name,
                        exception_message=str(exc),
                        crash_args=[repr(a) for a in args],
                        crash_kwargs={k: repr(v) for k, v in kwargs.items()},
                        strategy_applied=strategy,
                        original_source=orig_src,
                        patched_source=patched_src,
                        healthy_frames_verified=len(healthy),
                        healed_return_value=healed_out,
                        regression_test_path=test_path_str,
                        source_file_patched=patched_disk,
                        mode=mode,
                        patch_diff_path=str(diff_path) if diff_path else None,
                    )
                    self.surgeries.append(report)
                    self.surgeries_file.parent.mkdir(parents=True, exist_ok=True)
                    with self.surgeries_file.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(report.to_dict(), ensure_ascii=False) + "\n")

                    if mode == "shadow-pr":
                        raise
                    return healed_out

            setattr(wrapper, "_thauthi_immune_system", self)
            setattr(wrapper, "_thauthi_original_fn", target_fn)
            return wrapper

        if func is not None:
            return decorator(func)
        return decorator


_DEFAULT_IMMUNE_SYSTEM = AutopoieticImmuneSystem()
immune = _DEFAULT_IMMUNE_SYSTEM.protect
