#!/usr/bin/env python3
"""grumpy - a small, file-backed knowledge base that argues with you.

Stdlib only. No server, no embeddings, no vector store. Notes are markdown
files; search is SQLite FTS5 over them, rebuilt automatically when a file
changes. The whole thing is meant to stay readable with `cat` and editable
with any text editor; the CLI is a convenience, never a requirement.

  grumpy search <query> [--tag T] [--repo R] [--kind K]
  grumpy add --title T --kind K [--tags a,b] [--repos x,y]
  grumpy tags [--tree | --add SLUG --parent P | --move SLUG --parent P]
  grumpy discuss <id> -m "..."
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import secrets
import sqlite3
import sys
import textwrap
from pathlib import Path

def _resolve_root() -> Path:
    """Where the knowledge base lives — which is not necessarily where this file does.

    The engine is project-agnostic, so an instance can keep it as a sibling
    rather than a copy inside itself. That is the arrangement worth having: one
    engine, cloned once, serving every base on the box, and a base repo that
    holds nothing but knowledge. `init` still copies the engine in for anyone
    who prefers a self-contained base, and that keeps working — hence the last
    fallback.

    Resolution order, most explicit first:

    1. `--root PATH` on the command line.
    2. `$GRUMPY_ROOT`.
    3. The working directory, when it holds a `grumpy.conf`. This is what makes
       `cd my-kb && ../grumpy/grumpy.py search x` do the obvious thing.
    4. This file's own directory — the self-contained layout.

    Parsed off `sys.argv` by hand because ROOT is needed at import time, before
    argparse runs: NOTES, DOCS and the rest are derived from it below. `--root`
    is also registered on the parser so it documents itself and does not read as
    an unknown flag.
    """
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--root" and i + 1 < len(argv):
            return Path(argv[i + 1]).expanduser().resolve()
        if a.startswith("--root="):
            return Path(a.split("=", 1)[1]).expanduser().resolve()
    env = os.environ.get("GRUMPY_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd()
    if (cwd / "grumpy.conf").exists():
        return cwd
    return Path(__file__).resolve().parent


ROOT = _resolve_root()
NOTES = ROOT / "notes"
DOCS = ROOT / "docs"
TAGS_FILE = ROOT / "tags.md"
CONF_FILE = ROOT / "grumpy.conf"
INDEX = ROOT / ".index.db"

LIST_KEYS = {"tags", "repos", "links"}

# A note carries one issue. Past roughly this length it is carrying two, and
# the second one becomes unfindable, because nobody searches for a claim buried in
# paragraph six. The existing notes sit around 1200 characters; this ceiling
# leaves room to edit without inviting an essay. Splitting and linking is
# almost always the better move, which is why `add` refuses rather than warns.
MAX_BODY = 2000

# Overlap above this against an existing note means `add` stops and shows you
# what it found, rather than quietly creating a second copy that will drift.
DUP_THRESHOLD = 0.6

# Lifecycle, carried in `status:`. Anything in CLOSED is settled and drops out
# of search by default. A fixed bug is history, and history crowds out the
# things that still bite. `workaround-applied` and `reported-upstream` stay
# visible on purpose: both mean the defect is still there, and the first one
# also explains why some local override file exists.
OPEN_STATUS = ("open", "workaround-applied", "reported-upstream")
CLOSED_STATUS = ("resolved", "fixed", "done", "wontfix", "duplicate", "obsolete")


# ---------------------------------------------------------------------------
# Instance configuration
#
# This file is the engine. Everything that names a particular project lives in
# grumpy.conf and in the content beside it, so the same grumpy.py serves any
# knowledge bases. `grumpy init` scaffolds a new one.
# ---------------------------------------------------------------------------

DEFAULT_CONF = {"prefix": "n", "name": "knowledge-base", "title": "Knowledge base"}


def conf() -> dict:
    out = dict(DEFAULT_CONF)
    if CONF_FILE.exists():
        for line in CONF_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    return out


def prefix() -> str:
    return conf()["prefix"]


def wikilink_re() -> re.Pattern:
    # Deliberately `[0-9a-z]+`, not `\d+`: ids minted before 2026-08-04 are
    # zero-padded counters (n-0003) and ids minted after are base32 tokens
    # (n-0a7k9m). Both must resolve forever, so the pattern only ever widens.
    return re.compile(rf"\[\[({re.escape(prefix())}-[0-9a-z]+)\]\]")


# Crockford-style base32: no i/l/o/u, so an id is safe to read aloud and to
# retype from a screenshot without the 1/l and 0/O confusions.
ID_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"
ID_EPOCH = _dt.date(2026, 1, 1)


def _b32(n: int, width: int) -> str:
    out = []
    for _ in range(width):
        n, r = divmod(n, 32)
        out.append(ID_ALPHABET[r])
    return "".join(reversed(out))


def mint_id(pre: str, today: _dt.date | None = None) -> str:
    """A note id that needs no coordination to be unique.

    Three chars of day-since-epoch (sortable, good for ~89 years) followed by
    five random chars. The counter it replaces was `max(local files) + 1`,
    which is derived purely from the working tree: two people who both hold
    n-0038 as their maximum both mint n-0039, their filenames differ because
    their slugs differ, and git therefore merges both without a conflict. The
    duplicate is silent until someone runs the test suite.

    This never reads the notes directory, which is the property that matters:
    two clones offline from each other cannot agree on a wrong answer.

    Five random chars, not three: the random part only has to separate notes
    minted on the *same day*, but 32**3 is 32768, which by the birthday bound
    is a coin flip somewhere around 180 notes in a day. 32**5 is 33.5M, which
    puts a realistic team's daily output back into the noise.
    """
    day = ((today or _dt.date.today()) - ID_EPOCH).days
    rand = "".join(secrets.choice(ID_ALPHABET) for _ in range(5))
    return f"{pre}-{_b32(max(day, 0), 3)}{rand}"


def duplicate_ids() -> dict:
    """Ids held by more than one note file, as {id: [paths]}.

    Minting cannot collide, but a merge can still land two notes that were
    numbered under the old counter, and `-f` can force anything. Callers that
    write or index check this so the clash surfaces at the next command rather
    than whenever someone next runs the tests.
    """
    by_id: dict = {}
    for p in sorted(NOTES.glob("*.md")):
        m = re.match(rf"({re.escape(prefix())}-[0-9a-z]+)-", p.name)
        if m:
            by_id.setdefault(m.group(1), []).append(p)
    return {k: v for k, v in by_id.items() if len(v) > 1}


# ---------------------------------------------------------------------------
# Note parsing
#
# Frontmatter is deliberately not YAML: a hand-rolled reader keeps this file
# dependency-free and the format is small enough that the strictness of a real
# parser buys nothing. Scalars are bare strings; lists are [a, b, c].
# ---------------------------------------------------------------------------

def parse_note(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    meta: dict = {"tags": [], "repos": [], "links": []}
    body = raw

    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end != -1:
            head, body = raw[4:end], raw[end + 5:]
            for line in head.splitlines():
                line = line.strip()
                if not line or line.startswith("#") or ":" not in line:
                    continue
                key, _, val = line.partition(":")
                key, val = key.strip(), val.strip()
                if key in LIST_KEYS:
                    val = val.strip("[]")
                    meta[key] = [v.strip() for v in val.split(",") if v.strip()]
                else:
                    meta[key] = val

    meta["path"] = path
    meta["body"] = body.strip()
    meta.setdefault("id", path.stem)
    meta.setdefault("title", path.stem)
    meta.setdefault("kind", "note")
    meta.setdefault("status", "open")
    return meta


def all_notes() -> list[dict]:
    if not NOTES.is_dir():
        return []
    return [parse_note(p) for p in sorted(NOTES.glob("*.md"))]


# ---------------------------------------------------------------------------
# Reference documents
#
# Some things do not fit in one claim. A repo map, a port table, the shape of
# a single service: you consult those, you do not read them once. Forcing them
# under the note length cap would shred them into fragments nobody can follow.
#
# They live in docs/ instead, uncapped, and are loaded in layers so a reader
# spends context only on the part they need: search shows the summary and the
# section names, `read` shows one section, `read --full` shows everything.
# ---------------------------------------------------------------------------

def all_docs() -> list[dict]:
    if not DOCS.is_dir():
        return []
    out = []
    for p in sorted(DOCS.glob("*.md")):
        d = parse_note(p)
        d["kind"] = "reference"
        d.setdefault("summary", "")
        out.append(d)
    return out


def sections(body: str) -> list[tuple[str, str]]:
    """Split on `## ` headings. Text before the first heading is the preamble."""
    parts: list[tuple[str, str]] = []
    name, buf = "(preamble)", []
    for line in body.splitlines():
        if line.startswith("## "):
            if any(l.strip() for l in buf):
                parts.append((name, "\n".join(buf).strip()))
            name, buf = line[3:].strip(), []
        else:
            buf.append(line)
    if any(l.strip() for l in buf):
        parts.append((name, "\n".join(buf).strip()))
    return parts


def find_entry(ident: str) -> dict | None:
    for e in all_notes() + all_docs():
        if e["id"] == ident:
            return e
    for e in all_notes() + all_docs():
        if e["id"].startswith(ident) or e["path"].stem.startswith(ident):
            return e
    return None


# ---------------------------------------------------------------------------
# Tag tree
#
# tags.md is an indented markdown list and is the single source of truth for
# the hierarchy. Notes reference a tag by its slug only, never by its path, so
# re-parenting a tag is a one-line edit here and touches no note. That is the
# whole point of keeping tags in a tree: the tree can be reorganised freely
# without a migration.
# ---------------------------------------------------------------------------

def load_tags() -> dict[str, dict]:
    """slug -> {parent, desc, depth}. Duplicate slugs are a hard error."""
    tags: dict[str, dict] = {}
    if not TAGS_FILE.exists():
        return tags
    stack: list[tuple[int, str]] = []
    for line in TAGS_FILE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(\s*)-\s+([a-z0-9][a-z0-9-]*)\s*(?::\s*(.*))?$", line)
        if not m:
            continue
        indent, slug, desc = len(m.group(1)), m.group(2), (m.group(3) or "").strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        if slug in tags:
            sys.exit(f"tags.md: duplicate slug {slug!r}, and slugs must be unique")
        tags[slug] = {"parent": parent, "desc": desc, "depth": len(stack)}
        stack.append((indent, slug))
    return tags


def tag_path(slug: str, tags: dict) -> str:
    parts, seen = [], set()
    while slug and slug in tags and slug not in seen:
        seen.add(slug)
        parts.append(slug)
        slug = tags[slug]["parent"]
    return "/".join(reversed(parts))


def descendants(slug: str, tags: dict) -> set[str]:
    """A tag query matches the tag and everything under it."""
    out = {slug}
    changed = True
    while changed:
        changed = False
        for s, meta in tags.items():
            if meta["parent"] in out and s not in out:
                out.add(s)
                changed = True
    return out


def write_tags(tags: dict) -> None:
    roots = [s for s, m in tags.items() if m["parent"] is None]

    def emit(slug: str, depth: int, lines: list[str]) -> None:
        desc = tags[slug]["desc"]
        lines.append("  " * depth + f"- {slug}" + (f": {desc}" if desc else ""))
        for child in sorted(s for s, m in tags.items() if m["parent"] == slug):
            emit(child, depth + 1, lines)

    # Preserve everything above the first list item. That prose explains the
    # conventions this file exists to enforce; regenerating it from a literal
    # in here would silently discard any refinement made by hand.
    preamble: list[str] = []
    if TAGS_FILE.exists():
        for line in TAGS_FILE.read_text(encoding="utf-8").splitlines():
            if re.match(r"^\s*-\s+[a-z0-9]", line):
                break
            preamble.append(line)
    if not preamble:
        preamble = ["# Tag tree", ""]
    while preamble and not preamble[-1].strip():
        preamble.pop()

    lines = preamble + [""]
    for r in sorted(roots):
        emit(r, 0, lines)
    lines.append("")
    TAGS_FILE.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Link graph
#
# Links are written one way, a note citing what it depends on, but reading is
# always two way: whoever lands on a cited note wants to know who cited it. The
# graph is therefore symmetrised on load, and a `[[<prefix>-NNNN]]` reference in
# a body counts as an edge so prose links are not second-class.
# ---------------------------------------------------------------------------

def _self() -> str:
    """How this engine was invoked, for the `next:` hints.

    Hardcoding `./grumpy.py` was right for exactly one layout. `init` now
    leaves the engine BESIDE the base, so every printed hint told the reader to
    run a file that is not there. argv[0] is what they actually typed.
    """
    return sys.argv[0] if sys.argv and sys.argv[0] else "grumpy.py"


def link_graph(notes: list[dict]) -> dict[str, set[str]]:
    ids = {n["id"] for n in notes}
    graph: dict[str, set[str]] = {n["id"]: set() for n in notes}
    for n in notes:
        for target in set(n["links"]) | set(wikilink_re().findall(n["body"])):
            if target in ids and target != n["id"]:
                graph[n["id"]].add(target)
                graph[target].add(n["id"])
    return graph


def reachable(seeds: set[str], graph: dict[str, set[str]], hops: int) -> dict[str, str]:
    """Notes within `hops` of any seed, mapped to the seed that reached them.
    Seeds themselves are excluded, and a note reached twice is kept once."""
    seen = set(seeds)
    out: dict[str, str] = {}
    frontier = {s: s for s in seeds}
    for _ in range(hops):
        nxt: dict[str, str] = {}
        for node, origin in frontier.items():
            for nb in sorted(graph.get(node, ())):
                if nb not in seen:
                    seen.add(nb)
                    out[nb] = origin
                    nxt[nb] = origin
        frontier = nxt
        if not frontier:
            break
    return out


# ---------------------------------------------------------------------------
# Near-duplicate detection
#
# A knowledge base that several agents write to accumulates restatements of the
# same finding, and a restatement is worse than nothing: the two copies drift
# and a reader cannot tell which is current. `add` checks before writing.
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-z0-9_./-]{3,}")


def _shingles(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def similar_notes(title: str, body: str, notes: list[dict],
                  threshold: float = DUP_THRESHOLD,
                  floor: int = 12) -> list[tuple[float, dict]]:
    """Overlap coefficient, |A n B| / min(|A|, |B|), on word sets.

    Not Jaccard: the duplicate that actually happens is a short note restating
    part of a long one, and Jaccard divides by the union, so the long note's
    bulk drowns the signal out. Overlap asks the question that matters instead:
    is one of these two mostly contained in the other?

    `floor` keeps a three-word stub from matching everything.
    """
    mine = _shingles(f"{title} {body}")
    if len(mine) < floor:
        return []
    hits = []
    for n in notes:
        theirs = _shingles(f"{n['title']} {n['body']}")
        if len(theirs) < floor:
            continue
        score = len(mine & theirs) / min(len(mine), len(theirs))
        if score >= threshold:
            hits.append((score, n))
    return sorted(hits, key=lambda x: -x[0])


# ---------------------------------------------------------------------------
# Search index
# ---------------------------------------------------------------------------

def _stale() -> bool:
    if not INDEX.exists():
        return True
    idx = INDEX.stat().st_mtime
    watched = list(NOTES.glob("*.md")) + list(DOCS.glob("*.md")) + \
        ([TAGS_FILE] if TAGS_FILE.exists() else [])
    return any(p.stat().st_mtime > idx for p in watched)


def build_index(force: bool = False) -> sqlite3.Connection:
    if force or _stale():
        INDEX.unlink(missing_ok=True)
        con = sqlite3.connect(INDEX)
        con.execute(
            "CREATE VIRTUAL TABLE notes USING fts5("
            "id, title, kind, status, tags, repos, body, coll, "
            "tokenize='porter unicode61')"
        )
        rows = [(n, "note") for n in all_notes()] + [(d, "doc") for d in all_docs()]
        for n, coll in rows:
            # A doc's summary is indexed alongside its body so a query can hit
            # the framing as well as the detail.
            text = n["body"] + "\n" + str(n.get("summary", ""))
            con.execute(
                "INSERT INTO notes VALUES (?,?,?,?,?,?,?,?)",
                (n["id"], n["title"], n["kind"], n["status"],
                 " ".join(n["tags"]), " ".join(n["repos"]), text, coll),
            )
        con.commit()
        return con
    return sqlite3.connect(INDEX)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_search(args) -> int:
    tags = load_tags()
    con = build_index()

    if args.query:
        # Bare terms are OR-ed and individually quoted. Quoting matters: an
        # unquoted `foo-bar` is parsed by FTS5 as a NOT, and `foo:bar` as a
        # column filter, both of which fail with a confusing error on what
        # looks like an ordinary word. A query that already uses FTS5 syntax
        # is passed through untouched.
        if re.search(r'["*:]|\b(AND|OR|NOT)\b', args.query):
            q = args.query
        else:
            q = " OR ".join('"%s"' % t.replace('"', '""') for t in args.query.split())
        try:
            rows = con.execute(
                "SELECT id, title, kind, status, tags, repos, coll "
                "FROM notes WHERE notes MATCH ? ORDER BY rank", (q,)
            ).fetchall()
        except sqlite3.OperationalError as e:
            return _die(f"bad query: {e}")

        if not rows:
            # unicode61 treats an unbroken run of CJK as a single token, so a
            # substring of a Chinese sentence never matches via FTS. Notes and
            # discussion here are written in both languages, so fall back to a
            # plain substring scan. Same fallback rescues partial English words.
            like = f"%{args.query.strip()}%"
            rows = con.execute(
                "SELECT id, title, kind, status, tags, repos, coll FROM notes "
                "WHERE title LIKE ? OR body LIKE ? ORDER BY coll DESC, id",
                (like, like)
            ).fetchall()
    else:
        rows = con.execute(
            "SELECT id, title, kind, status, tags, repos, coll FROM notes "
            "ORDER BY coll DESC, id"
        ).fetchall()

    wanted = set()
    for t in args.tag or []:
        if t not in tags:
            print(f"warning: unknown tag {t!r} (see `grumpy tags`)", file=sys.stderr)
        wanted |= descendants(t, tags)

    out = []
    for nid, title, kind, status, ntags, nrepos, coll in rows:
        tset = set(ntags.split())
        if wanted and not (tset & wanted):
            continue
        if args.kind and kind != args.kind:
            continue
        if args.repo and args.repo not in nrepos.split():
            continue
        if args.status and status != args.status:
            continue
        # Settled entries are hidden unless asked for, either by --all or by
        # naming the status outright.
        if not args.all and not args.status and status in CLOSED_STATUS:
            continue
        out.append((nid, title, kind, status, ntags, nrepos, coll))

    if not out:
        print("no matches")
        return 1

    notes = all_notes()
    entries = notes + all_docs()
    by_id = {n["id"]: n for n in entries}
    graph = link_graph(entries)

    if args.json:
        hits = [{
            "id": nid, "title": title, "kind": kind, "status": status,
            "tags": ntags.split(), "repos": nrepos.split(),
            "links": sorted(graph.get(nid, ())),
        } for nid, title, kind, status, ntags, nrepos, coll in out]
        print(json.dumps(hits, ensure_ascii=False))
        return 0

    def show(nid, title, kind, status, ntags, nrepos, coll="note",
             prefix="", origin=None):
        pad = " " * len(prefix)
        flag = "" if status == "open" else f" [{status}]"
        via = f"   (via {origin})" if origin else ""
        print(f"{prefix}{nid}  {title}{flag}{via}")
        print(f"{pad}    kind={kind}  tags={ntags or '-'}  repos={nrepos or '-'}")
        if coll == "doc":
            # Never print a doc's body here. The point of the docs tier is that
            # a reader spends context on the outline first and pulls only the
            # section they need.
            d = by_id.get(nid)
            if d:
                if d.get("summary"):
                    print(f"{pad}    {d['summary']}")
                names = [s for s, _ in sections(d["body"])]
                if names:
                    print(f"{pad}    sections: {' | '.join(names)}")
                print(f"{pad}    read:  {_self()} read {nid} --section \"<name>\"")
        nbrs = sorted(graph.get(nid, ()))
        if nbrs:
            print(f"{pad}    -> {' '.join(nbrs)}")

    for row in out:
        show(*row)

    shown = {r[0] for r in out}
    extra = 0
    # A task is a pointer at work to be done; read without the findings that
    # motivated it, it is just a sentence. Expand by default so the worklist
    # view arrives self-contained.
    if args.expand is None:
        args.expand = 1 if args.kind == "task" else 0
    if args.expand:
        for nid, origin in sorted(reachable(shown, graph, args.expand).items()):
            n = by_id[nid]
            show(nid, n["title"], n["kind"], n["status"],
                 " ".join(n["tags"]), " ".join(n["repos"]),
                 "doc" if n["kind"] == "reference" else "note",
                 prefix="  ", origin=origin)
            extra += 1

    tail = f" (+{extra} linked)" if extra else ""
    hidden = sum(1 for r in rows if r[3] in CLOSED_STATUS) if not (args.all or args.status) else 0
    tail += f", {hidden} settled hidden" if hidden else ""
    print(f"\n{len(out)} match(es){tail}. Read one with:  cat notes/<id>-*.md")
    if not args.expand and any(graph.get(r[0]) for r in out):
        print("Follow the -> links, or re-run with --expand to pull them in.")
    return 0


def cmd_add(args) -> int:
    tags = load_tags()
    given = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
    # kind is both a field and a tag. The field guarantees exactly one primary
    # classification per note; mirroring it into tags means `--tag known-issue`
    # and `--kind known-issue` return the same set, so callers never have to
    # remember which axis a term lives on.
    if args.kind in tags and args.kind not in given:
        given.insert(0, args.kind)
    unknown = [t for t in given if t not in tags]
    if unknown and not args.force:
        return _die(f"unknown tag(s): {', '.join(unknown)}\n"
                    f"Add them first (`grumpy tags --add SLUG --parent P`) or pass --force.\n"
                    f"Keeping the tag set small is the point, so prefer an existing tag.")

    # Severity gate, enforced here rather than left to the test suite: a
    # known-issue or task must answer "how bad if you hit it"; every other kind
    # must carry none. Same fail-at-write, -f-clearable contract as the tag
    # gate, so a note that violates the invariant never reaches the content.
    carried_sev = [t for t in given if t in SEVERITY]
    if not args.force:
        if args.kind in ("known-issue", "task") and len(carried_sev) != 1:
            return _die(
                f"a {args.kind} needs exactly one severity tag "
                f"({', '.join(SEVERITY)}); got {carried_sev or 'none'}.\n"
                f"Add it to --tags, or pass -f."
            )
        if args.kind not in ("known-issue", "task") and carried_sev:
            return _die(
                f"severity ({', '.join(carried_sev)}) is only for known-issue/task; "
                f"a {args.kind} must carry none. Drop it from --tags, or pass -f."
            )

    # --links: validated at write time. The graph is note-to-note, so every
    # target must be an existing note id; a doc is linked from the doc's own
    # side, never a note's links: field (that is what breaks the link invariant).
    link_ids: list[str] = []
    if args.links:
        _notes_now = all_notes()
        _note_ids = {n["id"] for n in _notes_now}
        _doc_ids = {d["id"] for d in all_docs()}
        for t in (x.strip() for x in args.links.split(",") if x.strip()):
            if t in _note_ids or args.force:
                link_ids.append(t)
            elif t in _doc_ids:
                return _die(f"--links {t} is a reference doc; a note's links must "
                            f"point at notes. Link it from the doc side, or pass -f.")
            else:
                return _die(f"--links {t} matches no existing note "
                            f"(expected e.g. {prefix()}-0003). Check the id, or pass -f.")

    slug = re.sub(r"[^a-z0-9]+", "-", args.title.lower()).strip("-")[:48]
    is_doc = args.kind == "reference"
    if is_doc:
        # A doc is addressed by name, not by number: `grumpy read <name>` is
        # something a person can type from memory.
        DOCS.mkdir(exist_ok=True)
        nid = args.slug or slug
        path = DOCS / f"{nid}.md"
        if path.exists() and not args.force:
            return _die(f"{path.relative_to(ROOT)} already exists. Edit it, "
                        f"or pass a different --slug")
    else:
        pre = prefix()
        taken = {m.group(1) for p in NOTES.glob(f"{pre}-*.md")
                 if (m := re.match(rf"({re.escape(pre)}-[0-9a-z]+)-", p.name))}
        # Minting is coordination-free, so this loop is a belt-and-braces check
        # against the local tree, not the mechanism that makes ids unique.
        for _ in range(50):
            nid = mint_id(pre)
            if nid not in taken:
                break
        else:
            return _die("could not mint a free note id after 50 tries — run "
                        "`grumpy reindex` and check notes/ for damage")
        path = NOTES / f"{nid}-{slug}.md"

    # Read the body from a pipe automatically: an agent almost always heredocs
    # it in, and requiring an explicit --stdin for that was pure ceremony. An
    # explicit --body still wins, and an interactive tty is never blocked on.
    if args.stdin or (not args.body and not sys.stdin.isatty()):
        body = sys.stdin.read().strip()
    else:
        body = (args.body or "").strip()
    if not body:
        body = "TODO: what was observed, what it means, and what to do about it."

    # The length and overlap gates exist to keep atomic notes atomic. A
    # reference doc is the sanctioned way to be long, so neither applies,
    # but it must carry a summary, because that is all a search will show.
    if is_doc:
        if not args.summary and not args.force:
            return _die("a reference doc needs --summary: one or two lines, "
                        "because that is what search displays instead of the body")
        path.write_text(
            f"---\nid: {nid}\ntitle: {args.title}\nkind: reference\n"
            f"status: {args.status}\nsummary: {args.summary}\n"
            f"tags: [{', '.join(given)}]\n"
            f"repos: [{', '.join(r.strip() for r in (args.repos or '').split(',') if r.strip())}]\n"
            f"links: [{', '.join(link_ids)}]\ncreated: {_dt.date.today()}\n---\n\n{body}\n",
            encoding="utf-8")
        if args.json:
            print(json.dumps({"id": nid, "path": str(path.relative_to(ROOT)),
                              "kind": "reference", "links": link_ids,
                              "sections": [s for s, _ in sections(body)]},
                             ensure_ascii=False))
            return 0
        print(f"wrote {path.relative_to(ROOT)}  (id {nid}, {len(body)} chars, "
              f"{len(sections(body))} sections)")
        print(f"next: {_self()} read {nid}")
        return 0

    counted = prose_len(body)
    if counted > MAX_BODY and not args.force:
        extra = f" ({len(body)} with tables and code, which do not count)" \
            if counted != len(body) else ""
        return _die(
            f"body is {counted} characters of prose{extra}; the limit is {MAX_BODY}.\n"
            f"That is usually two issues in one note, and the second becomes\n"
            f"unfindable. Split it and link the halves, or re-run with -f."
        )

    # threshold=0 so the closest match can be reported even when it passes,
    # seeing "31%" is how an author learns where the line is.
    scored = similar_notes(args.title, body, all_notes(), threshold=0.0)
    over = [(s, n) for s, n in scored if s >= DUP_THRESHOLD]
    if over and not args.force:
        lines = [f"  {s:.0%}  {n['id']}  {n['title']}" for s, n in over[:5]]
        return _die(
            f"overlap with existing notes is {over[0][0]:.0%}; the limit is "
            f"{DUP_THRESHOLD:.0%}. Closest:\n" + "\n".join(lines) +
            "\n\nDecide which this is:\n"
            "  - the same finding      -> extend that note, or `grumpy discuss` it\n"
            "  - a follow-on           -> add it there as a Discussion entry\n"
            "  - genuinely separate    -> re-run with -f, then put the other\n"
            "                             note's id in this one's links: field"
        )

    repos = ", ".join(r.strip() for r in (args.repos or "").split(",") if r.strip())
    path.write_text(
        f"---\n"
        f"id: {nid}\n"
        f"title: {args.title}\n"
        f"kind: {args.kind}\n"
        f"status: {args.status}\n"
        f"tags: [{', '.join(given)}]\n"
        f"repos: [{repos}]\n"
        f"links: [{', '.join(link_ids)}]\n"
        f"created: {_dt.date.today()}\n"
        f"---\n\n{body}\n\n## Discussion\n",
        encoding="utf-8",
    )

    # Reported as advice, and advice that never changes stops being read. An
    # open `task` is long and shares vocabulary with everything near it, so it
    # won the closest slot for five notes out of seven in one session while
    # meaning nothing. Skipped here and here only: the overlap GATE above still
    # sees tasks, so a genuine duplicate of one is still refused.
    closest = None
    advisory = [(sc, n) for sc, n in scored if n.get("kind") != "task"] or scored
    if advisory:
        sc, n = advisory[0]
        closest = {"score": round(sc, 4), "id": n["id"], "title": n["title"]}

    if args.json:
        print(json.dumps({
            "id": nid,
            "path": str(path.relative_to(ROOT)),
            "kind": args.kind,
            "status": args.status,
            "tags": given,
            "repos": [r for r in repos.split(", ") if r],
            "links": link_ids,
            "chars": counted,
            "chars_total": len(body),
            "closest": closest,
        }, ensure_ascii=False))
        return 0

    print(f"wrote {path.relative_to(ROOT)}  (id {nid}, {counted}/{MAX_BODY} prose chars)")
    if closest:
        tail = "  <- link these" if closest["score"] >= DUP_THRESHOLD else ""
        print(f"closest existing note: {closest['score']:.0%}  "
              f"{closest['id']}  {closest['title']}{tail}")
    print(f"next: {_self()} read {nid} --context")
    return 0


# A note's length is measured over its PROSE.
#
# The limit exists to stop two findings sharing one note. A markdown table or a
# fenced block is the opposite of that: it is the densest, least redundant way
# to say one thing, and charging it by the character pushed authors to delete
# load-bearing sentences to fit a table they had already made as small as it
# goes. Three notes in one session were rejected at 1770-2119 characters, every
# one of them a single claim with a table under it.
#
# Structure is therefore free and prose is not.
_FENCE_RE = re.compile(r"(?ms)^```.*?^```\s*$")
_TABLE_ROW_RE = re.compile(r"(?m)^\s*\|.*\|\s*$")


def prose_len(body: str) -> int:
    """Characters that count toward MAX_BODY: everything but tables and code."""
    stripped = _FENCE_RE.sub("", body)
    stripped = _TABLE_ROW_RE.sub("", stripped)
    return len(stripped)


def cmd_tags(args) -> int:
    tags = load_tags()

    if args.add:
        if args.add in tags:
            return _die(f"tag {args.add!r} already exists at {tag_path(args.add, tags)}")
        if args.parent and args.parent not in tags:
            return _die(f"no such parent tag: {args.parent}")
        tags[args.add] = {"parent": args.parent, "desc": args.desc or "", "depth": 0}
        write_tags(tags)
        print(f"added {tag_path(args.add, tags)}")
        return 0

    if args.move:
        if args.move not in tags:
            return _die(f"no such tag: {args.move}")
        if args.parent and args.parent not in tags:
            return _die(f"no such parent tag: {args.parent}")
        if args.parent in descendants(args.move, tags):
            return _die("refusing to move a tag under its own descendant")
        tags[args.move]["parent"] = args.parent
        write_tags(tags)
        print(f"moved -> {tag_path(args.move, tags)}   (no notes needed changing)")
        return 0

    counts: dict[str, int] = {}
    for n in all_notes():
        for t in n["tags"]:
            counts[t] = counts.get(t, 0) + 1

    def show(slug: str, depth: int) -> None:
        c = counts.get(slug, 0)
        desc = tags[slug]["desc"]
        print("  " * depth + f"- {slug}" + (f" ({c})" if c else "") +
              (f"  - {desc}" if desc else ""))
        for child in sorted(s for s, m in tags.items() if m["parent"] == slug):
            show(child, depth + 1)

    for r in sorted(s for s, m in tags.items() if m["parent"] is None):
        show(r, 0)
    orphans = set(counts) - set(tags)
    if orphans:
        print(f"\nused but not in the tree: {', '.join(sorted(orphans))}")
    return 0


SEVERITY = ("blocker", "major", "minor")

STARTER_TAGS = """\
# Tag tree

