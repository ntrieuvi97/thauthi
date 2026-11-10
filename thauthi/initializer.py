#!/usr/bin/env python3
"""
World-Scale Repository Onboarding (`thauthi init`).

Turns any git repository on Earth into an Thauthi 2.0 Autopoietic Repository with a single command:
1. Portable Team DNA (`.thauthi/dna.jsonl`) committed to the repo
2. Git Pre-Commit Adversarial & Differential Guard (`.git/hooks/pre-commit`)
3. GitHub Actions Autonomous PR Gate & Scorecard (`.github/workflows/thauthi.yml`)
4. Multi-Runtime Agent Wiring:
   - Claude Code (`.claude/settings.json`)
   - Cursor (`.cursor/rules/thauthi.mdc`)
   - Google Antigravity (`.agent/rules/thauthi.md`)
"""

import json
import os
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional

PRE_COMMIT_MARKER = "# >>> thauthi-pre-commit >>>"

PRE_COMMIT_SCRIPT = f"""#!/usr/bin/env bash
{PRE_COMMIT_MARKER}
if command -v thauthi >/dev/null 2>&1; then
    echo "🧠 [Thauthi 2.0] Running pre-commit Adversarial Shadow Verifier & Symbolic Crystals..."
    if [ -f ".thauthi/dna.jsonl" ]; then
        thauthi dna import --path .thauthi/dna.jsonl >/dev/null 2>&1 || true
    fi
    thauthi crystallize --apply . >/dev/null 2>&1 || true
    thauthi verify
fi
# <<< thauthi-pre-commit <<<
"""

from thauthi import __version__  # noqa: E402  (defined before thauthi/__init__ imports this module)

# Pinned to this release's tag so a compromised `main` cannot reach every onboarded repo's CI.
GITHUB_WORKFLOW_YAML = f"""name: Thauthi 2.0 Autonomous Verification & Evolution Gate

on:
  push:
    branches: [ main, master ]
  pull_request:
    branches: [ main, master ]

jobs:
  thauthi-gate:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Thauthi 2.0 Kernel & Project Dependencies
        run: |
          python -m pip install --upgrade pip pytest
          python -m pip install git+https://github.com/ntrieuvi97/thauthi.git@v{__version__}
          if [ -f "pyproject.toml" ] || [ -f "setup.py" ]; then
            pip install -e . || true
          fi
          if [ -f "requirements.txt" ]; then
            pip install -r requirements.txt || true
          fi

      - name: Import Team Elite DNA & Apply 0-Token Symbolic AST Crystals
        run: |
          if [ -f ".thauthi/dna.jsonl" ]; then
            thauthi dna import --path .thauthi/dna.jsonl
          fi
          thauthi crystallize --apply . --dry-run

      - name: Run Adversarial Shadow Verifier & Test Suite
        run: |
          thauthi verify --run-tests

      - name: Publish Thauthi Evolution Scorecard to PR Summary
        if: always()
        run: |
          thauthi scorecard --markdown >> "$GITHUB_STEP_SUMMARY"
"""

AGENT_RULE_MARKDOWN = """# Thauthi 4.0 Post-Developer Singularity Kernel Rules

When working in this repository, use the Thauthi 4.0 CLI (`thauthi`) to eliminate static guessing and guarantee zero-regression code:

1. **Runtime Fault Localization (`thauthi trace <script.py>`):** Inspect live `FrameDNA` and Backward Dynamic Causal Slices instead of guessing why code crashes.
2. **In-Flight Self-Healing (`thauthi heal <script.py> --apply`):** Automatically synthesize `pytest` regression tests from crashing frames and verify AST patches in memory.
3. **Mathematical Equivalence & Complexity Proof (`thauthi prove --before <old.py> --after <new.py>`):** Prove semantic equivalence, input purity, and prevent `O(N) -> O(N^2)+` regressions.
4. **Zero-Token AST Crystallization (`thauthi crystallize`):** Compile recurring code patterns into `<2ms` symbolic AST codemods (`thauthi crystallize --apply .`).
5. **Autonomous O(N^2)->O(N) Optimization (`thauthi optimize <file.py> --apply`):** Automatically transmute nested scans & exponential recursion with equivalence proofs.
6. **Chronos Time-Travel Replay (`thauthi chronos --script <file.py>`):** Record and replay stateful I/O cassettes in `<1ms` with zero side effects.
7. **Contract-to-Code CEGIS Synthesis (`thauthi synthesize <file.py> --apply`):** Synthesize proven code from docstring `>>>` examples.
8. **Whole-Repo Call-Graph Hive (`thauthi hive . --apply`):** Automatically repair broken callers across files after function signature changes.
9. **Zero-Dep MCP Server (`thauthi mcp`):** Prefer Thauthi MCP tools (`thauthi_trace`, `thauthi_prove`, `thauthi_optimize`, `thauthi_hive`, `thauthi_chronos`) over static text guessing.
10. **Pre-Completion Verification (`thauthi verify --run-tests`):** Always run before finishing any task.
"""


