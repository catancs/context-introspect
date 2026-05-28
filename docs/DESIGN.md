# context-introspect — Design Spec

- **Date:** 2026-05-28
- **Status:** Approved design, pre-implementation
- **Form:** A Claude Code Skill, shipped as a public git repo (drop into `~/.claude/skills/context-introspect/`)
- **One-liner:** *Claude audits its own context — it tells you which MCP servers and skills are silently taxing every turn, which you haven't touched in weeks, and helps you disable them safely and reversibly.*

## 1. Why this exists

Every MCP server, skill, subagent, and slash command you install consumes context on every turn — MCP tool schemas alone can cost up to ~18k tokens per server. Most users accrete this config and never prune it. Claude Code's built-ins (`/context`, `/usage`, `/skills`) show *current/session* cost but never *historical usage*, so you can't tell what's actually earning its place.

**Positioning hook (for README):** Karpathy's viral `CLAUDE.md` fixed how your agent *writes code*. `context-introspect` fixes the bloat your agent *drags into every turn*. Same "Simplicity First" philosophy, applied to your configuration instead of your code.

## 2. Prior art & differentiation

- **`unclog`** (Python CLI, `uv tool install`): audits agents/skills/commands/MCP via transcript `tool_use` tallies over 30 days; deletes with **no undo**; **cannot** quantify per-MCP token cost.
- **Built-ins:** `/context` (totals, no per-item), `/usage` (session-level, paid plans), `/skills` (sort by tokens), `/mcp` (status + tool count). None do cumulative historical usage.

**Our edge (all real, all verified):**
1. **Form factor = the thesis.** It's a Skill — Claude audits *itself, in-conversation*, and can **reason** (redundancy, why-a-cut-is-safe) where a CLI can only list.
2. **Honesty.** Every number labeled `measured` vs `estimated`; *persistent* (paid every turn) separated from *on-demand* (paid only when invoked).
3. **Safety as identity.** Never deletes — disables reversibly with a printed undo. "The cleanup tool that can't hurt you."

## 3. Architecture: cruncher vs. brain

The irony to avoid: a pure-markdown skill would make Claude read giant transcripts into *its own* context to report on context bloat. So we split:

- **`scripts/analyze.py` (cruncher):** pure Python 3 stdlib, zero install. Does all heavy parsing, emits **compact JSON** (a few KB). Raw transcripts never enter Claude's context.
- **`SKILL.md` (brain):** instructs Claude to run the cruncher and **reason** over its small output — rank, detect redundancy, apply cross-project safety, present the report, offer reversible actions.

## 4. `analyze.py` — behavior

### 4a. Read-only audit (`--json`, default)

**Enumerate config (sources):**
- MCP servers: `~/.claude.json` (`mcpServers` + per-project `projects.<path>.mcpServers`) and project `.mcp.json`.
- Skills: `~/.claude/skills/*/SKILL.md`, project `.claude/skills/*/SKILL.md`, plugin skills under `~/.claude/plugins/`.
- Subagents: `~/.claude/agents/*.md` (+ project `.claude/agents/`).
- Slash commands: `~/.claude/commands/*.md` (+ project).
- `CLAUDE.md` / `MEMORY.md`: file sizes (user + project).

**Compute real usage** — walk **all** `~/.claude/projects/*/*.jsonl`:
- A usage event = a line with `type=="assistant"` whose `message.content[]` contains `{type:"tool_use", name, input}`; the line's top-level ISO `timestamp` is the time.
- Attribution: MCP tool name `mcp__<server>__<tool>` → server. `Skill` tool → invoked skill from `input.skill`. `Agent`/`Task` tool → subagent from `input.subagent_type`. Slash commands → best-effort from `<command-name>` tags in user lines (flagged as best-effort).
- Per item: `invocations_all`, `invocations_30d`, `last_used`, `projects_used` (set of project dirs).

**Estimate cost (honest):**
- Text token estimate via `chars/4` heuristic (stdlib has no tokenizer); label `estimated`.
- Separate `persistent_tokens_est` (always-loaded: skill description line, CLAUDE.md, MEMORY.md, subagent/command listing) from `ondemand_tokens_est` (skill body, loaded only on invoke).
- **MCP per-server exact schema cost is OUT for v1** (requires launching servers → v2). v1 reports tool **count** + usage as a proxy and says so explicitly.

