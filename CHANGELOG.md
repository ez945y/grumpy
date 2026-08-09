# Changelog

What changed, and what it was like before. The second half is the point: a
release note that lists a flag teaches nothing, and the reason a thing was
added is the part that stops it being removed by someone who does not know.

Dates are the day the work landed on `main`. Versions start here; everything
before 0.3.0 is reconstructed from the history and grouped rather than
pretended to be a release.

## 0.3.0 — 2026-08-09

Three frictions, all found the same way: an agent wrote seven notes in one
session and went around the tool three times.

### `link` — add links to a note that already exists

    ./grumpy.py link n-0004 n-0009 n-0011

**Before:** `add --links` validates its targets against notes that already
exist. That is the right check, and it is the wrong moment for the one ordering
worth encouraging — write the overview listing the threads it has, then write
each thread. Ids are assigned at creation, so a map could only cite what
preceded it, and finishing one meant editing the file by hand. That leaves the
search index describing the old state, which is the exact failure the tool
exists to prevent.

Links stay one-way on disk and two-way on read, so this touches only the citing
note, and it reindexes.

### The body limit measures prose

**Before:** the 2000-character limit counted every character. It exists to stop
two findings sharing one note — but a markdown table is the opposite of two
findings. It is the densest, least redundant way to say one thing, and charging
for it by the character pushed authors to delete load-bearing sentences to fit
a table that was already as small as it goes. Three notes in one session were
refused at 1770–2119 characters, every one a single claim with a table under
it.

Fenced blocks and table rows are now stripped before measuring. Structure is
free; prose is not. The refusal says which figure it used, and `--json` reports
both `chars` and `chars_total`.

### The advisory `closest` stopped being the same note every time

**Before:** an open `task` is long and shares vocabulary with everything near
it, so one won the `closest` slot for five notes out of seven while meaning
nothing. Advice that never changes stops being read — including on the
occasion it would have mattered.

Tasks are skipped when reporting `closest`. The overlap **gate** still sees
them, so a genuine duplicate of a task is refused exactly as before.

### Documentation

The cheat sheet named neither `status` nor `link`, and the text said to close a
note "by editing its `status:`" — pointing at the file rather than at the
command that had existed since 0.2.0. Both READMEs now show them, and say why
hand-editing is the thing to avoid.

150 tests, up from 146.

## 0.2.0 — 2026-08-03 → 2026-08-06

The agent-facing surface.

- `add` gained `--links`, `--json` and the write-time gates: unknown tag,
  over-length body, overlap with an existing note, a `known-issue`/`task` with
  no severity, a `--links` target that does not exist. Each is clearable with
  `-f`, because a gate that cannot be overridden gets worked around instead.
- `status` sets a note's lifecycle without an editor; `discuss` appends a
  comment and reads a heredoc.
- Ids are minted so that two people adding at once cannot collide.
- The engine lives *beside* a knowledge base rather than inside it, so one copy
  serves several. `init` scaffolds a base and no longer vendors a copy of the
  engine into it.

## 0.1.0 — 2026-08-03

A file-backed knowledge base with a searchable link graph. Markdown files,
SQLite FTS5 rebuilt when a file changes, no server and no embeddings. Readable
with `cat`, editable with any text editor; the CLI is a convenience and never a
requirement.
