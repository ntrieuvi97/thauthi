#!/usr/bin/env python3
"""
Pillar 8: Whole-Repo Call-Graph Ripple Propagator (`overmind.hive`).

Eliminates multi-file refactoring breakage and human tech-lead coordination by:
1. Indexing all function signatures (`FunctionSignatureInfo`) and cross-file call
   sites (`CallSiteDrift`) across an entire Python repository.
2. Detecting signature & contract drift when a function's parameters are renamed,
   extended with new required parameters, or pruned.
3. Autonomously propagating AST surgery across every affected caller file in the
   repository (`heal_workspace`), repairing keyword renames, missing required
   arguments (via type-inferred safe defaults), and excess deprecated arguments.
"""

import ast
import copy
import difflib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class FunctionSignatureInfo:
    func_name: str
    file_path: str
    lineno: int
    param_names: List[str]
    required_params: List[str]
    param_annotations: Dict[str, str]
    has_varargs: bool
    has_kwargs: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CallSiteSurgicalFix:
    caller_file: str
    lineno: int
    callee_name: str
    drift_kind: str
    detail: str
    before_call: str
    after_call: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HivePropagationReport:
    files_scanned: int
    functions_indexed: int
    drifts_detected: int
    files_healed: List[str] = field(default_factory=list)
    fixes: List[CallSiteSurgicalFix] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files_scanned": self.files_scanned,
            "functions_indexed": self.functions_indexed,
            "drifts_detected": self.drifts_detected,
            "files_healed": self.files_healed,
            "fixes": [f.to_dict() for f in self.fixes],
        }


def _default_ast_for_param(param_name: str, ann_str: str) -> ast.expr:
    low_ann = ann_str.lower()
    low_name = param_name.lower()

    if "float" in low_ann or any(k in low_name for k in ("rate", "ratio", "price", "amount", "weight", "factor")):
        return ast.Constant(value=0.0)
    if "bool" in low_ann or low_name.startswith(("is_", "has_", "enable", "allow", "flag")):
        return ast.Constant(value=False)
    if "int" in low_ann or any(k in low_name for k in ("count", "id", "limit", "index", "size", "num", "timeout")):
        return ast.Constant(value=0)
    if "str" in low_ann or any(k in low_name for k in ("name", "text", "key", "currency", "mode", "label", "tag")):
        return ast.Constant(value="")
    if any(k in low_ann for k in ("list", "sequence", "iterable")):
        return ast.List(elts=[], ctx=ast.Load())
    if "dict" in low_ann or "mapping" in low_ann:
        return ast.Dict(keys=[], values=[])
    return ast.Constant(value=None)


def _match_renamed_kwarg(old_kw: str, available_params: List[str]) -> Optional[str]:
    if not available_params:
        return None
    if len(available_params) == 1:
        return available_params[0]

    best_param = None
    best_score = 0.0
    for cand in available_params:
        score = difflib.SequenceMatcher(None, old_kw.lower(), cand.lower()).ratio()
        # Boost shared prefix/suffix tokens (e.g., `user_id` -> `account_id`)
        old_parts = set(old_kw.lower().split("_"))
        cand_parts = set(cand.lower().split("_"))
        if old_parts & cand_parts:
            score += 0.25
        if score > best_score:
            best_score = score
            best_param = cand

    return best_param if best_score >= 0.35 else available_params[0]


