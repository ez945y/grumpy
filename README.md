# kb

A file-backed knowledge base with a searchable link graph, for the things a
codebase does not tell you about itself.

## The problem

Large systems live in many repositories. An agent can read all of them — and
that is exactly the problem, because it reads them again every session. The
same afternoon of digging repeats: which repo owns this, why does that build
fail from a clean checkout, what did we decide about it and why. Nothing
learned survives the context it was learned in.

The usual answers do not hold. A memory file grows until loading it costs more
than the answer is worth. A README describes what the project intended, not
what it actually does on your machine. Both force the same bad trade: load
everything, or find nothing.

What is wanted is closer to recall than to reading — the flash of remembering
the one thing that matters right now, and nothing else. Our cells are replaced
continuously and we never notice it happening; what persists is not the
material but the pattern. An agent's context turns over the same way. This is
an attempt at the pattern that outlives it, so one agent can hand off to the
next without either of them re-deriving what was already known.

## What follows from that

Every decision here comes from wanting recall to be cheap:

- **Atomic notes, capped at 2000 characters.** A search hit should *be* the
  answer, not a document to skim. A longer note carries two claims and the
  second becomes unfindable — nobody searches for something buried in
  paragraph six.
- **Longer things are a different kind.** A map or a table is `reference`,
  lives in `docs/`, is uncapped, and is read a section at a time. Search shows
  its summary and its section names and never its body, so an outline costs a
  few lines and you pull only the part you need.
- **Links are symmetric.** A note cites what it depends on; whoever lands on
  the cited note needs to find the citer. One hit leads to the rest of the
  topic instead of dead-ending.
- **Near-duplicates are refused.** Two copies of one finding drift, and then
  nobody can tell which is current. `add` measures overlap and stops with the
  candidates listed so you can decide.
- **Tags are a tree, referenced by slug.** Re-parenting is a one-line edit that
  touches no note. The tree stays small because `add` refuses slugs that are
  not in it.
- **Settled entries disappear.** A fixed defect is history, and history crowds
  out what still bites. Search says how many it hid.

No dependencies, no server, no embeddings, no vector store. Python 3.11+ and
the standard library. Two files.

## Start a knowledge base

```bash
git clone <this repo> kb-engine
./kb-engine/kb.py init ~/my-project-kb --name my-project-kb \
    --title "My project knowledge base"
```

That copies the engine, writes an instance config, a starter tag tree, and a
`SKILL.md` for agent runtimes that load skills. Then edit two things — the
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

`read --context` is the one to reach for on a defect: the note, plus everything
linked to it listed by kind. A defect alone leaves the obvious questions open —
was a decision forced by it, is anyone assigned, which runbook routes around it
— and opening five files to answer them is enough friction that nobody does.

Six kinds: `architecture`, `known-issue`, `decision`, `runbook`, `reference`,
`task`. The test for choosing one — *if a different person had done this same
work, would the note read the same?* Always the same means the system simply is
that way. Possibly different means someone chose, and the note must be able to
name the alternatives it rejected.

Notes are markdown with a small frontmatter block. Search is SQLite FTS5,
rebuilt automatically when a file changes. Everything stays readable with `cat`
and editable in any editor — the CLI is a convenience, never a requirement.

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

`kb.py` and `test_kb.py` carry no project information; that is what lets one
engine serve any number of bases. Everything else in an instance is content.

## Writing notes worth keeping

The failure mode is not too few notes. It is notes nobody can trust. Two rules
carry most of the weight:

**Cite what you checked** — `file.go:123`, or the command and its output. A
claim nobody can re-verify gets trusted blindly, and is eventually wrong.

**Separate observed from inferred.** "I read this in the source" and "I ran this
and saw that" are different confidence levels; say which one it is. A confident
note that turns out to be false is worse than no note, because the next reader
will not re-check it.

Skip anything a `git log` or a glance at the file would have told you. The value
is entirely in what cost time to work out.

## Tests

```bash
python3 -m unittest test_kb -v
```

Unit tests cover frontmatter parsing, the tag tree, the link graph and its
traversal, section splitting, near-duplicate detection and index freshness.
End-to-end tests drive the CLI as a subprocess. A final group validates whatever
content sits beside it — every tag resolves, every wiki link points at something
real, ids are unique and match their filenames, no entry document cites a note
that was deleted. Those skip cleanly on a bare engine or a freshly scaffolded
base, so both are green out of the box.

Run it after editing notes by hand.
