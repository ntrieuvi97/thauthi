#!/usr/bin/env python3
"""
Pillar 1: Omniscient Runtime Tracer & Causal Fault Localization.

Eliminates "Static Text Guessing" by hooking directly into the Python execution
runtime (sys.settrace) to record:
- Live Call Frame DNA (exact arguments, local state transitions, return values)
- Line-by-line Variable Mutation Timelines (which line mutated which variable)
- Instruction/Line step counts for empirical O(N) complexity profiling
- Backward Dynamic Slicing & Causal Fault Localization when exceptions occur
"""

import ast
import copy
import hashlib
import inspect
import json
import linecache
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


def _safe_repr(val: Any, max_len: int = 120) -> str:
    try:
        r = repr(val)
    except Exception:
        r = f"<unrepresentable {type(val).__name__}>"
    if len(r) > max_len:
        return r[: max_len - 3] + "..."
    return r


def _safe_serialize_value(val: Any, depth: int = 0) -> Any:
    """Serialize live runtime objects into JSON-safe primitives for deterministic replay."""
    if depth > 3:
        return _safe_repr(val)
    if val is None or isinstance(val, (bool, int, float, str)):
        return val
    if isinstance(val, (list, tuple)):
        return [_safe_serialize_value(v, depth + 1) for v in val[:25]]
    if isinstance(val, dict):
        return {str(k): _safe_serialize_value(v, depth + 1) for k, v in list(val.items())[:25]}
    if isinstance(val, set):
        return [_safe_serialize_value(v, depth + 1) for v in list(val)[:25]]
    return _safe_repr(val)


@dataclass
class StateMutation:
    func_name: str
    filename: str
    lineno: int
    source_line: str
    variable: str
    old_value: Any
    new_value: Any
    step_index: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FrameDNA:
    frame_id: str
    func_name: str
    filename: str
    first_lineno: int
    args: Dict[str, Any]
    arg_types: Dict[str, str]
    return_value: Any = None
    return_type: Optional[str] = None
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    crash_lineno: Optional[int] = None
    crash_line_source: Optional[str] = None
    crash_locals: Dict[str, Any] = field(default_factory=dict)
    mutations: List[StateMutation] = field(default_factory=list)
    steps_executed: int = 0
    duration_us: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["mutations"] = [m.to_dict() for m in self.mutations]
        return d


@dataclass
class CausalFaultReport:
    func_name: str
    filename: str
    crash_lineno: int
    crash_line_source: str
    exception_type: str
    exception_message: str
    suspect_variables: List[str]
    causal_chain: List[Dict[str, Any]]
    root_cause_mutation: Optional[Dict[str, Any]]
    crash_locals: Dict[str, Any]
    frame_id: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def render_compact_diagnosis(self) -> str:
        lines = [
            f"⚡ [OMNISCIENT CAUSAL FAULT GRAPH] {self.exception_type}: {self.exception_message}",
            f"  • Crash Site: {self.func_name}() at {Path(self.filename).name}:{self.crash_lineno} -> `{self.crash_line_source}`",
            f"  • Suspect Symbols in Failing Expression: {', '.join(self.suspect_variables) or 'N/A'}",
        ]
        if self.root_cause_mutation:
            rc = self.root_cause_mutation
            lines.append(
                f"  • Root Cause Origin: Line {rc['lineno']} (`{rc['source_line']}`) "
                f"mutated `{rc['variable']}`: {rc['old_value']} -> {rc['new_value']}"
            )
        if self.causal_chain:
            lines.append("  • Backward Causal Slice:")
            for step in self.causal_chain:
                lines.append(
                    f"      [Step #{step['step_index']} | L{step['lineno']}] `{step['source_line']}` "
                    f"({step['variable']}: {step['old_value']} -> {step['new_value']})"
                )
        return "\n".join(lines)


def extract_expression_symbols(source_line: str) -> List[str]:
    """Extract variable identifiers referenced in a crashing source line."""
    text = source_line.strip()
    if not text:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Fallback regex token extraction if line is a partial multi-line statement or header
        import re
        tokens = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", text)
        keywords = {"return", "if", "elif", "else", "for", "while", "in", "and", "or", "not", "is", "None", "True", "False", "raise", "assert"}
        return [t for t in dict.fromkeys(tokens) if t not in keywords]

    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id not in names:
                names.append(node.id)
    return names


