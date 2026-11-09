#!/usr/bin/env python3
"""
Autopoietic Tool Forge for Overmind.

Monitors AI agent transcripts (Antigravity, Claude Code, OpenClaw), mines recurring
multi-step command sequences and error-recovery pipelines, synthesizes standalone
zero-dependency CLI tools (`om_<slug>.py`), self-verifies them in a sandbox, and
auto-binds them into Centsible's ephemeral micro-skill engine.
"""

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from overmind.kernel import CentsibleKernelBridge, get_overmind_home

TRIVIAL_COMMAND_HEADS = {
    # Navigation & standard inspection
    "cd", "pwd", "clear", "true", "false", "whoami", "date",
    "ls", "cat", "which", "where", "find", "echo", "printf", "head", "tail", "less", "more",
    "grep", "egrep", "fgrep", "rg", "ag", "wc", "file", "stat",
    # File manipulation primitives (when not part of a compound build/test)
    "mkdir", "touch", "cp", "mv", "rm", "rmdir", "chmod", "chown", "ln",
    # System & environment utilities
    "sleep", "env", "export", "set", "unset", "alias",
}
# Keep TRIVIAL_STANDALONE_BINS for backward compatibility
TRIVIAL_STANDALONE_BINS = TRIVIAL_COMMAND_HEADS
SHELL_TOOL_NAMES = {"run_command", "Bash", "bash", "execute_command", "exec", "shell"}

READONLY_GIT_SUBCOMMANDS = {
    "status", "log", "branch", "diff", "show", "rev-parse", "remote", "tag", "describe",
}

AGENT_META_PATTERNS = [
    re.compile(r"\bcheckpoint(?:\.py)?\b", re.IGNORECASE),
    re.compile(r"\b(?:overmind|centsible)\b", re.IGNORECASE),
    re.compile(r"\bom_[a-zA-Z0-9_]+\.py\b"),
]


def is_trivial_or_meta_step(cmd: str) -> bool:
    """Determine whether a command step is trivial noise or agent meta-infrastructure."""
    clean = _normalize_command(cmd)
    if not clean:
        return True

    # 1. Check for agent meta-infrastructure patterns
    for pat in AGENT_META_PATTERNS:
        if pat.search(clean):
            return True

    # 2. Split compound commands (&&, ;, ||)
    sub_cmds = [sc.strip() for sc in re.split(r"&&|;|\|\|", clean) if sc.strip()]
    if not sub_cmds:
        return True

    # If every sub-command is trivial/readonly, the entire compound command is trivial
    all_trivial = True
    for sub in sub_cmds:
        head = _command_head(sub)
        if not head:
            continue
        if head in TRIVIAL_COMMAND_HEADS:
            continue
        if head == "git":
            parts = sub.split()
            if len(parts) >= 2 and parts[1].lower() in READONLY_GIT_SUBCOMMANDS:
                continue
        all_trivial = False
        break

    return all_trivial


def _generalize_step_paths(cmd: str, workspace_dir: Optional[Path] = None) -> str:
    """
    De-hardcode absolute paths in command strings to relative or $HOME representations.
    """
    res = cmd
    ws = (workspace_dir or Path.cwd()).resolve()
    ws_str = str(ws)
    if ws_str in res:
        res = res.replace(ws_str + "/", "")
        res = res.replace(ws_str, ".")

    home_str = str(Path.home().resolve())
    if home_str in res:
        res = res.replace(home_str, "~")

    return res