class RepoInitializer:
    """Scaffolds Thauthi 2.0 into any target repository for universal team & CI adoption."""

    def __init__(self, repo_dir: Optional[Path] = None):
        self.repo_dir = (repo_dir or Path.cwd()).resolve()

    def init_dna_store(self) -> Path:
        dna_file = self.repo_dir / ".thauthi" / "dna.jsonl"
        dna_file.parent.mkdir(parents=True, exist_ok=True)
        if not dna_file.exists():
            dna_file.write_text("", encoding="utf-8")
        return dna_file

    def install_git_pre_commit_hook(self) -> Optional[Path]:
        git_dir = self.repo_dir / ".git"
        if not git_dir.exists() or not git_dir.is_dir():
            return None
        hooks_dir = git_dir / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        hook_file = hooks_dir / "pre-commit"

        if hook_file.exists():
            existing = hook_file.read_text(encoding="utf-8", errors="replace")
            if PRE_COMMIT_MARKER not in existing:
                updated = existing.rstrip() + "\n\n" + PRE_COMMIT_SCRIPT.split("\n", 1)[1]
                hook_file.write_text(updated, encoding="utf-8")
        else:
            hook_file.write_text(PRE_COMMIT_SCRIPT, encoding="utf-8")

        st = os.stat(hook_file)
        os.chmod(hook_file, st.st_mode | stat.S_IEXEC)
        return hook_file

    def install_github_workflow(self) -> Path:
        wf_file = self.repo_dir / ".github" / "workflows" / "thauthi.yml"
        wf_file.parent.mkdir(parents=True, exist_ok=True)
        wf_file.write_text(GITHUB_WORKFLOW_YAML, encoding="utf-8")
        return wf_file

    def install_agent_configs(self) -> List[str]:
        installed: List[str] = []

        # 1. Google Antigravity (.agent/rules/thauthi.md)
        agy_rule = self.repo_dir / ".agent" / "rules" / "thauthi.md"
        agy_rule.parent.mkdir(parents=True, exist_ok=True)
        agy_rule.write_text(AGENT_RULE_MARKDOWN, encoding="utf-8")
        installed.append(str(agy_rule.relative_to(self.repo_dir)))

        # 2. Cursor (.cursor/rules/thauthi.mdc)
        cursor_rule = self.repo_dir / ".cursor" / "rules" / "thauthi.mdc"
        cursor_rule.parent.mkdir(parents=True, exist_ok=True)
        cursor_mdc = (
            "---\n"
            "description: Thauthi 2.0 Omniscient Autopoietic Kernel workflow and verification rules\n"
            "globs: **/*.py\n"
            "alwaysApply: true\n"
            "---\n\n"
            + AGENT_RULE_MARKDOWN
        )
        cursor_rule.write_text(cursor_mdc, encoding="utf-8")
        installed.append(str(cursor_rule.relative_to(self.repo_dir)))

        # 3. Claude Code (.claude/settings.json)
        claude_settings_path = self.repo_dir / ".claude" / "settings.json"
        claude_settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings: Dict[str, Any] = {}
        if claude_settings_path.exists():
            try:
                settings = json.loads(claude_settings_path.read_text(encoding="utf-8"))
            except ValueError:
                settings = {}

        hooks_cfg = settings.setdefault("hooks", {})
        stop_hooks = hooks_cfg.setdefault("Stop", [])
        thauthi_hook_entry = {
            "matcher": "",
            "hooks": [
                {
                    "type": "command",
                    "command": "thauthi hook stop",
                }
            ],
        }
        if not any("thauthi hook stop" in json.dumps(h) for h in stop_hooks):
            stop_hooks.append(thauthi_hook_entry)
        claude_settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        installed.append(str(claude_settings_path.relative_to(self.repo_dir)))

        # 4. Standard MCP Server Config (.mcp.json & .cursor/mcp.json)
        for mcp_rel in (Path(".mcp.json"), Path(".cursor") / "mcp.json"):
            mcp_path = self.repo_dir / mcp_rel
            mcp_path.parent.mkdir(parents=True, exist_ok=True)
            mcp_cfg: Dict[str, Any] = {}
            if mcp_path.exists():
                try:
                    mcp_cfg = json.loads(mcp_path.read_text(encoding="utf-8"))
                except ValueError:
                    mcp_cfg = {}
            servers = mcp_cfg.setdefault("mcpServers", {})
            if "thauthi" not in servers:
                servers["thauthi"] = {
                    "command": "thauthi",
                    "args": ["mcp", "--root", "."],
                }
            mcp_path.write_text(json.dumps(mcp_cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            installed.append(str(mcp_path.relative_to(self.repo_dir)))

        return installed

    def initialize_repository(
        self,
        with_ci: bool = True,
        with_git_hook: bool = True,
        with_agents: bool = True,
    ) -> Dict[str, Any]:
        dna_path = self.init_dna_store()
        created_files: List[str] = [str(dna_path.relative_to(self.repo_dir))]

        git_hook_path: Optional[Path] = None
        if with_git_hook:
            git_hook_path = self.install_git_pre_commit_hook()
            if git_hook_path:
                created_files.append(str(git_hook_path.relative_to(self.repo_dir)))

        if with_ci:
            wf_path = self.install_github_workflow()
            created_files.append(str(wf_path.relative_to(self.repo_dir)))

        if with_agents:
            created_files.extend(self.install_agent_configs())

        return {
            "repository": str(self.repo_dir),
            "initialized": True,
            "files_configured": created_files,
        }