class CallGraphHive:
    """
    Whole-repository Call-Graph Indexer and Ripple AST Propagator.
    Scans all Python files in a workspace, detects broken call sites caused by
    signature evolution, and heals callers automatically.
    """

    IGNORE_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".overmind"}

    def __init__(self, workspace_dir: Optional[Path] = None):
        self.workspace_dir = (workspace_dir or Path.cwd()).resolve()

    def _iter_python_files(self) -> List[Path]:
        py_files: List[Path] = []
        for p in sorted(self.workspace_dir.rglob("*.py")):
            if any(part in self.IGNORE_DIRS for part in p.parts):
                continue
            py_files.append(p)
        return py_files

    def index_signatures(self, py_files: Optional[List[Path]] = None) -> Dict[str, FunctionSignatureInfo]:
        files = py_files if py_files is not None else self._iter_python_files()
        signatures: Dict[str, FunctionSignatureInfo] = {}
        ambiguous_names: Set[str] = set()

        for fpath in files:
            try:
                tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
            except Exception:
                continue

            for node in tree.body:
                if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                    args_obj = node.args
                    pos_args = [a.arg for a in args_obj.args if a.arg != "self"]
                    num_defaults = len(args_obj.defaults)
                    req_cutoff = len(pos_args) - num_defaults
                    required = pos_args[: max(0, req_cutoff)]

                    ann_map: Dict[str, str] = {}
                    for a in args_obj.args:
                        if a.annotation is not None:
                            ann_map[a.arg] = ast.unparse(a.annotation)
                        else:
                            ann_map[a.arg] = ""

                    info = FunctionSignatureInfo(
                        func_name=node.name,
                        file_path=str(fpath.relative_to(self.workspace_dir)),
                        lineno=node.lineno,
                        param_names=pos_args,
                        required_params=required,
                        param_annotations=ann_map,
                        has_varargs=args_obj.vararg is not None,
                        has_kwargs=args_obj.kwarg is not None,
                    )
                    if node.name in signatures and signatures[node.name].param_names != info.param_names:
                        ambiguous_names.add(node.name)
                    else:
                        signatures[node.name] = info

        for amb in ambiguous_names:
            signatures.pop(amb, None)
        return signatures

    def heal_workspace(self, apply: bool = False) -> HivePropagationReport:
        py_files = self._iter_python_files()
        signatures = self.index_signatures(py_files)

        all_fixes: List[CallSiteSurgicalFix] = []
        healed_files: List[str] = []

        for fpath in py_files:
            rel_path = str(fpath.relative_to(self.workspace_dir))
            try:
                orig_src = fpath.read_text(encoding="utf-8")
                tree = ast.parse(orig_src, filename=str(fpath))
            except Exception:
                continue

            file_fixes: List[CallSiteSurgicalFix] = []

            class _CallSiteHealer(ast.NodeTransformer):
                def visit_Call(self, node: ast.Call) -> ast.AST:
                    node = self.generic_visit(node)  # type: ignore[assignment]

                    callee_name: Optional[str] = None
                    if isinstance(node.func, ast.Name):
                        callee_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        callee_name = node.func.attr

                    if not callee_name or callee_name not in signatures:
                        return node

                    # Skip calls using `*args` or `**kwargs` unpacking
                    if any(isinstance(a, ast.Starred) for a in node.args):
                        return node
                    if any(kw.arg is None for kw in node.keywords):
                        return node

                    sig = signatures[callee_name]
                    # Only heal direct function calls or module.func calls matching top-level signatures
                    if isinstance(node.func, ast.Attribute) and not isinstance(node.func.value, ast.Name):
                        return node

                    before_str = ast.unparse(node)
                    mutated_node = copy.deepcopy(node)
                    reasons: List[Tuple[str, str]] = []

                    # 1. Heal renamed keyword arguments (`UNKNOWN_KWARG`)
                    if not sig.has_kwargs:
                        bound_positional = sig.param_names[: len(mutated_node.args)]
                        used_params = set(bound_positional) | {
                            kw.arg for kw in mutated_node.keywords if kw.arg in sig.param_names
                        }
                        available = [p for p in sig.param_names if p not in used_params]

                        for kw in mutated_node.keywords:
                            if kw.arg and kw.arg not in sig.param_names:
                                target_param = _match_renamed_kwarg(kw.arg, available)
                                if target_param:
                                    old_kw = kw.arg
                                    kw.arg = target_param
                                    available.remove(target_param)
                                    used_params.add(target_param)
                                    reasons.append(
                                        ("UNKNOWN_KWARG_RENAMED", f"Renamed keyword `{old_kw}=` -> `{target_param}=`")
                                    )

                    # 2. Heal excess positional arguments (`EXCESS_POSITIONAL_ARG`)
                    if not sig.has_varargs and len(mutated_node.args) > len(sig.param_names):
                        excess_count = len(mutated_node.args) - len(sig.param_names)
                        mutated_node.args = mutated_node.args[: len(sig.param_names)]
                        reasons.append(
                            ("EXCESS_POSITIONAL_PRUNED", f"Pruned {excess_count} deprecated positional argument(s)")
                        )

                    # 3. Heal missing required arguments (`MISSING_REQUIRED_ARG`)
                    bound_names = set(sig.param_names[: len(mutated_node.args)]) | {
                        kw.arg for kw in mutated_node.keywords if kw.arg
                    }
                    missing_req = [p for p in sig.required_params if p not in bound_names]
                    if missing_req:
                        for req_p in missing_req:
                            ann = sig.param_annotations.get(req_p, "")
                            default_expr = _default_ast_for_param(req_p, ann)
                            # If call uses only positional args and req_p is the next positional slot, append positionally or as kwarg
                            mutated_node.keywords.append(ast.keyword(arg=req_p, value=default_expr))
                            reasons.append(
                                (
                                    "MISSING_REQUIRED_INJECTED",
                                    f"Injected required argument `{req_p}={ast.unparse(default_expr)}`",
                                )
                            )

                    if not reasons:
                        return node

                    ast.fix_missing_locations(mutated_node)
                    after_str = ast.unparse(mutated_node)
                    drift_kinds = ",".join(k for k, _ in reasons)
                    detail_str = "; ".join(d for _, d in reasons)

                    file_fixes.append(
                        CallSiteSurgicalFix(
                            caller_file=rel_path,
                            lineno=getattr(node, "lineno", 1),
                            callee_name=callee_name,
                            drift_kind=drift_kinds,
                            detail=detail_str,
                            before_call=before_str,
                            after_call=after_str,
                        )
                    )
                    return mutated_node

            new_tree = ast.fix_missing_locations(_CallSiteHealer().visit(tree))
            if file_fixes:
                all_fixes.extend(file_fixes)
                healed_files.append(rel_path)
                if apply:
                    new_src = ast.unparse(new_tree) + "\n"
                    fpath.write_text(new_src, encoding="utf-8")

        return HivePropagationReport(
            files_scanned=len(py_files),
            functions_indexed=len(signatures),
            drifts_detected=len(all_fixes),
            files_healed=healed_files,
            fixes=all_fixes,
        )