def _subsume_candidates(candidates: Dict[Tuple[str, ...], "ToolCandidate"]) -> List["ToolCandidate"]:
    """
    Subsume shorter command sub-sequences into longer pipelines when their
    occurrence frequency indicates they were just part of the larger pipeline.
    """
    sorted_candidates = sorted(
        candidates.values(),
        key=lambda c: len(c.steps),
        reverse=True,
    )

    pruned: List["ToolCandidate"] = []
    for cand in sorted_candidates:
        cand_seq = tuple(cand.steps)
        is_subsumed = False
        for parent in pruned:
            parent_seq = tuple(parent.steps)
            if len(cand_seq) < len(parent_seq):
                for start_idx in range(len(parent_seq) - len(cand_seq) + 1):
                    if parent_seq[start_idx : start_idx + len(cand_seq)] == cand_seq:
                        if cand.occurrences <= parent.occurrences + 1:
                            is_subsumed = True
                            break
            if is_subsumed:
                break
        if not is_subsumed:
            pruned.append(cand)

    pruned.sort(
        key=lambda c: (c.occurrences * len(c.steps), c.estimated_token_savings),
        reverse=True,
    )
    return pruned


@dataclass
class CommandStep:
    command: str
    failed: bool = False


@dataclass
class ToolCandidate:
    slug: str
    title: str
    kind: str  # "pipeline" | "recovery"
    steps: List[str]
    occurrences: int
    trigger_pattern: str
    estimated_token_savings: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _normalize_command(cmd: str) -> str:
    """Collapse whitespace and strip outer quotes for sequence matching."""
    cmd = cmd.strip()
    if (cmd.startswith('"') and cmd.endswith('"')) or (cmd.startswith("'") and cmd.endswith("'")):
        cmd = cmd[1:-1].strip()
    cmd = re.sub(r"\s+", " ", cmd)
    return cmd


def _command_head(cmd: str) -> str:
    """Extract the primary executable token from a command string."""
    parts = cmd.strip().split()
    if not parts:
        return ""
    for p in parts:
        if "=" in p and not p.startswith("-"):
            continue
        return Path(p).name
    return Path(parts[0]).name


def extract_commands_from_transcript(transcript_path: Path) -> List[CommandStep]:
    """
    Parse an Antigravity, Claude Code, or OpenClaw JSONL transcript and return
    an ordered list of shell CommandSteps with their failure status.
    """
    if not transcript_path.exists():
        return []

    raw_steps: List[Dict[str, Any]] = []
    for line in transcript_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                raw_steps.append(obj)
        except ValueError:
            continue

    # Map Claude Code tool_use_id -> is_error
    tool_errors: Dict[str, bool] = {}
    for st in raw_steps:
        msg = st.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tid = str(block.get("tool_use_id") or "")
                    if tid:
                        tool_errors[tid] = bool(block.get("is_error"))

    extracted: List[CommandStep] = []
    for st in raw_steps:
        if st.get("isSidechain"):
            continue

        step_failed = (
            st.get("status") == "ERROR"
            or (
                "Command exited with code " in str(st.get("content", ""))
                and "Command exited with code 0" not in str(st.get("content", ""))
            )
        )

        # 1. Antigravity / generic `tool_calls` array
        for tc in st.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tname = str(tc.get("name") or tc.get("tool") or "")
            args = tc.get("args") or tc.get("arguments") or tc.get("input") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            if tname in SHELL_TOOL_NAMES and isinstance(args, dict):
                cmd = str(args.get("CommandLine") or args.get("command") or args.get("cmd") or "").strip()
                if cmd:
                    extracted.append(CommandStep(command=_normalize_command(cmd), failed=step_failed))

        # 2. Direct hook / step format (`tool_name` + `tool_input`)
        if st.get("tool_name") in SHELL_TOOL_NAMES and isinstance(st.get("tool_input"), dict):
            t_in = st["tool_input"]
            cmd = str(t_in.get("command") or t_in.get("CommandLine") or "").strip()
            if cmd:
                extracted.append(CommandStep(command=_normalize_command(cmd), failed=step_failed))

        # 3. Claude Code `message.content` blocks (`type == "tool_use"`)
        msg = st.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    bname = str(block.get("name") or "")
                    binput = block.get("input") or {}
                    bid = str(block.get("id") or "")
                    if bname in SHELL_TOOL_NAMES and isinstance(binput, dict):
                        cmd = str(binput.get("command") or binput.get("CommandLine") or "").strip()
                        if cmd:
                            is_err = tool_errors.get(bid, step_failed)
                            extracted.append(CommandStep(command=_normalize_command(cmd), failed=is_err))

    return extracted


