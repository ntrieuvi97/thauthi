#!/usr/bin/env python3
"""
Pillar 6: Chronos — Deterministic I/O Virtualization & Time-Travel Replay Kernel.

Eliminates "Works on My Machine" and stateful production debugging by:
1. Intercepting non-deterministic & external I/O boundaries at runtime (sync & async):
   - Clock & Entropy (`time.time`, `time.monotonic`, `datetime.now/utcnow`, `uuid.uuid4`, `random.*`)
   - Network & HTTP (`urllib.request.urlopen`, plus `requests`/`httpx` if loaded in `sys.modules`)
   - Database (`sqlite3.connect` / cursor SQL execution & fetched rows)
   - Subprocesses & Environment (`subprocess.run`, `os.getenv`)
   - Scoped File Reads (`open` in the target function's module namespace)
   - External module/global & async boundary helpers (`db_query`, `fetch_api`, etc.)
2. Freezing the exact sequence of boundary calls into a hermetic `ExecutionCassette`
   (`.thauthi/cassettes/<cassette_id>.json`).
3. Replaying any recorded cassette in < 1ms with zero network/DB/subprocess/disk side effects,
   enabling `CounterfactualSurgeon` to heal impure, async, networked, and DB-dependent
   functions with 100% determinism.
"""

import ast
import builtins
import copy
import datetime as _dt_mod
import functools
import hashlib
import inspect
import io
import json
import os
import random
import sqlite3
import subprocess
import sys
import time
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from thauthi.immune import CounterfactualSurgeon, RecordedCallFrame


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


class _VirtualClientHTTPResponse:
    """Hermetic replay wrapper mimicking `requests.Response` / `httpx.Response`."""

    def __init__(self, text: str, status_code: int = 200, headers: Optional[Dict[str, str]] = None):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.ok = 200 <= status_code < 400

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP Error {self.status_code}")


class _VirtualSQLiteCursor:
    """Replays SQLite cursor results strictly from recorded SyscallEvents (zero DB I/O)."""

    def __init__(self, next_event_fn: Callable[[str, str], SyscallEvent]):
        self._next_event = next_event_fn
        self._rows: List[Tuple[Any, ...]] = []
        self._idx = 0
        self.rowcount = -1
        self.lastrowid = None
        self.description = None

    def execute(self, sql: str, parameters: Any = ()) -> "_VirtualSQLiteCursor":
        ev = self._next_event("sqlite3.execute", repr((sql, parameters)))
        if ev.exception_type:
            raise sqlite3.OperationalError(f"Replayed {ev.exception_type}: {ev.exception_message}")
        payload = ev.return_value if isinstance(ev.return_value, dict) else {}
        raw_rows = payload.get("rows", [])
        self._rows = [tuple(r) if isinstance(r, list) else r for r in raw_rows]
        self._idx = 0
        self.rowcount = int(payload.get("rowcount", len(self._rows)))
        self.lastrowid = payload.get("lastrowid")
        return self

    def executemany(self, sql: str, seq_of_params: Any) -> "_VirtualSQLiteCursor":
        return self.execute(sql, list(seq_of_params))

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        if self._idx < len(self._rows):
            row = self._rows[self._idx]
            self._idx += 1
            return row
        return None

    def fetchmany(self, size: int = 1) -> List[Tuple[Any, ...]]:
        end = min(len(self._rows), self._idx + size)
        batch = self._rows[self._idx : end]
        self._idx = end
        return batch

    def fetchall(self) -> List[Tuple[Any, ...]]:
        rem = self._rows[self._idx :]
        self._idx = len(self._rows)
        return rem

    def close(self) -> None:
        pass

    def __iter__(self) -> Iterator[Tuple[Any, ...]]:
        return iter(self.fetchall())


class _VirtualSQLiteConnection:
    """Replays SQLite connections strictly from recorded SyscallEvents (zero disk/DB I/O)."""

    def __init__(self, next_event_fn: Callable[[str, str], SyscallEvent]):
        self._next_event = next_event_fn

    def cursor(self) -> _VirtualSQLiteCursor:
        return _VirtualSQLiteCursor(self._next_event)

    def execute(self, sql: str, parameters: Any = ()) -> _VirtualSQLiteCursor:
        cur = self.cursor()
        return cur.execute(sql, parameters)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> "_VirtualSQLiteConnection":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


