# grumpy

**English** · [繁體中文](README.zh-TW.md)

A small knowledge base for the things a project never writes down.

Two files, plain Python, nothing to install. Notes are ordinary text files you
can open in any editor.

## Why

When an AI assistant works on a large codebase, it works the same things out
over and over: which part owns what, why a build fails on a fresh copy, what
was decided about it and why. None of that survives into the next conversation.

The usual fix is one growing notes file. That works until it gets long enough
that reading it costs more than the answer was worth.

grumpy keeps itself small:

- **One note answers one question.** Notes are limited to about half a page, so
  a search result is the answer rather than something to read through.
- **Bigger things are kept apart.** A map of how the pieces fit together lives
  in `docs/` and is read one section at a time. A search shows you its contents
  page, not the whole thing.
- **Anything marked resolved stops showing up**, and it tells you how many it
  hid.
- **Notes point at each other in both directions**, so finding one leads you to
  the rest of the story.

It is called grumpy because it turns writes down. If a note repeats one you
already have, runs too long, or uses a label invented on the spot, it stops and
shows you what it found. Add `-f` if you meant it.

## Start

```bash
git clone <this repo> grumpy
./grumpy/grumpy.py init ~/my-notes --name my-notes --title "My notes"
```

That creates an empty knowledge base. Two things are worth editing before you
start writing: the list of areas in `tags.md`, and the description at the top
of `SKILL.md`, which is what decides whether an AI tool picks it up.

## Use

```bash
./grumpy.py search "why does the build fail"    # find something
./grumpy.py issues                              # known problems, worst first
./grumpy.py read n-0004 --context               # one note, plus what it links to
./grumpy.py add --title "..." --kind known-issue --tags major
./grumpy.py discuss n-0004 -m "we should fix this properly"
```

`read --context` is the useful one for a problem. The note tells you what is
broken; the context tells you what was decided about it, who is on it, and
which set of steps works around it.

Every note is one of six kinds: `architecture` (how something is built),
`known-issue` (something broken), `decision` (a choice and why), `runbook`
(steps that work), `reference` (a map or table), `task` (outstanding work).

To choose between the first three, ask whether someone else doing the same work
would have written the same note. Always the same means the system is simply
like that, so it is architecture. Possibly different means someone made a call,
so it is a decision, and it has to say what the other options were.

## For developers

Search is SQLite full-text search, built into Python, rebuilt automatically
when a file changes. There is no server, no separate database and no
embeddings. Queries the tokeniser cannot split, Chinese among them, fall back
to a substring match.

`grumpy.py` and `test_grumpy.py` contain nothing about any particular project,
so one copy can serve several knowledge bases. Everything project-specific sits
in `grumpy.conf`, `tags.md`, `notes/` and `docs/` beside them.

```bash
python3 -m unittest test_grumpy -v      # 107 tests
```

Unit tests cover parsing, the tag tree, the link graph and the duplicate check.
End-to-end tests run the command itself. A last group checks the content next
to it: every label resolves, every link points at a real note, ids are unique.
That group skips when there is no content, so a fresh clone is green.

## Writing notes worth keeping

The problem is never too few notes. It is notes nobody trusts.

Write down what you actually checked, and say whether you read it or ran it.
Those are different levels of confidence. Skip anything the version history
would have told you anyway.