def build_causal_fault_report(frame_dna: FrameDNA) -> Optional[CausalFaultReport]:
    """
    Perform Backward Dynamic Slicing over a crashing FrameDNA to isolate the exact
    state mutation chain and root-cause line that triggered the exception.
    """
    if not frame_dna.exception_type or not frame_dna.crash_lineno:
        return None

    crash_src = (frame_dna.crash_line_source or "").strip()
    suspects = extract_expression_symbols(crash_src)

    # Track dependencies backward through the recorded state mutations
    relevant_vars: Set[str] = set(suspects)
    causal_chain: List[Dict[str, Any]] = []

    for mut in reversed(frame_dna.mutations):
        if mut.variable in relevant_vars or not suspects:
            causal_chain.append(mut.to_dict())
            # Also add symbols on the RHS of the mutating line to trace transitive causes
            rhs_syms = extract_expression_symbols(mut.source_line)
            relevant_vars.update(rhs_syms)

    causal_chain.reverse()
    root_cause = causal_chain[0] if causal_chain else None

    return CausalFaultReport(
        func_name=frame_dna.func_name,
        filename=frame_dna.filename,
        crash_lineno=frame_dna.crash_lineno,
        crash_line_source=crash_src,
        exception_type=frame_dna.exception_type,
        exception_message=frame_dna.exception_message or "",
        suspect_variables=suspects,
        causal_chain=causal_chain,
        root_cause_mutation=root_cause,
        crash_locals=frame_dna.crash_locals,
        frame_id=frame_dna.frame_id,
    )


