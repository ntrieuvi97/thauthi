#!/usr/bin/env python3
"""
Thauthi 3.1 Zero-Dependency MCP Server (`thauthi mcp`).

Exposes Thauthi's Runtime Tracer, Differential Shadow Prover, Algorithmic Alchemist,
Contract CEGIS Synthesizer, Whole-Repo Call-Graph Hive, Chronos Time-Travel Kernel,
Symbolic AST Crystallizer, and Adversarial Shadow Verifier natively over Model Context
Protocol (MCP) stdio JSON-RPC 2.0 (supporting both newline-delimited JSON and
`Content-Length` framed stdio).
"""

import contextlib
import inspect
import io
import json
import runpy
import sys
import tempfile
from pathlib import Path
from typing import Any, BinaryIO, Dict, List, Optional

from thauthi import __version__
from thauthi.alchemist import AlgorithmicAlchemist
from thauthi.chronos import ChronosTimeMachine
from thauthi.crystallizer import NeuralSymbolicCrystallizer
from thauthi.hive import CallGraphHive
from thauthi.kernel import CentsibleKernelBridge
from thauthi.omniscient import OmniscientTracer
from thauthi.prover import BehavioralInvariantMiner, DifferentialShadowProver
from thauthi.scorecard import compute_evolution_telemetry
from thauthi.synthesizer import ContractSynthesizer
from thauthi.verifier import ShadowVerifier

MCP_PROTOCOL_VERSION = "2024-11-05"


