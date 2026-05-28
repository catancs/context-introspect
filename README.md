# context-introspect

> Claude Code audits its own context. It tells you which MCP servers and skills are silently taxing every turn, which you haven't touched in weeks, and helps you disable them — safely and reversibly.

Karpathy's `CLAUDE.md` fixed how your agent *writes code*. `context-introspect` fixes the bloat your agent *drags into every turn* — the same "Simplicity First" philosophy, applied to your configuration instead of your code.

## What it does

- **Measures your "context tax"** — an estimate of the tokens your installed skills, subagents, commands, and memory files cost on every turn.
- **Finds the freeloaders** — cross-references *real* usage parsed from your session history against cost, so you see what you actually haven't called in 30 days (across *all* your projects, so it never flags something you use elsewhere).
- **Reasons, not just lists** — because it runs *inside* Claude Code as a Skill, Claude can spot redundant servers and explain why a cut is safe.
- **Safe & reversible** — never deletes. It moves items aside and prints the exact undo. The cleanup tool that can't hurt you.

## Install

```bash
git clone https://github.com/catancs/context-introspect ~/.claude/skills/context-introspect
```

Then just ask Claude Code: **"audit my context"**.

No dependencies — the analyzer is pure Python 3 standard library (works on 3.9+).

## Sample output

Run on a real, heavily-used setup:

> **Your setup costs ~4,825 tokens/turn (estimated). ~3,172 of that is from skills you haven't called in the last 30 days, plus 3 MCP servers that were never invoked.** Usage is based on the last 38 days of transcripts.

| Item | Type | Est. tokens | Calls (30d / all) | Verdict |
|---|---|---:|---:|---|
| CLAUDE.md (user) | memory | 734 | — | ⚠️ REVIEW (always loaded) |
| docx | skill | 199 | 3 / 3 | ✅ KEEP |
| frontend-design | skill | 99 | 4 / 4 | ✅ KEEP |
| claude-api | skill | 186 | 0 / 0 | ✂️ CUT |
| pptx | skill | 173 | 0 / 0 | ✂️ CUT |
| hook-development | skill | 131 | 0 / 0 | ✂️ CUT |

Then, only if you confirm:

```
$ python3 .../analyze.py disable skill pptx
{ "ok": true, "disabled": "pptx", "undo": "python3 .../analyze.py undo skill pptx" }
```

## How it works

A tiny `scripts/analyze.py` (pure stdlib, zero install) enumerates your config and parses your session transcripts (`~/.claude/projects/*/*.jsonl`) into a compact JSON summary; `SKILL.md` tells Claude to run it and reason over the result. **Script crunches, agent advises** — so auditing your context doesn't itself bloat your context.

Honesty notes: token figures are estimates (`~chars/4`). Per-MCP-server token cost is **not** measured in v1 (unused MCP servers are identified by usage); measuring it exactly is the v2 headline.

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full design.

## License

MIT — see [LICENSE](LICENSE).
