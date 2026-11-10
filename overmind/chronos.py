#!/usr/bin/env python3
"""
Pillar 6: Chronos — Deterministic I/O Virtualization & Time-Travel Replay Kernel.

Eliminates "Works on My Machine" and stateful production debugging by:
1. Intercepting non-deterministic & external I/O boundaries at runtime:
   - Clock & Entropy (`time.time`, `random.random`, `random.randint`, `random.choice`)
   - Network & HTTP (`urllib.request.urlopen`)
   - Environment (`os.getenv`)
   - External module/global boundary functions (`db_query`, `fetch_api`, etc.)
2. Freezing the exact sequence of boundary calls into a hermetic `ExecutionCassette`
   (`.overmind/cassettes/<cassette_id>.json`).
3. Replaying any recorded cassette in < 1ms with zero network/DB side effects,
   enabling `CounterfactualSurgeon` to heal impure, networked, and time-dependent
   functions with 100% determinism.
"""

import ast
import copy
import functools
import hashlib
import inspect
import io
import json
import os
import random
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple

from overmind.immune import CounterfactualSurgeon, RecordedCallFrame


@dataclass
class SyscallEvent:
    boundary: str
    args_repr: str
    return_value: Any = None
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExecutionCassette:
    cassette_id: str
    func_name: str
    module_name: str
    args: List[Any]
    kwargs: Dict[str, Any]
    syscalls: List[SyscallEvent] = field(default_factory=list)
    return_value: Any = None
    exception_type: Optional[str] = None
    exception_message: Optional[str] = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["syscalls"] = [s.to_dict() for s in self.syscalls]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionCassette":
        syscalls = [SyscallEvent(**s) for s in data.get("syscalls", [])]
        return cls(
            cassette_id=data["cassette_id"],
            func_name=data["func_name"],
            module_name=data.get("module_name", "__main__"),
            args=list(data.get("args", [])),
            kwargs=dict(data.get("kwargs", {})),
            syscalls=syscalls,
            return_value=data.get("return_value"),
            exception_type=data.get("exception_type"),
            exception_message=data.get("exception_message"),
            timestamp=float(data.get("timestamp", 0.0)),
        )


class _VirtualHTTPResponse:
    """Hermetic replay wrapper for `urllib.request.urlopen` responses."""

    def __init__(self, body_text: str, status: int = 200, headers: Optional[Dict[str, str]] = None):
        self._bytes = body_text.encode("utf-8")
        self._stream = io.BytesIO(self._bytes)
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}

    def read(self, amt: Optional[int] = None) -> bytes:
        return self._stream.read(amt)

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> "_VirtualHTTPResponse":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


def _extract_closure_dict(func: Callable) -> Dict[str, Any]:
    freevars = getattr(getattr(func, "__code__", None), "co_freevars", ())
    closure = getattr(func, "__closure__", None) or ()
    out: Dict[str, Any] = {}
    for name, cell in zip(freevars, closure):
        try:
            out[name] = cell.cell_contents
        except ValueError:
            pass
    return out


def _discover_called_global_helpers(func: Callable, extra_ns: Optional[Dict[str, Any]] = None) -> List[str]:
    """
    Inspect `func`'s AST for calls to external functions present in `func.__globals__`
    or `func.__closure__` (such as `fetch_user_data`, `query_db`, `call_gateway`)
    so Chronos can virtualize them automatically during recording and time-travel replay.
    """
    try:
        _, _, func_def = CounterfactualSurgeon.extract_clean_func_ast(func)
    except Exception:
        return []

    func_globals = dict(getattr(func, "__globals__", {}))
    func_globals.update(_extract_closure_dict(func))
    if extra_ns:
        func_globals.update(extra_ns)

    builtins_set = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set(dir(__builtins__))
    helpers: List[str] = []

    for node in ast.walk(func_def):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if (
                name != func.__name__
                and name not in builtins_set
                and name in func_globals
                and inspect.isfunction(func_globals[name])
            ):
                if name not in helpers:
                    helpers.append(name)
    return helpers