def _build_slug_and_trigger(steps: List[str], kind: str) -> Tuple[str, str, str]:
    """Generate a deterministic tool slug, human title, and Centsible regex trigger."""
    heads = [_command_head(s) for s in steps if _command_head(s)]
    unique_heads: List[str] = []
    for h in heads:
        clean_h = re.sub(r"[^a-zA-Z0-9_]", "", h.lower())
        if clean_h and clean_h not in unique_heads:
            unique_heads.append(clean_h)

    base_name = "_".join(unique_heads[:3]) if unique_heads else "workflow"
    digest = hashlib.sha1(" && ".join(steps).encode("utf-8")).hexdigest()[:6]
    slug = f"om_{base_name}_{digest}"
    title = f"Auto-Forged {kind.title()}: {' -> '.join(unique_heads[:3])}"

    # Extract meaningful tokens for the Centsible regex trigger
    tokens: List[str] = []
    for s in steps:
        for tok in re.findall(r"[A-Za-z0-9_./-]{3,}", s):
            tok_clean = Path(tok).stem.lower()
            if tok_clean not in {"python", "python3", "bash", "sh", "run", "the", "and"} and len(tok_clean) >= 3:
                if tok_clean not in tokens:
                    tokens.append( re.escape(tok_clean) )
    if len(tokens) >= 2:
        trigger = f"{tokens[0]}.{{0,25}}({tokens[1]}|verify|check|test|run)"
    elif len(tokens) == 1:
        trigger = f"{tokens[0]}.{{0,24}}(pipeline|verify|check|test|fail)"
    else:
        trigger = f"{base_name}.{{0,20}}(run|verify|check)"

    return slug, title, trigger


def mine_candidates(
    command_steps: List[CommandStep],
    min_occurrences: int = 2,
) -> List[ToolCandidate]:
    """
    Mine recurring multi-step command sequences (2..3 steps) and error->recovery chains,
    filtering out trivial shell noise and subsuming redundant sub-pipelines.
    """
    candidates: Dict[Tuple[str, ...], ToolCandidate] = {}

    # 1. Mine Error -> Recovery Chains (Failed Command A -> Successful Command B [-> Command C])
    recovery_counts: Dict[Tuple[str, ...], int] = {}
    for i in range(len(command_steps) - 1):
        curr = command_steps[i]
        nxt = command_steps[i + 1]
        if curr.failed and not nxt.failed:
            if not is_trivial_or_meta_step(nxt.command):
                seq: Tuple[str, ...] = (nxt.command,)
                if i + 2 < len(command_steps) and not command_steps[i + 2].failed:
                    if not is_trivial_or_meta_step(command_steps[i + 2].command):
                        seq = (nxt.command, command_steps[i + 2].command)
                if len(seq) >= 1:
                    recovery_counts[seq] = recovery_counts.get(seq, 0) + 1

    for seq, count in recovery_counts.items():
        if count >= min_occurrences:
            slug, title, trigger = _build_slug_and_trigger(list(seq), kind="recovery")
            candidates[seq] = ToolCandidate(
                slug=slug,
                title=title,
                kind="recovery",
                steps=list(seq),
                occurrences=count,
                trigger_pattern=trigger,
                estimated_token_savings=max(450, len(seq) * 420),
            )

    # 2. Mine Recurring Successful Command Pipelines (2-grams and 3-grams)
    clean_cmds = [
        s.command
        for s in command_steps
        if not s.failed and not is_trivial_or_meta_step(s.command)
    ]

    for window in (3, 2):
        ngram_counts: Dict[Tuple[str, ...], int] = {}
        for i in range(len(clean_cmds) - window + 1):
            gram = tuple(clean_cmds[i : i + window])
            # Skip identical repeated single command (that's a loop, not a pipeline)
            if len(set(gram)) < 2:
                continue
            ngram_counts[gram] = ngram_counts.get(gram, 0) + 1

        for gram, count in ngram_counts.items():
            if count >= min_occurrences and gram not in candidates:
                slug, title, trigger = _build_slug_and_trigger(list(gram), kind="pipeline")
                candidates[gram] = ToolCandidate(
                    slug=slug,
                    title=title,
                    kind="pipeline",
                    steps=list(gram),
                    occurrences=count,
                    trigger_pattern=trigger,
                    estimated_token_savings=len(gram) * 450,
                )

    return _subsume_candidates(candidates)


