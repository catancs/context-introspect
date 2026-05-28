#!/usr/bin/env python3
"""context-introspect: audit Claude Code config cost vs. real usage."""
from __future__ import annotations

import argparse
import json
import shutil
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
        return [("skill", s)] if s else []
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
        if i["type"] != "memory"
        and i["persistent_tokens_est"] is not None
        and i["invocations_30d"] == 0
    )
    unused_mcp = sum(1 for i in items if i["type"] == "mcp" and i["invocations_30d"] == 0)
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
# Task 8: run_audit / main (CLI)
# ---------------------------------------------------------------------------

def run_audit(home: Path, project: Path, now: datetime) -> dict:
    items: list[Item] = []
    items += collect_skills(home, project)
    items += collect_plugin_skills(home)
    items += collect_md_items(home / ".claude" / "agents", "subagent", "user")
    items += collect_md_items(project / ".claude" / "agents", "subagent", "project")
    items += collect_md_items(home / ".claude" / "commands", "command", "user")
    items += collect_md_items(project / ".claude" / "commands", "command", "project")
    items += collect_memory(home, project)
    items += collect_mcp_servers(home, project)
    usage, earliest, parse_warnings = parse_usage(home / ".claude" / "projects", now)
    merge_usage(items, usage)
    out = build_output(items, earliest, now)
    out["totals"]["parse_warnings"] = parse_warnings
    return out


# ---------------------------------------------------------------------------
# Task 9: disable_item / undo_item
# ---------------------------------------------------------------------------

def _file_source(item_type: str, name: str, home: Path, project: Path) -> Path | None:
    if item_type == "skill":
        for root in (home, project):
            d = root / ".claude" / "skills" / name
            if d.exists():
                return d
    elif item_type in ("subagent", "command"):
        sub = "agents" if item_type == "subagent" else "commands"
        for root in (home, project):
            f = root / ".claude" / sub / f"{name}.md"
            if f.exists():
                return f
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
                "undo": f"python3 analyze.py undo mcp {name}", "backup": str(backup)}

    src = _file_source(item_type, name, home, project)
    if not src:
        return {"ok": False, "error": f"{item_type} '{name}' not found"}
    dest = _store(disabled_root, item_type, name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    (dest.parent / f"{name}.origin").write_text(str(src))
    return {"ok": True, "disabled": name, "undo": f"python3 analyze.py undo {item_type} {name}"}


def undo_item(item_type: str, name: str, home: Path, project: Path, disabled_root: Path) -> dict:
    if item_type == "mcp":
        store = _store(disabled_root, "mcp", name) / "server.json"
        if not store.exists():
            return {"ok": False, "error": f"no disabled MCP server '{name}'"}
        saved = json.loads(store.read_text())
        location = saved.get("location", {})
        file_path = Path(location["file"]) if location.get("file") else home / ".claude.json"
        project_key = location.get("project_key")

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
    sub.add_parser("audit", help="(default) print a JSON audit")
    for cmd in ("disable", "undo"):
        p = sub.add_parser(cmd)
        p.add_argument("item_type", choices=["skill", "subagent", "command", "mcp"])
        p.add_argument("name")
    args = parser.parse_args(argv)

    home = Path.home()
    project = Path.cwd()
    disabled_root = home / ".claude" / DISABLED_DIRNAME

    if args.cmd == "disable":
        print(json.dumps(disable_item(args.item_type, args.name, home, project, disabled_root), indent=2))
    elif args.cmd == "undo":
        print(json.dumps(undo_item(args.item_type, args.name, home, project, disabled_root), indent=2))
    else:
        now = datetime.now(timezone.utc)
        print(json.dumps(run_audit(home, project, now), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
