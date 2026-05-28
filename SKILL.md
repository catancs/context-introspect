---
name: context-introspect
description: Audit this Claude Code setup for context-window bloat. Use when the user asks to "audit my context", "what's eating my context window", "which MCP servers or skills are unused", "trim my setup", or "context introspect". Reports token cost vs. real usage and can disable unused items reversibly.
---

# context-introspect

Audit the user's own Claude Code config — which MCP servers, skills, subagents, commands, and memory files cost context tokens, and which are actually unused — then recommend and (only on confirmation) reversibly disable the freeloaders.

## Procedure

1. **Run the analyzer. Do NOT read transcripts or config yourself** — that would bloat this very context. The script crunches; you advise.
   ```bash
   python3 ~/.claude/skills/context-introspect/scripts/analyze.py
   ```
   (Adjust the path if the skill is installed elsewhere.) It prints compact JSON: `{ "totals": {...}, "items": [...] }`.

2. **Reason over the JSON:**
   - `invocations_30d == 0` → a CUT candidate. Usage is tallied across ALL projects, so zero means unused everywhere — this is already cross-project-safe, so never worry you're flagging something used in another repo.
   - `type: "memory"` (CLAUDE.md, MEMORY.md) is always-loaded context, judged by size only — never call it "unused."
   - `scope: "plugin"` skills are managed by their plugin: surface their cost but do NOT offer to disable them (the user removes the plugin instead).
   - `persistent_tokens_est` is paid every turn; prioritise high-persistent + zero-usage items.
   - `cost_basis: "unknown-v1"` (all MCP servers): the per-server token cost is NOT measured in v1 — say so plainly. Flag unused MCP servers by usage, and note MCP schemas can cost thousands of tokens per turn.
   - `type: "command"` (slash commands): usage is NOT tracked in v1, so commands always show 0 calls. Judge them by cost only, like memory — NEVER flag a command as a CUT candidate based on its (always-zero) usage.
   - Spot **redundancy**: items whose names/descriptions overlap (e.g. two GitHub MCP servers, where only one is ever called).

3. **Present the report:**
   - **Hero line first:** "Your setup costs ~{context_tax_est} tokens/turn (estimated). ~{reclaimable_est} is from {N} items unused in 30 days, plus {unused_mcp_count} unused MCP servers."
   - **Table:** Item | Type | Est. tokens | Calls (30d / all) | Last used | Verdict (✂️ CUT / ⚠️ REVIEW / ✅ KEEP) | Reason.
   - **Redundancy notes**, if any.
   - State the horizon: "usage is based on the last {history_horizon_days} days of transcripts." If `history_horizon_days` is small, say usage data is thin rather than over-claiming "unused."

4. **Offer reversible cleanup — NEVER act without explicit confirmation:**
   - List the ✂️ CUT items and ask: "Want me to disable these? They're moved aside, not deleted — I'll print the undo for each."
   - On confirmation, per item: `python3 ~/.claude/skills/context-introspect/scripts/analyze.py disable <type> <name>`, then show the user the `undo` command from its output.

## Rules

- NEVER delete. Disabling is reversible; always surface the printed undo command.
- NEVER recommend cutting an item used in any project, any `memory` item, any `command` item (command usage isn't tracked in v1), any `plugin`-scoped skill, or this skill itself.
- Always label token figures as estimates; never present an estimate as measured.
- If transcript history is thin, say usage data is limited rather than over-claiming "unused."
