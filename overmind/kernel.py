#!/usr/bin/env python3
"""
Overmind <-> Centsible Reflex Kernel Bridge.

Dynamically binds to the Centsible engine (either installed in site-packages,
located in a sibling repository, or installed in ~/.gemini/config/plugins/centsible)
and provides a zero-dependency standalone fallback if Centsible is not yet installed.
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def get_overmind_home() -> Path:
    """Return ~/.overmind or $OVERMIND_HOME."""
    env_home = os.environ.get("OVERMIND_HOME")
    base = Path(env_home).expanduser().resolve() if env_home else (Path.home() / ".overmind")
    base.mkdir(parents=True, exist_ok=True)
    return base


def _discover_centsible_path() -> Optional[Path]:
    """Locate Centsible repository if not already on sys.path."""
    candidates = []
    env_path = os.environ.get("CENTSIBLE_REPO_PATH")
    if env_path:
        candidates.append(Path(env_path).expanduser().resolve())

    here = Path(__file__).resolve().parent.parent
    candidates.extend([
        here.parent / "centsible",
        Path.home() / ".gemini" / "config" / "plugins" / "centsible",
        Path.home() / ".centsible" / "repo",
    ])
    for cand in candidates:
        if (cand / "core" / "facts_engine.py").exists():
            return cand
    return None


def bind_centsible() -> bool:
    """Ensure Centsible's `core` package is importable. Returns True if bound."""
    try:
        import core.facts_engine  # noqa: F401
        return True
    except ImportError:
        pass

    cand = _discover_centsible_path()
    if cand and str(cand) not in sys.path:
        sys.path.insert(0, str(cand))
        try:
            import core.facts_engine  # noqa: F401
            return True
        except ImportError:
            return False
    return False


