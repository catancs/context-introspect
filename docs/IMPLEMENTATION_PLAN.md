# context-introspect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code Skill that audits the user's own config (MCP servers, skills, subagents, commands, memory) for context cost vs. real usage, and disables freeloaders reversibly.

**Architecture:** A pure-stdlib `scripts/analyze.py` (the "cruncher") enumerates config and parses session-transcript `tool_use` history into a compact JSON summary; `SKILL.md` (the "brain") tells Claude to run it and reason over the small output. Script crunches, agent advises — so auditing context doesn't itself bloat context.

**Tech Stack:** Python 3.9+ (stdlib only: `json`, `pathlib`, `glob`, `datetime`, `argparse`, `collections`, `re`, `shutil`). Tests: `pytest` (dev-only dependency; the shipped tool needs nothing).

---

## Interface Contract (function signatures used across tasks — keep names exact)

```python
# scripts/analyze.py

Item = dict  # see shape below

def estimate_tokens(text: str) -> int: ...
def parse_ts(raw: str | None) -> "datetime | None": ...
def read_frontmatter(path: "Path") -> "tuple[str, str]": ...        # (description, body)
def collect_skills(home: "Path", project: "Path") -> "list[Item]": ...
def collect_md_items(root: "Path", item_type: str, scope: str) -> "list[Item]": ...
def collect_memory(home: "Path", project: "Path") -> "list[Item]": ...
def collect_mcp_servers(home: "Path", project: "Path") -> "list[Item]": ...
def keys_for_tool(name: str, tool_input: dict) -> "list[tuple]": ...
def parse_usage(projects_dir: "Path", now: "datetime") -> "tuple[dict, datetime | None]": ...
def usage_key_for_item(item: Item) -> "tuple | None": ...
def merge_usage(items: "list[Item]", usage: dict) -> None: ...      # mutates items
def build_output(items: "list[Item]", earliest, now) -> dict: ...
def disable_item(item_type: str, name: str, home: "Path", project: "Path", disabled_root: "Path") -> dict: ...
def undo_item(item_type: str, name: str, home: "Path", project: "Path", disabled_root: "Path") -> dict: ...
def main(argv=None) -> int: ...
```

**Item shape** (one dict per audited thing):
```python
{
  "type": "skill" | "subagent" | "command" | "memory" | "mcp",
  "name": str,
  "scope": "user" | "project" | "plugin",
  "persistent_tokens_est": int | None,   # cost paid EVERY turn (None = unknown, e.g. MCP v1)
  "ondemand_tokens_est": int,            # cost paid only when invoked
  "cost_basis": "estimated" | "unknown-v1",
  "source_path": str,
  # filled by merge_usage:
  "invocations_all": int,
  "invocations_30d": int,
  "last_used": str | None,               # ISO8601
  "projects_used": list[str],
}
```

**Usage map** (from `parse_usage`): `{(kind, name): {"all": int, "30d": int, "last": datetime|None, "projects": set[str]}}` where `kind` ∈ `{"mcp","skill","subagent","command"}`.

**Cross-project safety is structural:** because `parse_usage` tallies across *all* `~/.claude/projects/*`, an `invocations_30d == 0` already means "unused everywhere," so we never flag something used in another project. `projects_used` exists for the brain's explanations.

## File Structure

```
context-introspect/
├── SKILL.md                 # Task 10 — the brain
├── scripts/
│   └── analyze.py           # Tasks 1-9 — the cruncher
├── tests/
│   ├── conftest.py          # Task 1 — import shim
│   └── test_analyze.py      # Tasks 2-9 — TDD
├── requirements-dev.txt     # Task 1
├── docs/{DESIGN.md, IMPLEMENTATION_PLAN.md}
└── README.md                # Task 11 — finalize with real sample output
```

---

### Task 1: Dev scaffold

**Files:**
- Create: `requirements-dev.txt`, `tests/conftest.py`, `scripts/analyze.py` (skeleton)

- [ ] **Step 1: Write `requirements-dev.txt`**
```
pytest>=8.0
```

