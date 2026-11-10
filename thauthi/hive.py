#!/usr/bin/env python3
"""
Pillar 8: Whole-Repo Call-Graph Ripple Propagator (`thauthi.hive`).

Eliminates multi-file refactoring breakage and human tech-lead coordination by:
1. Indexing all top-level functions, async functions, and class methods across an
   entire Python repository into a module-qualified Symbol Graph (`RepoSymbolIndex`),
   including positional-only, regular, and keyword-only parameters.
2. Resolving per-file imports (`import ...`, `from ... import ... [as ...]`,
   relative imports), module-qualified calls (`mod.fn(...)`), class method
   calls (`self.method(...)`, `svc.method(...)` via annotations/instantiations),
   and local definitions so same-named functions in different modules never collide.
3. Autonomously propagating AST surgery across every affected caller file in the
   repository (`heal_workspace`), repairing keyword renames, missing required
   arguments (via type-inferred safe defaults), and excess deprecated arguments.
"""

import ast
import copy
import difflib
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# Method names of builtin containers/str/bytes/file objects. A call like `x.get(k, 0)` with an
# untyped receiver is overwhelmingly a builtin call, so the repo-wide unique-name fallback must
# never bind it to a same-named user function (that rewrote `d.get(k, 0)` -> `d.get(k)`).
_BUILTIN_METHOD_NAMES = frozenset(
    {
        # dict
        "get", "pop", "popitem", "setdefault", "update", "items", "keys", "values", "copy", "clear", "fromkeys",
        # list / set / deque
        "append", "extend", "insert", "remove", "index", "count", "sort", "reverse", "add", "discard",
        "union", "intersection", "difference", "symmetric_difference", "issubset", "issuperset", "isdisjoint",
        "appendleft", "popleft", "extendleft", "rotate",
        # str / bytes
        "join", "split", "rsplit", "strip", "lstrip", "rstrip", "replace", "find", "rfind", "startswith",
        "endswith", "format", "encode", "decode", "lower", "upper", "title", "partition", "rpartition",
        "splitlines", "zfill", "ljust", "rjust", "center",
        # file-like / misc
        "read", "write", "readline", "readlines", "writelines", "close", "flush", "seek", "tell", "open",
        "send", "put", "submit",
    }
)


def _apply_call_edits(src: str, edits: List[Tuple[int, int, int, int, str]]) -> str:
    """Replace only the given call-expression spans in `src`; everything else stays byte-identical.

    Each edit is `(lineno, col, end_lineno, end_col, replacement)` using `ast` positions
    (1-based lines, UTF-8 byte columns). Spans nested inside another edit are dropped because the
    outer replacement was unparsed from the already-healed subtree.
    """
    raw = src.encode("utf-8")
    starts = [0]
    for line in raw.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    spans = sorted(
        ((starts[l1 - 1] + c1, starts[l2 - 1] + c2, text) for l1, c1, l2, c2, text in edits),
        key=lambda e: (e[0], -e[1]),
    )
    kept: List[Tuple[int, int, str]] = []
    for start, end, text in spans:
        if kept and start < kept[-1][1]:
            continue
        kept.append((start, end, text))
    for start, end, text in reversed(kept):
        raw = raw[:start] + text.encode("utf-8") + raw[end:]
    return raw.decode("utf-8")


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
    qual_name: str = ""
    module_name: str = ""
    class_name: Optional[str] = None
    posonly_params: List[str] = field(default_factory=list)
    kwonly_params: List[str] = field(default_factory=list)

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
    backup_dir: Optional[str] = None
    skipped_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "files_scanned": self.files_scanned,
            "functions_indexed": self.functions_indexed,
            "drifts_detected": self.drifts_detected,
            "files_healed": self.files_healed,
            "fixes": [f.to_dict() for f in self.fixes],
            "backup_dir": self.backup_dir,
            "skipped_files": self.skipped_files,
        }