class StandaloneFactsKernel:
    """
    Lightweight compatible fallback for Centsible's FactsEngine when running
    on a machine before Centsible is installed. Uses the exact same JSONL format.
    """

    def __init__(self, storage_path: Optional[Path] = None, global_path: Optional[Path] = None):
        self.storage_path = storage_path or (get_overmind_home() / "facts.jsonl")
        self.global_path = global_path
        self.metrics_path = self.storage_path.parent / "metrics.json"

    def list_facts(self, category: Optional[str] = None, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.storage_path.exists():
            return []
        out: List[Dict[str, Any]] = []
        with open(self.storage_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    rec = json.loads(line)
                    if not category or rec.get("category") == category:
                        out.append(rec)
                except ValueError:
                    continue
        return out

    def _write_facts(self, facts: List[Dict[str, Any]]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        content = "\n".join(json.dumps(f, ensure_ascii=False) for f in facts) + ("\n" if facts else "")
        self.storage_path.write_text(content, encoding="utf-8")

    def add_fact(
        self,
        pattern: str,
        fact: str,
        category: str = "general",
        fact_id: Optional[str] = None,
        est_savings: int = 350,
        scope: str = "project",
        force: bool = False,
    ) -> Dict[str, Any]:
        re.compile(pattern, re.IGNORECASE)
        facts = self.list_facts()
        fid = fact_id or f"fact_{hashlib.sha1(pattern.encode('utf-8')).hexdigest()[:8]}"
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for existing in facts:
            if existing.get("id") == fid or existing.get("pattern") == pattern:
                existing["pattern"] = pattern
                existing["fact"] = fact
                existing["category"] = category
                existing["updated_at"] = now
                self._write_facts(facts)
                return existing
        new_rec = {
            "id": fid,
            "pattern": pattern,
            "fact": fact,
            "category": category,
            "hits": 0,
            "attributed_hits": 0,
            "saved_tokens": 0,
            "est_savings": est_savings,
            "created_at": now,
        }
        facts.append(new_rec)
        self._write_facts(facts)
        return new_rec

    def remove_fact(self, identifier: str, scope: str = "project") -> bool:
        facts = self.list_facts()
        remaining = [f for f in facts if f.get("id") != identifier and f.get("pattern") != identifier]
        if len(remaining) == len(facts):
            return False
        self._write_facts(remaining)
        return True

    def get_metrics_summary(self, usd_per_mtok: float = 3.0) -> Dict[str, Any]:
        facts = self.list_facts()
        hits = sum(int(f.get("hits", 0)) for f in facts)
        attr_hits = sum(int(f.get("attributed_hits", 0)) for f in facts)
        saved = sum(int(f.get("saved_tokens", int(f.get("hits", 0)) * 350)) for f in facts)
        attr_saved = sum(int(f.get("attributed_hits", 0)) * int(f.get("est_savings", 350)) for f in facts)
        attr_rate = round((attr_hits / hits * 100.0), 1) if hits > 0 else 0.0
        usd = round(saved / 1_000_000 * usd_per_mtok, 4)
        verified_usd = round(attr_saved / 1_000_000 * usd_per_mtok, 4)
        return {
            "total_facts": len(facts),
            "fact_hits": hits,
            "attributed_hits": attr_hits,
            "attribution_rate": attr_rate,
            "net_saved_tokens": saved,
            "net_tokens_saved": saved,
            "attributed_saved_tokens": attr_saved,
            "attributed_net_saved_tokens": attr_saved,
            "verified_tokens_saved": attr_saved,
            "saved_usd": usd,
            "estimated_usd_saved": usd,
            "verified_usd_saved": verified_usd,
            "loop_interventions": 0,
            "measured_loop_tokens": 0,
            "tool_calls_total": 0,
            "command_calls_total": 0,
            "first_try_rate": 100.0,
            "first_try_success_rate": 100.0,
            "first_try_cmd_rate": 100.0,
        }


def _resolve_smart_facts_path() -> Path:
    """
    Resolve default Centsible facts.jsonl path without letting an empty `.claude/`
    or `.agents/` directory (created by `overmind init` for hooks/rules) shadow an
    existing store in `.agents/` or `~/.centsible/projects/<repo>-<hash>/`.
    """
    from core.facts_engine import get_default_facts_path

    default_path = get_default_facts_path()
    if default_path.exists() or (default_path.parent / "metrics.json").exists():
        return default_path

    cwd = Path.cwd().resolve()
    root = next((p for p in (cwd, *cwd.parents) if (p / ".git").exists()), cwd)
    key = f"{root.name}-{hashlib.sha1(str(root).encode('utf-8')).hexdigest()[:8]}"
    env_home = os.environ.get("CENTSIBLE_HOME")
    global_dir = Path(env_home).expanduser().resolve() if env_home else (Path.home() / ".centsible")

    fallback_candidates = [
        cwd / ".agents" / "facts.jsonl",
        cwd / ".claude" / "facts.jsonl",
        global_dir / "projects" / key / "facts.jsonl",
    ]
    for cand in fallback_candidates:
        if cand.exists() or (cand.parent / "metrics.json").exists():
            return cand
    return default_path


def _discover_all_centsible_stores() -> List[Path]:
    """Discover all known Centsible project facts.jsonl paths (global projects + sibling repos)."""
    stores: List[Path] = []
    seen_dirs = set()

    def _add_store(facts_file: Path) -> None:
        try:
            parent_dir = facts_file.parent.resolve()
        except OSError:
            return
        if parent_dir in seen_dirs:
            return
        if facts_file.exists() or (parent_dir / "metrics.json").exists():
            seen_dirs.add(parent_dir)
            stores.append(facts_file)

    projects_dir = Path.home() / ".centsible" / "projects"
    if projects_dir.exists():
        for p_dir in sorted(projects_dir.glob("*")):
            if p_dir.is_dir():
                _add_store(p_dir / "facts.jsonl")

    cwd = Path.cwd().resolve()
    search_roots = [cwd]
    if cwd.parent.exists() and cwd.parent != cwd:
        try:
            search_roots.extend(sorted(d for d in cwd.parent.iterdir() if d.is_dir()))
        except OSError:
            pass

    for repo_dir in search_roots:
        if repo_dir == Path.home():
            continue
        for folder in (".agents", ".claude"):
            _add_store(repo_dir / folder / "facts.jsonl")

    return stores


class CentsibleKernelBridge:
    """
    Unified interface over Centsible's Reflex Kernel (FactsEngine, EventLog, Transcript).
    """

    def __init__(
        self,
        facts_path: Optional[Path] = None,
        events_path: Optional[Path] = None,
    ):
        self.centsible_bound = bind_centsible()
        self.facts_path = facts_path
        self.events_path = events_path

        if self.centsible_bound:
            from core.facts_engine import FactsEngine, get_global_facts_path
            target_path = facts_path or _resolve_smart_facts_path()
            self.engine: Any = FactsEngine(storage_path=target_path, global_path=get_global_facts_path())
            self.storage_path = self.engine.storage_path
        else:
            target_path = facts_path or (get_overmind_home() / "facts.jsonl")
            self.engine = StandaloneFactsKernel(storage_path=target_path)
            self.storage_path = self.engine.storage_path

    def list_facts(self, category: Optional[str] = None, scope: Optional[str] = "project") -> List[Dict[str, Any]]:
        if scope == "all" and self.centsible_bound and self.facts_path is None:
            all_facts: List[Dict[str, Any]] = []
            seen_ids = set()
            candidate_files = _discover_all_centsible_stores()
            gf = Path.home() / ".centsible" / "facts.jsonl"
            if gf.exists():
                candidate_files.append(gf)

            for pf in candidate_files:
                if not pf.exists():
                    continue
                for line in pf.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    try:
                        rec = json.loads(line)
                        fid = rec.get("id") or rec.get("pattern")
                        if fid and fid not in seen_ids:
                            if not category or rec.get("category") == category:
                                seen_ids.add(fid)
                                all_facts.append(rec)
                    except ValueError:
                        continue
            return all_facts

        return self.engine.list_facts(category=category, scope=scope)

    def add_fact(
        self,
        pattern: str,
        fact: str,
        category: str = "general",
        fact_id: Optional[str] = None,
        est_savings: int = 450,
        force: bool = True,
    ) -> Dict[str, Any]:
        return self.engine.add_fact(
            pattern=pattern,
            fact=fact,
            category=category,
            fact_id=fact_id,
            est_savings=est_savings,
            force=force,
        )

    def remove_fact(self, identifier: str) -> bool:
        if hasattr(self.engine, "remove_fact"):
            try:
                if self.engine.remove_fact(identifier, scope="project"):
                    return True
            except TypeError:
                pass
            try:
                if self.engine.remove_fact(identifier, scope="global"):
                    return True
            except (TypeError, Exception):
                pass
            try:
                return bool(self.engine.remove_fact(identifier))
            except Exception:
                return False
        return False

    def read_events(self) -> List[Dict[str, Any]]:
        if self.events_path and self.events_path.exists():
            events: List[Dict[str, Any]] = []
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        continue
            return events

        if self.centsible_bound:
            try:
                from core.event_log import read_events
                return read_events()
            except Exception:
                return []
        return []

    def get_metrics_summary(self, scope: Optional[str] = "project") -> Dict[str, Any]:
        if scope == "all" and self.centsible_bound and self.facts_path is None:
            from core.facts_engine import FactsEngine, derive_summary, get_global_facts_path
            sum_keys = (
                "prompt_injections", "fact_hits", "attributed_hits", "tool_hint_hits",
                "injected_tokens", "saved_discovery_tokens", "loop_interventions",
                "saved_loop_tokens", "measured_loop_tokens", "tool_calls_total",
                "tool_calls_failed", "first_try_successes", "command_calls_total",
                "command_calls_failed", "first_try_cmd_successes", "auto_learned_facts",
                "total_facts",
            )
            fact_keys = ("fact_hits", "attributed_hits", "injected_tokens", "saved_discovery_tokens", "auto_learned_facts")
            total = dict.fromkeys(sum_keys, 0)
            stores = _discover_all_centsible_stores()
            for sf in stores:
                for k, v in FactsEngine(storage_path=sf).get_metrics_summary().items():
                    if k in total:
                        total[k] += int(v)
            gpath = get_global_facts_path()
            if gpath.exists() or (gpath.parent / "metrics.json").exists():
                for k, v in FactsEngine(storage_path=gpath).get_metrics_summary().items():
                    if k in fact_keys:
                        total[k] = max(total[k], int(v))
                    elif k in total:
                        total[k] += int(v)
            m = dict(derive_summary({**total, "projects": len(stores)}))
        else:
            m = dict(self.engine.get_metrics_summary())

        # Normalize aliases between Centsible and Standalone engines
        net_est = int(m.get("net_tokens_saved", m.get("net_saved_tokens", 0)))
        usd_est = float(m.get("estimated_usd_saved", m.get("saved_usd", 0.0)))
        m["net_tokens_saved"] = net_est
        m["net_saved_tokens"] = net_est
        m["estimated_usd_saved"] = usd_est
        m["saved_usd"] = usd_est

        # Verified/Attributed token savings (backed by actual command attribution or halted loops)
        attr_net = m.get("attributed_net_saved_tokens")
        if attr_net is None:
            attr_net = m.get("attributed_saved_tokens", 0)
        verified_tok = max(0, int(attr_net))
        usd_rate = float(m.get("usd_per_mtok", 3.0))
        verified_usd = round((verified_tok / 1_000_000.0) * usd_rate, 4)
        m["verified_tokens_saved"] = verified_tok
        m["verified_usd_saved"] = verified_usd

        # Empirical execution rates
        m.setdefault("first_try_success_rate", float(m.get("first_try_rate", 100.0)))
        m.setdefault("first_try_rate", float(m.get("first_try_success_rate", 100.0)))
        m.setdefault("first_try_cmd_rate", float(m.get("first_try_success_rate", 100.0)))
        m.setdefault("attribution_rate", 0.0)
        return m
