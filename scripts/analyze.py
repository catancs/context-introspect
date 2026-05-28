#!/usr/bin/env python3
"""context-introspect: audit Claude Code config cost vs. real usage."""
from __future__ import annotations

import argparse
import json
import re
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