@dataclass
class RepoSymbolIndex:
    by_qual_name: Dict[str, FunctionSignatureInfo] = field(default_factory=dict)
    by_class_qual_name: Dict[str, str] = field(default_factory=dict)  # "mod.Class" -> "mod.Class"
    unique_bare_funcs: Dict[str, FunctionSignatureInfo] = field(default_factory=dict)
    unique_methods: Dict[str, FunctionSignatureInfo] = field(default_factory=dict)
    module_files: Set[str] = field(default_factory=set)


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


def _path_to_module_name(rel_path: Path) -> str:
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_relative_module(current_mod: str, is_pkg_init: bool, target_mod: Optional[str], level: int) -> str:
    if level == 0:
        return target_mod or ""
    parts = current_mod.split(".") if current_mod else []
    if not is_pkg_init and parts:
        parts = parts[:-1]
    up = max(0, level - 1)
    if up > 0:
        parts = parts[: max(0, len(parts) - up)]
    if target_mod:
        return ".".join(parts + [target_mod]) if parts else target_mod
    return ".".join(parts)


def _extract_func_signature(
    node: ast.AST,
    rel_path: str,
    mod_name: str,
    class_name: Optional[str] = None,
) -> Optional[FunctionSignatureInfo]:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    args_obj = node.args
    is_staticmethod = any(
        isinstance(dec, ast.Name) and dec.id == "staticmethod"
        for dec in node.decorator_list
    )

    raw_posonly = [a.arg for a in getattr(args_obj, "posonlyargs", [])]
    raw_regular = [a.arg for a in args_obj.args]
    all_pos = raw_posonly + raw_regular

    strip_first = False
    if class_name and not is_staticmethod and all_pos and all_pos[0] in ("self", "cls"):
        strip_first = True

    if strip_first:
        if raw_posonly:
            raw_posonly = raw_posonly[1:]
        elif raw_regular:
            raw_regular = raw_regular[1:]
        all_pos = raw_posonly + raw_regular

    # Defaults align with the tail of `posonlyargs + args`
    total_raw_pos = len(getattr(args_obj, "posonlyargs", [])) + len(args_obj.args)
    num_defaults = len(args_obj.defaults)
    req_raw_count = max(0, total_raw_pos - num_defaults)
    req_pos_names: List[str] = []
    full_raw = [a.arg for a in getattr(args_obj, "posonlyargs", [])] + [a.arg for a in args_obj.args]
    for idx, p_name in enumerate(full_raw[:req_raw_count]):
        if strip_first and idx == 0:
            continue
        req_pos_names.append(p_name)

    kwonly_names = [a.arg for a in args_obj.kwonlyargs]
    req_kwonly = [
        a.arg
        for a, d_val in zip(args_obj.kwonlyargs, args_obj.kw_defaults)
        if d_val is None
    ]

    ann_map: Dict[str, str] = {}
    for a in list(getattr(args_obj, "posonlyargs", [])) + list(args_obj.args) + list(args_obj.kwonlyargs):
        ann_map[a.arg] = ast.unparse(a.annotation) if a.annotation is not None else ""

    qual_parts = [p for p in (mod_name, class_name, node.name) if p]
    qual_name = ".".join(qual_parts)

    return FunctionSignatureInfo(
        func_name=node.name,
        file_path=rel_path,
        lineno=node.lineno,
        param_names=all_pos,
        required_params=req_pos_names + req_kwonly,
        param_annotations=ann_map,
        has_varargs=args_obj.vararg is not None,
        has_kwargs=args_obj.kwarg is not None,
        qual_name=qual_name,
        module_name=mod_name,
        class_name=class_name,
        posonly_params=raw_posonly,
        kwonly_params=kwonly_names,
    )


def _annotation_to_name(ann: Optional[ast.expr]) -> Optional[str]:
    if ann is None:
        return None
    if isinstance(ann, ast.Name):
        return ann.id
    if isinstance(ann, ast.Attribute):
        return ast.unparse(ann)
    return None


