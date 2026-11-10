"""Regression tests for v4.0.12: symlink-safe crystal storage + opt-in OpenClaw hook pack."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from thauthi.crystallizer import NeuralSymbolicCrystallizer

ROOT = Path(__file__).resolve().parents[1]


class _NullBridge:
    def add_fact(self, *a, **kw):
        return None


def _cryst(ws: Path) -> NeuralSymbolicCrystallizer:
    return NeuralSymbolicCrystallizer(bridge=_NullBridge(), workspace_dir=ws)


def _create(c: NeuralSymbolicCrystallizer, name: str = "t"):
    return c.crystallize_edit(
        name=name,
        before_code="subprocess.run(c, check=True)",
        after_code="subprocess.run(c, check=True, timeout=30)",
    )


def test_symlinked_thauthi_dir_is_rejected(tmp_path: Path) -> None:
    ws, outside = tmp_path / "ws", tmp_path / "outside"
    ws.mkdir()
    outside.mkdir()
    os.symlink(outside, ws / ".thauthi")
    with pytest.raises(ValueError):
        _create(_cryst(ws))
    assert list(outside.iterdir()) == []


def test_symlinked_crystals_dir_is_rejected(tmp_path: Path) -> None:
    ws, outside = tmp_path / "ws", tmp_path / "outside"
    (ws / ".thauthi").mkdir(parents=True)
    outside.mkdir()
    os.symlink(outside, ws / ".thauthi" / "crystals")
    with pytest.raises(ValueError):
        _create(_cryst(ws))
    assert list(outside.iterdir()) == []


def test_symlinked_registry_and_tool_file_are_not_written_through(tmp_path: Path) -> None:
    ws, outside = tmp_path / "ws", tmp_path / "outside"
    cdir = ws / ".thauthi" / "crystals"
    cdir.mkdir(parents=True)
    outside.mkdir()
    victim_reg = outside / "reg.json"
    victim_reg.write_text("ORIGINAL", encoding="utf-8")
    victim_tool = outside / "tool.py"
    victim_tool.write_text("ORIGINAL", encoding="utf-8")
    os.symlink(victim_reg, cdir / "registry.json")
    os.symlink(victim_tool, cdir / "cr_t.py")
    with pytest.raises(ValueError):
        _create(_cryst(ws))
    assert victim_reg.read_text(encoding="utf-8") == "ORIGINAL"
    assert victim_tool.read_text(encoding="utf-8") == "ORIGINAL"
    assert _cryst(ws).load_registry() == {}  # linked registry is never trusted/read


def test_normal_crystal_storage_still_works_and_is_executable(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    crystal = _create(_cryst(ws))
    tool = Path(crystal.tool_path)
    assert tool.parent == ws / ".thauthi" / "crystals" and os.access(tool, os.X_OK)
    assert "cr_t" in json.loads((tool.parent / "registry.json").read_text(encoding="utf-8"))
    assert not list(tool.parent.glob(".*.tmp"))


# ---- OpenClaw hook pack -------------------------------------------------------------------------

HOOK_DIR = ROOT / "hooks" / "thauthi-evolve"


def test_hook_manifest_is_opt_in_and_scoped() -> None:
    text = (HOOK_DIR / "HOOK.md").read_text(encoding="utf-8")
    assert '"requires": { "bins": ["thauthi"], "env": ["THAUTHI_OPENCLAW_HOOK"] }' in text  # off until opted in
    events = re.search(r'"events": \[(.*?)\]', text).group(1)
    assert set(re.findall(r'"([^"]+)"', events)) == {"command:new", "command:reset", "session:auto-reset"}


def test_hook_handler_never_forwards_transcript_or_event_data() -> None:
    raw = (HOOK_DIR / "handler.js").read_text(encoding="utf-8")
    src = "\n".join(line.split("//")[0] for line in raw.splitlines())  # ignore comments
    assert 'execFile(\n        "thauthi",\n        ["hook", "stop"]' in src  # fixed argv, no shell
    assert "shell:" not in src and "exec(" not in src.replace("execFile(", "")
    assert "child.stdin?.end()" in src  # empty stdin => no transcript mining/forging
    for forbidden in ("sessionFile", "transcript", "senderId", "content", "cfg", "process.env,"):
        assert forbidden not in src, forbidden


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_hook_handler_behaviour_with_stub_cli(tmp_path: Path) -> None:
    bindir, ws = tmp_path / "bin", tmp_path / "ws"
    bindir.mkdir()
    ws.mkdir()
    marker = tmp_path / "calls.log"
    stub = bindir / "thauthi"
    stub.write_text(f'#!/bin/sh\necho "$@|$(cat)|$PWD" >> "{marker}"\n', encoding="utf-8")
    stub.chmod(0o755)
    script = f"""
import handler from {json.dumps((HOOK_DIR / "handler.js").as_uri())};
const ev = (type, action, ctx) => ({{ type, action, context: ctx, messages: [], sessionKey: "k" }});
await handler(ev("command", "new", {{ workspaceDir: {json.dumps(str(ws))}, sessionFile: "/etc/passwd", senderId: "x" }}));
await handler(ev("command", "new", {{ workspaceDir: {json.dumps(str(ws))} }}));        // rate limited
await handler(ev("message", "received", {{ workspaceDir: {json.dumps(str(ws))} }}));   // wrong event
await handler(ev("command", "reset", {{ workspaceDir: "relative/dir" }}));              // bad workspace
await handler(ev("command", "reset", {{}}));                                              // no workspace
"""
    (tmp_path / "run.mjs").write_text(script, encoding="utf-8")
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(tmp_path)}
    subprocess.run([shutil.which("node"), str(tmp_path / "run.mjs")], check=True, env=env, timeout=30)
    lines = marker.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, lines
    args, stdin, cwd = lines[0].split("|")
    assert args == "hook stop" and stdin == "" and Path(cwd).resolve() == ws.resolve()


def test_codex_manifest_exposes_hook_pack_and_versions_match() -> None:
    codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert codex["hooks"] == "./hooks/" and codex["skills"] == "./skills/"
    assert codex["version"] == pkg["version"] == claude["version"]
    # Claude Code only accepts *.json hook files here; a directory would break its manifest validation.
    assert all(h.endswith(".json") for h in ([claude["hooks"]] if isinstance(claude["hooks"], str) else claude["hooks"]))
