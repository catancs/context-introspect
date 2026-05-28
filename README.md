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

> Your setup costs ~8,900 tokens/turn (estimated) before you type a word — and you actively use about a tenth of it. The rest splits three ways: unused skills and subagents you can disable on the spot (~600 tokens), plugins you'd reclaim by uninstalling, and MCP servers you've never once called (whose per-turn schema cost you can now measure exactly — and it's usually the single biggest win).

Then a ranked table — what to keep, what to cut, and why — and an offer to disable the *disable-able* dead weight for you. Reversibly. (Plugin-managed items it flags but won't touch — it tells you to remove the plugin instead.)

## What "auditing your own context" actually means in practice

A handful of design choices, each of which I'd make the other way for a human-facing tool:

**Real usage, not guesses.** It parses your actual session history (`~/.claude/projects/*/*.jsonl`) and counts how often each skill, subagent, and MCP server was *really* invoked, and when you last touched it. Usage is tallied across *all* your projects — so it never tells you to cut something you lean on in another repo.

**Honest about what it estimates.** Token figures for skills, agents, and commands are estimates, labelled as such. The default audit won't *guess* an MCP server's cost — it flags unused servers by usage instead. When you want the real number, `measure-mcp` briefly launches each stdio server and counts its actual tool schemas.

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

Want the exact per-turn cost of your MCP servers — usually the heaviest things in your context? Run the opt-in measure pass (Claude can do this for you, or run it directly). It briefly launches each stdio server, does the MCP handshake, and counts its real tool schemas:

```shell
python3 scripts/analyze.py measure-mcp
```

## What's still WIP

- **Slash-command usage** isn't tracked yet, so commands are judged by cost only, never flagged unused.
- **Pagination** — `measure-mcp` reads the first page of a server's `tools/list`; a server exposing a very large tool list may be slightly under-counted.

## Why I'm sharing it

I built it because my own setup had quietly turned into a pile of skills and servers I mostly don't use, and nothing would tell me that. If you live in Claude Code, you probably have the same blind spot. It's open source, it's reversible, and it takes one command to try.

It's also the inward-facing cousin of the other agent-first tools I build — the ones that give the agent better hands. This one gives it a sense of its own weight.

— Catalin ([@catancs](https://github.com/catancs))

MIT licensed — see [LICENSE](LICENSE). Design notes in [`docs/DESIGN.md`](docs/DESIGN.md).