Slugs are globally unique and are what notes reference. Position in this tree is
metadata about the slug, not part of its identity, so moving a line re-parents a
tag without touching a single note.

A search for a parent tag also matches everything beneath it. Query the leaves.

Keep this small. Before adding a slug, check whether an existing one already
carries the meaning. A tag used once is noise. Repos are **not** tags; they
live in each note's `repos:` field so the two axes stay independent.

Everything above the first list item is preserved when the CLI rewrites this
file, so edit this prose freely.

## Choosing a kind

The test: *if a different person had done this same work, would the note read
the same?* Always the same means the system simply is that way: `architecture`,
or `known-issue` where the behaviour contradicts the project's own docs.
Possibly different means someone chose: `decision`, and it must be able to name
the alternatives it rejected. Reproducible by following it: `runbook`.

- area: which part of the system it touches
  - EDITME: replace these with your own areas
- severity: how bad it is if you hit it. Only known-issue and task carry one
  - blocker: work stops until this is resolved
  - major: costs real time, or there is a workaround you must know about
  - minor: worth knowing, cheap to route around
- kind: what a note is. Exactly one per note, mirrored from the `kind:` field
  - architecture: a fact about how the system is built, decided upstream
  - decision: a choice we made where another team would reasonably differ
  - known-issue: a defect that breaks a promise the project's own docs make
  - reference: too large for one claim; a map you consult, lives in docs/
  - runbook: an executable procedure, with a way to check it worked
  - task: open work. `status: open` until done