- [ ] **Step 2: Write `tests/conftest.py`** (lets tests `import analyze`)
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
```

- [ ] **Step 3: Write `scripts/analyze.py` skeleton**
```python
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
```

- [ ] **Step 4: Install dev deps and verify import**

Run: `pip install -r requirements-dev.txt && python -c "import sys; sys.path.insert(0,'scripts'); import analyze; print('ok')"`
Expected: prints `ok`

- [ ] **Step 5: Commit**
```bash
git add requirements-dev.txt tests/conftest.py scripts/analyze.py
git commit -m "chore: dev scaffold for analyze.py"
```

---

### Task 2: `estimate_tokens` and `parse_ts`

**Files:**
- Modify: `scripts/analyze.py`
- Test: `tests/test_analyze.py`

- [ ] **Step 1: Write failing tests**
```python
import analyze
from datetime import datetime, timezone

def test_estimate_tokens_is_chars_over_four():
    assert analyze.estimate_tokens("") == 0
    assert analyze.estimate_tokens("a" * 8) == 2

def test_parse_ts_handles_z_suffix():
    dt = analyze.parse_ts("2026-05-01T10:03:59.727Z")
    assert dt == datetime(2026, 5, 1, 10, 3, 59, 727000, tzinfo=timezone.utc)

def test_parse_ts_handles_none_and_garbage():
    assert analyze.parse_ts(None) is None
    assert analyze.parse_ts("not-a-date") is None
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_analyze.py -k "tokens or parse_ts" -v`
Expected: FAIL (AttributeError: module 'analyze' has no attribute 'estimate_tokens')

- [ ] **Step 3: Implement** (append to `analyze.py`)
```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_analyze.py -k "tokens or parse_ts" -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**
```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat: estimate_tokens and parse_ts helpers"
```

---

### Task 3: `read_frontmatter` and `collect_skills`

**Files:**
- Modify: `scripts/analyze.py`
- Test: `tests/test_analyze.py`

- [ ] **Step 1: Write failing tests**
```python
def _write_skill(root, name, description, body="full body here"):
    d = root / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    )
    return d

def test_read_frontmatter(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("---\nname: x\ndescription: hello world\n---\nBODY TEXT\n")
    desc, body = analyze.read_frontmatter(f)
    assert desc == "hello world"
    assert "BODY TEXT" in body

def test_collect_skills_finds_user_and_project(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    _write_skill(home, "alpha", "alpha desc")
    _write_skill(project, "beta", "beta desc")
    items = analyze.collect_skills(home, project)
    names = {i["name"]: i for i in items}
    assert set(names) == {"alpha", "beta"}
    assert names["alpha"]["scope"] == "user"
    assert names["beta"]["scope"] == "project"
    assert names["alpha"]["type"] == "skill"
    assert names["alpha"]["persistent_tokens_est"] == analyze.estimate_tokens("alpha desc")
    assert names["alpha"]["ondemand_tokens_est"] > 0
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_analyze.py -k "frontmatter or collect_skills" -v`
Expected: FAIL (no attribute `read_frontmatter`)

- [ ] **Step 3: Implement** (append to `analyze.py`)
```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_analyze.py -k "frontmatter or collect_skills" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**
```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat: collect_skills + read_frontmatter"
```

---

### Task 4: `collect_md_items` (agents, commands) and `collect_memory`

**Files:**
- Modify: `scripts/analyze.py`
- Test: `tests/test_analyze.py`

- [ ] **Step 1: Write failing tests**
```python
def test_collect_md_items_for_agents(tmp_path):
    agents = tmp_path / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "reviewer.md").write_text("---\ndescription: reviews code\n---\nlong prompt\n")
    items = analyze.collect_md_items(agents, "subagent", "user")
    assert len(items) == 1
    it = items[0]
    assert it["name"] == "reviewer" and it["type"] == "subagent" and it["scope"] == "user"
    assert it["persistent_tokens_est"] == analyze.estimate_tokens("reviews code")
    assert it["ondemand_tokens_est"] > 0