def _json_rpc_result(req_id: Any, result: Any) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _json_rpc_error(req_id: Any, code: int, message: str, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


class ThauthiMCPServer:
    """
    Zero-dependency Model Context Protocol (MCP) server for Thauthi.
    """

    def __init__(self, workspace_dir: Optional[Path] = None, facts_path: Optional[Path] = None):
        self.workspace_dir = (workspace_dir or Path.cwd()).resolve()
        self.facts_path = facts_path

    def _resolve_path(self, raw_path: str) -> Path:
        p = Path(raw_path).expanduser()
        if not p.is_absolute():
            p = self.workspace_dir / p
        p = p.resolve()
        # Trust boundary: MCP callers may only read/write/execute inside the served workspace.
        if p != self.workspace_dir and self.workspace_dir not in p.parents:
            raise ValueError(f"Path escapes MCP workspace ({self.workspace_dir}): {raw_path}")
        return p

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "thauthi_status",
                "description": (
                    "Get Thauthi kernel telemetry: Evolution IQ, Darwinian Gene Pool, "
                    "forged tools, Symbolic AST Crystals, immune surgeries, and verified token ROI."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "all_projects": {
                            "type": "boolean",
                            "description": "Aggregate metrics across all known repositories.",
                        }
                    },
                },
            },
            {
                "name": "thauthi_trace",
                "description": (
                    "Run a Python script or inline source snippet under OmniscientTracer (sys.settrace) "
                    "to capture live FrameDNA variable mutations, execution steps, and Backward Dynamic "
                    "Causal Fault Graphs on crash instead of guessing from static text."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "script_path": {"type": "string", "description": "Path to a Python script to execute."},
                        "source_code": {"type": "string", "description": "Inline Python source code to trace."},
                    },
                },
            },
            {
                "name": "thauthi_prove",
                "description": (
                    "Run BehavioralInvariantMiner or DifferentialShadowProver (Old AST vs New AST) "
                    "with randomized property fuzzing and O(N) complexity verification to "
                    "prove semantic equivalence, argument purity, and zero asymptotic regression. "
                    "WARNING: EXECUTES the given code (imports, module-level assignments and every "
                    "public function, with fuzzed inputs) in this process, unsandboxed."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "before_source": {"type": "string", "description": "Original Python source code."},
                        "after_source": {"type": "string", "description": "Modified Python source code."},
                        "before_path": {"type": "string", "description": "Path to original Python file."},
                        "after_path": {"type": "string", "description": "Path to modified Python file."},
                        "mine_path": {"type": "string", "description": "Path to mine single-file invariants."},
                        "mine_source": {"type": "string", "description": "Inline source to mine invariants."},
                    },
                },
            },
            {
                "name": "thauthi_optimize",
                "description": (
                    "Run AlgorithmicAlchemist on a Python file, directory, or inline snippet to detect and optimize "
                    "O(N^2)->O(N) loops and O(2^N)->O(N) recursion, verifying behavioral equivalence on sampled inputs "
                    "before returning or applying. WARNING: EXECUTES the target code (imports, module-level "
                    "assignments and every public function, with fuzzed inputs) in this process, unsandboxed."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Target Python file or directory path."},
                        "target_path": {"type": "string", "description": "Alias for target Python file or directory path."},
                        "source_code": {"type": "string", "description": "Inline Python source code."},
                        "apply": {"type": "boolean", "description": "Write proven optimizations to file(s)."},
                    },
                },
            },
            {
                "name": "thauthi_synthesize",
                "description": (
                    "Run Contract-to-Code CEGIS Synthesizer on stub functions with docstring >>> examples, "
                    "optionally verifying and auto-crystallizing caller-proposed candidate implementations. "
                    "WARNING: EXECUTES the target module and every candidate in this process, unsandboxed."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "Path to Python file with stub functions."},
                        "source_code": {"type": "string", "description": "Inline Python stub source code."},
                        "candidate_sources": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional candidate expressions or function bodies to verify via CEGIS.",
                        },
                        "apply": {"type": "boolean", "description": "Write synthesized implementations to file_path."},
                    },
                },
            },
            {
                "name": "thauthi_hive",
                "description": (
                    "Scan the repository call graph (import/alias/class-method aware) to detect broken "
                    "call sites after function signature evolution and optionally auto-heal callers."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "workspace_path": {"type": "string", "description": "Repository root path (defaults to cwd)."},
                        "apply": {"type": "boolean", "description": "Apply surgical call-site fixes across files."},
                    },
                },
            },
            {
                "name": "thauthi_chronos",
                "description": (
                    "Interact with Chronos Deterministic I/O Virtualization & Time-Travel Cassettes: "
                    "list saved ExecutionCassettes or record a script's execution."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "record"],
                            "description": "Whether to list existing cassettes or record a script.",
                        },
                        "script_path": {"type": "string", "description": "Python script to record (when action=record)."},
                        "func_name": {"type": "string", "description": "Optional function name filter when listing."},
                    },
                },
            },
            {
                "name": "thauthi_crystallize",
                "description": (
                    "Compile a before->after code edit into a deterministic 0-token Symbolic AST Crystal "
                    "or apply existing crystals across a file/directory."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "apply", "list"],
                            "description": "Create a new crystal, apply crystals to a target, or list crystals.",
                        },
                        "name": {"type": "string", "description": "Crystal slug name (when action=create)."},
                        "before": {"type": "string", "description": "Before code snippet (when action=create)."},
                        "after": {"type": "string", "description": "After code snippet (when action=create)."},
                        "target_path": {"type": "string", "description": "Target file or directory (when action=apply)."},
                        "dry_run": {"type": "boolean", "description": "Preview rewrites without writing."},
                    },
                },
            },
            {
                "name": "thauthi_verify",
                "description": (
                    "Run Thauthi Adversarial Shadow Verifier across workspace Python files "
                    "(compile checks, debug trap/secret leak scan, and optional pytest execution)."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "workspace_path": {"type": "string", "description": "Target directory to verify."},
                        "run_tests": {"type": "boolean", "description": "Also execute pytest suite."},
                    },
                },
            },
        ]

    def _execute_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        bridge = CentsibleKernelBridge(facts_path=self.facts_path)

        if name == "thauthi_status":
            all_proj = args.get("all_projects")
            return compute_evolution_telemetry(bridge=bridge, all_projects=all_proj)

        if name == "thauthi_trace":
            script_raw = args.get("script_path")
            src_code = args.get("source_code")
            if script_raw:
                script_path = self._resolve_path(str(script_raw))
                tracer = OmniscientTracer(target_files={str(script_path)})
                _, exc, frames = tracer.run_callable(runpy.run_path, str(script_path), run_name="__main__")
                return {
                    "script": str(script_path),
                    "crashed": exc is not None,
                    "exception": f"{type(exc).__name__}: {exc}" if exc else None,
                    "frames_recorded": len(frames),
                    "frames": [f.to_dict() for f in frames],
                    "fault_reports": [r.to_dict() for r in tracer.fault_reports],
                }
            if src_code:
                with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp:
                    tmp.write(str(src_code))
                    tmp_path = Path(tmp.name)
                try:
                    tracer = OmniscientTracer(target_files={str(tmp_path)})
                    _, exc, frames = tracer.run_callable(runpy.run_path, str(tmp_path), run_name="__main__")
                    return {
                        "script": "<inline>",
                        "crashed": exc is not None,
                        "exception": f"{type(exc).__name__}: {exc}" if exc else None,
                        "frames_recorded": len(frames),
                        "frames": [f.to_dict() for f in frames],
                        "fault_reports": [r.to_dict() for r in tracer.fault_reports],
                    }
                finally:
                    tmp_path.unlink(missing_ok=True)
            raise ValueError("Provide `script_path` or `source_code` for thauthi_trace.")

        if name == "thauthi_prove":
            mine_path_raw = args.get("mine_path")
            mine_src = args.get("mine_source")
            if mine_path_raw or mine_src:
                src_path = self._resolve_path(str(mine_path_raw)) if mine_path_raw else None
                src = src_path.read_text(encoding="utf-8") if src_path else str(mine_src)
                funcs, asts = DifferentialShadowProver._compile_functions(src, "mcp_mine", source_path=src_path)
                contracts = [
                    BehavioralInvariantMiner.mine_contract(fn, asts[fn_name]).to_dict()
                    for fn_name, fn in funcs.items()
                ]
                return {"mode": "mine", "contracts": contracts}

            before_src = args.get("before_source")
            after_src = args.get("after_source")
            b_path = self._resolve_path(str(args["before_path"])) if args.get("before_path") else None
            a_path = self._resolve_path(str(args["after_path"])) if args.get("after_path") else None
            if b_path:
                before_src = b_path.read_text(encoding="utf-8")
            if a_path:
                after_src = a_path.read_text(encoding="utf-8")
            if not before_src or not after_src:
                raise ValueError("Provide before_source/after_source (or before_path/after_path), or mine_path/mine_source.")
            report = DifferentialShadowProver.prove_equivalence(
                str(before_src),
                str(after_src),
                before_path=b_path,
                after_path=a_path,
            )
            return report.to_dict()

        if name == "thauthi_optimize":
            apply_changes = bool(args.get("apply", False))
            target_raw = args.get("file_path") or args.get("target_path") or args.get("path")
            if target_raw:
                f_path = self._resolve_path(str(target_raw))
                if f_path.is_dir():
                    alchemist = AlgorithmicAlchemist(storage_dir=f_path / ".thauthi")
                    return alchemist.optimize_directory(f_path, apply=apply_changes)
                alchemist = AlgorithmicAlchemist(storage_dir=f_path.parent / ".thauthi")
                new_src, reports = alchemist.optimize_file(f_path, apply=apply_changes)
                return {
                    "target": str(f_path),
                    "file": str(f_path),
                    "applied": apply_changes,
                    "files_scanned": 1,
                    "files_optimized": [str(f_path)] if reports else [],
                    "transmutations_count": len(reports),
                    "transmutations": [r.to_dict() for r in reports],
                    "optimized_source": new_src,
                }
            if args.get("source_code"):
                alchemist = AlgorithmicAlchemist(storage_dir=self.workspace_dir / ".thauthi")
                new_src, reports = alchemist.optimize_source(str(args["source_code"]))
                return {
                    "target": "<inline>",
                    "file": "<inline>",
                    "applied": False,
                    "files_scanned": 1,
                    "files_optimized": [],
                    "transmutations_count": len(reports),
                    "transmutations": [r.to_dict() for r in reports],
                    "optimized_source": new_src,
                }
            raise ValueError("Provide `file_path` (or `target_path`) or `source_code` for thauthi_optimize.")

        if name == "thauthi_synthesize":
            candidates = args.get("candidate_sources")
            apply_changes = bool(args.get("apply", False))
            if args.get("file_path"):
                f_path = self._resolve_path(str(args["file_path"]))
                new_src, results = ContractSynthesizer.synthesize_file(
                    f_path,
                    apply=apply_changes,
                    candidate_sources=candidates,
                )
                return {
                    "file": str(f_path),
                    "applied": apply_changes,
                    "synthesized_count": len(results),
                    "results": [r.to_dict() for r in results],
                    "synthesized_source": new_src,
                }
            if args.get("source_code"):
                with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp:
                    tmp.write(str(args["source_code"]))
                    tmp_path = Path(tmp.name)
                try:
                    new_src, results = ContractSynthesizer.synthesize_file(
                        tmp_path,
                        apply=False,
                        candidate_sources=candidates,
                    )
                    return {
                        "file": "<inline>",
                        "applied": False,
                        "synthesized_count": len(results),
                        "results": [r.to_dict() for r in results],
                        "synthesized_source": new_src,
                    }
                finally:
                    tmp_path.unlink(missing_ok=True)
            raise ValueError("Provide `file_path` or `source_code` for thauthi_synthesize.")

        if name == "thauthi_hive":
            ws = self._resolve_path(str(args["workspace_path"])) if args.get("workspace_path") else self.workspace_dir
            apply_changes = bool(args.get("apply", False))
            hive = CallGraphHive(workspace_dir=ws)
            rep = hive.heal_workspace(apply=apply_changes)
            return rep.to_dict()

        if name == "thauthi_chronos":
            action = str(args.get("action") or "list")
            if action == "list":
                chronos = ChronosTimeMachine(storage_dir=self.workspace_dir / ".thauthi" / "cassettes")
                cassettes = chronos.load_cassettes(func_name=args.get("func_name"))
                return {
                    "count": len(cassettes),
                    "cassettes": [c.to_dict() for c in cassettes],
                }
            if action == "record":
                script_raw = args.get("script_path")
                if not script_raw:
                    raise ValueError("Provide `script_path` when action='record'.")
                script_path = self._resolve_path(str(script_raw))
                chronos = ChronosTimeMachine(storage_dir=script_path.parent / ".thauthi" / "cassettes")
                mod_globals = runpy.run_path(str(script_path), run_name="__thauthi_chronos__")
                for fn_name, obj in list(mod_globals.items()):
                    if inspect.isfunction(obj) and not fn_name.startswith("_"):
                        mod_globals[fn_name] = chronos.protect(obj, auto_heal=True)
                        if hasattr(obj, "__globals__"):
                            obj.__globals__[fn_name] = mod_globals[fn_name]
                if "main" in mod_globals and callable(mod_globals["main"]):
                    if inspect.iscoroutinefunction(mod_globals["main"]):
                        import asyncio
                        asyncio.run(mod_globals["main"]())
                    else:
                        mod_globals["main"]()
                return {
                    "script": str(script_path),
                    "cassettes_recorded": len(chronos.cassettes),
                    "cassettes": [c.to_dict() for c in chronos.cassettes],
                }
            raise ValueError(f"Unsupported chronos action: {action}")

        if name == "thauthi_crystallize":
            action = str(args.get("action") or "list")
            crys = NeuralSymbolicCrystallizer(bridge=bridge, workspace_dir=self.workspace_dir)
            if action == "list":
                return {"crystals": crys.load_registry()}
            if action == "create":
                if not (args.get("name") and args.get("before") and args.get("after")):
                    raise ValueError("Provide `name`, `before`, and `after` when action='create'.")
                entry = crys.crystallize_edit(
                    name=str(args["name"]),
                    before_code=str(args["before"]),
                    after_code=str(args["after"]),
                )
                return entry.to_dict()
            if action == "apply":
                target = self._resolve_path(str(args.get("target_path") or "."))
                dry_run = bool(args.get("dry_run", False))
                return crys.apply_all_crystals(target_path=target, dry_run=dry_run)
            raise ValueError(f"Unsupported crystallize action: {action}")

        if name == "thauthi_verify":
            ws = self._resolve_path(str(args["workspace_path"])) if args.get("workspace_path") else self.workspace_dir
            run_tests = bool(args.get("run_tests", False))
            verifier = ShadowVerifier(workspace_dir=ws)
            return verifier.verify_workspace(run_tests=run_tests)

        raise KeyError(f"Unknown Thauthi MCP tool: {name}")

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        args = dict(arguments or {})
        stdout_buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout_buf):
                payload = self._execute_tool(name, args)
            captured = stdout_buf.getvalue().strip()
            if captured and isinstance(payload, dict) and "_captured_stdout" not in payload:
                payload["_captured_stdout"] = captured
            return {
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2, default=str)}],
                "isError": False,
            }
        except Exception as exc:
            err_payload = {
                "error": f"{type(exc).__name__}: {exc}",
                "tool": name,
            }
            captured = stdout_buf.getvalue().strip()
            if captured:
                err_payload["_captured_stdout"] = captured
            return {
                "content": [{"type": "text", "text": json.dumps(err_payload, ensure_ascii=False, indent=2, default=str)}],
                "isError": True,
            }

    def handle_message(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method = msg.get("method")
        req_id = msg.get("id")
        params = msg.get("params") or {}

        # Notifications have no `id` and expect no response
        if req_id is None and isinstance(method, str) and method.startswith("notifications/"):
            return None

        if method == "initialize":
            client_proto = params.get("protocolVersion") or MCP_PROTOCOL_VERSION
            return _json_rpc_result(
                req_id,
                {
                    "protocolVersion": str(client_proto),
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": "thauthi",
                        "version": __version__,
                    },
                },
            )

        if method == "ping":
            return _json_rpc_result(req_id, {})

        if method == "tools/list":
            return _json_rpc_result(req_id, {"tools": self.list_tools()})

        if method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments") or {}
            if not tool_name:
                return _json_rpc_error(req_id, -32602, "Missing tool `name` in tools/call params")
            return _json_rpc_result(req_id, self.call_tool(str(tool_name), dict(tool_args)))

        if req_id is None:
            return None
        return _json_rpc_error(req_id, -32601, f"Method not found: {method}")

    @staticmethod
    def _write_framed_response(out_stream: BinaryIO, payload: Dict[str, Any], framing: str) -> None:
        raw_bytes = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        if framing == "lsp":
            header = f"Content-Length: {len(raw_bytes)}\r\n\r\n".encode("ascii")
            out_stream.write(header + raw_bytes)
        else:
            out_stream.write(raw_bytes + b"\n")
        out_stream.flush()

    def serve_streams(self, in_stream: BinaryIO, out_stream: BinaryIO) -> int:
        while True:
            line = in_stream.readline()
            if not line:
                break
            stripped = line.strip()
            if not stripped:
                continue

            framing = "jsonl"
            if stripped.lower().startswith(b"content-length:"):
                framing = "lsp"
                try:
                    content_len = int(stripped.split(b":", 1)[1].strip())
                except ValueError:
                    continue
                # Consume remaining headers until blank line
                while True:
                    hdr_line = in_stream.readline()
                    if not hdr_line or hdr_line in (b"\r\n", b"\n"):
                        break
                body_bytes = in_stream.read(content_len)
                if not body_bytes:
                    break
                text = body_bytes.decode("utf-8", errors="replace")
            else:
                text = stripped.decode("utf-8", errors="replace")

            try:
                msg = json.loads(text)
            except ValueError as exc:
                resp = _json_rpc_error(None, -32700, f"Parse error: {exc}")
                self._write_framed_response(out_stream, resp, framing)
                continue

            if isinstance(msg, list):
                for item in msg:
                    if isinstance(item, dict):
                        resp = self.handle_message(item)
                        if resp is not None:
                            self._write_framed_response(out_stream, resp, framing)
            elif isinstance(msg, dict):
                resp = self.handle_message(msg)
                if resp is not None:
                    self._write_framed_response(out_stream, resp, framing)

        return 0

    def serve_stdio(self) -> int:
        return self.serve_streams(sys.stdin.buffer, sys.stdout.buffer)