**Output (compact JSON):**
```json
{
  "totals": { "context_tax_est": 0, "reclaimable_est": 0, "history_horizon_days": 0 },
  "items": [
    { "type": "mcp|skill|subagent|command|memory",
      "name": "", "scope": "user|project|plugin",
      "persistent_tokens_est": 0, "ondemand_tokens_est": 0, "cost_basis": "measured|estimated",
      "invocations_all": 0, "invocations_30d": 0,
      "last_used": "ISO8601|null", "projects_used": ["..."],
      "source_path": "" }
  ]
}
```

**Definitions:** `context_tax_est` = sum of `persistent_tokens_est` (cost paid every turn before you type). `reclaimable_est` = persistent cost of items verdicted CUT. Exact transcript field names (e.g. the `Skill` tool's skill-name key) are confirmed against real transcripts during implementation, not assumed.

### 4b. Reversible disable (`--disable <type> <name>`)

- **File-based** (skill/subagent/command): move into `~/.claude/.context-introspect-disabled/<type>/<name>/`, preserving a `.origin` note with the original absolute path.
- **Config-based** (MCP server): timestamped backup of the JSON first, then lift the entry out into `~/.claude/.context-introspect-disabled/mcp-servers.json` (recording its original location/scope).
- **Always:** back up before edits, never delete, print the exact undo command. An `--undo <type> <name>` restores from the disabled store.

## 5. The report (what the user sees)

Written in the terse, imperative, bold-header voice that made Karpathy's file spread.

- **Hero line (first, quotable):** *"Your setup costs ~58k tokens/turn. ~31k is from 6 items you haven't touched in 30 days."*
- **Tiered cut list (table):** `Item | Type | Est. tokens | Calls (30d / all) | Last used | Verdict ✂️CUT / ⚠️REVIEW / ✅KEEP | Reason`.
- **Redundancy notes (agent reasoning):** e.g. *"`git-mcp` and `github-mcp` overlap — only one is ever called."*
- **Suggested actions:** reversible disables, offered only; executed on explicit confirmation.

## 6. Data flow

`"audit my context"` → Claude invokes skill → runs `analyze.py --json` → compact JSON → ranks (cost×usage) + reasons (redundancy, cross-project safety) → prints report → offers to disable SAFE items → on explicit confirm runs `analyze.py --disable …` → prints undo.

## 7. Safety rules

- Never delete; disable is always reversible; back up config before any edit.
- **Cross-project safety:** never recommend cutting an item with usage in *any* project.
- Never flag core tools or the skill itself.
- State the **history horizon**: "never used" is bounded by how far transcripts go back.
- Label estimates honestly; never present an estimate as measured.

## 8. Edge cases

- Fresh install / thin history → cost-only report + honest "not enough usage data."
- `python3` missing → SKILL.md notes the prerequisite.
- Item configured but absent from all transcripts → "never used (or used before logging began)."
- Malformed/oversized JSONL lines → skipped, counted in a `parse_warnings` field.

## 9. Scope

- **v1 (this spec):** report + reversible disable; cost via labeled estimates/proxies.
- **v2 (later):** launch MCP servers to measure exact per-server schema token cost (the headline upgrade).
- **YAGNI (out):** auto-delete, GUI, daemon, telemetry, any network calls.

## 10. Repo structure

```
context-introspect/
├── SKILL.md            # the brain (terse, imperative)
├── scripts/
│   └── analyze.py      # the cruncher (pure stdlib)
├── docs/
│   └── DESIGN.md       # this file
├── README.md           # the flex: hero number + before→after + Karpathy hook
└── LICENSE             # MIT
```

## 11. Testing

- Run `analyze.py` on the author's own machine (this is also the demo).
- Fixture-based: a synthetic `projects/` transcript + synthetic config → assert tallies, last_used, totals.
- Disable dry-run on a throwaway item → assert it moves and `--undo` restores it byte-for-byte.

## 12. Distribution

Public repo under `catancs`. Submit to awesome-claude-code lists, lobehub, and the plugin marketplace. README leads with the context-tax screenshot and the Karpathy companion framing.