"""

STARTER_README = """\
# {name}

Knowledge base. The notes are the point; this file only says how to run them.

**It does not contain the engine.** `grumpy.py` lives beside this directory, so
a fresh clone of this repo has no runnable tool until that exists next to it.

## First thing, once

```bash
git clone https://github.com/ez945y/grumpy ../grumpy
```

Layout, after that:

```
{name}/     notes, docs, tags.md, grumpy.conf, SKILL.md
grumpy/     the engine, cloned once, shared by every base on this box
```

## Running it

Always **from this directory** — the engine resolves the base from the working
directory when it holds a `grumpy.conf`, which this does.

```bash
cd {name}
../grumpy/grumpy.py search <query>
../grumpy/grumpy.py issues
../grumpy/grumpy.py read <id>
```

Stdlib Python only. No dependencies, no server, no build step.

## For an agent

`SKILL.md` is the instruction set — when to search this before reading source,
what a note must contain, and what the `add` gates refuse. Read that, not this.
Link it into your agent's skills directory with:

```bash
../grumpy/grumpy.py install
```

Restart the agent tool afterwards; skills are read once at startup.
"""


STARTER_SKILL = """\
---
name: {name}
description: {description}
---

# {title}

{blurb}

**Search it before you grep.** Rediscovering something already written here is
the failure mode this exists to prevent.