class OmniscientTracer:
    """
    Zero-dependency live execution tracer that hooks into Python call frames,
    recording variable mutations, instruction counts, and Causal Fault Graphs.
    """

    def __init__(
        self,
        target_files: Optional[Set[str]] = None,
        target_funcs: Optional[Set[str]] = None,
        max_steps_per_frame: int = 5000,
        storage_dir: Optional[Path] = None,
    ):
        self.target_files = {str(Path(p).resolve()) for p in target_files} if target_files else None
        self.target_funcs = set(target_funcs) if target_funcs else None
        self.max_steps_per_frame = max_steps_per_frame
        self.storage_dir = storage_dir or (Path.cwd() / ".thauthi" / "frames")
        self.recorded_frames: List[FrameDNA] = []
        self.fault_reports: List[CausalFaultReport] = []
        self._active_stack: Dict[int, Dict[str, Any]] = {}
        self._step_counter: int = 0

    def _should_trace_frame(self, frame: Any) -> bool:
        code = frame.f_code
        func_name = code.co_name
        if func_name in ("<genexpr>", "<listcomp>", "<setcomp>", "<dictcomp>"):
            if frame.f_back is not None and id(frame.f_back) in self._active_stack:
                return True
            return False
        if func_name.startswith("<") and func_name != "<module>":
            return False
        filename = code.co_filename
        if filename.startswith("<") and not filename.startswith("<omniscient"):
            return False

        # Ignore Thauthi's own internal tracer/immune machinery unless explicitly targeted
        if "thauthi/omniscient.py" in filename.replace("\\", "/") or "thauthi/immune.py" in filename.replace("\\", "/"):
            if not self.target_funcs or func_name not in self.target_funcs:
                return False

        if self.target_funcs and func_name in self.target_funcs:
            return True

        if self.target_files:
            try:
                resolved = str(Path(filename).resolve())
            except OSError:
                resolved = filename
            return resolved in self.target_files

        # Default: trace files inside current working directory or <omniscient*> buffers
        if filename.startswith("<omniscient"):
            return True
        try:
            resolved_path = Path(filename).resolve()
            cwd = Path.cwd().resolve()
            return cwd in resolved_path.parents or resolved_path.parent == cwd
        except OSError:
            return False

    def _snapshot_locals(self, f_locals: Dict[str, Any]) -> Dict[str, Any]:
        snap: Dict[str, Any] = {}
        for k, v in f_locals.items():
            if k.startswith("__"):
                continue
            if inspect.ismodule(v) or inspect.isfunction(v) or inspect.isclass(v):
                continue
            snap[k] = _safe_serialize_value(v)
        return snap

    def _get_source_line(self, filename: str, lineno: int, source_map: Optional[Dict[int, str]] = None) -> str:
        if source_map and lineno in source_map:
            return source_map[lineno].strip()
        line = linecache.getline(filename, lineno)
        return line.strip() if line else ""

    def _trace_dispatch(self, frame: Any, event: str, arg: Any) -> Optional[Callable]:
        if event == "call":
            if not self._should_trace_frame(frame):
                return None
            frame_key = id(frame)
            code = frame.f_code
            arg_info = inspect.getargvalues(frame)
            captured_args: Dict[str, Any] = {}
            captured_types: Dict[str, str] = {}
            for a_name in arg_info.args:
                if a_name in arg_info.locals:
                    val = arg_info.locals[a_name]
                    captured_args[a_name] = _safe_serialize_value(val)
                    captured_types[a_name] = type(val).__name__

            raw_id = f"{code.co_name}:{code.co_firstlineno}:{json.dumps(captured_args, sort_keys=True, default=str)}:{time.time_ns()}"
            frame_id = "frm_" + hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:10]

            dna = FrameDNA(
                frame_id=frame_id,
                func_name=code.co_name,
                filename=code.co_filename,
                first_lineno=code.co_firstlineno,
                args=captured_args,
                arg_types=captured_types,
            )
            self._active_stack[frame_key] = {
                "dna": dna,
                "prev_locals": self._snapshot_locals(frame.f_locals),
                "prev_lineno": code.co_firstlineno,
                "start_ns": time.perf_counter_ns(),
            }
            return self._trace_dispatch

        frame_key = id(frame)
        state = self._active_stack.get(frame_key)
        if state is None:
            return None

        dna: FrameDNA = state["dna"]

        if event == "line":
            dna.steps_executed += 1
            self._step_counter += 1
            if dna.steps_executed <= self.max_steps_per_frame:
                curr_locals = self._snapshot_locals(frame.f_locals)
                prev_locals = state["prev_locals"]
                executed_lineno = state["prev_lineno"]
                executed_src = self._get_source_line(dna.filename, executed_lineno)

                for var_name, new_val in curr_locals.items():
                    old_val = prev_locals.get(var_name, "<undefined>")
                    if old_val != new_val:
                        dna.mutations.append(
                            StateMutation(
                                func_name=dna.func_name,
                                filename=dna.filename,
                                lineno=executed_lineno,
                                source_line=executed_src,
                                variable=var_name,
                                old_value=old_val,
                                new_value=new_val,
                                step_index=self._step_counter,
                            )
                        )
                state["prev_locals"] = curr_locals
            state["prev_lineno"] = frame.f_lineno
            return self._trace_dispatch

        if event == "exception":
            exc_type, exc_value, _ = arg
            dna.exception_type = exc_type.__name__ if exc_type else "Exception"
            dna.exception_message = str(exc_value)
            dna.crash_lineno = frame.f_lineno
            dna.crash_line_source = self._get_source_line(dna.filename, frame.f_lineno)
            dna.crash_locals = self._snapshot_locals(frame.f_locals)
            return self._trace_dispatch

        if event == "return":
            dna.duration_us = round((time.perf_counter_ns() - state["start_ns"]) / 1000.0, 2)
            if dna.exception_type is None:
                dna.return_value = _safe_serialize_value(arg)
                dna.return_type = type(arg).__name__
            self.recorded_frames.append(dna)
            if dna.exception_type is not None:
                report = build_causal_fault_report(dna)
                if report is not None:
                    self.fault_reports.append(report)
            self._active_stack.pop(frame_key, None)
            return None

        return self._trace_dispatch

    def run_callable(self, func: Callable, *args: Any, **kwargs: Any) -> Tuple[Any, Optional[ Exception ], List[FrameDNA]]:
        """Execute a callable under the Omniscient Kernel and return (result, exception, frames)."""
        prev_trace = sys.gettrace()
        exc_caught: Optional[Exception] = None
        result: Any = None
        start_idx = len(self.recorded_frames)
        sys.settrace(self._trace_dispatch)
        try:
            result = func(*args, **kwargs)
        except Exception as e:
            exc_caught = e
        finally:
            sys.settrace(prev_trace)
        return result, exc_caught, self.recorded_frames[start_idx:]

    def persist_frames(self, output_path: Optional[Path] = None) -> Path:
        out = output_path or (self.storage_dir / "trace_frames.jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            for frm in self.recorded_frames:
                f.write(json.dumps(frm.to_dict(), ensure_ascii=False) + "\n")
        return out