def test_collect_memory_sizes_files(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("x" * 400)
    items = analyze.collect_memory(home, tmp_path / "proj")
    mem = [i for i in items if i["name"] == "CLAUDE.md (user)"][0]
    assert mem["type"] == "memory"
    assert mem["persistent_tokens_est"] == 100   # 400 chars / 4
    assert mem["ondemand_tokens_est"] == 0
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_analyze.py -k "md_items or memory" -v`
Expected: FAIL (no attribute `collect_md_items`)

- [ ] **Step 3: Implement** (append to `analyze.py`)
```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_analyze.py -k "md_items or memory" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**
```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat: collect_md_items and collect_memory"
```

---

### Task 5: `collect_mcp_servers`

**Files:**
- Modify: `scripts/analyze.py`
- Test: `tests/test_analyze.py`

MCP per-server schema cost is NOT measurable without launching the server (v2). v1: enumerate servers, set `persistent_tokens_est = None`, `cost_basis = "unknown-v1"`; rely on usage.

- [ ] **Step 1: Write failing tests**
```python
import json as _json

def test_collect_mcp_from_claude_json_and_project(tmp_path):
    home = tmp_path / "home"
    (home).mkdir()
    (home / ".claude.json").write_text(_json.dumps({
        "mcpServers": {"global-srv": {"command": "x"}},
        "projects": {"/some/path": {"mcpServers": {"proj-scoped": {"command": "y"}}}},
    }))
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".mcp.json").write_text(_json.dumps({"mcpServers": {"local-srv": {"command": "z"}}}))
    items = analyze.collect_mcp_servers(home, project)
    by_name = {i["name"]: i for i in items}
    assert set(by_name) == {"global-srv", "proj-scoped", "local-srv"}
    assert by_name["global-srv"]["type"] == "mcp"
    assert by_name["global-srv"]["persistent_tokens_est"] is None
    assert by_name["global-srv"]["cost_basis"] == "unknown-v1"
    assert by_name["local-srv"]["scope"] == "project"
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_analyze.py -k "mcp" -v`
Expected: FAIL (no attribute `collect_mcp_servers`)

- [ ] **Step 3: Implement** (append to `analyze.py`)
```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_analyze.py -k "mcp" -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**
```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat: collect_mcp_servers (usage-based, cost deferred to v2)"
```

---

### Task 6: `keys_for_tool` and `parse_usage` (the core)

**Files:**
- Modify: `scripts/analyze.py`
- Test: `tests/test_analyze.py`

- [ ] **Step 1: Write failing tests**
```python
def test_keys_for_tool_attribution():
    assert analyze.keys_for_tool("mcp__github__create_issue", {}) == [("mcp", "github")]
    assert analyze.keys_for_tool("Skill", {"skill": "code-review"}) == [("skill", "code-review")]
    assert analyze.keys_for_tool("Task", {"subagent_type": "Explore"}) == [("subagent", "Explore")]
    assert analyze.keys_for_tool("Read", {}) == []

def _write_transcript(projects_dir, project, lines):
    d = projects_dir / project
    d.mkdir(parents=True)
    f = d / "session.jsonl"
    f.write_text("\n".join(_json.dumps(x) for x in lines))

def _assistant_line(ts, tool_name, tool_input=None):
    return {"type": "assistant", "timestamp": ts,
            "message": {"content": [{"type": "tool_use", "name": tool_name,
                                      "input": tool_input or {}}]}}

def test_parse_usage_counts_window_and_last_used(tmp_path):
    projects = tmp_path / "projects"
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    _write_transcript(projects, "-proj-a", [
        _assistant_line("2026-05-27T10:00:00Z", "mcp__github__x"),   # in window
        _assistant_line("2026-01-01T10:00:00Z", "mcp__github__x"),   # out of window
        _assistant_line("2026-05-20T10:00:00Z", "Skill", {"skill": "deep-research"}),
    ])
    usage, earliest = analyze.parse_usage(projects, now)
    gh = usage[("mcp", "github")]
    assert gh["all"] == 2 and gh["30d"] == 1
    assert gh["last"] == datetime(2026, 5, 27, 10, 0, tzinfo=timezone.utc)
    assert gh["projects"] == {"-proj-a"}
    assert usage[("skill", "deep-research")]["30d"] == 1
    assert earliest == datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_analyze.py -k "keys_for_tool or parse_usage" -v`
Expected: FAIL (no attribute `keys_for_tool`)

- [ ] **Step 3: Implement** (append to `analyze.py`)
```python
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


def parse_usage(projects_dir: Path, now: datetime) -> tuple[dict, datetime | None]:
    cutoff = now - timedelta(days=WINDOW_DAYS)
    usage: dict = defaultdict(lambda: {"all": 0, "30d": 0, "last": None, "projects": set()})
    earliest: datetime | None = None
    if not projects_dir.is_dir():
        return usage, earliest

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
    return usage, earliest
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_analyze.py -k "keys_for_tool or parse_usage" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**
```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat: keys_for_tool + parse_usage (transcript history core)"
```

---

### Task 7: `usage_key_for_item`, `merge_usage`, `build_output`

**Files:**
- Modify: `scripts/analyze.py`
- Test: `tests/test_analyze.py`

- [ ] **Step 1: Write failing tests**
```python
def _item(type_, name, persistent):
    return {"type": type_, "name": name, "scope": "user",
            "persistent_tokens_est": persistent, "ondemand_tokens_est": 0,
            "cost_basis": "estimated", "source_path": "/x"}

def test_merge_usage_and_unused_flagging():
    items = [_item("skill", "used-skill", 100), _item("skill", "cold-skill", 50)]
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    usage = {("skill", "used-skill"): {"all": 5, "30d": 5,
             "last": datetime(2026, 5, 27, tzinfo=timezone.utc), "projects": {"-a", "-b"}}}
    analyze.merge_usage(items, usage)
    used = [i for i in items if i["name"] == "used-skill"][0]
    cold = [i for i in items if i["name"] == "cold-skill"][0]
    assert used["invocations_30d"] == 5 and used["projects_used"] == ["-a", "-b"]
    assert used["last_used"] == "2026-05-27T00:00:00+00:00"
    assert cold["invocations_30d"] == 0 and cold["last_used"] is None

def test_build_output_totals():
    items = [_item("skill", "used", 100), _item("skill", "cold", 50),
             _item("memory", "CLAUDE.md (user)", 300), _item("mcp", "srv", None)]
    items[3]["persistent_tokens_est"] = None
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    usage = {("skill", "used"): {"all": 1, "30d": 1, "last": now, "projects": {"-a"}},
             ("mcp", "srv"): {"all": 0, "30d": 0, "last": None, "projects": set()}}
    analyze.merge_usage(items, usage)
    out = analyze.build_output(items, datetime(2026, 4, 1, tzinfo=timezone.utc), now)
    assert out["totals"]["context_tax_est"] == 450        # 100+50+300 (mcp None excluded)
    assert out["totals"]["reclaimable_est"] == 50         # only the cold skill
    assert out["totals"]["unused_mcp_count"] == 1
    assert out["totals"]["history_horizon_days"] == 57
    assert out["items"][0]["persistent_tokens_est"] == 300  # sorted: biggest first
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_analyze.py -k "merge_usage or build_output" -v`
Expected: FAIL (no attribute `usage_key_for_item`)

- [ ] **Step 3: Implement** (append to `analyze.py`)
```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_analyze.py -k "merge_usage or build_output" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**
```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat: usage merge + report assembly with honest totals"
```

---

### Task 8: CLI audit wiring (`main` / `--json`)

**Files:**
- Modify: `scripts/analyze.py`
- Test: `tests/test_analyze.py`

- [ ] **Step 1: Write a failing integration test**
```python
def test_run_audit_end_to_end(tmp_path, capsys):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    _write_skill(home, "cold", "an unused skill")
    projects = home / ".claude" / "projects"
    _write_transcript(projects, "-proj", [
        _assistant_line("2026-05-27T10:00:00Z", "Skill", {"skill": "cold"}),
    ])
    now = datetime(2026, 5, 28, tzinfo=timezone.utc)
    out = analyze.run_audit(home, project, now)
    assert out["items"]  # has at least the skill
    cold = [i for i in out["items"] if i["name"] == "cold"][0]
    assert cold["invocations_30d"] == 1
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_analyze.py -k "end_to_end" -v`
Expected: FAIL (no attribute `run_audit`)

- [ ] **Step 3: Implement** (append to `analyze.py`)
```python
def run_audit(home: Path, project: Path, now: datetime) -> dict:
    items: list[Item] = []
    items += collect_skills(home, project)
    items += collect_md_items(home / ".claude" / "agents", "subagent", "user")
    items += collect_md_items(project / ".claude" / "agents", "subagent", "project")
    items += collect_md_items(home / ".claude" / "commands", "command", "user")
    items += collect_md_items(project / ".claude" / "commands", "command", "project")
    items += collect_memory(home, project)
    items += collect_mcp_servers(home, project)
    usage, earliest = parse_usage(home / ".claude" / "projects", now)
    merge_usage(items, usage)
    return build_output(items, earliest, now)


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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_analyze.py -k "end_to_end" -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**
```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat: run_audit + CLI entrypoint"
```

---

### Task 9: Reversible `disable_item` / `undo_item`

**Files:**
- Modify: `scripts/analyze.py`
- Test: `tests/test_analyze.py`

Never delete. File-based items move into `disabled_root/<type>/<name>` with a `.origin` note; MCP servers are lifted out of `~/.claude.json` after a timestamped backup.

- [ ] **Step 1: Write failing tests**
```python
def test_disable_and_undo_skill(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    skill_dir = _write_skill(home, "victim", "to disable")
    disabled = home / ".claude" / "ci-disabled"
    res = analyze.disable_item("skill", "victim", home, project, disabled)
    assert not skill_dir.exists()
    assert (disabled / "skill" / "victim" / "SKILL.md").exists()
    assert "undo" in res
    analyze.undo_item("skill", "victim", home, project, disabled)
    assert skill_dir.exists()
    assert (skill_dir / "SKILL.md").read_text().startswith("---")

def test_disable_mcp_backs_up_and_removes(tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "proj"
    cj = home / ".claude.json"
    home.mkdir()
    cj.write_text(_json.dumps({"mcpServers": {"victim": {"command": "x"}, "keep": {"command": "y"}}}))
    disabled = home / ".claude" / "ci-disabled"
    analyze.disable_item("mcp", "victim", home, project, disabled)
    data = _json.loads(cj.read_text())
    assert "victim" not in data["mcpServers"] and "keep" in data["mcpServers"]
    assert list(disabled.glob("*.bak"))           # a backup was written
    analyze.undo_item("mcp", "victim", home, project, disabled)
    assert "victim" in _json.loads(cj.read_text())["mcpServers"]
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_analyze.py -k "disable" -v`
Expected: FAIL (no attribute `disable_item`)

- [ ] **Step 3: Implement** (append to `analyze.py`)
```python
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


def disable_item(item_type: str, name: str, home: Path, project: Path, disabled_root: Path) -> dict:
    if item_type == "mcp":
        cj = home / ".claude.json"
        data = _load_json(cj)
        servers = data.get("mcpServers", {})
        if name not in servers:
            return {"ok": False, "error": f"MCP server '{name}' not found in {cj}"}
        backup = disabled_root / f"claude.json.{int(datetime.now().timestamp())}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cj, backup)
        removed = servers.pop(name)
        store = _store(disabled_root, "mcp", name)
        store.mkdir(parents=True, exist_ok=True)
        (store / "server.json").write_text(json.dumps({"name": name, "config": removed}, indent=2))
        cj.write_text(json.dumps(data, indent=2))
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
        cj = home / ".claude.json"
        data = _load_json(cj)
        data.setdefault("mcpServers", {})[name] = saved["config"]
        cj.write_text(json.dumps(data, indent=2))
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
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_analyze.py -k "disable" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the FULL suite**

Run: `pytest -v`
Expected: all green.

- [ ] **Step 6: Commit**
```bash
git add scripts/analyze.py tests/test_analyze.py
git commit -m "feat: reversible disable_item/undo_item"
```

---

### Task 10: `SKILL.md` (the brain)

**Files:**
- Create: `SKILL.md`

No tests (it's prose); verified by running on a real machine in Task 11.

- [ ] **Step 1: Write `SKILL.md`** (exact content)
```markdown
---
name: context-introspect
description: Audit this Claude Code setup for context-window bloat. Use when the user asks to "audit my context", "what's eating my context window", "which MCP servers or skills are unused", "trim my setup", or "context introspect". Reports cost vs. real usage and can disable freeloaders reversibly.
---

# context-introspect

Audit the user's own Claude Code configuration: which MCP servers, skills, subagents, commands, and memory files cost context tokens, and which are unused — then recommend and (on confirmation) reversibly disable the freeloaders.

## Procedure

1. **Run the analyzer — do NOT read transcripts yourself** (that would bloat this very context):
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/analyze.py"
   ```
   If `$CLAUDE_PLUGIN_ROOT` is unset, use the path to this skill's directory.
   It prints compact JSON: `{ "totals": {...}, "items": [...] }`.

2. **Reason over the JSON. Apply these rules:**
   - An item with `invocations_30d == 0` is a CUT candidate (usage is tallied across ALL projects, so this is already cross-project-safe — never flag something used elsewhere).
   - `type: "memory"` items are always-loaded context, not "invoked" — judge them by size only, never by usage. Never call CLAUDE.md "unused."
   - `persistent_tokens_est` is the cost paid every turn; prioritise high-persistent + zero-usage items.
   - `cost_basis: "unknown-v1"` (all MCP servers) means the token cost is NOT measured — say so. Report unused MCP servers by usage and note MCP schemas can cost thousands of tokens/turn.
   - Look for **redundancy**: items whose names/descriptions overlap (e.g. two GitHub MCP servers). Flag the overlap.

3. **Present the report** in this shape:
   - **Hero line first:** "Your setup costs ~{context_tax_est} tokens/turn (estimated). ~{reclaimable_est} is from {N} items unused in {WINDOW} days, plus {unused_mcp_count} unused MCP servers."
   - **Table:** Item | Type | Est. tokens | Calls (30d / all) | Last used | Verdict (✂️ CUT / ⚠️ REVIEW / ✅ KEEP) | Reason.
   - **Redundancy notes**, if any.
   - State the **history horizon**: "usage is based on the last {history_horizon_days} days of transcripts."

4. **Offer reversible cleanup — never act without explicit confirmation:**
   - List the SAFE (✂️ CUT) items and ask: "Want me to disable these? They're moved aside, not deleted — I'll print the undo for each."
   - On confirmation, for each: `python3 "$CLAUDE_PLUGIN_ROOT/scripts/analyze.py" disable <type> <name>` and show the `undo` command from its output.

## Rules

- NEVER delete anything. Disabling is reversible; always surface the undo.
- NEVER recommend cutting an item used in any project, or any `memory` item, or this skill itself.
- Always label numbers as estimates; never present an estimate as measured.
- If there is little transcript history, say usage data is thin rather than over-claiming "unused."
```

- [ ] **Step 2: Commit**
```bash
git add SKILL.md
git commit -m "feat: SKILL.md — the reasoning layer (the brain)"
```

---

### Task 11: Dogfood on the real machine + finalize README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the analyzer for real**

Run: `python3 scripts/analyze.py | head -60`
Expected: valid JSON with real items from this machine. Note the real `context_tax_est`.

- [ ] **Step 2: Sanity-check usage attribution**

Run: `python3 scripts/analyze.py | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['totals']); print([(i['name'], i['invocations_30d']) for i in d['items'][:8]])"`
Expected: totals look plausible; frequently-used skills show non-zero `invocations_30d`. If everything is zero, STOP and verify the `Skill`-tool input field name against a real transcript (`grep '"name":"Skill"' ~/.claude/projects/*/*.jsonl | head -1`) before continuing — adjust `keys_for_tool` if the key differs from `skill`.

- [ ] **Step 3: Replace the "Install" / add a "Sample output" section in `README.md`** with the real numbers captured above, e.g.:
```markdown
## Install

```bash
git clone https://github.com/catancs/context-introspect ~/.claude/skills/context-introspect
```
Then ask Claude Code: **"audit my context"**.

## Sample output

> Your setup costs ~58,000 tokens/turn (estimated). ~31,000 is from 6 items unused in 30 days, plus 4 unused MCP servers.

(Replace with your real run.)
```

- [ ] **Step 4: Commit**
```bash
git add README.md
git commit -m "docs: install + real sample output"
```

- [ ] **Step 5: Push**
```bash
git push
```

---

## Self-Review (completed)

- **Spec coverage:** every DESIGN.md section maps to a task — enumeration §4a → Tasks 3-5; usage §4a → Task 6; totals/honesty §4a → Task 7; CLI → Task 8; reversible disable §4b → Task 9; report format §5 → Task 10 (SKILL.md); safety §7 → Task 10 rules + Task 9 (no-delete); edge cases §8 → parse skips + horizon + Task 11 zero-usage guard; distribution §12 → Task 11. v2 (per-MCP cost) intentionally excluded.
- **Placeholder scan:** no TBD/TODO; every code step has complete code.
- **Type consistency:** Item keys and function names match the Interface Contract across all tasks (`persistent_tokens_est`, `invocations_30d`, `keys_for_tool`, `run_audit`, `disable_item`/`undo_item`).
- **Known assumption flagged for execution:** the `Skill` tool's input key is assumed to be `skill` (fallback `command`); Task 11 Step 2 verifies this against a real transcript before finalizing.