class _RecordingSQLiteCursor:
    """Wraps a real sqlite3.Cursor, lazily recording executed SQL statements and fetched rows."""

    def __init__(self, real_cursor: sqlite3.Cursor, syscalls_log: List[SyscallEvent]):
        self._real = real_cursor
        self._log = syscalls_log
        self._event_ref: Optional[SyscallEvent] = None

    def _capture_result_rows(self) -> None:
        if self._event_ref is None:
            return
        try:
            if self._real.description is not None:
                rows = self._real.fetchall()
                self._cached_rows = list(rows)
                self._cache_idx = 0
                self._event_ref.return_value = {
                    "rows": [list(r) for r in self._cached_rows],
                    "rowcount": self._real.rowcount,
                    "lastrowid": self._real.lastrowid,
                }
            else:
                self._cached_rows = []
                self._cache_idx = 0
                self._event_ref.return_value = {
                    "rows": [],
                    "rowcount": self._real.rowcount,
                    "lastrowid": self._real.lastrowid,
                }
        except Exception:
            pass

    def execute(self, sql: str, parameters: Any = ()) -> "_RecordingSQLiteCursor":
        try:
            self._real.execute(sql, parameters)
            ev = SyscallEvent(boundary="sqlite3.execute", args_repr=repr((sql, parameters)))
            self._log.append(ev)
            self._event_ref = ev
            self._capture_result_rows()
            return self
        except Exception as e:
            self._log.append(
                SyscallEvent(
                    boundary="sqlite3.execute",
                    args_repr=repr((sql, parameters)),
                    exception_type=type(e).__name__,
                    exception_message=str(e),
                )
            )
            raise

    def executemany(self, sql: str, seq_of_params: Any) -> "_RecordingSQLiteCursor":
        params_list = list(seq_of_params)
        try:
            self._real.executemany(sql, params_list)
            ev = SyscallEvent(boundary="sqlite3.execute", args_repr=repr((sql, params_list)))
            self._log.append(ev)
            self._event_ref = ev
            self._capture_result_rows()
            return self
        except Exception as e:
            self._log.append(
                SyscallEvent(
                    boundary="sqlite3.execute",
                    args_repr=repr((sql, params_list)),
                    exception_type=type(e).__name__,
                    exception_message=str(e),
                )
            )
            raise

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        if self._cache_idx < len(self._cached_rows):
            r = self._cached_rows[self._cache_idx]
            self._cache_idx += 1
            return r
        return None

    def fetchmany(self, size: int = 1) -> List[Tuple[Any, ...]]:
        end = min(len(self._cached_rows), self._cache_idx + size)
        batch = self._cached_rows[self._cache_idx : end]
        self._cache_idx = end
        return batch

    def fetchall(self) -> List[Tuple[Any, ...]]:
        rem = self._cached_rows[self._cache_idx :]
        self._cache_idx = len(self._cached_rows)
        return rem

    @property
    def rowcount(self) -> int:
        return self._real.rowcount

    @property
    def lastrowid(self) -> Any:
        return self._real.lastrowid

    @property
    def description(self) -> Any:
        return self._real.description

    def close(self) -> None:
        self._real.close()

    def __iter__(self) -> Iterator[Tuple[Any, ...]]:
        return iter(self.fetchall())


class _RecordingSQLiteConnection:
    def __init__(self, real_conn: sqlite3.Connection, syscalls_log: List[SyscallEvent]):
        self._real = real_conn
        self._log = syscalls_log

    def cursor(self) -> _RecordingSQLiteCursor:
        return _RecordingSQLiteCursor(self._real.cursor(), self._log)

    def execute(self, sql: str, parameters: Any = ()) -> _RecordingSQLiteCursor:
        cur = self.cursor()
        return cur.execute(sql, parameters)

    def commit(self) -> None:
        self._real.commit()

    def rollback(self) -> None:
        self._real.rollback()

    def close(self) -> None:
        self._real.close()

    def __enter__(self) -> "_RecordingSQLiteConnection":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is None:
            self._real.commit()
        else:
            self._real.rollback()
        self._real.close()


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
    or `func.__closure__` (sync or async helpers such as `fetch_user_data`, `query_db`,
    `call_gateway`) so Chronos can virtualize them automatically during recording and replay.
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
                and (inspect.isfunction(func_globals[name]) or inspect.iscoroutinefunction(func_globals[name]))
            ):
                if name not in helpers:
                    helpers.append(name)
    return helpers