class CallGraphHive:
    """
    Whole-repository Call-Graph Indexer and Ripple AST Propagator.
    Scans all Python files in a workspace, resolves imports/aliases/classes/methods,
    detects broken call sites caused by signature evolution, and heals callers automatically.
    """

    IGNORE_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".thauthi"}

    def __init__(self, workspace_dir: Optional[Path] = None):
        self.workspace_dir = (workspace_dir or Path.cwd()).resolve()

    def _iter_python_files(self) -> List[Path]:
        py_files: List[Path] = []
        for p in sorted(self.workspace_dir.rglob("*.py")):
            if any(part in self.IGNORE_DIRS for part in p.parts):
                continue
            py_files.append(p)
        return py_files

    def index_repo_symbols(self, py_files: Optional[List[Path]] = None) -> RepoSymbolIndex:
        files = py_files if py_files is not None else self._iter_python_files()
        index = RepoSymbolIndex()
        bare_groups: Dict[str, List[FunctionSignatureInfo]] = {}
        method_groups: Dict[str, List[FunctionSignatureInfo]] = {}

        for fpath in files:
            rel_path_obj = fpath.relative_to(self.workspace_dir)
            rel_path = str(rel_path_obj)
            mod_name = _path_to_module_name(rel_path_obj)
            if mod_name:
                index.module_files.add(mod_name)
            try:
                tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
            except Exception:
                continue

            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                    info = _extract_func_signature(node, rel_path, mod_name, class_name=None)
                    if info is not None:
                        index.by_qual_name[info.qual_name] = info
                        bare_groups.setdefault(info.func_name, []).append(info)
                elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                    cls_qual = f"{mod_name}.{node.name}" if mod_name else node.name
                    index.by_class_qual_name[cls_qual] = cls_qual
                    for item in node.body:
                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            if item.name.startswith("__") and item.name.endswith("__"):
                                continue
                            if item.name.startswith("_") and not item.name.startswith("__"):
                                # Index private helper methods for `self._helper(...)` resolution too
                                pass
                            info = _extract_func_signature(item, rel_path, mod_name, class_name=node.name)
                            if info is not None:
                                index.by_qual_name[info.qual_name] = info
                                method_groups.setdefault(item.name, []).append(info)

        for name, group in bare_groups.items():
            # Unique if only defined once or all definitions share identical parameter schema
            first = group[0]
            if all(
                g.param_names == first.param_names
                and g.kwonly_params == first.kwonly_params
                and g.required_params == first.required_params
                for g in group
            ):
                index.unique_bare_funcs[name] = first

        for name, group in method_groups.items():
            first = group[0]
            if len(group) == 1:
                index.unique_methods[name] = first

        return index

    def index_signatures(self, py_files: Optional[List[Path]] = None) -> Dict[str, FunctionSignatureInfo]:
        """Backward-compatible signature map keyed by unambiguous top-level function name."""
        index = self.index_repo_symbols(py_files)
        return dict(index.unique_bare_funcs)

    @staticmethod
    def _build_file_scope(
        tree: ast.Module,
        mod_name: str,
        is_pkg_init: bool,
        index: RepoSymbolIndex,
    ) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, str]]:
        """
        Return `(symbol_aliases, module_aliases, module_vars)` for a Python module:
        - `symbol_aliases`: local_name -> qualified symbol (`billing.issue_invoice` or `models.InvoiceService`)
        - `module_aliases`: local_name -> module name (`billing` or `services.billing`)
        - `module_vars`: top-level variable name -> qualified class name
        """
        symbol_aliases: Dict[str, str] = {}
        module_aliases: Dict[str, str] = {}
        module_vars: Dict[str, str] = {}

        # 1. Local definitions inside this module take priority
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{mod_name}.{node.name}" if mod_name else node.name
                if q in index.by_qual_name:
                    symbol_aliases[node.name] = q
            elif isinstance(node, ast.ClassDef):
                cq = f"{mod_name}.{node.name}" if mod_name else node.name
                if cq in index.by_class_qual_name:
                    symbol_aliases[node.name] = cq

        # 2. Import graph resolution
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    local = alias.asname or alias.name.split(".")[0]
                    module_aliases[local] = alias.name
            elif isinstance(node, ast.ImportFrom):
                base_mod = _resolve_relative_module(mod_name, is_pkg_init, node.module, node.level)
                for alias in node.names:
                    if alias.name == "*":
                        # Import all indexed top-level symbols from `base_mod`
                        prefix = f"{base_mod}." if base_mod else ""
                        for qname in index.by_qual_name:
                            if qname.startswith(prefix):
                                rest = qname[len(prefix) :]
                                if "." not in rest:
                                    symbol_aliases[rest] = qname
                        for cq in index.by_class_qual_name:
                            if cq.startswith(prefix):
                                rest = cq[len(prefix) :]
                                if "." not in rest:
                                    symbol_aliases[rest] = cq
                        continue
                    local = alias.asname or alias.name
                    cand_sym = f"{base_mod}.{alias.name}" if base_mod else alias.name
                    if cand_sym in index.by_qual_name or cand_sym in index.by_class_qual_name:
                        symbol_aliases[local] = cand_sym
                    elif cand_sym in index.module_files:
                        module_aliases[local] = cand_sym
                    else:
                        # Record anyway so `mod.fn()` or `fn()` still resolves if indexed by suffix
                        symbol_aliases[local] = cand_sym

        # 3. Module-level variable annotations/instantiations
        for node in tree.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                ann_name = _annotation_to_name(node.annotation)
                if ann_name and ann_name in symbol_aliases:
                    module_vars[node.target.id] = symbol_aliases[ann_name]
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name):
                    cls_local = node.value.func.id
                    if cls_local in symbol_aliases:
                        module_vars[node.targets[0].id] = symbol_aliases[cls_local]

        return symbol_aliases, module_aliases, module_vars

    def heal_workspace(self, apply: bool = False) -> HivePropagationReport:
        py_files = self._iter_python_files()
        index = self.index_repo_symbols(py_files)

        all_fixes: List[CallSiteSurgicalFix] = []
        healed_files: List[str] = []
        skipped_files: List[str] = []
        backup_root: Optional[Path] = None

        for fpath in py_files:
            rel_path_obj = fpath.relative_to(self.workspace_dir)
            rel_path = str(rel_path_obj)
            mod_name = _path_to_module_name(rel_path_obj)
            is_pkg_init = fpath.name == "__init__.py"
            try:
                orig_src = fpath.read_text(encoding="utf-8")
                tree = ast.parse(orig_src, filename=str(fpath))
            except Exception:
                continue

            symbol_aliases, module_aliases, module_vars = self._build_file_scope(
                tree, mod_name, is_pkg_init, index
            )
            file_fixes: List[CallSiteSurgicalFix] = []
            file_edits: List[Tuple[int, int, int, int, str]] = []

            class _CallSiteHealer(ast.NodeTransformer):
                def __init__(self) -> None:
                    self._class_stack: List[str] = []
                    self._scope_vars_stack: List[Dict[str, str]] = [dict(module_vars)]

                def _current_var_class(self, var_name: str) -> Optional[str]:
                    for scope in reversed(self._scope_vars_stack):
                        if var_name in scope:
                            return scope[var_name]
                    return None

                def _resolve_class_qual(self, expr: Optional[ast.expr]) -> Optional[str]:
                    if expr is None:
                        return None
                    if isinstance(expr, ast.Name):
                        if expr.id in symbol_aliases and symbol_aliases[expr.id] in index.by_class_qual_name:
                            return symbol_aliases[expr.id]
                        q_local = f"{mod_name}.{expr.id}" if mod_name else expr.id
                        if q_local in index.by_class_qual_name:
                            return q_local
                    elif isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
                        mod_ref = module_aliases.get(expr.value.id)
                        if mod_ref:
                            q_cls = f"{mod_ref}.{expr.attr}"
                            if q_cls in index.by_class_qual_name:
                                return q_cls
                    return None

                def visit_ClassDef(self, node: ast.ClassDef) -> ast.AST:
                    cls_qual = f"{mod_name}.{node.name}" if mod_name else node.name
                    self._class_stack.append(cls_qual)
                    res = self.generic_visit(node)
                    self._class_stack.pop()
                    return res

                def _visit_func(self, node: ast.AST) -> ast.AST:
                    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    local_vars: Dict[str, str] = {}
                    # Map parameter annotations to known classes
                    for a in (
                        list(getattr(node.args, "posonlyargs", []))
                        + list(node.args.args)
                        + list(node.args.kwonlyargs)
                    ):
                        cls_q = self._resolve_class_qual(a.annotation)
                        if cls_q:
                            local_vars[a.arg] = cls_q
                    # Map simple local instantiations/annotations in direct body
                    for stmt in node.body:
                        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                            cls_q = self._resolve_class_qual(stmt.annotation)
                            if cls_q:
                                local_vars[stmt.target.id] = cls_q
                        elif (
                            isinstance(stmt, ast.Assign)
                            and len(stmt.targets) == 1
                            and isinstance(stmt.targets[0], ast.Name)
                            and isinstance(stmt.value, ast.Call)
                        ):
                            cls_q = self._resolve_class_qual(stmt.value.func)
                            if cls_q:
                                local_vars[stmt.targets[0].id] = cls_q
                    self._scope_vars_stack.append(local_vars)
                    res = self.generic_visit(node)
                    self._scope_vars_stack.pop()
                    return res

                def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:
                    return self._visit_func(node)

                def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AST:
                    return self._visit_func(node)

                def _resolve_callee(self, call_node: ast.Call) -> Optional[FunctionSignatureInfo]:
                    fn = call_node.func
                    if isinstance(fn, ast.Name):
                        sym = fn.id
                        if sym in symbol_aliases:
                            target_q = symbol_aliases[sym]
                            if target_q in index.by_qual_name:
                                return index.by_qual_name[target_q]
                        return index.unique_bare_funcs.get(sym)

                    if isinstance(fn, ast.Attribute):
                        attr_name = fn.attr
                        # 1. `self.method(...)` or `cls.method(...)` inside class body
                        if isinstance(fn.value, ast.Name) and fn.value.id in ("self", "cls") and self._class_stack:
                            q_method = f"{self._class_stack[-1]}.{attr_name}"
                            if q_method in index.by_qual_name:
                                return index.by_qual_name[q_method]

                        # 2. `var.method(...)` via annotation/instantiation tracking
                        if isinstance(fn.value, ast.Name):
                            var_cls = self._current_var_class(fn.value.id)
                            if var_cls:
                                q_method = f"{var_cls}.{attr_name}"
                                if q_method in index.by_qual_name:
                                    return index.by_qual_name[q_method]

                            # 3. `ClassName.method(...)` static/class method call
                            cls_q = self._resolve_class_qual(fn.value)
                            if cls_q:
                                q_method = f"{cls_q}.{attr_name}"
                                if q_method in index.by_qual_name:
                                    return index.by_qual_name[q_method]

                            # 4. `mod_alias.func(...)`
                            if fn.value.id in module_aliases:
                                mod_target = module_aliases[fn.value.id]
                                q_func = f"{mod_target}.{attr_name}"
                                if q_func in index.by_qual_name:
                                    return index.by_qual_name[q_func]

                        # 5. Chained module attribute `pkg.submod.func(...)`
                        chain_parts: List[str] = []
                        curr: ast.AST = fn.value
                        while isinstance(curr, ast.Attribute):
                            chain_parts.append(curr.attr)
                            curr = curr.value
                        if isinstance(curr, ast.Name):
                            chain_parts.append(curr.id)
                            chain_parts.reverse()
                            first = chain_parts[0]
                            if first in module_aliases:
                                mod_prefix = module_aliases[first]
                                full_mod = ".".join([mod_prefix] + chain_parts[1:])
                                q_func = f"{full_mod}.{attr_name}"
                                if q_func in index.by_qual_name:
                                    return index.by_qual_name[q_func]

                        # 6. Fallback for unique top-level/method name across repo.
                        # Skipped for builtin-container method names: with an untyped receiver the
                        # call is almost certainly `dict.get`/`list.append`/..., not user code.
                        if isinstance(fn.value, ast.Name) and attr_name not in _BUILTIN_METHOD_NAMES:
                            if attr_name in index.unique_bare_funcs:
                                return index.unique_bare_funcs[attr_name]
                            if attr_name in index.unique_methods:
                                return index.unique_methods[attr_name]

                    return None

                def visit_Call(self, node: ast.Call) -> ast.AST:
                    node = self.generic_visit(node)  # type: ignore[assignment]

                    # Skip calls using `*args` or `**kwargs` unpacking
                    if any(isinstance(a, ast.Starred) for a in node.args):
                        return node
                    if any(kw.arg is None for kw in node.keywords):
                        return node

                    sig = self._resolve_callee(node)
                    if sig is None:
                        return node

                    before_str = ast.unparse(node)
                    mutated_node = copy.deepcopy(node)
                    reasons: List[Tuple[str, str]] = []

                    # Parameters eligible for keyword arguments (excludes positional-only)
                    posonly_set = set(sig.posonly_params)
                    kw_eligible_params = [p for p in sig.param_names if p not in posonly_set] + list(sig.kwonly_params)
                    all_named_params = list(sig.param_names) + list(sig.kwonly_params)

                    # 1. Heal renamed keyword arguments (`UNKNOWN_KWARG`)
                    if not sig.has_kwargs:
                        bound_positional = sig.param_names[: len(mutated_node.args)]
                        used_params = set(bound_positional) | {
                            kw.arg for kw in mutated_node.keywords if kw.arg in kw_eligible_params
                        }
                        available = [p for p in kw_eligible_params if p not in used_params]

                        for kw in mutated_node.keywords:
                            if kw.arg and kw.arg not in kw_eligible_params:
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
                        kw.arg for kw in mutated_node.keywords if kw.arg and kw.arg in all_named_params
                    }
                    missing_req = [p for p in sig.required_params if p not in bound_names]
                    if missing_req:
                        for req_p in missing_req:
                            ann = sig.param_annotations.get(req_p, "")
                            default_expr = _default_ast_for_param(req_p, ann)
                            if req_p in posonly_set:
                                mutated_node.args.append(default_expr)
                                reasons.append(
                                    (
                                        "MISSING_REQUIRED_INJECTED",
                                        f"Injected required positional argument `{ast.unparse(default_expr)}` for `{req_p}`",
                                    )
                                )
                            else:
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
                            callee_name=sig.func_name,
                            drift_kind=drift_kinds,
                            detail=detail_str,
                            before_call=before_str,
                            after_call=after_str,
                        )
                    )
                    end_lineno = getattr(node, "end_lineno", None)
                    end_col = getattr(node, "end_col_offset", None)
                    if end_lineno is not None and end_col is not None:
                        file_edits.append((node.lineno, node.col_offset, end_lineno, end_col, after_str))
                    return mutated_node

            _CallSiteHealer().visit(tree)
            if not file_fixes:
                continue
            if apply:
                new_src = _apply_call_edits(orig_src, file_edits)
                try:
                    ast.parse(new_src, filename=str(fpath))
                except SyntaxError:
                    skipped_files.append(rel_path)
                    continue
                if backup_root is None:
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    backup_root = self.workspace_dir / ".thauthi" / "hive-backup" / stamp
                backup_path = backup_root / rel_path_obj
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_text(orig_src, encoding="utf-8")
                fpath.write_text(new_src, encoding="utf-8")
            all_fixes.extend(file_fixes)
            healed_files.append(rel_path)

        return HivePropagationReport(
            files_scanned=len(py_files),
            functions_indexed=len(index.by_qual_name),
            drifts_detected=len(all_fixes),
            files_healed=healed_files,
            fixes=all_fixes,
            backup_dir=str(backup_root) if backup_root else None,
            skipped_files=skipped_files,
        )
