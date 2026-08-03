# grumpy

**English** · [繁體中文](README.zh-TW.md)

A knowledge base that argues with you before it lets you write anything down.

Two files. No dependencies. No server, no embeddings, no vector database.
Python 3.11+ and the standard library.

## Why it says no

Most tools for agent memory optimise for capture: make it easy to save
something, worry about the mess later. That mess is the whole problem. After a
month you have four versions of one fact, none of them dated, and no way to
tell which is current. Loading it costs more than the answer is worth.

grumpy refuses instead. Every write has to get past three gates:

| Gate | Limit | Why |
|------|-------|-----|
| Unknown tag | must already be in the tag tree | a vocabulary that grows without limit stops being a vocabulary |
| Body length | 2000 characters | a longer note holds two claims, and the second one becomes unfindable |
| Overlap with an existing note | 60% | two copies of a fact drift apart, and then nobody knows which is right |

Each refusal shows you what it found and why, so you can extend the existing
note instead. `-f` overrides all three when you really do mean it.

That is the entire pitch. Everything else follows from wanting recall to stay
cheap.

## The problem it was built for

Large systems live in many repositories. An agent can read all of them, and
that is exactly the problem, because it reads them again every session. The
same afternoon of digging repeats: which repo owns this, why does that build
fail from a clean checkout, what did we decide about it and why. Nothing
learned survives the context it was learned in.

A growing memory file does not fix this, and neither does a README, because
both force the same trade: load everything, or find nothing.

What is wanted is closer to recall than to reading. The one thing that matters
right now, and nothing else. Our cells are replaced continuously and we never
notice it happening; what persists is not the material but the pattern. An
agent's context turns over the same way. This is an attempt at the pattern that
outlives it, so one agent can hand off to the next without either of them
re-deriving what was already known.

## Light on context by design

- **Atomic notes.** A search hit should *be* the answer, not a document to
  skim.
- **Big things are a separate kind.** A map or a table is `reference`, lives in
  `docs/`, and is read a section at a time. Search shows its summary and its
  section names, never its body, so an outline costs a few lines and you pull
  only the part you need.
- **Links are symmetric.** A note cites what it depends on; whoever lands on
  the cited note needs to find the citer. One hit leads to the rest of the
  topic instead of dead-ending.
- **Settled entries disappear.** A fixed defect is history, and history crowds
  out what still bites. Search tells you how many it hid.
- **Tags are a tree, referenced by slug.** Re-parenting one is a single line
  edit that touches no note.

## Start

```bash
git clone <this repo> grumpy
./grumpy/grumpy.py init ~/my-project-notes --name my-project-notes \
    --title "My project notes"
```

That copies the engine, writes an instance config with its own note id prefix,
a starter tag tree, and a `SKILL.md` for agent runtimes that load skills. Then
edit two things: the `area` branch of `tags.md`, and the `description` in
`SKILL.md`, which is what decides whether the skill ever fires.

## Use

```bash
./grumpy.py search <query> [--tag T] [--kind K] [--repo R] [--all] [--expand [HOPS]]
./grumpy.py issues [--severity S] [--repo R]      # open defects, worst first
./grumpy.py read <id> [--context] [--section NAME] [--full]
./grumpy.py add --title T --kind K [--tags a,b] [--repos x,y] [--stdin]
./grumpy.py tags [--add SLUG --parent P] [--move SLUG --parent P]
./grumpy.py discuss <id> -m "..."
./grumpy.py init <dir>
```

`read --context` is the one to reach for on a defect. It gives you the note
plus everything linked to it, listed by kind. A defect on its own leaves the
obvious questions open (was a decision forced by it, is anyone assigned, which
runbook routes around it) and opening five files to answer them is enough
friction that nobody does.

Six kinds: `architecture`, `known-issue`, `decision`, `runbook`, `reference`,
`task`. The test for choosing one: *if a different person had done this same
work, would the note read the same?* Always the same means the system simply is
that way. Possibly different means someone chose, and the note has to be able
to name the alternatives it rejected.

Search is bilingual. FTS5 handles English with stemming and ranking, and
because its tokeniser treats an unbroken run of CJK as a single token, a query
FTS cannot answer falls back to a substring scan.

Notes are markdown with a small frontmatter block. Everything stays readable
with `cat` and editable in any editor. The CLI is a convenience, never a
requirement.

## Layout

```
grumpy.py        the engine, which knows nothing about any particular project
test_grumpy.py   107 tests
grumpy.conf      instance config: note id prefix, name, title
tags.md          the tag tree, and the conventions it enforces
SKILL.md         entry point for agent runtimes
notes/           atomic notes
docs/            reference documents
```

The two engine files carry no project information, which is what lets one copy
serve any number of knowledge bases. Everything else in an instance is content.

## Notes worth keeping

The failure mode is not too few notes. It is notes nobody can trust.

**Cite what you checked**: `file.go:123`, or the command and its output. A claim
nobody can re-verify gets trusted blindly, and is eventually wrong.

**Separate observed from inferred.** "I read this in the source" and "I ran this
and saw that" are different confidence levels, so say which one it is. A
confident note that turns out to be false is worse than no note, because the
next reader will not re-check it.

Skip anything a `git log` or a glance at the file would have told you. The value
is entirely in what cost time to work out.

## Tests

```bash
python3 -m unittest test_grumpy -v
```

Unit tests cover frontmatter parsing, the tag tree, the link graph and its
traversal, section splitting, near-duplicate detection and index freshness.
End-to-end tests drive the CLI as a subprocess. A final group validates whatever
content sits beside it: every tag resolves, every wiki link points at something
real, ids are unique and match their filenames, no entry document cites a note
that was deleted. Those skip cleanly on a bare engine or a freshly scaffolded
base, so both are green out of the box.

Run it after editing notes by hand.
