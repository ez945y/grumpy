# grumpy

**English** · [繁體中文](README.zh-TW.md)

A small knowledge base for what falls between repositories.

Two files, plain Python, nothing to install. Notes are ordinary text files you
can open in any editor.

## Why

Anything that spans several repositories has nowhere to live. No single README
owns it, so it gets worked out again by whoever asks next.

One growing notes file is the usual answer. It fails the same way every time:
eventually reading it costs more than the answer.

grumpy stays small:

- One note, one question, about half a page.
- Maps and tables live apart in `docs/`, read a section at a time.
- Resolved things stop showing up.
- Notes point at each other both ways, so one leads to the rest.

It is called grumpy because it turns writes down that would undo this: a
duplicate, an over-long note, a label invented on the spot. It says what it
found. Add `-f` if you meant it.

## Start

```bash
git clone <this repo> grumpy
./grumpy/grumpy.py init ~/my-notes --name my-notes --install
```

`--install` links it into `~/.claude/skills/`, so Claude Code and anything else
that reads skills will find it. Use `--project` instead to link it beside the
code, in `./.claude/skills/`. **Restart your agent tool afterwards**, since the
skill list is read once at startup.

Already have a knowledge base and want to link it? `./grumpy.py install`.

Two things are worth editing before you start writing: the list of areas in
`tags.md`, and the description at the top of `SKILL.md`. That description is
the only thing deciding whether an agent reaches for this, so a vague one means
it never gets used.

## Use

```bash
./grumpy.py search "why does the build fail"    # find something
./grumpy.py issues                              # known problems, worst first
./grumpy.py read n-0004 --context               # one note, plus what it links to
./grumpy.py add --title "..." --kind known-issue --tags major
./grumpy.py discuss n-0004 -m "we should fix this properly"
./grumpy.py install                             # make it visible to agent tools
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