## Commands

Run from `{root}`. The engine is the sibling clone, not a file in here — see
the README. Stdlib Python only, nothing to install.

```bash
../grumpy/grumpy.py search <query> [--tag T] [--kind K] [--repo R] [--all] [--expand [HOPS]] [--json]
../grumpy/grumpy.py issues [--severity S] [--repo R]
../grumpy/grumpy.py read <id> [--context] [--section NAME] [--full] [--json]
../grumpy/grumpy.py add --title T --kind K [--tags a,b] [--repos x,y] [--links id,id] [--json]
../grumpy/grumpy.py tags [--add SLUG --parent P] [--move SLUG --parent P]
../grumpy/grumpy.py discuss <id> -m "..."
```

`add` reads the body from a pipe (heredoc) automatically, or takes `--body`.

Notes are atomic: one claim, capped at {cap} characters. Reference docs in
`docs/` are the sanctioned way to be long and are read a section at a time.
`add` argues at write time: it refuses an unknown tag, an over-length body,
{dup:.0%}+ overlap, a known-issue/task with no severity, or a `--links` target
that is not an existing note; `-f` clears any of them. Wire edges in the same
call with `--links`, and pass `--json` (on add/search/read) to chain without
parsing prose; `add --json` returns the new id.

## Start here

```bash
../grumpy/grumpy.py search --kind architecture
../grumpy/grumpy.py issues
../grumpy/grumpy.py search --kind task --status open
```

