<div align="center">

<h1>Grumpy</h1>

**Your AI coding agent starts every chat with amnesia. This is where it keeps notes.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python)](https://www.python.org/downloads/)
</br>
[繁體中文](README.zh-TW.md) · [Quick start](#quick-start) · [How you actually use it](#how-you-actually-use-it) · [Commands](#commands) · [Advanced](docs/advanced.md)

</br>
<img width="558" height="439" alt="image" src="https://github.com/user-attachments/assets/78f20453-c16d-4b68-b7d8-8c857591142b" />
</div>

## The problem

You spend an hour with your agent working out why the build breaks. Next week,
new chat, same question — and it works the whole thing out from scratch. Again.

The usual fix is a `notes.md` that grows until reading it costs more than the
answer, so nobody reads it — including the agent.

grumpy keeps the notes small enough to stay worth reading:

- One note answers one question, in about half a page.
- Search hands back the answer, not a wall of text.
- Notes link to each other, so finding one leads you to the rest.
- Solved things stop showing up.

<img src="docs/why.svg" alt="Without a knowledge base, every chat re-derives the same answer. With grumpy, the first chat writes it down and the rest look it up." width="600">

## Quick start

```bash
git clone https://github.com/ez945y/grumpy.git
./grumpy/grumpy.py init ~/my-notes --name my-notes --install
```

Restart Claude Code and you're done. `--install` registers the knowledge base as
a skill, so your agent finds it on its own — no setup in your project, nothing
to import.

## How you actually use it

Mostly you don't type grumpy commands. You talk to your agent:

> "Write that up in the knowledge base."

> "Check the knowledge base first — have we hit this before?"

> "What do we know about the deploy script?"

It runs the commands, writes the notes, and searches them next time. You just
say what's worth keeping.

## What a note looks like

A markdown file in a folder. That's the whole storage format.

```markdown
---
id: d-06w5t43c
title: Build fails on a clean clone until you run make env
kind: known-issue
status: open
tags: [known-issue, major]
links: [d-0f2xk91b]
---

`make build` dies on a missing .env. `make env` generates it from
1Password and has to run first. Not in the repo README.

## Discussion
2026-08-09: fixed on the CI image, still an issue locally.
```

Editable in any editor, greppable, diffable, merged by git like any other file.
No database, no server, nothing to keep running.

## Commands

You'll rarely need these, but they're there:

```bash
./grumpy.py search "why does the build fail"    # find something
./grumpy.py issues                              # open problems, worst first
./grumpy.py read n-0004 --context               # a note, plus what it links to
./grumpy.py add --title "..." --kind known-issue --tags major
./grumpy.py status n-0004 resolved              # close it
./grumpy.py discuss n-0004 -m "we should fix this properly"
```

`read --context` is the one worth knowing. The note tells you what's broken;
the context tells you what was decided about it and which workaround actually
works.

## Why "grumpy"

Because it argues with you. A write that would junk up the collection —
a duplicate, an over-long note, a tag invented on the spot — gets turned down,
and it tells you why:

```
overlap with existing notes is 100%; the limit is 60%. Closest:
  100%  d-06w5t43c  Build fails on a clean clone until you run make env
```

That's the whole idea. The failure mode isn't too few notes, it's notes nobody
trusts, so bad writes get stopped at the door instead of cleaned up later.

## For developers

Python 3.11+, standard library only. Search is SQLite full-text search over the
files, rebuilt automatically when one changes. No embeddings, no vector store.

```bash
python3 -m unittest test_grumpy -v      # 150 tests
```

`grumpy.py` knows nothing about any particular project, so one clone can serve
several knowledge bases.

## More

- [docs/advanced.md](docs/advanced.md) — where the engine and base live, `--root`, note kinds, writing notes worth keeping
- [CHANGELOG.md](CHANGELOG.md) — what changed
- MIT licensed. See [LICENSE](LICENSE).