def _render_tool_source(candidate: ToolCandidate, workspace_dir: Optional[Path] = None) -> str:
    """Render a zero-dependency, self-verifying, parameterized Python CLI tool script."""
    clean_steps = [_generalize_step_paths(s, workspace_dir) for s in candidate.steps]
    steps_json = json.dumps(clean_steps, ensure_ascii=False, indent=4)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return f'''#!/usr/bin/env python3
"""
{candidate.title}
Auto-forged by Overmind Autopoietic Tool Forge at {now}.
Trigger Pattern: {candidate.trigger_pattern}
Occurrences Observed: {candidate.occurrences}
"""

import argparse
import json
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

PIPELINE_STEPS: List[str] = {steps_json}


def run_pipeline(
    extra_args: Optional[List[str]] = None,
    dry_run: bool = False,
    stop_on_error: bool = True,
    timeout: int = 120,
) -> Dict[str, Any]:
    if dry_run:
        return {{
            "tool": "{candidate.slug}",
            "status": "dry_run_ok",
            "steps_count": len(PIPELINE_STEPS),
            "steps": PIPELINE_STEPS,
            "extra_args": extra_args or [],
        }}

    results: List[Dict[str, Any]] = []
    overall_ok = True
    start_all = time.monotonic()

    for idx, cmd in enumerate(PIPELINE_STEPS, start=1):
        effective_cmd = cmd
        if extra_args and idx == len(PIPELINE_STEPS):
            effective_cmd = f"{{cmd}} {{' '.join(extra_args)}}"

        t0 = time.monotonic()
        proc = subprocess.run(
            effective_cmd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        step_rec = {{
            "step": idx,
            "command": effective_cmd,
            "exit_code": proc.returncode,
            "elapsed_ms": elapsed_ms,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }}
        results.append(step_rec)
        if proc.returncode != 0:
            overall_ok = False
            if stop_on_error:
                break

    return {{
        "tool": "{candidate.slug}",
        "status": "ok" if overall_ok else "failed",
        "total_elapsed_ms": round((time.monotonic() - start_all) * 1000, 1),
        "steps_executed": len(results),
        "steps_total": len(PIPELINE_STEPS),
        "results": results,
    }}


def main() -> int:
    parser = argparse.ArgumentParser(description={json.dumps(candidate.title)})
    parser.add_argument("--dry-run", action="store_true", help="Validate pipeline steps without executing")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON output")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue remaining steps if a step fails")
    parser.add_argument("--timeout", type=int, default=120, help="Per-step timeout in seconds")
    parser.add_argument("extra_args", nargs="*", default=[], help="Optional pass-through arguments appended to the final command step")
    args = parser.parse_args()

    report = run_pipeline(
        extra_args=args.extra_args,
        dry_run=args.dry_run,
        stop_on_error=not args.continue_on_error,
        timeout=args.timeout,
    )
    if args.json or args.dry_run:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\\n")
    else:
        for r in report.get("results", []):
            status_icon = "PASS" if r["exit_code"] == 0 else "FAIL"
            print(f"[{{status_icon}}] Step {{r['step']}}: {{r['command']}} ({{r['elapsed_ms']}}ms)")
            if r["stdout"]:
                print(r["stdout"])
            if r["stderr"] and r["exit_code"] != 0:
                print(r["stderr"], file=sys.stderr)

    return 0 if report["status"] in ("ok", "dry_run_ok") else 1


if __name__ == "__main__":
    sys.exit(main())
'''