## Writing a note

Say what you verified and what you inferred. Cite `file:line` or the command
you ran. One claim per note. If it needs "and also", it is two notes. Do not
invent tags; `tags.md` explains the vocabulary.

Run `python3 -m unittest test_grumpy` afterwards: the last group checks that every
tag resolves and every `[[{prefix}-NNNN]]` link points at something real.
"""


def _link_skill(base: Path, name: str, project: bool) -> tuple[bool, str]:
    """Symlink a knowledge base into a skills directory.

    A link rather than a copy, so editing notes takes effect immediately and
    there is only ever one of them. The directory has to be named after the
    skill, which is why the name in grumpy.conf and the link name must agree.
    """
    skills = (Path.cwd() / ".claude" / "skills") if project \
        else (Path.home() / ".claude" / "skills")
    skills.mkdir(parents=True, exist_ok=True)
    link = skills / name

    if link.is_symlink():
        if link.resolve() == base.resolve():
            return True, f"already linked: {link}"
        return False, (f"{link} points at {link.resolve()}, not {base}.\n"
                       f"Remove it first, or use a different --name.")
    if link.exists():
        return False, f"{link} exists and is not a link. Move it out of the way."

    link.symlink_to(base)
    return True, f"linked {link} -> {base}"


def cmd_install(args) -> int:
    """Make an existing knowledge base visible to agent tools."""
    name = conf().get("name") or ROOT.name
    if not (ROOT / "SKILL.md").exists():
        return _die("no SKILL.md here, so there is nothing for an agent to load")
    ok, msg = _link_skill(ROOT, name, args.project)
    print(("  " if ok else "") + msg)
    if not ok:
        return 2
    print("\nRestart your agent tool for it to notice.")
    return 0


def cmd_init(args) -> int:
    """Scaffold a new knowledge base somewhere else.

    The engine - grumpy.py and test_grumpy.py - is copied verbatim; nothing in it names
    a project. Everything that does lives in grumpy.conf and the content, which is
    what makes the same code serve any number of bases.
    """
    import shutil

    dest = Path(args.dest).expanduser().resolve()
    if dest.exists() and any(dest.iterdir()) and not args.force:
        return _die(f"{dest} exists and is not empty - pass --force to add to it")
    (dest / "notes").mkdir(parents=True, exist_ok=True)
    (dest / "docs").mkdir(exist_ok=True)

    name = args.name or dest.name
    pre = args.prefix or "".join(w[0] for w in re.split(r"[^a-z0-9]+", name.lower())
                                 if w)[:3] or "n"

    # The engine is NOT copied in. A base holds knowledge; the engine is cloned
    # once and sits beside it, which is what lets one clone serve every base and
    # keeps a shared base's history free of engine diffs. ROOT resolution
    # already supports both layouts, so anyone who wants a self-contained base
    # can copy grumpy.py in themselves — but the generated README describes the
    # sibling layout, and a default that contradicted it would be worse than
    # either choice alone.

    (dest / "grumpy.conf").write_text(
        "# Instance configuration. The engine (grumpy.py, test_grumpy.py) is\n"
        "# project-agnostic; everything naming this project lives here.\n"
        f"prefix = {pre}\nname = {name}\ntitle = {args.title or name}\n",
        encoding="utf-8")
    (dest / "tags.md").write_text(STARTER_TAGS, encoding="utf-8")
    # README and SKILL are both generated, and they are not the same document.
    # README is the ninety seconds before anything works — clone the engine next
    # door, run it from here. SKILL is what an agent loads to decide whether to
    # search at all. A base with only one of them either strands a human at a
    # repo with no runnable tool, or an agent with no reason to open it.
    (dest / "README.md").write_text(STARTER_README.format(name=name), encoding="utf-8")
    (dest / ".gitignore").write_text(".index.db\n__pycache__/\n*.pyc\n",
                                     encoding="utf-8")
    (dest / "SKILL.md").write_text(STARTER_SKILL.format(
        name=name, title=args.title or name,
        description=args.description or (
            f"Search the {name} knowledge base before grepping the codebase, and "
            f"record what you learn. Use when working in "
            f"<EDITME: repo or directory paths> or on <EDITME: the component names "
            f"people actually say>; and when debugging <EDITME: the concrete "
            f"symptoms and error strings that should trigger this>. Real paths and "
            f"real error text make it fire at the right moment; a vague description "
            f"never does."),
        blurb=args.title or f"Knowledge base for {name}.",
        root=dest, cap=MAX_BODY, dup=DUP_THRESHOLD, prefix=pre), encoding="utf-8")

    print(f"created {dest}")
    print(f"  note ids: {pre}-0001, {pre}-0002, ...")

    if args.install:
        ok, msg = _link_skill(dest, name, args.project)
        print("  " + msg)
        if not ok:
            print("  (the knowledge base itself was still created)")

    print("\nNext:")
    print("  1. edit tags.md    replace the EDITME area branch")
    print("  2. edit SKILL.md   the description decides whether an agent loads it")
    print(f"  3. cd {dest} && ../grumpy/grumpy.py add --title ... --kind architecture")
    if not args.install:
        print(f"  4. ../grumpy/grumpy.py install   make it visible to your agent tool")
    return 0


def cmd_issues(args) -> int:
    """The defect list, worst first.

    Sugar over search, but the ordering is the point: `--kind known-issue`
    returns them in id order, which is the order they were written down, and
    that has nothing to do with which one will hurt you today.
    """
    notes = all_notes()
    graph = link_graph(notes + all_docs())
    rank = {s: i for i, s in enumerate(SEVERITY)}

    rows = [n for n in notes if n["kind"] == "known-issue"]
    if not args.all:
        rows = [n for n in rows if n["status"] not in CLOSED_STATUS]
    if args.severity:
        rows = [n for n in rows if args.severity in n["tags"]]
    if args.repo:
        rows = [n for n in rows if args.repo in n["repos"]]
    rows.sort(key=lambda n: (min((rank.get(t, 9) for t in n["tags"]), default=9),
                             n["id"]))
    if not rows:
        print("no open issues")
        return 1

    shown_sev = None
    for n in rows:
        sev = next((t for t in n["tags"] if t in rank), "unrated")
        if sev != shown_sev:
            print(f"\n{sev.upper()}")
            shown_sev = sev
        flag = "" if n["status"] == "open" else f"  [{n['status']}]"
        print(f"  {n['id']}  {n['title']}{flag}")
        meta = " ".join(n["repos"]) or "-"
        print(f"           {meta}")
        nbrs = sorted(graph.get(n["id"], ()))
        if nbrs:
            print(f"           -> {' '.join(nbrs)}")
    print(f"\n{len(rows)} issue(s).  {_self()} read <id> --context  for one with "
          f"its decisions and tasks")
    return 0


def cmd_read(args) -> int:
    """Three layers, so a reader spends only the context they need."""
    e = find_entry(args.id)
    if not e:
        return _die(f"no note or doc matching {args.id!r}")

    if args.json:
        entries = all_notes() + all_docs()
        by_id = {x["id"]: x for x in entries}
        nbrs = sorted(link_graph(entries).get(e["id"], ()))
        out = {k: e[k] for k in
               ("id", "title", "kind", "status", "tags", "repos", "links", "body")}
        if e["kind"] == "reference":
            out["summary"] = e.get("summary", "")
            out["sections"] = [s for s, _ in sections(e["body"])]
        if args.context:
            out["context"] = [{"id": i, "title": by_id[i]["title"],
                               "kind": by_id[i]["kind"], "status": by_id[i]["status"]}
                              for i in nbrs]
        print(json.dumps(out, ensure_ascii=False))
        return 0

    if e["kind"] != "reference":
        print(f"# {e['title']}")
        print(f"_{e['kind']}, {e['status']}"
              + (f", repos: {', '.join(e['repos'])}" if e["repos"] else "") + "_\n")
        print(e["body"])
        if args.context:
            _print_context(e)
        return 0

    parts = sections(e["body"])
    if args.section:
        want = args.section.lower()
        for name, text in parts:
            if want in name.lower():
                print(f"## {name}\n\n{text}")
                return 0
        return _die(f"no section matching {args.section!r} in {e['id']}\n"
                    f"sections: {', '.join(n for n, _ in parts)}")

    if args.full:
        print(e["body"])
        return 0

    print(f"{e['id']}  {e['title']}")
    if e.get("summary"):
        print(f"\n{e['summary']}")
    print(f"\n{len(parts)} section(s):")
    for name, text in parts:
        print(f"  {len(text.splitlines()):4} lines  {name}")
    print(f"\n{_self()} read {e['id']} --section \"<name>\"   |   --full for all of it")
    if args.context:
        _print_context(e)
    return 0


def _print_context(e: dict) -> None:
    """The neighbourhood of one entry, one line each.

    Reading a defect on its own leaves the obvious questions unanswered - was a
    decision forced by it, is anyone assigned, which runbook works around it.
    Those live in linked entries, and opening five files to find out is enough
    friction that nobody does.
    """
    entries = all_notes() + all_docs()
    by_id = {x["id"]: x for x in entries}
    nbrs = sorted(link_graph(entries).get(e["id"], ()))
    if not nbrs:
        print("\n## Context\n\n(nothing links to this yet)")
        return
    print("\n## Context\n")
    for nid in sorted(nbrs, key=lambda i: (by_id[i]["kind"], i)):
        n = by_id[nid]
        flag = "" if n["status"] == "open" else f" [{n['status']}]"
        print(f"  {n['kind']:<12} {nid}  {n['title']}{flag}")
    print(f"\n  {_self()} read <id>   |   {_self()} search --expand for the full sweep")


def _reindex_quiet() -> None:
    """Refresh the index after a write, without the reindex banner.

    Every command that edits a note calls this. Leaving it to the caller is how
    a note ends up saying one thing and search another, and the gap is silent.
    """
    try:
        build_index(force=True)
    except Exception:  # noqa: BLE001 — a stale index must never fail the write
        pass


def cmd_status(args) -> int:
    """Set a note's lifecycle status, and reindex so search agrees.

    Editing the frontmatter by hand is what this replaces: a sed over `status:`
    leaves the index describing the old state until someone remembers to
    reindex, and search is the whole point of the file.
    """
    matches = list(NOTES.glob(f"{args.id}*.md"))
    if not matches:
        return _die(f"no note matching {args.id!r}")
    valid = OPEN_STATUS + CLOSED_STATUS
    if args.value not in valid:
        return _die(f"unknown status {args.value!r}. One of: {', '.join(valid)}")
    path = matches[0]
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r"(?m)^status: .*$", f"status: {args.value}", text, count=1)
    if not n:
        return _die(f"{path.name} has no status: line to set")
    path.write_text(new, encoding="utf-8")
    print(f"{path.stem.split('-')[0]}-{path.stem.split('-')[1]} -> {args.value}")
    _reindex_quiet()
    return 0


def cmd_link(args) -> int:
    """Add links to an existing note, so a map can be written before its parts.

    `add --links` validates against notes that already exist, which is the
    right check and the wrong moment for one workflow: writing the overview
    first and the threads afterwards. That ordering is the one worth
    encouraging — it is how you find out which threads there are — and without
    this the only way to finish it was editing the file, which leaves the
    index describing the old state.

    Links are written one way and read both ways (see link_graph), so this
    touches only the source note.
    """
    matches = list(NOTES.glob(f"{args.id}*.md"))
    if not matches:
        return _die(f"no note matching {args.id!r}")
    path = matches[0]
    text = path.read_text(encoding="utf-8")

    known = {n["id"] for n in all_notes()}
    doc_ids = {d["id"] for d in all_docs()}
    targets: list[str] = []
    for t in args.targets:
        t = t.strip()
        if not t or t == args.id:
            continue
        if t in known or args.force:
            targets.append(t)
        elif t in doc_ids:
            return _die(f"{t} is a reference doc; a note's links must point at "
                        f"notes. Link it from the doc side, or pass -f.")
        else:
            return _die(f"{t} matches no existing note. Check the id, or pass -f.")

    m = re.search(r"(?m)^links: \[(.*)\]$", text)
    if not m:
        return _die(f"{path.name} has no links: line to extend")
    have = [x.strip() for x in m.group(1).split(",") if x.strip()]
    added = [t for t in targets if t not in have]
    if not added:
        print(f"{args.id}: already links {', '.join(targets)}")
        return 0
    merged = have + added
    text = text[:m.start()] + f"links: [{', '.join(merged)}]" + text[m.end():]
    path.write_text(text, encoding="utf-8")
    print(f"{args.id} -> {', '.join(added)}")
    _reindex_quiet()
    return 0


def cmd_discuss(args) -> int:
    matches = list(NOTES.glob(f"{args.id}*.md"))
    if not matches:
        return _die(f"no note matching {args.id!r}")
    path = matches[0]

    # Same contract as `add`: a piped or heredoc body wins, -m is the shortcut
    # for one line. -m goes through the shell, so backticks in it become
    # command substitution and the text lands mangled — a heredoc does not.
    message = args.message
    if not message and not sys.stdin.isatty():
        message = sys.stdin.read()
    if not message or not message.strip():
        return _die("no message. Pass -m, or pipe one:  grumpy discuss ID <<'EOF' ... EOF")

    who = args.who or os.environ.get("USER", "someone")
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    text = path.read_text(encoding="utf-8").rstrip("\n")
    if "## Discussion" not in text:
        text += "\n\n## Discussion"
    entry = textwrap.indent(message.strip(), "  ").lstrip()
    path.write_text(f"{text}\n\n**{who}** · {stamp}\n\n{entry}\n", encoding="utf-8")
    print(f"appended to {path.relative_to(ROOT)}")
    _reindex_quiet()
    return 0


def cmd_reindex(_args) -> int:
    # Checked here because reindex is what runs right after a pull, which is
    # exactly when a merge would have landed two notes sharing one id. Two
    # files, different slugs, no git conflict: the clash is otherwise silent
    # until someone happens to run the test suite.
    dupes = duplicate_ids()
    build_index(force=True)
    print(f"indexed {len(all_notes())} note(s)")
    if dupes:
        for nid, paths in sorted(dupes.items()):
            names = ", ".join(p.name for p in paths)
            print(f"duplicate id {nid}: {names}", file=sys.stderr)
        print(f"\n{len(dupes)} duplicate id(s). Links to them are ambiguous. "
              f"Rename the newer file and its `id:` field, then fix inbound "
              f"[[links]].", file=sys.stderr)
        return 1
    return 0


def _die(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 2


def main() -> int:
    p = argparse.ArgumentParser(prog="grumpy", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    # Consumed in _resolve_root() at import time; declared here so --help
    # documents it and argparse does not reject it as unknown.
    p.add_argument("--root", metavar="PATH",
                   help="the knowledge base to operate on (default: $GRUMPY_ROOT, "
                        "else the working directory if it holds a grumpy.conf, "
                        "else this script's directory)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="full-text search, filtered by tag/kind/repo")
    s.add_argument("query", nargs="?", default="")
    s.add_argument("--tag", action="append", help="tag slug; also matches its children")
    s.add_argument("--kind")
    s.add_argument("--repo")
    s.add_argument("--status", help="exact status; implies --all")
    s.add_argument("--all", action="store_true",
                   help=f"include settled entries ({', '.join(CLOSED_STATUS)})")
    s.add_argument("--json", action="store_true",
                   help="print hits as a JSON array (id, kind, tags, links) for chaining")
    s.add_argument("--expand", nargs="?", type=int, const=1, default=None,
                   metavar="HOPS",
                   help="also show linked notes, up to HOPS away (default 1). "
                        "Implied for --kind task; pass --expand 0 to suppress")
    s.set_defaults(fn=cmd_search)

    a = sub.add_parser("add", help="create a note")
    a.add_argument("--title", required=True)
    a.add_argument("--kind", required=True,
                   choices=["known-issue", "architecture", "runbook",
                            "decision", "reference", "task"])
    a.add_argument("--summary", help="reference docs only: the one or two lines "
                                     "search shows instead of the body")
    a.add_argument("--slug", help="reference docs only: the id to address it by")
    a.add_argument("--tags", help="comma-separated slugs")
    a.add_argument("--repos", help="comma-separated repo names")
    a.add_argument("--status", default="open")
    a.add_argument("--links", help="comma-separated note ids to link, "
                   "validated against existing notes at write time")
    a.add_argument("--body")
    a.add_argument("--stdin", action="store_true",
                   help="force reading the body from stdin (a pipe is auto-detected "
                        "anyway, so this is rarely needed)")
    a.add_argument("--json", action="store_true",
                   help="print the created note as JSON (id, path, links, closest) "
                        "for chaining")
    a.add_argument("-f", "--force", action="store_true",
                   help="write anyway: allow an unknown tag, an over-length "
                        "body, or overlap with an existing note")
    a.set_defaults(fn=cmd_add)

    t = sub.add_parser("tags", help="show or reshape the tag tree")
    t.add_argument("--add", metavar="SLUG")
    t.add_argument("--move", metavar="SLUG")
    t.add_argument("--parent", metavar="SLUG")
    t.add_argument("--desc")
    t.set_defaults(fn=cmd_tags)

    r0 = sub.add_parser("read", help="read a note, or a doc one section at a time")
    r0.add_argument("id")
    r0.add_argument("--section", help="substring of a section name")
    r0.add_argument("--full", action="store_true", help="print the whole doc")
    r0.add_argument("--context", action="store_true",
                    help="also list what links to it - decisions, tasks, runbooks")
    r0.add_argument("--json", action="store_true",
                    help="print the entry as JSON (fields, body, and context if asked)")
    r0.set_defaults(fn=cmd_read)

    n0 = sub.add_parser("init", help="scaffold a new knowledge base elsewhere")
    n0.add_argument("dest")
    n0.add_argument("--name", help="skill name; defaults to the directory name")
    n0.add_argument("--prefix", help="note id prefix; defaults to initials")
    n0.add_argument("--title")
    n0.add_argument("--description", help="what makes the skill trigger")
    n0.add_argument("--force", action="store_true")
    n0.add_argument("--install", action="store_true",
                    help="also link it into your skills directory")
    n0.add_argument("--project", action="store_true",
                    help="with --install, link into ./.claude/skills "
                         "instead of ~/.claude/skills")
    n0.set_defaults(fn=cmd_init)

    v = sub.add_parser("install",
                       help="link this knowledge base into a skills directory")
    v.add_argument("--project", action="store_true",
                   help="link into ./.claude/skills instead of ~/.claude/skills")
    v.set_defaults(fn=cmd_install)

    i = sub.add_parser("issues", help="open defects, worst first")
    i.add_argument("--severity", choices=list(SEVERITY))
    i.add_argument("--repo")
    i.add_argument("--all", action="store_true", help="include settled ones")
    i.set_defaults(fn=cmd_issues)

    d = sub.add_parser("discuss", help="append a comment to a note")
    d.add_argument("id")
    # Optional, and a heredoc is the better way to pass one: -m puts the body
    # through the shell, where backticks become command substitution and the
    # text arrives mangled. `add` has read stdin for exactly this reason.
    d.add_argument("-m", "--message")
    d.add_argument("--who")
    d.set_defaults(fn=cmd_discuss)

    st = sub.add_parser("status", help="set a note's lifecycle status")
    st.add_argument("id")
    st.add_argument("value", help=f"one of: {', '.join(OPEN_STATUS + CLOSED_STATUS)}")
    st.set_defaults(fn=cmd_status)

    ln = sub.add_parser("link", help="add links to an existing note")
    ln.add_argument("id")
    ln.add_argument("targets", nargs="+", help="note ids to link to")
    ln.add_argument("-f", "--force", action="store_true",
                    help="link a target that does not exist yet")
    ln.set_defaults(fn=cmd_link)

    r = sub.add_parser("reindex", help="force a rebuild of the search index")
    r.set_defaults(fn=cmd_reindex)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
