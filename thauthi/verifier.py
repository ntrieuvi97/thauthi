#!/usr/bin/env python3
"""
Adversarial Shadow Verifier for Thauthi.

Acts as an uncompromising autonomous Principal Engineer before an agent session finishes:
- Compiles Python files to catch syntax/indentation regressions immediately.
- Scans for leftover debug traps (pdb.set_trace, breakpoint), unresolved git merge markers,
  and accidentally hardcoded credentials.
- Optionally runs the repository's fast test suite to guarantee zero regression.
"""

from thauthi.safepaths import iter_python_files
import os
import py_compile
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

TRAP_PATTERNS = [
    ("debug_trap", re.compile(r"^\s*(?:import\s+pdb|pdb\.set_trace\s*\(|breakpoint\s*\()"), "Leftover Python debugger statement"),
    ("merge_conflict", re.compile(r"^(?:<{7}\s|={7}$|>{7}\s)"), "Unresolved Git merge conflict marker"),
    ("secret_leak", re.compile(r"\b(?:sk-[A-Za-z0-9]{24,}|ghp_[A-Za-z0-9]{30,}|AIza[0-9A-Za-z\-_]{35})\b"), "Potential hardcoded API secret key"),
]

IGNORED_DIRS = {".git", ".hg", ".svn", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv", "dist", "build"}


class ShadowVerifier:
    def __init__(self, workspace_dir: Optional[Path] = None):
        self.workspace_dir = (workspace_dir or Path.cwd()).resolve()

    def _collect_python_files(self) -> List[Path]:
        return iter_python_files(self.workspace_dir, IGNORED_DIRS)

    def verify_workspace(self, run_tests: bool = False, timeout: int = 60) -> Dict[str, Any]:
        py_files = self._collect_python_files()
        issues: List[Dict[str, Any]] = []

        for fpath in py_files:
            rel = str(fpath.relative_to(self.workspace_dir))
            # 1. Syntax compilation gate
            try:
                py_compile.compile(str(fpath), doraise=True)
            except py_compile.PyCompileError as e:
                issues.append({
                    "kind": "syntax_error",
                    "file": rel,
                    "line": 0,
                    "detail": str(e.msg),
                })
                continue

            # 2. Trap & secret scan
            try:
                lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue

            for lineno, line in enumerate(lines, start=1):
                for kind, regex, desc in TRAP_PATTERNS:
                    if regex.search(line):
                        issues.append({
                            "kind": kind,
                            "file": rel,
                            "line": lineno,
                            "detail": desc,
                        })

        test_result: Optional[Dict[str, Any]] = None
        if run_tests and (self.workspace_dir / "tests").exists():
            env = dict(os.environ)
            existing_pp = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{self.workspace_dir}{os.pathsep}{existing_pp}" if existing_pp else str(self.workspace_dir)
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "tests", "-q", "--tb=short"],
                cwd=str(self.workspace_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            test_result = {
                "exit_code": proc.returncode,
                "passed": proc.returncode == 0,
                "output": (proc.stdout + "\n" + proc.stderr).strip()[-600:],
            }
            if proc.returncode != 0:
                issues.append({
                    "kind": "test_failure",
                    "file": "tests/",
                    "line": 0,
                    "detail": f"Test suite failed with exit code {proc.returncode}",
                })

        passed = len(issues) == 0
        score = max(0, 100 - len(issues) * 20)
        return {
            "workspace": str(self.workspace_dir),
            "passed": passed,
            "integrity_score": score,
            "files_checked": len(py_files),
            "issues_count": len(issues),
            "issues": issues,
            "test_suite": test_result,
        }