class AutopoieticToolForge:
    """
    Synthesizes, verifies, and binds new CLI tools from agent execution transcripts.
    """

    def __init__(
        self,
        bridge: Optional[CentsibleKernelBridge] = None,
        tools_dir: Optional[Path] = None,
    ):
        self.bridge = bridge or CentsibleKernelBridge()
        self.tools_dir = tools_dir or (get_overmind_home() / "forged_tools")
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.tools_dir / "registry.json"

    def load_registry(self) -> Dict[str, Dict[str, Any]]:
        if not self.registry_path.exists():
            return {}
        try:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except ValueError:
            return {}

    def _save_registry(self, registry: Dict[str, Dict[str, Any]]) -> None:
        self.registry_path.write_text(
            json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def verify_forged_tool(self, tool_path: Path) -> Tuple[bool, str]:
        """
        Deterministic 3-stage sandbox verification before any forged tool is admitted:
        1. `python3 -m py_compile <tool_path>`
        2. `python3 <tool_path> --help`
        3. `python3 <tool_path> --dry-run --json`
        """
        if not tool_path.exists():
            return False, "Tool file does not exist"

        # Gate 1: Syntax compilation
        c_proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(tool_path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if c_proc.returncode != 0:
            return False, f"Syntax check failed: {c_proc.stderr.strip()}"

        # Gate 2: --help contract
        h_proc = subprocess.run(
            [sys.executable, str(tool_path), "--help"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if h_proc.returncode != 0:
            return False, f"--help contract failed: {h_proc.stderr.strip()}"

        # Gate 3: --dry-run --json contract
        d_proc = subprocess.run(
            [sys.executable, str(tool_path), "--dry-run", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if d_proc.returncode != 0:
            return False, f"--dry-run failed: {d_proc.stderr.strip()}"

        try:
            payload = json.loads(d_proc.stdout)
            if payload.get("status") != "dry_run_ok" or not payload.get("steps"):
                return False, "Invalid dry-run JSON payload structure"
        except ValueError as e:
            return False, f"Malformed dry-run JSON output: {e}"

        return True, "Verified (syntax + CLI + dry-run JSON passed)"

    def forge_candidate(
        self,
        candidate: ToolCandidate,
        bind_to_centsible: bool = True,
        workspace_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """
        Synthesize the tool script, run the 3-stage verification gate, and bind to Centsible.
        """
        candidate.steps = [_generalize_step_paths(s, workspace_dir) for s in candidate.steps]
        tool_path = self.tools_dir / f"{candidate.slug}.py"
        source = _render_tool_source(candidate, workspace_dir=workspace_dir)
        tool_path.write_text(source, encoding="utf-8")

        # Make executable (chmod +x)
        mode = tool_path.stat().st_mode
        tool_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        verified, msg = self.verify_forged_tool(tool_path)
        if not verified:
            tool_path.unlink(missing_ok=True)
            raise RuntimeError(f"Forged tool '{candidate.slug}' failed verification gate: {msg}")

        fact_id = f"forge_{candidate.slug}"
        directive = (
            f"MUST RUN: 'python3 {tool_path}' — {candidate.title} "
            f"(replaces {len(candidate.steps)} manual commands)"
        )
        if len(directive) > 235:
            directive = directive[:232].rstrip() + "..."

        if bind_to_centsible:
            self.bridge.add_fact(
                pattern=candidate.trigger_pattern,
                fact=directive,
                category="forged-tool",
                fact_id=fact_id,
                est_savings=candidate.estimated_token_savings,
                force=True,
            )

        reg = self.load_registry()
        entry = {
            **candidate.to_dict(),
            "tool_path": str(tool_path),
            "fact_id": fact_id,
            "directive": directive,
            "verified": True,
            "verification_detail": msg,
            "forged_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        reg[candidate.slug] = entry
        self._save_registry(reg)
        return entry

    def forge_from_transcript(
        self,
        transcript_path: Path,
        min_occurrences: int = 2,
        max_tools: int = 5,
        workspace_dir: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """
        Mine a transcript file and automatically forge + verify the top recurring pipelines.
        """
        cmd_steps = extract_commands_from_transcript(transcript_path)
        candidates = mine_candidates(cmd_steps, min_occurrences=min_occurrences)
        forged: List[Dict[str, Any]] = []
        for cand in candidates[:max_tools]:
            forged.append(self.forge_candidate(cand, workspace_dir=workspace_dir))
        return forged

    def forge_custom_pipeline(
        self,
        name: str,
        steps: List[str],
        trigger_pattern: Optional[str] = None,
        workspace_dir: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Explicitly forge and verify a named multi-step pipeline tool."""
        clean_steps = [_generalize_step_paths(_normalize_command(s), workspace_dir) for s in steps if s.strip()]
        if not clean_steps:
            raise ValueError("Pipeline must contain at least one command step.")
        auto_slug, auto_title, auto_trigger = _build_slug_and_trigger(clean_steps, kind="pipeline")
        slug_clean = re.sub(r"[^a-zA-Z0-9_]", "_", name.lower()).strip("_")
        slug = f"om_{slug_clean}" if not slug_clean.startswith("om_") else slug_clean
        cand = ToolCandidate(
            slug=slug,
            title=f"Forged Tool ({name}): {' -> '.join(_command_head(s) for s in clean_steps)}",
            kind="pipeline",
            steps=clean_steps,
            occurrences=1,
            trigger_pattern=trigger_pattern or auto_trigger,
            estimated_token_savings=max(450, len(clean_steps) * 450),
        )
        return self.forge_candidate(cand, workspace_dir=workspace_dir)

    def prune_tools(
        self,
        slug: Optional[str] = None,
        only_dead: bool = False,
        all_tools: bool = False,
        dry_run: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Prune unverified, dead, or noisy forged tools from filesystem, registry, and Centsible facts.
        """
        reg = self.load_registry()
        to_prune: List[str] = []

        if slug:
            clean_slug = slug.strip()
            for k in list(reg.keys()):
                if k == clean_slug or k == f"om_{clean_slug}" or clean_slug in k:
                    to_prune.append(k)
        elif all_tools:
            to_prune = list(reg.keys())
        elif only_dead:
            facts = {f.get("id"): f for f in self.bridge.list_facts()}
            for s, item in reg.items():
                fid = item.get("fact_id")
                f = facts.get(fid, {})
                hits = int(f.get("hits", 0))
                attributed = int(f.get("attributed_hits", 0))
                if hits == 0 or attributed == 0 or not item.get("verified"):
                    to_prune.append(s)
        else:
            # Default prune: prune noisy/trivial tools (e.g. composed entirely of trivial heads)
            for s, item in reg.items():
                steps = item.get("steps") or []
                if not steps or all(is_trivial_or_meta_step(st) for st in steps) or not item.get("verified"):
                    to_prune.append(s)

        pruned_records: List[Dict[str, Any]] = []
        for s in to_prune:
            item = reg.get(s, {})
            pruned_records.append(item)
            if not dry_run:
                t_path = Path(item.get("tool_path") or (self.tools_dir / f"{s}.py"))
                t_path.unlink(missing_ok=True)

                fid = item.get("fact_id")
                if fid:
                    self.bridge.remove_fact(fid)
                if item.get("trigger_pattern"):
                    self.bridge.remove_fact(item["trigger_pattern"])

                if s in reg:
                    del reg[s]

        if not dry_run and to_prune:
            self._save_registry(reg)

        return pruned_records
