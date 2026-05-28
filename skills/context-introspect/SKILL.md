---
name: context-introspect
description: Audit this Claude Code setup for context-window bloat. Use when the user asks to "audit my context", "what's eating my context window", "which MCP servers or skills are unused", "trim my setup", or "context introspect". Reports token cost vs. real usage and can disable unused items reversibly.
---

# context-introspect

Audit the user's own Claude Code config — which MCP servers, skills, subagents, commands, and memory files cost context tokens, and which are actually unused — then recommend and (only on confirmation) reversibly disable the freeloaders.

## Procedure

1. **Run the analyzer. Do NOT read transcripts or config yourself, and do NOT pass `--full`** — the default output is a small, pre-digested summary on purpose (reading raw data would bloat the very context you're auditing). The script crunches and judges; you reason and present.
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/analyze.py"
   ```
   `${CLAUDE_PLUGIN_ROOT}` is set when this runs from an installed plugin. If it's unset (e.g. running from the repo, un-installed), the script is at the plugin/repo root's `scripts/analyze.py` — i.e. two directories up from this SKILL.md.

   It prints a compact digest:
   ```json
   {
     "totals": { "context_tax_est", "reclaimable_est", "unused_mcp_count", "history_horizon_days", "parse_warnings", "note" },
     "cut":    [ { "type", "name", "scope", "tokens", "calls_all", "last_used" } ],
     "cut_truncated": 0,
     "review": [ { "type", "name", "tokens" } ],
     "kept":   { "count", "tokens" }
   }
   ```

2. **Reason over the digest:**
   - `cut` — items the analyzer judged unused (skills/subagents with 0 calls in 30 days, and MCP servers never invoked). These are the disable candidates. For `type: "mcp"`, `tokens` is `null` — flag it as unused but say its token cost isn't measured in v1.
   - `review` — memory files (CLAUDE.md/MEMORY.md), slash commands, and plugin-provided skills. Judge these by size only; NEVER auto-cut them.
   - `kept` — a count + token sum of actively-used items. Summarize it; don't list them.
   - `cut_truncated > 0` → say there are that many more low-value items beyond the top ones shown.
   - Spot **redundancy** among the `cut`/`review` names (e.g. two GitHub MCP servers where only one is ever called).

3. **Present the report:**
   - **Hero line first:** "Your setup costs ~{context_tax_est} tokens/turn (estimated). ~{reclaimable_est} is from {len(cut minus mcp)} items unused in 30 days, plus {unused_mcp_count} unused MCP servers."
   - **Cut table:** Item | Type | Est. tokens | Last used | Why — one row per `cut` entry. This is the recommendation set.
   - **Kept:** one line — "Keeping {kept.count} actively-used items (~{kept.tokens} tokens)."
   - **Review:** brief mention of memory/command/plugin items by cost.
   - State the horizon: "usage is based on the last {history_horizon_days} days of transcripts." If small, say usage data is thin rather than over-claiming "unused."

4. **Offer reversible cleanup — NEVER act without explicit confirmation:**
   - List the `cut` items and ask: "Want me to disable these? They're moved aside, not deleted — I'll print the undo for each."
   - On confirmation, per item: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/analyze.py" disable <type> <name>`, then show the user the `undo` command from its output.

## Rules

- NEVER delete. Disabling is reversible; always surface the printed undo command.
- Only `cut` items are disable candidates. NEVER offer to disable anything in `review` (memory, commands, plugin skills) or this skill itself.
- Always label token figures as estimates; never present an estimate as measured.
- If transcript history is thin, say usage data is limited rather than over-claiming "unused."
