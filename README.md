# context-introspect

> Claude Code audits its own context. It tells you which MCP servers and skills are silently taxing every turn, which you haven't touched in weeks, and helps you disable them — safely and reversibly.

**Status:** 🚧 Work in progress (v1 in development).

Karpathy's `CLAUDE.md` fixed how your agent *writes code*. `context-introspect` fixes the bloat your agent *drags into every turn* — the same "Simplicity First" philosophy, applied to your configuration instead of your code.

## What it does (v1)

- **Measures your "context tax"** — an estimate of the tokens your installed MCP servers, skills, subagents, and commands cost on every turn.
- **Finds the freeloaders** — cross-references *real* usage (from your session history) against cost, so you see what you haven't called in 30 days.
- **Reasons, not just lists** — because it runs *inside* Claude Code as a Skill, it can spot redundant servers and explain why a cut is safe.
- **Safe & reversible** — never deletes; disables with a printed undo.

## How it works

A tiny `analyze.py` (pure Python stdlib, zero install) parses your config and session transcripts and emits a compact summary; the `SKILL.md` tells Claude to run it and reason over the result. **Script crunches, agent advises** — so auditing your context doesn't itself bloat your context.

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full design.

## Install

_Coming soon._

## License

MIT — see [LICENSE](LICENSE).
