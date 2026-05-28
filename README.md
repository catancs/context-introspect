# context-introspect

I have a lot of MCP servers and skills installed in Claude Code. You probably do too. Here's the part nobody mentions: every one of them taxes your context window on *every single turn* — and I had no idea which ones I actually use.

`/context` shows you a total. `/usage` shows you the current session. Neither answers the question that matters: *which of this stuff have I not touched in weeks, and what is it costing me?*

So I built context-introspect. It's a Claude Code skill that turns Claude's attention inward — it audits your *own* setup, tells you what's dead weight, and helps you cut it without the risk of breaking anything.

## The gap

There's already a tool, `unclog`, that does part of this. But it's a separate CLI you drop down to, and it *deletes* — with no undo. I wanted something different on both counts:

- It should run *inside* Claude Code, as a skill, so the agent can actually **reason** about the results — "you've got two GitHub MCP servers and only ever call one" — instead of just printing a table.
- It should never be able to hurt me. No deletes. Everything reversible.

## What it does

You ask Claude **"audit my context."** It runs a small analyzer and comes back with something like:

> Your setup costs ~4,800 tokens/turn (estimated). ~3,200 of that is from skills you haven't called in 30 days, plus 3 MCP servers that were never invoked.

Then a ranked table — what to keep, what to cut, and why — and an offer to disable the dead weight for you. Reversibly.

## What "auditing your own context" actually means in practice

A handful of design choices, each of which I'd make the other way for a human-facing tool:

**Real usage, not guesses.** It parses your actual session history (`~/.claude/projects/*/*.jsonl`) and counts how often each skill, subagent, and MCP server was *really* invoked, and when you last touched it. Usage is tallied across *all* your projects — so it never tells you to cut something you lean on in another repo.

**Honest about what it can't measure.** Token figures are estimates, and they're labelled as estimates. Per-MCP-server cost isn't measured yet — so it flags unused servers by usage and *says so*, instead of inventing a number.

**It can't hurt you.** "Disable" moves an item aside and prints the exact undo command. It never deletes. For a tool whose whole job is telling you what to remove, that felt non-negotiable.

**Auditing context doesn't bloat your context.** The heavy lifting — parsing megabytes of transcripts — happens in a small Python script that returns a compact summary. Claude reasons over the summary, not the raw logs. The script crunches; the agent advises.

## Install

It ships as a Claude Code plugin. Add the marketplace, install the plugin:

```shell
/plugin marketplace add catancs/context-introspect
/plugin install context-introspect@catancs
```

Then just ask Claude: **"audit my context."**

The analyzer is pure Python 3 standard library — nothing to `pip install`. You do need `python3` on your PATH.

## What's still WIP

- **Per-MCP-server token cost.** Right now MCP servers are flagged by usage, not priced — measuring their real schema cost means launching each server and counting its tool definitions. That's the next thing I want to build; it's the number that actually hurts.
- **Slash-command usage** isn't tracked yet, so commands are judged by cost only, never flagged unused.

## Why I'm sharing it

I built it because my own setup had quietly turned into a pile of skills and servers I mostly don't use, and nothing would tell me that. If you live in Claude Code, you probably have the same blind spot. It's open source, it's reversible, and it takes one command to try.

It's also the inward-facing cousin of the other agent-first tools I build — the ones that give the agent better hands. This one gives it a sense of its own weight.

— Catalin ([@catancs](https://github.com/catancs))

MIT licensed — see [LICENSE](LICENSE). Design notes in [`docs/DESIGN.md`](docs/DESIGN.md).
