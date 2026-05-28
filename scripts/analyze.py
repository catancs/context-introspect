#!/usr/bin/env python3
"""context-introspect: audit Claude Code config cost vs. real usage."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

WINDOW_DAYS = 30
DISABLED_DIRNAME = ".context-introspect-disabled"

Item = dict


# ---------------------------------------------------------------------------
# Task 2: estimate_tokens / parse_ts
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """Rough heuristic: ~4 chars per token. Labelled 'estimated' everywhere."""
    if not text:
        return 0
    return len(text) // 4


def parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Task 3: read_frontmatter / collect_skills
# ---------------------------------------------------------------------------

def read_frontmatter(path: Path) -> tuple[str, str]:
    """Return (description, body). Minimal parser; only needs 'description'."""
    text = path.read_text(encoding="utf-8", errors="replace")
    description = ""
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            header = text[3:end]
            body = text[end + 4:]
            for line in header.splitlines():
                if line.strip().lower().startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip('"\'')
                    break
    return description, body


def _skill_item(skill_dir: Path, scope: str) -> Item:
    md = skill_dir / "SKILL.md"
    description, body = read_frontmatter(md) if md.exists() else ("", "")
    return {
        "type": "skill",
        "name": skill_dir.name,
        "scope": scope,
        "persistent_tokens_est": estimate_tokens(description),
        "ondemand_tokens_est": estimate_tokens(body),
        "cost_basis": "estimated",
        "source_path": str(skill_dir),
    }


def collect_plugin_skills(home: Path) -> list[Item]:
    """Return Items for plugin-provided skills under ~/.claude/plugins/."""
    plugins_dir = home / ".claude" / "plugins"
    if not plugins_dir.is_dir():
        return []
    items: list[Item] = []
    seen_paths: set[str] = set()
    for md in plugins_dir.rglob("SKILL.md"):
        if md.parent.parent.name != "skills":
            continue
        skill_dir = md.parent
        src = str(skill_dir)
        if src in seen_paths:
            continue
        seen_paths.add(src)
        description, body = read_frontmatter(md)
        items.append({
            "type": "skill",
            "name": skill_dir.name,
            "scope": "plugin",
            "persistent_tokens_est": estimate_tokens(description),
            "ondemand_tokens_est": estimate_tokens(body),
            "cost_basis": "estimated",
            "source_path": src,
        })
    return items


def collect_plugin_agents(home: Path) -> list[Item]:
    """Return Items for plugin-provided agents under ~/.claude/plugins/."""
    plugins_dir = home / ".claude" / "plugins"
    if not plugins_dir.is_dir():
        return []
    items: list[Item] = []
    seen_paths: set[str] = set()
    for md in plugins_dir.rglob("*.md"):
        if md.parent.name != "agents":
            continue
        src = str(md)
        if src in seen_paths:
            continue
        seen_paths.add(src)
        description, body = read_frontmatter(md)
        items.append({
            "type": "subagent",
            "name": md.stem,
            "scope": "plugin",
            "persistent_tokens_est": estimate_tokens(description),
            "ondemand_tokens_est": estimate_tokens(body),
            "cost_basis": "estimated",
            "source_path": src,
        })
    return items


def collect_plugin_commands(home: Path) -> list[Item]:
    """Return Items for plugin-provided commands under ~/.claude/plugins/."""
    plugins_dir = home / ".claude" / "plugins"
    if not plugins_dir.is_dir():
        return []
    items: list[Item] = []
    seen_paths: set[str] = set()
    for md in plugins_dir.rglob("*.md"):
        if md.parent.name != "commands":
            continue
        src = str(md)
        if src in seen_paths:
            continue
        seen_paths.add(src)
        description, body = read_frontmatter(md)
        items.append({
            "type": "command",
            "name": md.stem,
            "scope": "plugin",
            "persistent_tokens_est": estimate_tokens(description),
            "ondemand_tokens_est": estimate_tokens(body),
            "cost_basis": "estimated",
            "source_path": src,
        })
    return items


def collect_skills(home: Path, project: Path) -> list[Item]:
    items: list[Item] = []
    for root, scope in ((home, "user"), (project, "project")):
        skills_dir = root / ".claude" / "skills"
        if not skills_dir.is_dir():
            continue
        for child in sorted(skills_dir.iterdir()):
            if child.is_dir() and (child / "SKILL.md").exists():
                items.append(_skill_item(child, scope))
    return items


# ---------------------------------------------------------------------------
# Task 4: collect_md_items / collect_memory
# ---------------------------------------------------------------------------

def collect_md_items(root: Path, item_type: str, scope: str) -> list[Item]:
    items: list[Item] = []
    if not root.is_dir():
        return items
    for md in sorted(root.glob("*.md")):
        description, body = read_frontmatter(md)
        items.append({
            "type": item_type,
            "name": md.stem,
            "scope": scope,
            "persistent_tokens_est": estimate_tokens(description),
            "ondemand_tokens_est": estimate_tokens(body),
            "cost_basis": "estimated",
            "source_path": str(md),
        })
    return items


def _memory_item(path: Path, label: str) -> Item | None:
    if not path.is_file():
        return None
    # MEMORY.md only loads its first ~25KB each session; cap the estimate.
    raw = path.read_text(encoding="utf-8", errors="replace")
    capped = raw[:25_000] if path.name == "MEMORY.md" else raw
    return {
        "type": "memory",
        "name": label,
        "scope": "user" if "(user)" in label else "project",
        "persistent_tokens_est": estimate_tokens(capped),
        "ondemand_tokens_est": 0,
        "cost_basis": "estimated",
        "source_path": str(path),
    }


def collect_memory(home: Path, project: Path) -> list[Item]:
    candidates = [
        (home / ".claude" / "CLAUDE.md", "CLAUDE.md (user)"),
        (home / ".claude" / "MEMORY.md", "MEMORY.md (user)"),
        (project / ".claude" / "CLAUDE.md", "CLAUDE.md (project)"),
        (project / "CLAUDE.md", "CLAUDE.md (project-root)"),
    ]
    return [it for path, label in candidates if (it := _memory_item(path, label))]


# ---------------------------------------------------------------------------
# Task 5: collect_mcp_servers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _mcp_item(name: str, scope: str, source_path: str) -> Item:
    return {
        "type": "mcp",
        "name": name,
        "scope": scope,
        "persistent_tokens_est": None,   # not measurable in v1
        "ondemand_tokens_est": 0,
        "cost_basis": "unknown-v1",
        "source_path": source_path,
    }


def collect_mcp_servers(home: Path, project: Path) -> list[Item]:
    items: list[Item] = []
    seen: set[str] = set()

    def add(servers: dict, scope: str, src: str):
        for name in (servers or {}):
            if name not in seen:
                seen.add(name)
                items.append(_mcp_item(name, scope, src))

    claude_json = home / ".claude.json"
    if claude_json.exists():
        data = _load_json(claude_json)
        add(data.get("mcpServers", {}), "user", str(claude_json))
        for proj_cfg in (data.get("projects", {}) or {}).values():
            add(proj_cfg.get("mcpServers", {}), "project", str(claude_json))

    mcp_json = project / ".mcp.json"
    if mcp_json.exists():
        add(_load_json(mcp_json).get("mcpServers", {}), "project", str(mcp_json))

    return items


# ---------------------------------------------------------------------------
# Task 5 v2: _mcp_server_configs / measure_mcp_servers
# ---------------------------------------------------------------------------

def _mcp_server_configs(home: Path, project: Path) -> dict:
    """Return {name: config_dict} for every configured MCP server.

    Reads the same locations as collect_mcp_servers:
      - ~/.claude.json  top-level mcpServers
      - ~/.claude.json  projects.<path>.mcpServers
      - <project>/.mcp.json  mcpServers
    """
    configs: dict = {}
    seen: set = set()

    def add(servers: dict, _scope: str):
        for name, cfg in (servers or {}).items():
            if name not in seen:
                seen.add(name)
                configs[name] = cfg

    claude_json = home / ".claude.json"
    if claude_json.exists():
        data = _load_json(claude_json)
        add(data.get("mcpServers", {}), "user")
        for proj_cfg in (data.get("projects", {}) or {}).values():
            add(proj_cfg.get("mcpServers", {}), "project")

    mcp_json = project / ".mcp.json"
    if mcp_json.exists():
        add(_load_json(mcp_json).get("mcpServers", {}), "project")

    return configs


def _send(proc: subprocess.Popen, msg: dict) -> None:
    """Write one newline-terminated JSON-RPC message to the process stdin."""
    line = json.dumps(msg, separators=(",", ":")) + "\n"
    proc.stdin.write(line)
    proc.stdin.flush()


def _recv_result(proc: subprocess.Popen, expected_id: int, timeout: float) -> dict:
    """Read lines from proc.stdout until we get a JSON-RPC response with the expected id.

    Uses a background thread so we can enforce a deadline without blocking forever.
    Returns the parsed response dict, or raises RuntimeError on timeout/error.
    """
    result_holder: list = []
    error_holder: list = []

    def reader():
        try:
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("id") == expected_id:
                    result_holder.append(msg)
                    break
        except Exception as exc:  # noqa: BLE001
            error_holder.append(exc)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise RuntimeError(f"timeout waiting for response id={expected_id}")
    if error_holder:
        raise RuntimeError(f"read error: {error_holder[0]}")
    if not result_holder:
        raise RuntimeError("process closed stdout without sending response")
    return result_holder[0]


def _terminate_proc(proc: subprocess.Popen) -> None:
    """Terminate a subprocess, escalating to SIGKILL if needed, and close its pipes."""
    # Close pipes first so the process sees EOF and is less likely to block.
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    except Exception:  # noqa: BLE001
        pass


_MCP_PROTOCOL_VERSION = "2025-11-25"
_REMOTE_SKIP_REASON = "remote (not measured in this version)"


def measure_mcp_servers(configs: dict, timeout: float = 10.0) -> dict:
    """Launch each stdio MCP server, perform the JSON-RPC handshake, and count tools.

    Args:
        configs: {name: config_dict} as returned by _mcp_server_configs.
        timeout: per-server wall-clock deadline in seconds.

    Returns a dict with keys: measured, skipped, total_measured_tokens, measured_count.
    """
    measured: list = []
    skipped: list = []

    for name, cfg in configs.items():
        command = cfg.get("command")
        if not command:
            # No command → remote/SSE server
            skipped.append({"name": name, "reason": _REMOTE_SKIP_REASON})
            continue

        # Build argv
        argv = [command] + list(cfg.get("args") or [])
        env = {**os.environ, **(cfg.get("env") or {})}

        proc: subprocess.Popen | None = None
        try:
            proc = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                env=env,
            )

            # Step 1: send initialize
            _send(proc, {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "context-introspect", "version": "2.0.0"},
                },
            })

            # Step 2: read initialize response (we don't need to validate the version)
            _recv_result(proc, 1, timeout)

            # Step 3: send notifications/initialized  (notification — no id)
            _send(proc, {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            })

            # Step 4: send tools/list
            _send(proc, {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            })

            # Step 5: read tools/list response
            resp = _recv_result(proc, 2, timeout)
            if "error" in resp:
                raise RuntimeError(f"tools/list error: {resp['error']}")

            tools = (resp.get("result") or {}).get("tools") or []
            token_count = estimate_tokens(json.dumps(tools))
            measured.append({
                "name": name,
                "tokens": token_count,
                "tool_count": len(tools),
            })

        except Exception as exc:  # noqa: BLE001
            skipped.append({"name": name, "reason": str(exc)})
        finally:
            if proc is not None:
                _terminate_proc(proc)

    total = sum(m["tokens"] for m in measured)
    return {
        "measured": measured,
        "skipped": skipped,
        "total_measured_tokens": total,
        "measured_count": len(measured),
    }


# ---------------------------------------------------------------------------
# Task 6: keys_for_tool / parse_usage
# ---------------------------------------------------------------------------

def keys_for_tool(name: str, tool_input: dict) -> list[tuple]:
    if name.startswith("mcp__"):
        parts = name.split("__")
        if len(parts) >= 2 and parts[1]:
            return [("mcp", parts[1])]
        return []
    if name == "Skill":
        s = tool_input.get("skill") or tool_input.get("command")
        if s:
            s = s.split(":")[-1]   # "superpowers:brainstorming" -> "brainstorming"
            return [("skill", s)]
        return []
    if name in ("Task", "Agent"):
        st = tool_input.get("subagent_type")
        return [("subagent", st)] if st else []
    return []


def _iter_tool_uses(obj: dict):
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            yield block.get("name", ""), (block.get("input") or {})


def parse_usage(projects_dir: Path, now: datetime) -> tuple[dict, datetime | None, int]:
    cutoff = now - timedelta(days=WINDOW_DAYS)
    usage: dict = defaultdict(lambda: {"all": 0, "30d": 0, "last": None, "projects": set()})
    earliest: datetime | None = None
    parse_warnings: int = 0
    if not projects_dir.is_dir():
        return usage, earliest, parse_warnings

    for jsonl in projects_dir.glob("*/*.jsonl"):
        project = jsonl.parent.name
        try:
            handle = jsonl.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    parse_warnings += 1
                    continue
                ts = parse_ts(obj.get("timestamp"))
                if ts and (earliest is None or ts < earliest):
                    earliest = ts
                for tool_name, tool_input in _iter_tool_uses(obj):
                    for key in keys_for_tool(tool_name, tool_input):
                        rec = usage[key]
                        rec["all"] += 1
                        if ts and ts >= cutoff:
                            rec["30d"] += 1
                        if ts and (rec["last"] is None or ts > rec["last"]):
                            rec["last"] = ts
                        rec["projects"].add(project)
    return usage, earliest, parse_warnings


# ---------------------------------------------------------------------------
# Task 7: usage_key_for_item / merge_usage / build_output
# ---------------------------------------------------------------------------

def usage_key_for_item(item: Item) -> tuple | None:
    if item["type"] in ("skill", "subagent", "command", "mcp"):
        return (item["type"], item["name"])
    return None  # memory is always-loaded, not "invoked"


def merge_usage(items: list[Item], usage: dict) -> None:
    for item in items:
        key = usage_key_for_item(item)
        rec = usage.get(key) if key else None
        if rec:
            item["invocations_all"] = rec["all"]
            item["invocations_30d"] = rec["30d"]
            item["last_used"] = rec["last"].isoformat() if rec["last"] else None
            item["projects_used"] = sorted(rec["projects"])
        else:
            item["invocations_all"] = 0
            item["invocations_30d"] = 0
            item["last_used"] = None
            item["projects_used"] = []


def build_output(items: list[Item], earliest, now) -> dict:
    def persistent(i):
        return i["persistent_tokens_est"] or 0

    context_tax = sum(persistent(i) for i in items)
    reclaimable = sum(
        persistent(i) for i in items
        if verdict_for_item(i) == "cut"
        and i["persistent_tokens_est"] is not None
    )
    unused_mcp = sum(
        1 for i in items
        if i["type"] == "mcp" and verdict_for_item(i) == "cut"
    )
    horizon = (now - earliest).days if earliest else 0

    ordered = sorted(items, key=lambda i: (-persistent(i), i["invocations_30d"]))
    return {
        "totals": {
            "context_tax_est": context_tax,
            "reclaimable_est": reclaimable,
            "unused_mcp_count": unused_mcp,
            "history_horizon_days": horizon,
            "note": "Token figures are estimates (chars/4). MCP per-server cost is not "
                    "measured in v1; unused MCP servers are identified by usage only.",
        },
        "items": ordered,
    }


# ---------------------------------------------------------------------------
# Change 1: verdict_for_item
# ---------------------------------------------------------------------------

def verdict_for_item(item: Item) -> str:
    """Return "cut" | "keep" | "review" for a fully-merged item.

    Rules (in priority order):
    - type == "memory"               → "review"  (always-loaded; judged by size)
    - invocations_30d > 0            → "keep"    (used → keep, regardless of scope)
    - type == "command" or
      scope == "plugin"              → "review"  (unused, but not individually disable-able)
    - otherwise                      → "cut"     (unused AND disable-able)
    """
    t = item["type"]
    if t == "memory":
        return "review"
    if item.get("invocations_30d", 0) > 0:
        return "keep"
    if t == "command" or item.get("scope") == "plugin":
        return "review"
    return "cut"


# ---------------------------------------------------------------------------
# Change 2: build_summary
# ---------------------------------------------------------------------------

_SUMMARY_CUT_CAP = 40
_SUMMARY_REVIEW_CAP = 30


def build_summary(items: list[Item], earliest, now) -> dict:
    """Return a compact, decision-ready digest.

    Shape::

        {
            "totals": { ... },           # same as build_output produces
            "cut": [ {type,name,scope,tokens,calls_all,last_used}, ... ],
            "cut_truncated": 0,
            "review": [ {type,name,tokens}, ... ],
            "review_truncated": 0,
            "kept": {"count": int, "tokens": int},
        }

    ``cut`` is sorted by tokens desc (null last), capped at 40 items.
    ``review`` is sorted by tokens desc, capped at 30 items.
    ``kept`` is aggregated (count + summed tokens) — not listed.
    """
    # Reuse build_output for totals (it also sorts items, which we ignore here)
    full = build_output(items, earliest, now)
    totals = full["totals"]

    cut_raw: list[dict] = []
    review_raw: list[dict] = []
    kept_count = 0
    kept_tokens = 0

    for item in items:
        v = verdict_for_item(item)
        tokens = item.get("persistent_tokens_est")
        if v == "cut":
            cut_raw.append({
                "type": item["type"],
                "name": item["name"],
                "scope": item.get("scope"),
                "tokens": tokens,
                "calls_all": item.get("invocations_all", 0),
                "last_used": item.get("last_used"),
            })
        elif v == "review":
            review_raw.append({
                "type": item["type"],
                "name": item["name"],
                "tokens": tokens,
            })
        else:  # "keep"
            kept_count += 1
            kept_tokens += tokens if tokens is not None else 0

    # Sort cut by tokens desc (None last)
    cut_sorted = sorted(cut_raw, key=lambda x: (x["tokens"] is None, -(x["tokens"] or 0)))
    cut_truncated = max(0, len(cut_sorted) - _SUMMARY_CUT_CAP)
    cut_list = cut_sorted[:_SUMMARY_CUT_CAP]

    # Sort review by tokens desc (None last), cap at _SUMMARY_REVIEW_CAP
    review_sorted = sorted(review_raw, key=lambda x: (x["tokens"] is None, -(x["tokens"] or 0)))
    review_truncated = max(0, len(review_sorted) - _SUMMARY_REVIEW_CAP)
    review_list = review_sorted[:_SUMMARY_REVIEW_CAP]

    return {
        "totals": totals,
        "cut": cut_list,
        "cut_truncated": cut_truncated,
        "review": review_list,
        "review_truncated": review_truncated,
        "kept": {"count": kept_count, "tokens": kept_tokens},
    }


# ---------------------------------------------------------------------------
# Task 8: run_audit / main (CLI)
# ---------------------------------------------------------------------------

def _dedup_items(items: list[Item]) -> list[Item]:
    """Collapse duplicates sharing the same (type, name), keeping one representative.

    The kept copy is the one with the largest ``persistent_tokens_est``; ``None``
    is treated as -1 so that any real number wins over a missing value.
    Multiple physical copies of the same plugin skill are the primary source of
    duplicates — Claude Code loads only one, so summing them would over-count cost.
    """
    best: dict[tuple, Item] = {}
    for item in items:
        key = (item["type"], item["name"])
        pt = item.get("persistent_tokens_est")
        score = pt if pt is not None else -1
        if key not in best:
            best[key] = item
        else:
            current_pt = best[key].get("persistent_tokens_est")
            current_score = current_pt if current_pt is not None else -1
            if score > current_score:
                best[key] = item
    return list(best.values())


def _enumerate_and_merge(home: Path, project: Path, now: datetime) -> tuple[list[Item], "datetime | None", int]:
    """Collect, dedup, parse usage, merge, and return (items, earliest, parse_warnings).

    Shared by run_audit and the summary path so collection is not duplicated.
    """
    items: list[Item] = []
    items += collect_skills(home, project)
    items += collect_plugin_skills(home)
    items += collect_plugin_agents(home)
    items += collect_plugin_commands(home)
    items += collect_md_items(home / ".claude" / "agents", "subagent", "user")
    items += collect_md_items(project / ".claude" / "agents", "subagent", "project")
    items += collect_md_items(home / ".claude" / "commands", "command", "user")
    items += collect_md_items(project / ".claude" / "commands", "command", "project")
    items += collect_memory(home, project)
    items += collect_mcp_servers(home, project)
    items = _dedup_items(items)
    usage, earliest, parse_warnings = parse_usage(home / ".claude" / "projects", now)
    merge_usage(items, usage)
    return items, earliest, parse_warnings


def run_audit(home: Path, project: Path, now: datetime) -> dict:
    items, earliest, parse_warnings = _enumerate_and_merge(home, project, now)
    out = build_output(items, earliest, now)
    out["totals"]["parse_warnings"] = parse_warnings
    return out


def run_summary(home: Path, project: Path, now: datetime) -> dict:
    """Return the compact digest produced by build_summary."""
    items, earliest, parse_warnings = _enumerate_and_merge(home, project, now)
    out = build_summary(items, earliest, now)
    out["totals"]["parse_warnings"] = parse_warnings
    return out


# ---------------------------------------------------------------------------
# Task 9: disable_item / undo_item
# ---------------------------------------------------------------------------

def _file_source(item_type: str, name: str, home: Path, project: Path) -> Path | None:
    def _skill_tokens(path: Path) -> int:
        """Return persistent_tokens_est for a skill/agent/command path."""
        if item_type == "skill":
            md = path / "SKILL.md"
        else:
            md = path  # for subagent/command the path IS the .md file
        if not md.exists():
            return -1
        description, _body = read_frontmatter(md)
        return estimate_tokens(description)

    if item_type == "skill":
        candidates = []
        for root in (home, project):
            d = root / ".claude" / "skills" / name
            if d.exists():
                candidates.append(d)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # Both scopes have the skill — return the one with the larger persistent_tokens_est
        return max(candidates, key=_skill_tokens)
    elif item_type in ("subagent", "command"):
        sub = "agents" if item_type == "subagent" else "commands"
        candidates = []
        for root in (home, project):
            f = root / ".claude" / sub / f"{name}.md"
            if f.exists():
                candidates.append(f)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        return max(candidates, key=_skill_tokens)
    return None


def _store(disabled_root: Path, item_type: str, name: str) -> Path:
    return disabled_root / item_type / name


def _find_mcp_server(name: str, home: Path, project: Path) -> tuple[Path, str | None, dict] | None:
    """Find an MCP server entry and return (file_path, project_key, full_data).

    Searches in order:
      1. ~/.claude.json top-level mcpServers
      2. ~/.claude.json projects.<key>.mcpServers for each key
      3. project/.mcp.json mcpServers

    Returns None if not found.
    """
    cj = home / ".claude.json"
    if cj.exists():
        data = _load_json(cj)
        if name in (data.get("mcpServers") or {}):
            return (cj, None, data)
        for proj_key, proj_cfg in (data.get("projects") or {}).items():
            if name in (proj_cfg.get("mcpServers") or {}):
                return (cj, proj_key, data)

    mcp_json = project / ".mcp.json"
    if mcp_json.exists():
        data = _load_json(mcp_json)
        if name in (data.get("mcpServers") or {}):
            return (mcp_json, None, data)

    return None


def disable_item(item_type: str, name: str, home: Path, project: Path, disabled_root: Path) -> dict:
    if item_type == "mcp":
        found = _find_mcp_server(name, home, project)
        if found is None:
            return {"ok": False, "error": f"MCP server '{name}' not found"}
        file_path, project_key, data = found

        backup = disabled_root / f"{file_path.name}.{int(datetime.now().timestamp())}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, backup)

        # Remove from the correct location
        if project_key is not None:
            servers = data["projects"][project_key]["mcpServers"]
        else:
            servers = data["mcpServers"]
        removed = servers.pop(name)

        store = _store(disabled_root, "mcp", name)
        store.mkdir(parents=True, exist_ok=True)
        (store / "server.json").write_text(json.dumps({
            "name": name,
            "config": removed,
            "location": {
                "file": str(file_path),
                "project_key": project_key,
            },
        }, indent=2))
        file_path.write_text(json.dumps(data, indent=2))
        return {"ok": True, "disabled": name,
                "undo": f"python3 {Path(__file__)} undo mcp {name}", "backup": str(backup)}

    src = _file_source(item_type, name, home, project)
    if not src:
        if item_type == "skill":
            plugin_skills = collect_plugin_skills(home)
            if any(ps["name"] == name for ps in plugin_skills):
                return {"ok": False, "error": f"'{name}' is a plugin-provided skill; disable it by removing its plugin, not via this tool"}
        return {"ok": False, "error": f"{item_type} '{name}' not found"}
    dest = _store(disabled_root, item_type, name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return {"ok": False, "error": f"{item_type} '{name}' is already disabled"}
    shutil.move(str(src), str(dest))
    (dest.parent / f"{name}.origin").write_text(str(src))
    return {"ok": True, "disabled": name, "undo": f"python3 {Path(__file__)} undo {item_type} {name}"}


def undo_item(item_type: str, name: str, home: Path, project: Path, disabled_root: Path) -> dict:
    if item_type == "mcp":
        store = _store(disabled_root, "mcp", name) / "server.json"
        if not store.exists():
            return {"ok": False, "error": f"no disabled MCP server '{name}'"}
        saved = json.loads(store.read_text())
        location = saved.get("location", {})
        file_path = Path(location["file"]) if location.get("file") else home / ".claude.json"
        project_key = location.get("project_key")

        if not file_path.exists():
            return {"ok": False, "error": f"original config file no longer exists: {file_path}"}

        data = _load_json(file_path)
        if project_key is not None:
            data.setdefault("projects", {}).setdefault(project_key, {}).setdefault(
                "mcpServers", {}
            )[name] = saved["config"]
        else:
            data.setdefault("mcpServers", {})[name] = saved["config"]
        file_path.write_text(json.dumps(data, indent=2))
        shutil.rmtree(_store(disabled_root, "mcp", name))
        return {"ok": True, "restored": name}

    dest = _store(disabled_root, item_type, name)
    origin_file = dest.parent / f"{name}.origin"
    if not dest.exists() or not origin_file.exists():
        return {"ok": False, "error": f"no disabled {item_type} '{name}'"}
    original = Path(origin_file.read_text().strip())
    original.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dest), str(original))
    origin_file.unlink()
    return {"ok": True, "restored": name}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit Claude Code context cost vs. usage.")
    sub = parser.add_subparsers(dest="cmd")
    audit_p = sub.add_parser("audit", help="(default) print a compact JSON summary")
    audit_p.add_argument("--full", action="store_true", help="Print full item list instead of digest")
    for cmd in ("disable", "undo"):
        p = sub.add_parser(cmd)
        p.add_argument("item_type", choices=["skill", "subagent", "command", "mcp"])
        p.add_argument("name")
    sub.add_parser("measure-mcp", help="Launch each stdio MCP server and measure real tool token cost")
    # Allow --full at the top level too (when no subcommand is given)
    parser.add_argument("--full", action="store_true", help="Print full item list instead of digest")
    args = parser.parse_args(argv)

    home = Path.home()
    project = Path.cwd()
    disabled_root = home / ".claude" / DISABLED_DIRNAME

    if args.cmd == "disable":
        print(json.dumps(disable_item(args.item_type, args.name, home, project, disabled_root), indent=2))
    elif args.cmd == "undo":
        print(json.dumps(undo_item(args.item_type, args.name, home, project, disabled_root), indent=2))
    elif args.cmd == "measure-mcp":
        configs = _mcp_server_configs(home, project)
        result = measure_mcp_servers(configs)
        print(json.dumps(result, separators=(",", ":")))
    else:
        now = datetime.now(timezone.utc)
        full_flag = getattr(args, "full", False)
        if full_flag:
            print(json.dumps(run_audit(home, project, now), indent=2, default=str))
        else:
            print(json.dumps(run_summary(home, project, now), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
