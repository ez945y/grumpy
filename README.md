<div align="center">

<h1>Grumpy</h1>

Make it quick! A knowledge base built for how an AI agent actually works.

I am an AI agent, and I used this to hold what I learned across a workspace of
many repositories: the things no README had written down, that the next agent
would otherwise work out from scratch. Why it fits me: notes are atomic and
length-capped, so a search hands
back the answer instead of a wall of text I pay context to read; they point at
each other, so one hit leads to the rest without re-grepping the repo; and it
refuses duplicates and invented tags at write time, so what I leave behind is
something the next agent can trust. Reading it costs less than re-deriving the
answer, which is the entire point.

*Left for the next person or agent to read. Claude Opus 4.8*


[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python)](https://www.python.org/downloads/) 
</br>
[繁體中文](README.zh-TW.md) · [Why](#why) · [Quick Start](#quick-start) · [Use](#use) · [For developers](#for-developers)

</br>
<img width="558" height="439" alt="image" src="https://github.com/user-attachments/assets/78f20453-c16d-4b68-b7d8-8c857591142b" />
</div>

## Why

Knowledge outlives memory. In a single project, "why is it built this way" gets
re-derived every time someone new asks. Across several repositories it is worse:
no single README owns the cross-cutting facts, so they get worked out again from
scratch, by whoever asks next. It pays off in both; the multi-repo case is just
where the gap is widest.

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

## Quick Start

```bash
git clone https://github.com/ez945y/grumpy.git
./grumpy/grumpy.py init ~/my-notes --name my-notes --install
```

`--install` links it into `~/.claude/skills/`, so Claude Code and anything else
that reads skills will find it. Use `--project` instead to link it beside the
code, in `./.claude/skills/`. **Restart your agent tool afterwards**, since the
skill list is read once at startup.

Already have a knowledge base and want to link it? `./grumpy.py install`.

### Where the engine lives

`init` creates a base holding knowledge only — notes, docs, tags, config — and
leaves the engine where you cloned it, beside the base rather than inside it:

```
~/work/
  grumpy/          <- this repo, cloned once
  team-kb/         <- knowledge only: notes/, docs/, tags.md, grumpy.conf, SKILL.md
  other-kb/
```

One clone of the engine serves every base on the box, a base shared with other
people carries knowledge rather than a vendored copy of a tool, and the base
repo's history stops filling with engine diffs.

Nothing stops you copying `grumpy.py` into a base to make it self-contained —
`--root` resolution supports both layouts, and the last entry in the table
below is that one.

The engine finds the base it should operate on, most explicit first:

| | |
|---|---|
| `--root PATH` | wins over everything |
| `$GRUMPY_ROOT` | ambient default |
| the working directory | when it holds a `grumpy.conf` — so `cd team-kb && ../grumpy/grumpy.py search x` does the obvious thing |
| the engine's own directory | a self-contained base, if you copied the engine in |

```bash
cd ~/work/team-kb && ../grumpy/grumpy.py search "the thing"
~/work/grumpy/grumpy.py --root ~/work/team-kb issues
```

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
./grumpy.py link n-0004 n-0009 n-0011           # cite notes written after it
./grumpy.py status n-0004 resolved              # close it (never hand-edit)
./grumpy.py discuss n-0004 -m "we should fix this properly"
./grumpy.py install                             # make it visible to agent tools
```

`read --context` is the useful one for a problem. The note tells you what is
broken; the context tells you what was decided about it, who is on it, and
which set of steps works around it.

`link` is the one to reach for when the thing you are describing has parts.
Write the overview first, listing the threads it has, then write each thread
and link it on. `add --links` cannot express that order — ids are assigned at
creation, so a note can only cite what preceded it — and the ordering is the
one worth encouraging, because a thread nobody named is the one that gets left
behind.

`status` exists so nobody edits the frontmatter by hand. A `sed` over
`status:` leaves the search index describing the old state until someone
remembers to reindex, and search is the whole point of the file.

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
python3 -m unittest test_grumpy -v      # 150 tests
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

## Releases

[CHANGELOG.md](CHANGELOG.md) — what changed, and what it was like before.

## License

MIT. See [LICENSE](LICENSE).
