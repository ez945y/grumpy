# kb

A file-backed knowledge base with a searchable link graph, for the things a
codebase does not tell you about itself.

Written for agents and people to share. An agent that greps fourteen repos to
rediscover a fact someone already wrote down has wasted the context that was
the point of writing it down. This gives it somewhere to look first, and enough
structure that what it writes back stays findable.

No dependencies, no server, no embeddings, no vector store. Python 3.11+ and
the standard library. Two files.

## What it is

Notes are markdown with a small frontmatter block. Search is SQLite FTS5,
rebuilt automatically when a file changes. Everything stays readable with
`cat` and editable in any editor — the CLI is a convenience, never a
requirement.

The opinions it holds, and why:

- **One claim per note, capped at 2000 characters.** A longer note carries two
  claims and the second becomes unfindable; nobody searches for something
  buried in paragraph six.
- **Longer things are a different kind.** A map or a table is `reference`,
  lives in `docs/`, is uncapped, and is read a section at a time so a reader
  spends context only on the part they need.
- **Adding a near-duplicate is refused.** Two copies of one finding drift, and
  then nobody can tell which is current. `add` measures overlap against what
  exists and stops with the candidates listed.
- **Tags are a tree, referenced by slug.** Re-parenting a tag is a one-line
  edit and touches no note. The tree stays small because `add` refuses slugs
  that are not in it.
- **Links are symmetric.** A note cites what it depends on; whoever lands on
  the cited note needs to find the citer.
- **Settled entries disappear.** A fixed defect is history, and history crowds
  out what still bites.

## Start a knowledge base

```bash
git clone <this repo> kb-engine
./kb-engine/kb.py init ~/my-project-kb --name my-project-kb \
    --title "My project knowledge base"
```

That copies the engine, writes an instance config, a starter tag tree, and a
`SKILL.md` for agent runtimes that load skills. Then edit two files — the
`area` branch of `tags.md`, and the `description` in `SKILL.md`, which decides
whether the skill ever fires — and start writing.

## Use it

```bash
./kb.py search <query> [--tag T] [--kind K] [--repo R] [--all] [--expand [HOPS]]
./kb.py issues [--severity S] [--repo R]        # open defects, worst first
./kb.py read <id> [--context] [--section NAME] [--full]
./kb.py add --title T --kind K [--tags a,b] [--repos x,y] [--stdin]
./kb.py tags [--add SLUG --parent P] [--move SLUG --parent P]
./kb.py discuss <id> -m "..."
./kb.py init <dir>
```

Six kinds: `architecture`, `known-issue`, `decision`, `runbook`, `reference`,
`task`. The test for picking one — *if a different person had done this same
work, would the note read the same?* Always the same means the system simply is
that way. Possibly different means someone chose, and the note must be able to
name the alternatives it rejected.

Search is bilingual: FTS5 handles English with stemming and ranking, and
because its tokeniser treats an unbroken run of CJK as a single token, a query
FTS cannot answer falls back to a substring scan.

## Layout

```
kb.py        the engine — knows nothing about any particular project
test_kb.py   107 tests
kb.conf      instance config: id prefix, name, title
tags.md      the tag tree, and the conventions it enforces
SKILL.md     entry point for agent runtimes
notes/       atomic notes
docs/        reference documents
```

`kb.py` and `test_kb.py` carry no project information; that is what makes one
engine serve any number of bases. Everything else in an instance is content.

## Tests

```bash
python3 -m unittest test_kb -v
```

Unit tests cover frontmatter parsing, the tag tree, the link graph and its
traversal, section splitting, near-duplicate detection and index freshness.
End-to-end tests drive the CLI as a subprocess. A final group validates
whatever content sits beside it — every tag resolves, every wiki link points at
something real, ids are unique and match their filenames, no entry document
cites a note that was deleted. Those skip cleanly on a freshly scaffolded base.

Run it after editing notes by hand.