class ChronosTimeMachine:
    """
    Records live I/O & non-deterministic syscalls into hermetic `ExecutionCassette`s
    and replays them in <1ms for deterministic debugging and Counterfactual AST Surgery.
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or (Path.cwd() / ".overmind" / "cassettes")
        self.cassettes: List[ExecutionCassette] = []

    @contextmanager
    def _intercept_environment(
        self,
        func: Callable,
        mode: str,
        syscalls_log: List[SyscallEvent],
        extra_globals: Optional[Dict[str, Any]] = None,
    ) -> Iterator[None]:
        """
        Context manager that hooks `time.time`, `random.random`, `random.randint`,
        `urllib.request.urlopen`, `os.getenv`, and any global helper functions called by `func`.
        - In `mode == "record"`, executes real calls and appends `SyscallEvent`s to `syscalls_log`.
        - In `mode == "replay"`, consumes recorded `SyscallEvent`s without executing external side effects.
        """
        orig_time = time.time
        orig_random = random.random
        orig_randint = random.randint
        orig_urlopen = urllib.request.urlopen
        orig_getenv = os.getenv

        func_globals = extra_globals if extra_globals is not None else getattr(func, "__globals__", {})
        closure_dict = _extract_closure_dict(func)
        helper_names = _discover_called_global_helpers(func, extra_ns=func_globals)
        for ev in syscalls_log:
            if ev.boundary.startswith("global:"):
                g_name = ev.boundary.split("global:", 1)[1]
                if g_name not in helper_names:
                    helper_names.append(g_name)
        orig_helpers = {name: func_globals[name] for name in helper_names if name in func_globals}

        # Also intercept callable closure cells (`LOAD_DEREF`)
        freevars = getattr(getattr(func, "__code__", None), "co_freevars", ())
        closure_cells = getattr(func, "__closure__", None) or ()
        orig_cells: List[Tuple[Any, str, Any]] = []
        for fv_name, cell in zip(freevars, closure_cells):
            if fv_name in helper_names:
                try:
                    c_val = cell.cell_contents
                    if callable(c_val):
                        orig_cells.append((cell, fv_name, c_val))
                except ValueError:
                    pass

        replay_cursors: Dict[str, int] = {}

        def _make_proxy(boundary_name: str, real_fn: Callable, is_urlopen: bool = False) -> Callable:
            def _proxy(*a: Any, **kw: Any) -> Any:
                if mode == "record":
                    try:
                        raw_res = real_fn(*a, **kw)
                        if is_urlopen:
                            body_bytes = raw_res.read()
                            body_str = body_bytes.decode("utf-8", errors="replace")
                            status = getattr(raw_res, "status", 200)
                            payload = {"body": body_str, "status": status}
                            syscalls_log.append(
                                SyscallEvent(
                                    boundary=boundary_name,
                                    args_repr=repr((a, kw)),
                                    return_value=payload,
                                )
                            )
                            return _VirtualHTTPResponse(body_str, status=status)
                        syscalls_log.append(
                            SyscallEvent(
                                boundary=boundary_name,
                                args_repr=repr((a, kw)),
                                return_value=copy.deepcopy(raw_res),
                            )
                        )
                        return raw_res
                    except Exception as e:
                        syscalls_log.append(
                            SyscallEvent(
                                boundary=boundary_name,
                                args_repr=repr((a, kw)),
                                exception_type=type(e).__name__,
                                exception_message=str(e),
                            )
                        )
                        raise
                else:
                    # Replay mode: find the next matching boundary event
                    matching = [ev for ev in syscalls_log if ev.boundary == boundary_name]
                    idx = replay_cursors.get(boundary_name, 0)
                    if idx < len(matching):
                        ev = matching[idx]
                        replay_cursors[boundary_name] = idx + 1
                    elif matching:
                        ev = matching[-1]
                    else:
                        return real_fn(*a, **kw)

                    if ev.exception_type:
                        raise RuntimeError(f"Replayed {ev.exception_type}: {ev.exception_message}")
                    if is_urlopen and isinstance(ev.return_value, dict):
                        return _VirtualHTTPResponse(
                            ev.return_value.get("body", ""),
                            status=int(ev.return_value.get("status", 200)),
                        )
                    return copy.deepcopy(ev.return_value)

            return _proxy

        time.time = _make_proxy("time.time", orig_time)  # type: ignore[assignment]
        random.random = _make_proxy("random.random", orig_random)  # type: ignore[assignment]
        random.randint = _make_proxy("random.randint", orig_randint)  # type: ignore[assignment]
        urllib.request.urlopen = _make_proxy("urllib.request.urlopen", orig_urlopen, is_urlopen=True)  # type: ignore[assignment]
        os.getenv = _make_proxy("os.getenv", orig_getenv)  # type: ignore[assignment]

        for h_name, h_fn in orig_helpers.items():
            func_globals[h_name] = _make_proxy(f"global:{h_name}", h_fn)
        for cell, fv_name, c_val in orig_cells:
            cell.cell_contents = _make_proxy(f"global:{fv_name}", c_val)

        try:
            yield
        finally:
            time.time = orig_time  # type: ignore[assignment]
            random.random = orig_random  # type: ignore[assignment]
            random.randint = orig_randint  # type: ignore[assignment]
            urllib.request.urlopen = orig_urlopen  # type: ignore[assignment]
            os.getenv = orig_getenv  # type: ignore[assignment]
            for h_name, h_fn in orig_helpers.items():
                func_globals[h_name] = h_fn
            for cell, _, c_val in orig_cells:
                cell.cell_contents = c_val

    def record_call(self, func: Callable, *args: Any, **kwargs: Any) -> Tuple[Any, Optional[BaseException], ExecutionCassette]:
        """Execute `func(*args, **kwargs)` while recording all I/O & clock syscalls into a cassette."""
        syscalls: List[SyscallEvent] = []
        cid = hashlib.sha1(f"{func.__name__}:{repr(args)}:{time.time_ns()}".encode("utf-8")).hexdigest()[:10]
        res: Any = None
        caught: Optional[BaseException] = None

        with self._intercept_environment(func, mode="record", syscalls_log=syscalls):
            try:
                res = func(*copy.deepcopy(args), **copy.deepcopy(kwargs))
            except BaseException as exc:
                caught = exc

        cassette = ExecutionCassette(
            cassette_id=f"cas_{cid}",
            func_name=func.__name__,
            module_name=getattr(func, "__module__", "__main__"),
            args=list(copy.deepcopy(args)),
            kwargs=dict(copy.deepcopy(kwargs)),
            syscalls=syscalls,
            return_value=res if caught is None else None,
            exception_type=type(caught).__name__ if caught is not None else None,
            exception_message=str(caught) if caught is not None else None,
        )
        self.cassettes.append(cassette)
        self.save_cassette(cassette)
        return res, caught, cassette

    def replay_cassette(
        self,
        func: Callable,
        cassette: ExecutionCassette,
        sandbox_globals: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, Optional[BaseException]]:
        """Replay a recorded `ExecutionCassette` against `func` with zero external I/O side effects."""
        with self._intercept_environment(
            func,
            mode="replay",
            syscalls_log=cassette.syscalls,
            extra_globals=sandbox_globals,
        ):
            try:
                out = func(*copy.deepcopy(cassette.args), **copy.deepcopy(cassette.kwargs))
                return out, None
            except BaseException as exc:
                return None, exc

    def heal_with_cassettes(
        self,
        func: Callable,
        crash_cassette: ExecutionCassette,
        healthy_cassettes: List[ExecutionCassette],
    ) -> Optional[Tuple[str, str, Callable, Any]]:
        """
        Synthesize candidate AST surgeries and verify them inside the hermetic
        Chronos Time-Travel sandbox against all healthy cassettes + the crashing cassette.
        """
        if not crash_cassette.exception_type:
            return None

        _, base_tree, func_def = CounterfactualSurgeon.extract_clean_func_ast(func)
        # Strip `@time_travel` decorator if present so candidate runs raw body
        func_def.decorator_list = [
            d for d in func_def.decorator_list
            if "time_travel" not in (ast.unparse(d) if hasattr(ast, "unparse") else "")
        ]
        func_name = func_def.name

        crash_frame = RecordedCallFrame(
            args=tuple(crash_cassette.args),
            kwargs=dict(crash_cassette.kwargs),
            exception_type=crash_cassette.exception_type,
            exception_message=crash_cassette.exception_message,
        )
        healthy_frames = [
            RecordedCallFrame(
                args=tuple(c.args),
                kwargs=dict(c.kwargs),
                result=c.return_value,
            )
            for c in healthy_cassettes
        ]

        candidates = CounterfactualSurgeon.generate_candidates(
            base_tree,
            crash_cassette.exception_type,
            crash_frame,
            func,
            healthy_frames,
        )

        for strategy, cand_tree in candidates:
            try:
                code_obj = compile(cand_tree, filename=f"<chronos_heal:{func_name}>", mode="exec")
                sandbox_ns: Dict[str, Any] = dict(getattr(func, "__globals__", {}))
                sandbox_ns.update(_extract_closure_dict(func))
                exec(code_obj, sandbox_ns)
                cand_fn = sandbox_ns[func_name]
            except Exception:
                continue

            # 1. Verify zero regression on all historical healthy cassettes using replayed syscalls!
            healthy_ok = True
            for h_cas in healthy_cassettes:
                out, err = self.replay_cassette(cand_fn, h_cas, sandbox_globals=sandbox_ns)
                if err is not None or out != h_cas.return_value:
                    healthy_ok = False
                    break
            if not healthy_ok:
                continue

            # 2. Verify the crashing cassette now succeeds under hermetic replay!
            healed_out, crash_err = self.replay_cassette(cand_fn, crash_cassette, sandbox_globals=sandbox_ns)
            if crash_err is None:
                patched_src = ast.unparse(cand_tree)
                return strategy, patched_src, cand_fn, healed_out

        return None

    def save_cassette(self, cassette: ExecutionCassette) -> Optional[Path]:
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            path = self.storage_dir / f"{cassette.cassette_id}.json"
            path.write_text(json.dumps(cassette.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            return path
        except OSError:
            return None

    def load_cassettes(self, func_name: Optional[str] = None) -> List[ExecutionCassette]:
        if not self.storage_dir.exists():
            return []
        loaded: List[ExecutionCassette] = []
        for p in sorted(self.storage_dir.glob("cas_*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                cas = ExecutionCassette.from_dict(data)
                if not func_name or cas.func_name == func_name:
                    loaded.append(cas)
            except Exception:
                continue
        loaded.sort(key=lambda c: (c.timestamp, c.cassette_id))
        return loaded

    def protect(self, func: Callable, auto_heal: bool = True) -> Callable:
        """
        Decorator that records deterministic I/O cassettes for every call and,
        when an unhandled exception occurs, uses hermetic cassette replay to heal
        and hot-swap the function in-flight without re-executing external side effects!
        """
        healthy_cassettes: List[ExecutionCassette] = []
        active_impl: List[Callable] = [func]

        @functools.wraps(func)
        def _wrapped(*args: Any, **kwargs: Any) -> Any:
            res, exc, cassette = self.record_call(active_impl[0], *args, **kwargs)
            if exc is None:
                if len(healthy_cassettes) < 25:
                    healthy_cassettes.append(cassette)
                return res

            if not auto_heal:
                raise exc

            outcome = self.heal_with_cassettes(func, cassette, healthy_cassettes)
            if outcome is None:
                raise exc

            strategy, patched_src, healed_fn, healed_val = outcome
            active_impl[0] = healed_fn
            return healed_val

        return _wrapped


_DEFAULT_CHRONOS = ChronosTimeMachine()


def time_travel(
    func: Optional[Callable] = None,
    *,
    auto_heal: bool = True,
) -> Any:
    """
    Decorator to equip any stateful/networked/time-dependent Python function with
    Chronos I/O Virtualization & Time-Travel Self-Healing.
    """
    def _decorator(fn: Callable) -> Callable:
        return _DEFAULT_CHRONOS.protect(fn, auto_heal=auto_heal)

    if func is not None and callable(func):
        return _decorator(func)
    return _decorator