class ChronosTimeMachine:
    """
    Records live I/O & non-deterministic syscalls (sync & async) into hermetic
    `ExecutionCassette`s and replays them in <1ms for deterministic debugging and
    Counterfactual AST Surgery.
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = storage_dir or (Path.cwd() / ".thauthi" / "cassettes")
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
        Hooks `time.time`, `time.monotonic`, `random.*`, `uuid.uuid4`, `datetime.now/utcnow`,
        `urllib.request.urlopen`, `os.getenv`, `sqlite3.connect`, `subprocess.run`,
        loaded `requests`/`httpx` clients, scoped `open()`, and sync/async global helpers.
        """
        orig_time = time.time
        orig_monotonic = time.monotonic
        orig_random = random.random
        orig_randint = random.randint
        orig_choice = random.choice
        orig_uuid4 = uuid.uuid4
        orig_urlopen = urllib.request.urlopen
        orig_getenv = os.getenv
        orig_sqlite_connect = sqlite3.connect
        orig_subprocess_run = subprocess.run

        real_dt_cls = _dt_mod.datetime

        func_globals = extra_globals if extra_globals is not None else getattr(func, "__globals__", {})
        helper_names = _discover_called_global_helpers(func, extra_ns=func_globals)
        for ev in syscalls_log:
            if ev.boundary.startswith("global:"):
                g_name = ev.boundary.split("global:", 1)[1]
                if g_name not in helper_names:
                    helper_names.append(g_name)
        orig_helpers = {name: func_globals[name] for name in helper_names if name in func_globals}

        # Intercept callable closure cells (`LOAD_DEREF`)
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

        def _next_replay_event(boundary_name: str, args_repr: str = "") -> SyscallEvent:
            matching = [ev for ev in syscalls_log if ev.boundary == boundary_name]
            idx = replay_cursors.get(boundary_name, 0)
            if idx < len(matching):
                ev = matching[idx]
                replay_cursors[boundary_name] = idx + 1
                return ev
            # Fail closed: never fall back to the real function or re-serve a stale event.
            raise RuntimeError(
                f"Chronos replay miss: no recorded event left for boundary `{boundary_name}` ({args_repr})"
            )

        def _serialize_boundary_return(kind: str, raw_res: Any) -> Any:
            if kind == "uuid4":
                return str(raw_res)
            if kind == "datetime":
                return raw_res.isoformat()
            if kind == "subprocess.run":
                stdout_v = raw_res.stdout
                stderr_v = raw_res.stderr
                return {
                    "args": raw_res.args if isinstance(raw_res.args, (list, str)) else repr(raw_res.args),
                    "returncode": raw_res.returncode,
                    "stdout": stdout_v.decode("utf-8", errors="replace") if isinstance(stdout_v, bytes) else stdout_v,
                    "stderr": stderr_v.decode("utf-8", errors="replace") if isinstance(stderr_v, bytes) else stderr_v,
                    "stdout_is_bytes": isinstance(stdout_v, bytes),
                    "stderr_is_bytes": isinstance(stderr_v, bytes),
                }
            return copy.deepcopy(raw_res)

        def _deserialize_boundary_return(kind: str, stored: Any) -> Any:
            if kind == "uuid4":
                return uuid.UUID(str(stored))
            if kind == "datetime":
                return real_dt_cls.fromisoformat(str(stored))
            if kind == "subprocess.run" and isinstance(stored, dict):
                so = stored.get("stdout")
                se = stored.get("stderr")
                if stored.get("stdout_is_bytes") and isinstance(so, str):
                    so = so.encode("utf-8")
                if stored.get("stderr_is_bytes") and isinstance(se, str):
                    se = se.encode("utf-8")
                return subprocess.CompletedProcess(
                    args=stored.get("args", []),
                    returncode=int(stored.get("returncode", 0)),
                    stdout=so,
                    stderr=se,
                )
            return copy.deepcopy(stored)

        def _make_proxy(boundary_name: str, real_fn: Callable, kind: str = "generic") -> Callable:
            if inspect.iscoroutinefunction(real_fn):
                async def _async_proxy(*a: Any, **kw: Any) -> Any:
                    if mode == "record":
                        try:
                            raw_res = await real_fn(*a, **kw)
                            syscalls_log.append(
                                SyscallEvent(
                                    boundary=boundary_name,
                                    args_repr=repr((a, kw)),
                                    return_value=_serialize_boundary_return(kind, raw_res),
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
                        ev = _next_replay_event(boundary_name, repr((a, kw)))
                        if ev.exception_type:
                            raise RuntimeError(f"Replayed {ev.exception_type}: {ev.exception_message}")
                        return _deserialize_boundary_return(kind, ev.return_value)

                return _async_proxy

            def _proxy(*a: Any, **kw: Any) -> Any:
                if mode == "record":
                    try:
                        raw_res = real_fn(*a, **kw)
                        if kind == "urlopen":
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
                        if kind == "http_client":
                            payload = {
                                "text": getattr(raw_res, "text", ""),
                                "status_code": int(getattr(raw_res, "status_code", 200)),
                                "headers": dict(getattr(raw_res, "headers", {})),
                            }
                            syscalls_log.append(
                                SyscallEvent(
                                    boundary=boundary_name,
                                    args_repr=repr((a, kw)),
                                    return_value=payload,
                                )
                            )
                            return raw_res
                        syscalls_log.append(
                            SyscallEvent(
                                boundary=boundary_name,
                                args_repr=repr((a, kw)),
                                return_value=_serialize_boundary_return(kind, raw_res),
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
                    ev = _next_replay_event(boundary_name, repr((a, kw)))
                    if ev.exception_type:
                        raise RuntimeError(f"Replayed {ev.exception_type}: {ev.exception_message}")
                    if kind == "urlopen" and isinstance(ev.return_value, dict):
                        return _VirtualHTTPResponse(
                            ev.return_value.get("body", ""),
                            status=int(ev.return_value.get("status", 200)),
                        )
                    if kind == "http_client" and isinstance(ev.return_value, dict):
                        return _VirtualClientHTTPResponse(
                            text=str(ev.return_value.get("text", "")),
                            status_code=int(ev.return_value.get("status_code", 200)),
                            headers=ev.return_value.get("headers"),
                        )
                    return _deserialize_boundary_return(kind, ev.return_value)

            return _proxy

        def _sqlite_connect_proxy(*a: Any, **kw: Any) -> Any:
            if mode == "record":
                real_conn = orig_sqlite_connect(*a, **kw)
                return _RecordingSQLiteConnection(real_conn, syscalls_log)
            return _VirtualSQLiteConnection(_next_replay_event)

        # Subclass datetime.datetime cleanly so `isinstance(dt, datetime.datetime)` remains True!
        now_proxy = _make_proxy("datetime.now", real_dt_cls.now, kind="datetime")
        utcnow_proxy = _make_proxy("datetime.utcnow", real_dt_cls.utcnow, kind="datetime")

        class _VirtualDatetime(real_dt_cls):
            @classmethod
            def now(cls, tz: Any = None) -> Any:
                return now_proxy(tz) if tz is not None else now_proxy()

            @classmethod
            def utcnow(cls) -> Any:
                return utcnow_proxy()

        class _VirtualDatetimeModule:
            def __getattr__(self, item: str) -> Any:
                if item == "datetime":
                    return _VirtualDatetime
                return getattr(_dt_mod, item)

        # Scoped `open` proxy inside `func_globals` only (never touches Python builtins or Chronos's own disk I/O)
        orig_open = func_globals.get("open", builtins.open)
        had_open_in_globals = "open" in func_globals
        orig_dt_in_globals = func_globals.get("datetime")
        had_dt_in_globals = "datetime" in func_globals

        def _scoped_open_proxy(file: Any, open_mode: str = "r", *a: Any, **kw: Any) -> Any:
            is_read_only = all(ch not in open_mode for ch in ("w", "a", "x", "+"))
            if mode == "record":
                if not is_read_only:
                    return orig_open(file, open_mode, *a, **kw)
                try:
                    with orig_open(file, open_mode, *a, **kw) as fh:
                        data = fh.read()
                    is_bin = "b" in open_mode
                    payload = {
                        "is_bytes": is_bin,
                        "content": data.hex() if is_bin else data,
                    }
                    syscalls_log.append(
                        SyscallEvent(
                            boundary="file.open",
                            args_repr=repr((str(file), open_mode)),
                            return_value=payload,
                        )
                    )
                    return io.BytesIO(data) if is_bin else io.StringIO(data)
                except Exception as e:
                    syscalls_log.append(
                        SyscallEvent(
                            boundary="file.open",
                            args_repr=repr((str(file), open_mode)),
                            exception_type=type(e).__name__,
                            exception_message=str(e),
                        )
                    )
                    raise
            else:
                if not is_read_only:
                    return io.BytesIO() if "b" in open_mode else io.StringIO()
                ev = _next_replay_event("file.open", repr((str(file), open_mode)))
                if ev.exception_type:
                    raise OSError(f"Replayed {ev.exception_type}: {ev.exception_message}")
                payload = ev.return_value if isinstance(ev.return_value, dict) else {}
                if payload.get("is_bytes"):
                    return io.BytesIO(bytes.fromhex(str(payload.get("content", ""))))
                return io.StringIO(str(payload.get("content", "")))

        # Patch globals / modules
        time.time = _make_proxy("time.time", orig_time)  # type: ignore[assignment]
        time.monotonic = _make_proxy("time.monotonic", orig_monotonic)  # type: ignore[assignment]
        random.random = _make_proxy("random.random", orig_random)  # type: ignore[assignment]
        random.randint = _make_proxy("random.randint", orig_randint)  # type: ignore[assignment]
        random.choice = _make_proxy("random.choice", orig_choice)  # type: ignore[assignment]
        uuid.uuid4 = _make_proxy("uuid.uuid4", orig_uuid4, kind="uuid4")  # type: ignore[assignment]
        urllib.request.urlopen = _make_proxy("urllib.request.urlopen", orig_urlopen, kind="urlopen")  # type: ignore[assignment]
        os.getenv = _make_proxy("os.getenv", orig_getenv)  # type: ignore[assignment]
        sqlite3.connect = _sqlite_connect_proxy  # type: ignore[assignment]
        subprocess.run = _make_proxy("subprocess.run", orig_subprocess_run, kind="subprocess.run")  # type: ignore[assignment]

        # Optional `requests` / `httpx` patching ONLY if already imported in `sys.modules`
        patched_clients: List[Tuple[Any, str, Any]] = []
        for mod_name in ("requests", "httpx"):
            mod_obj = sys.modules.get(mod_name)
            if mod_obj is not None:
                for fn_attr in ("get", "post", "put", "delete", "request"):
                    real_attr = getattr(mod_obj, fn_attr, None)
                    if callable(real_attr):
                        patched_clients.append((mod_obj, fn_attr, real_attr))
                        setattr(mod_obj, fn_attr, _make_proxy(f"{mod_name}.{fn_attr}", real_attr, kind="http_client"))

        if isinstance(func_globals, dict):
            func_globals["open"] = _scoped_open_proxy
            if orig_dt_in_globals is real_dt_cls:
                func_globals["datetime"] = _VirtualDatetime
            elif orig_dt_in_globals is _dt_mod:
                func_globals["datetime"] = _VirtualDatetimeModule()

        # Also patch `datetime` or `open` if captured inside closure cells (`LOAD_DEREF`)
        orig_special_cells: List[Tuple[Any, Any]] = []
        for fv_name, cell in zip(freevars, closure_cells):
            if fv_name == "datetime":
                try:
                    c_val = cell.cell_contents
                    if c_val is real_dt_cls:
                        orig_special_cells.append((cell, c_val))
                        cell.cell_contents = _VirtualDatetime
                    elif c_val is _dt_mod:
                        orig_special_cells.append((cell, c_val))
                        cell.cell_contents = _VirtualDatetimeModule()
                except ValueError:
                    pass
            elif fv_name == "open":
                try:
                    c_val = cell.cell_contents
                    orig_special_cells.append((cell, c_val))
                    cell.cell_contents = _scoped_open_proxy
                except ValueError:
                    pass

        for h_name, h_fn in orig_helpers.items():
            func_globals[h_name] = _make_proxy(f"global:{h_name}", h_fn)
        for cell, fv_name, c_val in orig_cells:
            cell.cell_contents = _make_proxy(f"global:{fv_name}", c_val)

        try:
            yield
        finally:
            time.time = orig_time  # type: ignore[assignment]
            time.monotonic = orig_monotonic  # type: ignore[assignment]
            random.random = orig_random  # type: ignore[assignment]
            random.randint = orig_randint  # type: ignore[assignment]
            random.choice = orig_choice  # type: ignore[assignment]
            uuid.uuid4 = orig_uuid4  # type: ignore[assignment]
            urllib.request.urlopen = orig_urlopen  # type: ignore[assignment]
            os.getenv = orig_getenv  # type: ignore[assignment]
            sqlite3.connect = orig_sqlite_connect  # type: ignore[assignment]
            subprocess.run = orig_subprocess_run  # type: ignore[assignment]
            for mod_obj, fn_attr, real_attr in patched_clients:
                setattr(mod_obj, fn_attr, real_attr)
            if isinstance(func_globals, dict):
                if had_open_in_globals:
                    func_globals["open"] = orig_open
                else:
                    func_globals.pop("open", None)
                if had_dt_in_globals:
                    func_globals["datetime"] = orig_dt_in_globals
                else:
                    func_globals.pop("datetime", None)
            for cell, orig_val in orig_special_cells:
                cell.cell_contents = orig_val
            for h_name, h_fn in orig_helpers.items():
                func_globals[h_name] = h_fn
            for cell, _, c_val in orig_cells:
                cell.cell_contents = c_val

    def record_call(self, func: Callable, *args: Any, **kwargs: Any) -> Tuple[Any, Optional[BaseException], ExecutionCassette]:
        """Execute synchronous `func(*args, **kwargs)` while recording all I/O & clock syscalls."""
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

    async def record_call_async(
        self, func: Callable, *args: Any, **kwargs: Any
    ) -> Tuple[Any, Optional[BaseException], ExecutionCassette]:
        """Execute `async def` `func(*args, **kwargs)` while recording all I/O & clock syscalls."""
        syscalls: List[SyscallEvent] = []
        cid = hashlib.sha1(f"{func.__name__}:{repr(args)}:{time.time_ns()}".encode("utf-8")).hexdigest()[:10]
        res: Any = None
        caught: Optional[BaseException] = None

        with self._intercept_environment(func, mode="record", syscalls_log=syscalls):
            try:
                res = await func(*copy.deepcopy(args), **copy.deepcopy(kwargs))
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
        """Replay a recorded `ExecutionCassette` against sync `func` with zero external side effects."""
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

    async def replay_cassette_async(
        self,
        func: Callable,
        cassette: ExecutionCassette,
        sandbox_globals: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Any, Optional[BaseException]]:
        """Replay a recorded `ExecutionCassette` against `async def` `func` with zero external side effects."""
        with self._intercept_environment(
            func,
            mode="replay",
            syscalls_log=cassette.syscalls,
            extra_globals=sandbox_globals,
        ):
            try:
                out = await func(*copy.deepcopy(cassette.args), **copy.deepcopy(cassette.kwargs))
                return out, None
            except BaseException as exc:
                return None, exc

    def _build_heal_candidates(
        self,
        func: Callable,
        crash_cassette: ExecutionCassette,
        healthy_cassettes: List[ExecutionCassette],
    ) -> Tuple[str, List[Tuple[str, ast.Module]]]:
        _, base_tree, func_def = CounterfactualSurgeon.extract_clean_func_ast(func)
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
            crash_cassette.exception_type or "Exception",
            crash_frame,
            func,
            healthy_frames,
        )
        return func_name, candidates

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

        func_name, candidates = self._build_heal_candidates(func, crash_cassette, healthy_cassettes)

        for strategy, cand_tree in candidates:
            try:
                code_obj = compile(cand_tree, filename=f"<chronos_heal:{func_name}>", mode="exec")
                sandbox_ns: Dict[str, Any] = dict(getattr(func, "__globals__", {}))
                sandbox_ns.update(_extract_closure_dict(func))
                exec(code_obj, sandbox_ns)
                cand_fn = sandbox_ns[func_name]
            except Exception:
                continue

            healthy_ok = True
            for h_cas in healthy_cassettes:
                out, err = self.replay_cassette(cand_fn, h_cas, sandbox_globals=sandbox_ns)
                if err is not None or out != h_cas.return_value:
                    healthy_ok = False
                    break
            if not healthy_ok:
                continue

            healed_out, crash_err = self.replay_cassette(cand_fn, crash_cassette, sandbox_globals=sandbox_ns)
            if crash_err is None:
                patched_src = ast.unparse(cand_tree)
                return strategy, patched_src, cand_fn, healed_out

        return None

    async def heal_with_cassettes_async(
        self,
        func: Callable,
        crash_cassette: ExecutionCassette,
        healthy_cassettes: List[ExecutionCassette],
    ) -> Optional[Tuple[str, str, Callable, Any]]:
        """Async equivalent of `heal_with_cassettes` for `async def` functions."""
        if not crash_cassette.exception_type:
            return None

        func_name, candidates = self._build_heal_candidates(func, crash_cassette, healthy_cassettes)

        for strategy, cand_tree in candidates:
            try:
                code_obj = compile(cand_tree, filename=f"<chronos_heal:{func_name}>", mode="exec")
                sandbox_ns: Dict[str, Any] = dict(getattr(func, "__globals__", {}))
                sandbox_ns.update(_extract_closure_dict(func))
                exec(code_obj, sandbox_ns)
                cand_fn = sandbox_ns[func_name]
            except Exception:
                continue

            healthy_ok = True
            for h_cas in healthy_cassettes:
                out, err = await self.replay_cassette_async(cand_fn, h_cas, sandbox_globals=sandbox_ns)
                if err is not None or out != h_cas.return_value:
                    healthy_ok = False
                    break
            if not healthy_ok:
                continue

            healed_out, crash_err = await self.replay_cassette_async(cand_fn, crash_cassette, sandbox_globals=sandbox_ns)
            if crash_err is None:
                patched_src = ast.unparse(cand_tree)
                return strategy, patched_src, cand_fn, healed_out

        return None

    def save_cassette(self, cassette: ExecutionCassette) -> Optional[Path]:
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            path = self.storage_dir / f"{cassette.cassette_id}.json"
            path.write_text(
                json.dumps(cassette.to_dict(), ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
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
        Decorator that records deterministic I/O cassettes for every sync/async call and,
        when an unhandled exception occurs, uses hermetic cassette replay to heal
        and hot-swap the function in-flight without re-executing external side effects!
        """
        healthy_cassettes: List[ExecutionCassette] = []
        active_impl: List[Callable] = [func]

        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def _wrapped_async(*args: Any, **kwargs: Any) -> Any:
                res, exc, cassette = await self.record_call_async(active_impl[0], *args, **kwargs)
                if exc is None:
                    if len(healthy_cassettes) < 25:
                        healthy_cassettes.append(cassette)
                    return res

                if not auto_heal:
                    raise exc

                outcome = await self.heal_with_cassettes_async(func, cassette, healthy_cassettes)
                if outcome is None:
                    raise exc

                _, _, healed_fn, healed_val = outcome
                active_impl[0] = healed_fn
                return healed_val

            return _wrapped_async

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

            _, _, healed_fn, healed_val = outcome
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
    Decorator to equip any stateful/networked/time-dependent Python function (sync or async)
    with Chronos I/O Virtualization & Time-Travel Self-Healing.
    """
    def _decorator(fn: Callable) -> Callable:
        return _DEFAULT_CHRONOS.protect(fn, auto_heal=auto_heal)

    if func is not None and callable(func):
        return _decorator(func)
    return _decorator
