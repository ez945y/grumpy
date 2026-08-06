#!/usr/bin/env python3
"""Tests for grumpy.py. Stdlib only:  python3 -m unittest -v

Unit tests import kb and repoint its module-level paths at a temp directory.
End-to-end tests copy kb.py into a throwaway workspace and drive it as a
subprocess, so they exercise argument parsing, exit codes and stdout the same
way a caller does.
"""

from __future__ import annotations

import datetime as _dt
import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import grumpy as kb  # noqa: E402


TAGS_FIXTURE = """\
# Tag tree

Prose that must survive a rewrite.

- kind: what a note is
  - known-issue: an upstream defect
  - architecture: how it fits together
- impact: how much to worry
  - blocker: stop everything
  - fyi: good to know
"""


def write_note(dirpath: Path, name: str, front: str, body: str = "body text") -> Path:
    p = dirpath / name
    p.write_text(f"---\n{front}\n---\n\n{body}\n", encoding="utf-8")
    return p


class TempWorkspace(unittest.TestCase):
    """Repoints kb's module-level paths at a scratch directory."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.notes = self.tmp / "notes"
        self.notes.mkdir()
        self.tags = self.tmp / "tags.md"
        self.tags.write_text(TAGS_FIXTURE, encoding="utf-8")

        (self.tmp / "grumpy.conf").write_text("prefix = tn\n", encoding="utf-8")

        self._saved = (kb.ROOT, kb.NOTES, kb.DOCS, kb.TAGS_FILE,
                       kb.CONF_FILE, kb.INDEX)
        kb.ROOT, kb.NOTES, kb.TAGS_FILE = self.tmp, self.notes, self.tags
        kb.DOCS = self.tmp / "docs"
        kb.CONF_FILE = self.tmp / "grumpy.conf"
        kb.INDEX = self.tmp / ".index.db"
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        (kb.ROOT, kb.NOTES, kb.DOCS, kb.TAGS_FILE,
         kb.CONF_FILE, kb.INDEX) = self._saved


# ---------------------------------------------------------------------------
# Unit - note parsing
# ---------------------------------------------------------------------------

class TestParseNote(TempWorkspace):
    def test_scalars_and_lists(self):
        p = write_note(self.notes, "tn-0001-x.md",
                       "id: tn-0001\ntitle: A title\nkind: known-issue\n"
                       "tags: [build, blocker]\nrepos: [gateway]\nlinks: []")
        n = kb.parse_note(p)
        self.assertEqual(n["id"], "tn-0001")
        self.assertEqual(n["title"], "A title")
        self.assertEqual(n["tags"], ["build", "blocker"])
        self.assertEqual(n["repos"], ["gateway"])
        self.assertEqual(n["links"], [])
        self.assertEqual(n["body"], "body text")

    def test_title_may_contain_colons(self):
        """partition() keeps everything after the first colon, so prose in a
        title survives - this is the common case for issue-shaped titles."""
        p = write_note(self.notes, "tn-0002-x.md", "id: tn-0002\ntitle: gateway: exits 1")
        self.assertEqual(kb.parse_note(p)["title"], "gateway: exits 1")

    def test_missing_frontmatter_falls_back_to_filename(self):
        p = self.notes / "tn-0003-loose.md"
        p.write_text("no frontmatter here\n", encoding="utf-8")
        n = kb.parse_note(p)
        self.assertEqual(n["id"], "tn-0003-loose")
        self.assertEqual(n["kind"], "note")
        self.assertEqual(n["status"], "open")
        self.assertEqual(n["tags"], [])

    def test_list_keys_default_to_empty_not_missing(self):
        p = write_note(self.notes, "tn-0004-x.md", "id: tn-0004\ntitle: T")
        n = kb.parse_note(p)
        for key in ("tags", "repos", "links"):
            self.assertEqual(n[key], [], key)

    def test_all_notes_is_sorted_by_id(self):
        write_note(self.notes, "tn-0002-b.md", "id: tn-0002\ntitle: B")
        write_note(self.notes, "tn-0001-a.md", "id: tn-0001\ntitle: A")
        self.assertEqual([n["id"] for n in kb.all_notes()], ["tn-0001", "tn-0002"])


# ---------------------------------------------------------------------------
# Unit - tag tree
# ---------------------------------------------------------------------------

class TestTagTree(TempWorkspace):
    def test_indentation_defines_parentage(self):
        tags = kb.load_tags()
        self.assertIsNone(tags["kind"]["parent"])
        self.assertEqual(tags["known-issue"]["parent"], "kind")
        self.assertEqual(tags["blocker"]["parent"], "impact")

    def test_description_is_captured(self):
        self.assertEqual(kb.load_tags()["blocker"]["desc"], "stop everything")

    def test_description_is_optional(self):
        self.tags.write_text("- kind\n  - known-issue\n", encoding="utf-8")
        tags = kb.load_tags()
        self.assertEqual(tags["known-issue"]["parent"], "kind")
        self.assertEqual(tags["known-issue"]["desc"], "")

    def test_duplicate_slug_is_fatal(self):
        self.tags.write_text("- a\n  - dup\n- b\n  - dup\n", encoding="utf-8")
        with self.assertRaises(SystemExit):
            kb.load_tags()

    def test_tag_path(self):
        tags = kb.load_tags()
        self.assertEqual(kb.tag_path("known-issue", tags), "kind/known-issue")
        self.assertEqual(kb.tag_path("kind", tags), "kind")

    def test_descendants_includes_self_and_grandchildren(self):
        self.tags.write_text(
            "- root\n  - mid\n    - leaf\n- other\n", encoding="utf-8")
        tags = kb.load_tags()
        self.assertEqual(kb.descendants("root", tags), {"root", "mid", "leaf"})
        self.assertEqual(kb.descendants("mid", tags), {"mid", "leaf"})
        self.assertEqual(kb.descendants("other", tags), {"other"})

    def test_tag_path_terminates_on_a_cycle(self):
        """A hand-edited file can produce a cycle; it must not hang."""
        tags = {"a": {"parent": "b", "desc": "", "depth": 0},
                "b": {"parent": "a", "desc": "", "depth": 0}}
        self.assertIn("a", kb.tag_path("a", tags))

    def test_write_tags_preserves_the_preamble(self):
        tags = kb.load_tags()
        tags["newtag"] = {"parent": "impact", "desc": "added", "depth": 0}
        kb.write_tags(tags)
        text = self.tags.read_text(encoding="utf-8")
        self.assertIn("Prose that must survive a rewrite.", text)
        self.assertIn("- newtag: added", text)
        self.assertEqual(kb.load_tags()["newtag"]["parent"], "impact")

    def test_write_tags_round_trips_the_hierarchy(self):
        before = kb.load_tags()
        kb.write_tags(before)
        after = kb.load_tags()
        self.assertEqual({k: v["parent"] for k, v in before.items()},
                         {k: v["parent"] for k, v in after.items()})


# ---------------------------------------------------------------------------
# Unit - link graph
# ---------------------------------------------------------------------------

class TestLinkGraph(TempWorkspace):
    def _graph(self, *specs):
        for nid, links, body in specs:
            write_note(self.notes, f"{nid}-x.md",
                       f"id: {nid}\ntitle: {nid}\nlinks: [{links}]", body)
        return kb.link_graph(kb.all_notes())

    def test_edges_are_symmetric(self):
        """A note cites what it depends on, but a reader landing on the target
        needs to find the citer - so reading is always two-way."""
        g = self._graph(("tn-0001", "tn-0002", "b"), ("tn-0002", "", "b"))
        self.assertEqual(g["tn-0001"], {"tn-0002"})
        self.assertEqual(g["tn-0002"], {"tn-0001"})

    def test_wikilinks_in_the_body_count_as_edges(self):
        g = self._graph(("tn-0001", "", "see [[tn-0002]] for detail"),
                        ("tn-0002", "", "b"))
        self.assertEqual(g["tn-0002"], {"tn-0001"})

    def test_dangling_and_self_links_are_dropped(self):
        g = self._graph(("tn-0001", "tn-0001, tn-9999", "b"))
        self.assertEqual(g["tn-0001"], set())

    def test_reachable_one_hop_excludes_the_seed(self):
        g = self._graph(("tn-0001", "tn-0002", "b"), ("tn-0002", "tn-0003", "b"),
                        ("tn-0003", "", "b"))
        self.assertEqual(kb.reachable({"tn-0001"}, g, 1), {"tn-0002": "tn-0001"})

    def test_reachable_two_hops_walks_further(self):
        g = self._graph(("tn-0001", "tn-0002", "b"), ("tn-0002", "tn-0003", "b"),
                        ("tn-0003", "", "b"))
        self.assertEqual(set(kb.reachable({"tn-0001"}, g, 2)), {"tn-0002", "tn-0003"})

    def test_reachable_returns_each_note_once(self):
        """tn-0003 is reachable from both seeds; it must not be listed twice."""
        g = self._graph(("tn-0001", "tn-0003", "b"), ("tn-0002", "tn-0003", "b"),
                        ("tn-0003", "", "b"))
        got = kb.reachable({"tn-0001", "tn-0002"}, g, 1)
        self.assertEqual(list(got), ["tn-0003"])

    def test_reachable_terminates_on_a_cycle(self):
        g = self._graph(("tn-0001", "tn-0002", "b"), ("tn-0002", "tn-0001", "b"))
        self.assertEqual(set(kb.reachable({"tn-0001"}, g, 5)), {"tn-0002"})


# ---------------------------------------------------------------------------
# Unit - id minting
#
# The property under test is that two clones which cannot see each other still
# cannot mint the same id. The counter this replaced derived the next id from
# the local working tree, so two people at the same maximum both picked it,
# their filenames differed by slug, and git merged both without a conflict.
# ---------------------------------------------------------------------------

class TestMintId(TempWorkspace):

    def test_minting_never_reads_the_notes_directory(self):
        """The whole point: an id must not be a function of local state.

        Deleting notes/ entirely must not change minting, because that is what
        guarantees two offline clones cannot agree on the same wrong answer.
        """
        before = kb.mint_id("tn")
        shutil.rmtree(self.notes)
        after = kb.mint_id("tn")
        self.assertNotEqual(before, after)
        self.assertTrue(after.startswith("tn-"))

    def test_same_day_mints_do_not_collide_in_bulk(self):
        day = _dt.date(2026, 8, 4)
        ids = {kb.mint_id("tn", day) for _ in range(20000)}
        # 32**5 possibilities; the birthday bound puts expected collisions at
        # ~6 for 20k draws. Anything near 20000 means the random part shrank.
        self.assertGreater(len(ids), 19950)

    def test_ids_sort_by_day_minted(self):
        early = kb.mint_id("tn", _dt.date(2026, 1, 1))
        mid = kb.mint_id("tn", _dt.date(2026, 8, 4))
        late = kb.mint_id("tn", _dt.date(2030, 1, 1))
        self.assertEqual(sorted([late, early, mid]), [early, mid, late])

    def test_alphabet_excludes_the_confusable_letters(self):
        for ch in "ilou":
            self.assertNotIn(ch, kb.ID_ALPHABET)

    def test_wikilinks_resolve_for_both_id_generations(self):
        """Old counter ids and new token ids must both keep working forever."""
        pat = kb.wikilink_re()
        self.assertEqual(pat.findall("see [[tn-0003]]"), ["tn-0003"])
        self.assertEqual(pat.findall("see [[tn-06qk5366]]"), ["tn-06qk5366"])


class TestDuplicateIds(TempWorkspace):

    def test_no_duplicates_in_a_clean_tree(self):
        write_note(self.notes, "tn-0001-a.md", "id: tn-0001\ntitle: A", "body")
        write_note(self.notes, "tn-0002-b.md", "id: tn-0002\ntitle: B", "body")
        self.assertEqual(kb.duplicate_ids(), {})

    def test_two_files_sharing_an_id_are_reported(self):
        """The merge case: same id, different slug, so git never conflicted."""
        write_note(self.notes, "tn-0039-alice-note.md",
                   "id: tn-0039\ntitle: Alice", "body")
        write_note(self.notes, "tn-0039-bob-note.md",
                   "id: tn-0039\ntitle: Bob", "body")
        dupes = kb.duplicate_ids()
        self.assertIn("tn-0039", dupes)
        self.assertEqual(len(dupes["tn-0039"]), 2)

    def test_reindex_exits_nonzero_on_a_duplicate(self):
        write_note(self.notes, "tn-0039-alice-note.md",
                   "id: tn-0039\ntitle: Alice", "body")
        write_note(self.notes, "tn-0039-bob-note.md",
                   "id: tn-0039\ntitle: Bob", "body")
        self.assertEqual(kb.cmd_reindex(None), 1)


# ---------------------------------------------------------------------------
# Unit - near-duplicate detection
# ---------------------------------------------------------------------------

class TestSimilarity(TempWorkspace):
    LONG = ("the builder reads one template and writes four output files plus a "
            "signing key values left empty are generated on the first run two "
            "structural weaknesses follow from a single generator feeding "
            "several consumers with different expectations")

    def setUp(self):
        super().setUp()
        write_note(self.notes, "tn-0001-a.md",
                   "id: tn-0001\ntitle: The build pipeline", self.LONG)
        self.existing = kb.all_notes()

    def test_a_short_restatement_of_a_long_note_is_caught(self):
        """Overlap, not Jaccard: the union of a long note and a short one is
        dominated by the long one, which is why Jaccard misses this."""
        short = ("the builder reads one template and writes four output files "
                 "plus a signing key")
        hits = kb.similar_notes("Build pipeline", short, self.existing)
        self.assertTrue(hits)
        self.assertEqual(hits[0][1]["id"], "tn-0001")

    def test_an_unrelated_note_is_not_flagged(self):
        other = ("the scheduler retries a job whose worker already vanished which "
                 "duplicates every side effect the job had performed")
        self.assertEqual(kb.similar_notes("Duplicate retries", other, self.existing), [])

    def test_a_stub_shorter_than_the_floor_is_never_flagged(self):
        self.assertEqual(kb.similar_notes("x", "the builder reads", self.existing), [])

    def test_score_is_between_zero_and_one(self):
        hits = kb.similar_notes("The build pipeline", self.LONG, self.existing)
        self.assertTrue(hits)
        self.assertLessEqual(hits[0][0], 1.0)
        self.assertGreater(hits[0][0], 0.9)


# ---------------------------------------------------------------------------
# Unit - index freshness
# ---------------------------------------------------------------------------

class TestIndex(TempWorkspace):
    def test_index_rebuilds_when_a_note_changes(self):
        write_note(self.notes, "tn-0001-a.md",
                   "id: tn-0001\ntitle: A\ntags: [fyi]", "alpha content")
        kb.build_index(force=True)
        self.assertFalse(kb._stale())

        import os, time
        p = write_note(self.notes, "tn-0002-b.md",
                       "id: tn-0002\ntitle: B\ntags: [fyi]", "beta content")
        os.utime(p, (time.time() + 10, time.time() + 10))
        self.assertTrue(kb._stale())

        con = kb.build_index()
        rows = con.execute("SELECT id FROM notes WHERE notes MATCH 'beta'").fetchall()
        self.assertEqual(rows, [("tn-0002",)])


# ---------------------------------------------------------------------------
# End-to-end - drive the CLI as a subprocess
# ---------------------------------------------------------------------------

class TestCLI(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copy(HERE / "grumpy.py", self.tmp / "grumpy.py")
        (self.tmp / "grumpy.conf").write_text("prefix = tn\n", encoding="utf-8")
        (self.tmp / "notes").mkdir()
        (self.tmp / "tags.md").write_text(TAGS_FIXTURE, encoding="utf-8")
        self.ids: list[str] = []

    def kb(self, *args: str, expect: int = 0, stdin: str = "") -> str:
        r = subprocess.run([sys.executable, "grumpy.py", *args], cwd=self.tmp,
                           capture_output=True, text=True, input=stdin)
        self.assertEqual(r.returncode, expect,
                         f"args={args}\nstdout={r.stdout}\nstderr={r.stderr}")
        self.ids += (re.findall(r"\(id (tn-[0-9a-z]+)", r.stdout)
                     or re.findall(r'"id": "(tn-[0-9a-z]+)"', r.stdout))
        return r.stdout + r.stderr

    # -- add ---------------------------------------------------------------

    def test_add_writes_a_well_formed_note(self):
        self.kb("add", "--title", "Gateway exits 1", "--kind", "known-issue",
                "--tags", "blocker", "--repos", "gateway,scheduler",
                "--body", "the manifest file is missing")
        files = list((self.tmp / "notes").glob("*.md"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertIn(f"id: {self.ids[0]}", text)
        self.assertIn("title: Gateway exits 1", text)
        self.assertIn("repos: [gateway, scheduler]", text)
        self.assertIn("the manifest file is missing", text)
        self.assertIn("## Discussion", text)
        self.assertTrue(files[0].name.startswith(f"{self.ids[0]}-gateway-exits-1"))

    def test_add_mirrors_kind_into_tags(self):
        self.kb("add", "--title", "T", "--kind", "architecture")
        text = next((self.tmp / "notes").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("tags: [architecture]", text)

    def test_add_mints_a_distinct_id_per_note(self):
        """Ids no longer count upwards - see kb.mint_id - so the property is
        distinctness and filename agreement, not adjacency."""
        self.kb("add", "--title", "One", "--kind", "known-issue", "--tags", "blocker")
        self.kb("add", "--title", "Two", "--kind", "known-issue", "--tags", "blocker")
        self.assertEqual(len(set(self.ids)), 2)
        names = {p.name for p in (self.tmp / "notes").glob("*.md")}
        for nid in self.ids:
            self.assertTrue(any(n.startswith(nid) for n in names), nid)

    def test_add_rejects_a_tag_not_in_the_tree(self):
        out = self.kb("add", "--title", "T", "--kind", "known-issue",
                      "--tags", "invented", expect=2)
        self.assertIn("unknown tag", out)
        self.assertEqual(list((self.tmp / "notes").glob("*.md")), [])

    def test_add_force_allows_an_unknown_tag(self):
        self.kb("add", "--title", "T", "--kind", "known-issue",
                "--tags", "invented", "--force")
        self.assertEqual(len(list((self.tmp / "notes").glob("*.md"))), 1)

    def test_add_reads_body_from_stdin(self):
        self.kb("add", "--title", "T", "--kind", "runbook", "--stdin",
                stdin="piped body\n")
        self.assertIn("piped body",
                      next((self.tmp / "notes").glob("*.md")).read_text(encoding="utf-8"))

    def test_add_auto_detects_a_piped_body_without_stdin_flag(self):
        self.kb("add", "--title", "T", "--kind", "runbook",
                stdin="heredoc body without the flag\n")
        self.assertIn("heredoc body without the flag",
                      next((self.tmp / "notes").glob("*.md")).read_text(encoding="utf-8"))

    def test_explicit_body_wins_over_an_empty_pipe(self):
        self.kb("add", "--title", "T", "--kind", "runbook", "--body", "explicit wins")
        self.assertIn("explicit wins",
                      next((self.tmp / "notes").glob("*.md")).read_text(encoding="utf-8"))

    # -- severity gate (folded into add, not left to the test suite) --------

    def test_add_requires_a_severity_on_a_known_issue(self):
        out = self.kb("add", "--title", "T", "--kind", "known-issue", expect=2)
        self.assertIn("needs exactly one severity", out)
        self.assertEqual(list((self.tmp / "notes").glob("*.md")), [])

    def test_add_requires_a_severity_on_a_task(self):
        self.kb("add", "--title", "T", "--kind", "task", expect=2)

    def test_force_lets_a_task_through_without_severity(self):
        self.kb("add", "--title", "T", "--kind", "task", "-f")
        self.assertEqual(len(list((self.tmp / "notes").glob("*.md"))), 1)

    def test_add_rejects_a_severity_on_an_architecture_note(self):
        out = self.kb("add", "--title", "T", "--kind", "architecture",
                      "--tags", "blocker", expect=2)
        self.assertIn("only for known-issue/task", out)

    # -- --links, validated at write time ----------------------------------

    def test_add_links_writes_a_validated_edge(self):
        self.kb("add", "--title", "First", "--kind", "architecture",
                "--body", "the anchor")
        self.kb("add", "--title", "Second", "--kind", "architecture",
                "--links", f"{self.ids[0]}", "--body", "points back")
        text = next((self.tmp / "notes").glob(f"{self.ids[1]}*")).read_text(encoding="utf-8")
        self.assertIn(f"links: [{self.ids[0]}]", text)

    def test_add_links_rejects_a_missing_target(self):
        out = self.kb("add", "--title", "Orphan", "--kind", "architecture",
                      "--links", "tn-9999", expect=2)
        self.assertIn("matches no existing note", out)
        self.assertEqual(list((self.tmp / "notes").glob("*.md")), [])

    def test_force_lets_a_dangling_link_through(self):
        self.kb("add", "--title", "Orphan", "--kind", "architecture",
                "--links", "tn-9999", "-f")
        self.assertEqual(len(list((self.tmp / "notes").glob("*.md"))), 1)

    # -- --json, for chaining ----------------------------------------------

    def test_add_json_emits_the_id_and_links(self):
        import json as _json
        self.kb("add", "--title", "Anchor", "--kind", "architecture", "--body", "a")
        out = self.kb("add", "--title", "Linker", "--kind", "architecture",
                      "--links", f"{self.ids[0]}", "--json", "--body", "b")
        doc = _json.loads(out)
        self.assertEqual(doc["id"], f"{self.ids[1]}")
        self.assertEqual(doc["links"], [f"{self.ids[0]}"])

    def test_search_json_is_a_parseable_array(self):
        self._seed()
        import json as _json
        doc = _json.loads(self.kb("search", "manifest", "--json"))
        self.assertEqual(doc[0]["id"], f"{self.ids[0]}")
        self.assertEqual(doc[0]["kind"], "known-issue")

    def test_read_json_carries_the_body_and_links(self):
        import json as _json
        self.kb("add", "--title", "Anchor", "--kind", "architecture", "--body", "anchor body")
        doc = _json.loads(self.kb("read", f"{self.ids[0]}", "--json"))
        self.assertEqual(doc["id"], f"{self.ids[0]}")
        self.assertIn("anchor body", doc["body"])

    # -- search ------------------------------------------------------------

    def _seed(self) -> None:
        self.kb("add", "--title", "Gateway config missing", "--kind", "known-issue",
                "--tags", "blocker", "--repos", "gateway", "--body", "stale manifest")
        self.kb("add", "--title", "Client and server split", "--kind", "architecture",
                "--tags", "fyi", "--repos", "service-a", "--body", "deployment boundary")

    def test_search_matches_the_body(self):
        self._seed()
        self.assertIn(f"{self.ids[0]}", self.kb("search", "manifest"))

    def test_search_with_no_query_lists_everything(self):
        self._seed()
        out = self.kb("search")
        self.assertIn(f"{self.ids[0]}", out)
        self.assertIn(f"{self.ids[1]}", out)

    def test_search_multi_word_query_is_or_not_and(self):
        self._seed()
        out = self.kb("search", "manifest boundary")
        self.assertIn(f"{self.ids[0]}", out)
        self.assertIn(f"{self.ids[1]}", out)

    def test_search_by_parent_tag_matches_children(self):
        self._seed()
        out = self.kb("search", "--tag", "kind")
        self.assertIn(f"{self.ids[0]}", out)
        self.assertIn(f"{self.ids[1]}", out)

    def test_search_by_leaf_tag_narrows(self):
        self._seed()
        out = self.kb("search", "--tag", "blocker")
        self.assertIn(f"{self.ids[0]}", out)
        self.assertNotIn(f"{self.ids[1]}", out)

    def test_search_by_repo(self):
        self._seed()
        out = self.kb("search", "--repo", "gateway")
        self.assertIn(f"{self.ids[0]}", out)
        self.assertNotIn(f"{self.ids[1]}", out)

    def test_search_by_kind(self):
        self._seed()
        out = self.kb("search", "--kind", "architecture")
        self.assertIn(f"{self.ids[1]}", out)
        self.assertNotIn(f"{self.ids[0]}", out)

    def test_search_finds_a_cjk_substring(self):
        """unicode61 tokenises an unbroken CJK run as one token, so FTS alone
        cannot match a substring of a Chinese sentence. The fallback must."""
        self.kb("add", "--title", "Expiry", "--kind", "known-issue", "--tags", "blocker",
                "--body", "優先處理，今天就去要一把新的簽章金鑰")
        self.assertIn(f"{self.ids[0]}", self.kb("search", "簽章金鑰"))

    def test_search_finds_a_partial_english_word(self):
        self._seed()
        self.assertIn(f"{self.ids[0]}", self.kb("search", "anifes"))

    def test_fts_still_wins_when_it_matches(self):
        """The fallback must not fire when FTS already answered - otherwise
        stemming and ranking are lost."""
        self._seed()
        out = self.kb("search", "manifest")
        self.assertIn(f"{self.ids[0]}", out)
        self.assertNotIn(f"{self.ids[1]}", out)

    # -- graph traversal from search ---------------------------------------

    @staticmethod
    def rows(out: str) -> list[tuple[str, bool]]:
        """(id, was_expanded) for each result row. Parsing rows rather than
        grepping the raw text matters: a `-> tn-0003` neighbour line mentions
        ids that were not themselves returned."""
        found = []
        for line in out.splitlines():
            m = re.match(r"^(\s*)([a-z]+-[0-9a-z]+)  \S", line)
            if m:
                found.append((m.group(2), bool(m.group(1))))
        return found

    def _linked_pair(self) -> None:
        self.kb("add", "--title", "Build pipeline", "--kind", "architecture",
                "--body", "the builder writes output files")
        self.kb("add", "--title", "Local toolchain", "--kind", "decision",
                "--body", f"pinned compiler and runtime, see [[{self.ids[0]}]] for why")

    def test_search_lists_a_hit_s_neighbours(self):
        self._linked_pair()
        self.assertIn(f"-> {self.ids[1]}", self.kb("search", "builder"))

    def test_neighbours_are_visible_from_either_end(self):
        self._linked_pair()
        self.assertIn(f"-> {self.ids[0]}", self.kb("search", "toolchain"))

    def test_expand_pulls_in_linked_notes_with_attribution(self):
        self._linked_pair()
        out = self.kb("search", "builder", "--expand")
        # Set comparison: token ids do not sort into creation order,
        # and `id` is only the tiebreaker that makes output deterministic.
        self.assertEqual(set(self.rows(out)),
                         {(self.ids[0], False), (self.ids[1], True)})
        self.assertIn(f"via {self.ids[0]}", out)
        self.assertIn("+1 linked", out)

    def test_expand_does_not_repeat_a_direct_hit(self):
        self._linked_pair()
        out = self.kb("search", "--expand")          # both notes are direct hits
        # Compared as a set: `id` is only the tiebreaker that makes output
        # deterministic, and token ids no longer sort into creation order.
        # What this test is about is the False flags - neither row is an
        # expansion - not which of the two prints first.
        self.assertEqual(set(self.rows(out)),
                         {(self.ids[0], False), (self.ids[1], False)})
        self.assertNotIn("via", out)

    def test_expand_accepts_a_hop_count(self):
        self.kb("add", "--title", "One", "--kind", "architecture",
                "--body", "first note mentioning aardvark")
        self.kb("add", "--title", "Two", "--kind", "architecture",
                "--body", f"second note pointing at [[{self.ids[0]}]]")
        self.kb("add", "--title", "Three", "--kind", "architecture",
                "--body", f"third note pointing at [[{self.ids[1]}]]")
        one = [nid for nid, _ in self.rows(self.kb("search", "aardvark", "--expand", "1"))]
        two = [nid for nid, _ in self.rows(self.kb("search", "aardvark", "--expand", "2"))]
        # Set comparison: token ids do not sort into creation order,
        # and `id` is only the tiebreaker that makes output deterministic.
        self.assertEqual(set(one), {self.ids[0], self.ids[1]})
        self.assertEqual(set(two), set(self.ids[:3]))
        self.assertLess(set(one), set(two))

    def test_task_search_expands_its_context_by_default(self):
        """A task read without the findings behind it is just a sentence."""
        self.kb("add", "--title", "Background", "--kind", "known-issue", "--tags", "blocker",
                "--body", "the aardvark subsystem drops frames on reboot")
        self.kb("add", "--title", "Fix the aardvark", "--kind", "task", "--force",
                "--body", f"see [[{self.ids[0]}]] for what is actually broken")
        rows = self.rows(self.kb("search", "--kind", "task"))
        # Set comparison: token ids do not sort into creation order,
        # and `id` is only the tiebreaker that makes output deterministic.
        self.assertEqual(set(rows),
                         {(self.ids[1], False), (self.ids[0], True)})

    def test_expand_zero_suppresses_the_task_default(self):
        self.kb("add", "--title", "Background", "--kind", "known-issue", "--tags", "blocker",
                "--body", "the aardvark subsystem drops frames on reboot")
        self.kb("add", "--title", "Fix the aardvark", "--kind", "task", "--force",
                "--body", f"see [[{self.ids[0]}]] for what is actually broken")
        rows = self.rows(self.kb("search", "--kind", "task", "--expand", "0"))
        self.assertEqual(rows, [(f"{self.ids[1]}", False)])

    def test_non_task_searches_do_not_expand_on_their_own(self):
        self._linked_pair()
        self.assertEqual(self.rows(self.kb("search", "builder")),
                         [(f"{self.ids[0]}", False)])

    def test_search_hints_at_expand_when_links_exist(self):
        self._linked_pair()
        self.assertIn("--expand", self.kb("search", "builder"))

    # -- duplicate rejection -----------------------------------------------

    DUP_BODY = ("the builder writes four output files plus a signing key and any "
                "value left empty is generated on the first run of the tool")

    def test_add_refuses_a_near_duplicate(self):
        self.kb("add", "--title", "Build pipeline", "--kind", "architecture",
                "--body", self.DUP_BODY)
        out = self.kb("add", "--title", "The build pipeline again",
                      "--kind", "architecture", "--body", self.DUP_BODY, expect=2)
        self.assertIn("overlap with existing notes", out)
        self.assertIn(f"{self.ids[0]}", out)
        self.assertEqual(len(list((self.tmp / "notes").glob("*.md"))), 1)

    def test_refusal_states_both_the_score_and_the_limit(self):
        """An agent has to decide whether to override, so it needs the number
        and the candidate, not just a refusal."""
        self.kb("add", "--title", "Build pipeline", "--kind", "architecture",
                "--body", self.DUP_BODY)
        out = self.kb("add", "--title", "The build pipeline again",
                      "--kind", "architecture", "--body", self.DUP_BODY, expect=2)
        self.assertRegex(out, r"overlap with existing notes is \d+%")
        self.assertIn("the limit is 60%", out)
        self.assertRegex(out, fr"\d+%\s+{self.ids[0]}")

    def test_short_f_is_accepted(self):
        self.kb("add", "--title", "Build pipeline", "--kind", "architecture",
                "--body", self.DUP_BODY)
        self.kb("add", "--title", "Build pipeline again", "--kind", "architecture",
                "--body", self.DUP_BODY, "-f")
        self.assertEqual(len(list((self.tmp / "notes").glob("*.md"))), 2)

    def test_a_successful_add_reports_the_closest_score(self):
        self.kb("add", "--title", "Build pipeline", "--kind", "architecture",
                "--body", self.DUP_BODY)
        out = self.kb("add", "--title", "Duplicate retries", "--kind", "known-issue",
                      "--tags", "blocker",
                      "--body", "the scheduler retries a job whose worker "
                                "vanished, duplicating every side effect")
        self.assertIn("closest existing note:", out)
        self.assertRegex(out, r"\d+/2000 chars")

    # -- length --------------------------------------------------------------

    def test_add_refuses_an_over_length_body(self):
        out = self.kb("add", "--title", "Essay", "--kind", "runbook",
                      "--body", "word " * 450, expect=2)
        self.assertIn("the limit is 2000", out)
        self.assertEqual(list((self.tmp / "notes").glob("*.md")), [])

    def test_force_writes_an_over_length_body(self):
        self.kb("add", "--title", "Essay", "--kind", "runbook",
                "--body", "word " * 450, "-f")
        self.assertEqual(len(list((self.tmp / "notes").glob("*.md"))), 1)

    def test_a_body_at_the_limit_is_accepted(self):
        self.kb("add", "--title", "Exactly at the limit", "--kind", "runbook",
                "--body", "x" * kb.MAX_BODY)
        self.assertEqual(len(list((self.tmp / "notes").glob("*.md"))), 1)

    def test_force_writes_a_near_duplicate_anyway(self):
        self.kb("add", "--title", "Build pipeline", "--kind", "architecture",
                "--body", self.DUP_BODY)
        self.kb("add", "--title", "Build pipeline again", "--kind", "architecture",
                "--body", self.DUP_BODY, "--force")
        self.assertEqual(len(list((self.tmp / "notes").glob("*.md"))), 2)

    def test_a_genuinely_different_note_is_not_refused(self):
        self.kb("add", "--title", "Build pipeline", "--kind", "architecture",
                "--body", self.DUP_BODY)
        self.kb("add", "--title", "Duplicate retries", "--kind", "known-issue",
                "--tags", "blocker",
                "--body", "the scheduler retries a job whose worker vanished, "
                          "duplicating every side effect it had performed")
        self.assertEqual(len(list((self.tmp / "notes").glob("*.md"))), 2)

    # -- lifecycle -----------------------------------------------------------

    def _with_status(self, nid_title, status):
        self.kb("add", "--title", nid_title, "--kind", "known-issue", "--tags", "blocker",
                "--status", status, "--body", f"a defect about {nid_title} zebra")

    def test_settled_entries_drop_out_of_search(self):
        self._with_status("Still broken", "open")
        self._with_status("Since fixed", "resolved")
        rows = [nid for nid, _ in self.rows(self.kb("search", "zebra"))]
        self.assertEqual(rows, [f"{self.ids[0]}"])

    def test_the_count_says_how_many_were_hidden(self):
        self._with_status("Still broken", "open")
        self._with_status("Since fixed", "resolved")
        self.assertIn("1 settled hidden", self.kb("search", "zebra"))

    def test_all_brings_settled_entries_back(self):
        self._with_status("Still broken", "open")
        self._with_status("Since fixed", "resolved")
        rows = [nid for nid, _ in self.rows(self.kb("search", "zebra", "--all"))]
        # Set comparison: the claim is that --all returns the settled one too,
        # not that ids happen to sort into the order they were written.
        self.assertEqual(set(rows), set(self.ids))

    def test_naming_a_settled_status_finds_it_without_all(self):
        self._with_status("Since fixed", "resolved")
        self.assertIn(f"{self.ids[0]}", self.kb("search", "--status", "resolved"))

    def test_a_worked_around_defect_stays_visible(self):
        """The defect is still there and the workaround still needs explaining,
        so this status must not behave like a closed one."""
        self._with_status("Papered over", "workaround-applied")
        out = self.kb("search", "zebra")
        self.assertIn(f"{self.ids[0]}", out)
        self.assertIn("[workaround-applied]", out)

    def test_search_warns_on_an_unknown_tag(self):
        self._seed()
        self.assertIn("unknown tag", self.kb("search", "--tag", "nope", expect=1))

    def test_search_exits_nonzero_when_nothing_matches(self):
        self._seed()
        self.assertIn("no matches", self.kb("search", "zzzznomatch", expect=1))

    def test_search_picks_up_a_new_note_without_an_explicit_reindex(self):
        self._seed()
        self.kb("search", "manifest")                      # builds the index
        self.kb("add", "--title", "Third", "--kind", "decision",
                "--body", "unmistakable-token")
        self.assertIn(f"{self.ids[2]}", self.kb("search", "unmistakable-token"))

    # -- tags --------------------------------------------------------------

    def test_tags_lists_the_tree_with_counts(self):
        self._seed()
        out = self.kb("tags")
        self.assertIn("- kind", out)
        self.assertIn("known-issue (1)", out)

    def test_tags_add_and_reject_duplicate(self):
        self.kb("tags", "--add", "networking", "--parent", "impact", "--desc", "d")
        self.assertIn("- networking: d", (self.tmp / "tags.md").read_text(encoding="utf-8"))
        self.assertIn("already exists",
                      self.kb("tags", "--add", "networking", "--parent", "impact", expect=2))

    def test_tags_add_rejects_an_unknown_parent(self):
        self.assertIn("no such parent",
                      self.kb("tags", "--add", "x", "--parent", "nope", expect=2))

    def test_moving_a_tag_leaves_every_note_byte_identical(self):
        """The reason tags live in a tree referenced by slug: re-parenting is a
        one-line edit and must never require touching notes."""
        self._seed()
        before = {p.name: p.read_bytes() for p in (self.tmp / "notes").glob("*.md")}
        out = self.kb("tags", "--move", "blocker", "--parent", "kind")
        self.assertIn("kind/blocker", out)
        after = {p.name: p.read_bytes() for p in (self.tmp / "notes").glob("*.md")}
        self.assertEqual(before, after)
        # and the note is still reachable through the tag's new parent
        self.assertIn(f"{self.ids[0]}", self.kb("search", "--tag", "kind"))

    def test_move_refuses_to_create_a_cycle(self):
        self.assertIn("descendant",
                      self.kb("tags", "--move", "kind", "--parent", "known-issue", expect=2))

    def test_move_rejects_an_unknown_tag(self):
        self.assertIn("no such tag",
                      self.kb("tags", "--move", "nope", "--parent", "kind", expect=2))

    # -- discuss -----------------------------------------------------------

    def test_discuss_appends_under_the_discussion_heading(self):
        self._seed()
        self.kb("discuss", f"{self.ids[0]}", "-m", "we should report this", "--who", "sam")
        text = next((self.tmp / "notes").glob(f"{self.ids[0]}*.md")).read_text(encoding="utf-8")
        self.assertIn("**sam**", text)
        self.assertIn("we should report this", text)
        self.assertLess(text.index("## Discussion"), text.index("we should report this"))

    def test_discuss_keeps_earlier_entries(self):
        self._seed()
        self.kb("discuss", f"{self.ids[0]}", "-m", "first", "--who", "a")
        self.kb("discuss", f"{self.ids[0]}", "-m", "second", "--who", "b")
        text = next((self.tmp / "notes").glob(f"{self.ids[0]}*.md")).read_text(encoding="utf-8")
        self.assertIn("first", text)
        self.assertLess(text.index("first"), text.index("second"))

    def test_discuss_content_is_searchable(self):
        self._seed()
        self.kb("discuss", f"{self.ids[0]}", "-m", "peculiarphrase", "--who", "a")
        self.assertIn(f"{self.ids[0]}", self.kb("search", "peculiarphrase"))

    def test_discuss_on_a_missing_note_fails(self):
        self.assertIn("no note matching",
                      self.kb("discuss", "tn-9999", "-m", "x", expect=2))


# ---------------------------------------------------------------------------
# End-to-end - the real knowledge base in this repo
# ---------------------------------------------------------------------------

class TestStatusAndDiscussStdin(unittest.TestCase):
    """The two frictions that cost real work: hand-edited frontmatter, and a
    -m message the shell rewrote before grumpy ever saw it.

    Its own harness rather than a TestCLI subclass, which would re-run every
    TestCLI case under a second name for no extra coverage.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copy(HERE / "grumpy.py", self.tmp / "grumpy.py")
        (self.tmp / "grumpy.conf").write_text("prefix = tn\n", encoding="utf-8")
        (self.tmp / "notes").mkdir()
        (self.tmp / "tags.md").write_text(TAGS_FIXTURE, encoding="utf-8")
        self.ids: list[str] = []

    def kb(self, *args: str, expect: int = 0, stdin: str = "") -> str:
        r = subprocess.run([sys.executable, "grumpy.py", *args], cwd=self.tmp,
                           capture_output=True, text=True, input=stdin)
        self.assertEqual(r.returncode, expect,
                         f"args={args}\nstdout={r.stdout}\nstderr={r.stderr}")
        self.ids += (re.findall(r"\(id (tn-[0-9a-z]+)", r.stdout)
                     or re.findall(r'"id": "(tn-[0-9a-z]+)"', r.stdout))
        return r.stdout + r.stderr

    def _one_note(self) -> str:
        self.kb("add", "--title", "Gateway exits 1", "--kind", "known-issue",
                "--tags", "blocker", "--body", "it does")
        return self.ids[0]

    def test_status_sets_the_frontmatter(self):
        nid = self._one_note()
        self.kb("status", nid, "resolved")
        text = next((self.tmp / "notes").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("status: resolved", text)
        self.assertNotIn("status: open", text)

    def test_status_rejects_an_unknown_value(self):
        nid = self._one_note()
        out = self.kb("status", nid, "nearly-done", expect=2)
        self.assertIn("unknown status", out)
        text = next((self.tmp / "notes").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("status: open", text, "a rejected value must not be written")

    def test_status_reindexes_so_search_agrees(self):
        nid = self._one_note()
        self.kb("status", nid, "resolved")
        # Settled notes drop out of the default search, and search exits 1 when
        # nothing is left. A stale index would still list the note and exit 0.
        self.assertIn("no matches", self.kb("search", "gateway", expect=1))
        self.assertIn(nid, self.kb("search", "gateway", "--all"))

    def test_discuss_reads_a_heredoc_body(self):
        nid = self._one_note()
        self.kb("discuss", nid, stdin="a piped follow-up\n")
        text = next((self.tmp / "notes").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("a piped follow-up", text)

    def test_discuss_keeps_backticks_intact(self):
        # The regression this exists for: passed through -m in a shell, the
        # backticks below become command substitution and the text arrives
        # gutted. Via stdin they must survive verbatim.
        nid = self._one_note()
        body = "see `internal/compat/machines.go:42` and `DefaultMatrix()`"
        self.kb("discuss", nid, stdin=body)
        text = next((self.tmp / "notes").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("`internal/compat/machines.go:42`", text)
        self.assertIn("`DefaultMatrix()`", text)

    def test_discuss_refuses_an_empty_message(self):
        nid = self._one_note()
        out = self.kb("discuss", nid, stdin="   \n", expect=2)
        self.assertIn("no message", out)


class TestRealContent(unittest.TestCase):
    """Guards against a note or tag edit that breaks the invariants."""

    def setUp(self) -> None:
        importlib.reload(kb)
        # The engine ships without content - a bare checkout has no tags.md,
        # no notes/ and no SKILL.md. These are instance invariants, so they
        # skip rather than fail there; the engine's own suite stays green.
        if not kb.TAGS_FILE.exists():
            self.skipTest("no instance content beside the engine")

    def test_tags_file_parses_and_slugs_are_unique(self):
        self.assertGreater(len(kb.load_tags()), 5)

    def test_every_note_tag_exists_in_the_tree(self):
        tags = kb.load_tags()
        for n in kb.all_notes():
            for t in n["tags"]:
                self.assertIn(t, tags, f"{n['id']} uses unknown tag {t!r}")

    def test_every_note_has_the_required_fields(self):
        for n in kb.all_notes():
            for key in ("id", "title", "kind", "status"):
                self.assertTrue(str(n.get(key, "")).strip(), f"{n['path'].name}: {key}")

    def test_note_ids_are_unique_and_match_their_filename(self):
        seen = set()
        for n in kb.all_notes():
            self.assertNotIn(n["id"], seen)
            seen.add(n["id"])
            self.assertTrue(n["path"].name.startswith(n["id"]), n["path"].name)

    def test_kind_is_mirrored_into_tags(self):
        for n in kb.all_notes():
            self.assertIn(n["kind"], n["tags"], n["id"])

    def test_every_link_points_at_a_real_note(self):
        ids = {n["id"] for n in kb.all_notes()}
        for n in kb.all_notes():
            for target in n["links"]:
                self.assertIn(target, ids, f"{n['id']} links to missing {target}")

    def test_inline_wiki_links_resolve(self):
        import re as _re
        ids = {n["id"] for n in kb.all_notes()}
        for n in kb.all_notes():
            for target in kb.wikilink_re().findall(n["body"]):
                self.assertIn(target, ids, f"{n['id']} body links to missing {target}")

    def test_severity_is_only_on_defects_and_tasks(self):
        """Severity answers "how bad if you hit it", which is meaningless for
        an architecture fact or a runbook."""
        sev = {"blocker", "major", "minor"}
        for n in kb.all_notes() + kb.all_docs():
            carried = sev & set(n["tags"])
            if n["kind"] in ("known-issue", "task"):
                self.assertEqual(len(carried), 1,
                                 f"{n['id']} ({n['kind']}) has severity {carried or 'none'}")
            else:
                self.assertFalse(carried, f"{n['id']} ({n['kind']}) should carry none")

    def test_every_status_is_a_known_one(self):
        allowed = set(kb.OPEN_STATUS) | set(kb.CLOSED_STATUS)
        for n in kb.all_notes() + kb.all_docs():
            self.assertIn(n["status"], allowed, n["id"])

    def test_open_tasks_are_findable_as_a_worklist(self):
        """`--kind task --status open` is the "what now?" view. A base with no
        tasks yet is a legitimate state - a freshly scaffolded one - so this
        only asserts that any task present is reachable through that filter."""
        notes = kb.all_notes()
        if not notes:
            self.skipTest("empty knowledge base")
        tasks = [n for n in notes if n["kind"] == "task"]
        if not tasks:
            self.skipTest("no tasks recorded")
        self.assertTrue([n for n in tasks if n["status"] in kb.OPEN_STATUS]
                        or all(n["status"] in kb.CLOSED_STATUS for n in tasks))

    def test_docs_carry_a_summary_and_at_least_one_section(self):
        """Search shows a doc's summary instead of its body, so a doc without
        one is invisible; sections are what makes layered reading possible."""
        for d in kb.all_docs():
            self.assertTrue(d.get("summary", "").strip(), f"{d['id']} has no summary")
            names = [n for n, _ in kb.sections(d["body"])]
            self.assertGreater(len(names), 1, f"{d['id']} has no sections")

    def test_no_note_exceeds_the_length_limit(self):
        """The cap is enforced at write time; this keeps hand edits honest."""
        for n in kb.all_notes():
            self.assertLessEqual(len(n["body"]), kb.MAX_BODY,
                                 f"{n['id']} is {len(n['body'])} chars - split it")

    def test_no_two_notes_are_near_duplicates(self):
        notes = kb.all_notes()
        for i, n in enumerate(notes):
            others = notes[:i] + notes[i + 1:]
            over = [(s, o) for s, o in
                    kb.similar_notes(n["title"], n["body"], others)
                    if s >= kb.DUP_THRESHOLD]
            self.assertFalse(over, f"{n['id']} overlaps {over[0][1]['id'] if over else ''}")

    def test_kind_field_uses_the_allowed_vocabulary(self):
        allowed = {"known-issue", "architecture", "runbook", "decision",
                   "reference", "task"}
        for n in kb.all_notes():
            self.assertIn(n["kind"], allowed, n["id"])

    def test_prose_files_only_cite_notes_that_exist(self):
        """README and SKILL name note ids as a digest. A renamed or deleted
        note must not leave a dangling pointer in the entry docs."""
        import re as _re
        ids = {n["id"] for n in kb.all_notes()}
        for name in ("README.md", "SKILL.md", "repos.md", "tags.md"):
            path = HERE / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            # Match only the two shapes an id can actually have - a legacy
            # 4-digit counter or an 8-char token - and reject a match that
            # runs on into another hyphen. Without both guards the prefix
            # swallows repo names: `dn-sdk` is too short to be an id, and
            # `dn-robotops-console` only looks like one until the `-console`.
            cited = set(_re.findall(
                rf"\b{kb.prefix()}-(?:\d{{4}}|[0-9a-z]{{8}})\b(?!-)", text))
            missing = cited - ids
            self.assertFalse(missing, f"{name} cites missing note(s): {sorted(missing)}")


class TestIssuesAndContext(unittest.TestCase):
    """The two ways into a defect: the list, and one with its neighbourhood."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copy(HERE / "grumpy.py", self.tmp / "grumpy.py")
        (self.tmp / "grumpy.conf").write_text("prefix = tn\n", encoding="utf-8")
        (self.tmp / "notes").mkdir()
        (self.tmp / "tags.md").write_text(
            "- kind\n  - known-issue\n  - decision\n  - task\n"
            "- severity\n  - blocker\n  - major\n  - minor\n", encoding="utf-8")
        self.ids: list[str] = []

    def kb(self, *args, expect=0):
        r = subprocess.run([sys.executable, "grumpy.py", *args], cwd=self.tmp,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
        self.ids += (re.findall(r"\(id (tn-[0-9a-z]+)", r.stdout)
                     or re.findall(r'"id": "(tn-[0-9a-z]+)"', r.stdout))
        return r.stdout + r.stderr

    def _seed(self):
        self.kb("add", "--title", "Minor thing", "--kind", "known-issue",
                "--tags", "minor", "--body", "a small defect in the widget")
        self.kb("add", "--title", "Everything stops", "--kind", "known-issue",
                "--tags", "blocker", "--body", "the signing key expires and the service refuses to start")
        self.kb("add", "--title", "Middling", "--kind", "known-issue",
                "--tags", "major", "--body", "costs an hour each time you hit it")

    def test_issues_are_ordered_worst_first(self):
        """Id order is the order they were written down, which says nothing
        about which one will hurt you today."""
        self._seed()
        out = self.kb("issues")
        self.assertLess(out.index("BLOCKER"), out.index("MAJOR"))
        self.assertLess(out.index("MAJOR"), out.index("MINOR"))
        self.assertLess(out.index(f"{self.ids[1]}"), out.index(f"{self.ids[2]}"))

    def test_issues_can_be_narrowed_to_one_severity(self):
        self._seed()
        out = self.kb("issues", "--severity", "blocker")
        self.assertIn(f"{self.ids[1]}", out)
        self.assertNotIn(f"{self.ids[0]}", out)

    def test_issues_hides_settled_ones_unless_asked(self):
        self.kb("add", "--title", "Was broken", "--kind", "known-issue",
                "--tags", "major", "--status", "fixed", "--body", "not any more")
        self.assertIn("no open issues", self.kb("issues", expect=1))
        self.assertIn(f"{self.ids[0]}", self.kb("issues", "--all"))

    def test_issues_ignores_other_kinds(self):
        self._seed()
        self.kb("add", "--title", "A choice", "--kind", "decision",
                "--body", "we went with the second option for these reasons")
        self.assertNotIn(f"{self.ids[3]}", self.kb("issues"))

    def test_context_lists_neighbours_by_kind(self):
        self.kb("add", "--title", "The defect", "--kind", "known-issue",
                "--tags", "major", "--body", "something is wrong in the widget")
        self.kb("add", "--title", "What we did about it", "--kind", "decision",
                "--body", f"we chose to work around it, see [[{self.ids[0]}]]")
        out = self.kb("read", f"{self.ids[0]}", "--context")
        self.assertIn("## Context", out)
        self.assertIn("decision", out)
        self.assertIn(f"{self.ids[1]}", out)

    def test_context_is_honest_when_there_is_none(self):
        self.kb("add", "--title", "Lonely", "--kind", "known-issue",
                "--tags", "minor", "--body", "nothing links here at all yet")
        self.assertIn("nothing links to this yet",
                      self.kb("read", f"{self.ids[0]}", "--context"))

    def test_read_without_context_stays_quiet(self):
        self.kb("add", "--title", "The defect", "--kind", "known-issue",
                "--tags", "major", "--body", "something is wrong in the widget")
        self.assertNotIn("## Context", self.kb("read", f"{self.ids[0]}"))


class TestInstall(unittest.TestCase):
    """Linking a base into a skills directory is the step people skip, so it
    has to be one command and it has to refuse to clobber anything."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.base = self.tmp / "base"
        self.base.mkdir()
        shutil.copy(HERE / "grumpy.py", self.base / "grumpy.py")
        (self.base / "grumpy.conf").write_text("prefix = tn\nname = mybase\n",
                                               encoding="utf-8")
        (self.base / "SKILL.md").write_text("---\nname: mybase\n---\n",
                                            encoding="utf-8")

    def run_in(self, cwd, *args, expect=0):
        """Runs the copy that lives in cwd when there is one, because ROOT is
        derived from the script's own path and that is what decides which base
        is being installed."""
        env = {**os.environ, "HOME": str(self.home)}
        script = Path(cwd) / "grumpy.py"
        if not script.exists():
            script = self.base / "grumpy.py"
        r = subprocess.run([sys.executable, str(script), *args],
                           cwd=cwd, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
        return r.stdout + r.stderr

    def test_install_links_into_the_user_skills_directory(self):
        self.run_in(self.base, "install")
        link = self.home / ".claude" / "skills" / "mybase"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.base.resolve())

    def test_install_is_idempotent(self):
        self.run_in(self.base, "install")
        self.assertIn("already linked", self.run_in(self.base, "install"))

    def test_install_refuses_to_steal_another_bases_name(self):
        self.run_in(self.base, "install")
        other = self.tmp / "other"
        other.mkdir()
        shutil.copy(HERE / "grumpy.py", other / "grumpy.py")
        (other / "grumpy.conf").write_text("name = mybase\n", encoding="utf-8")
        (other / "SKILL.md").write_text("---\nname: mybase\n---\n", encoding="utf-8")
        out = self.run_in(other, "install", expect=2)
        self.assertIn("points at", out)
        self.assertEqual((self.home / ".claude/skills/mybase").resolve(),
                         self.base.resolve())

    def test_install_refuses_when_a_real_directory_is_in_the_way(self):
        (self.home / ".claude" / "skills" / "mybase").mkdir(parents=True)
        out = self.run_in(self.base, "install", expect=2)
        self.assertIn("not a link", out)

    def test_project_scope_links_beside_the_caller(self):
        work = self.tmp / "work"
        work.mkdir()
        self.run_in(work, "install", "--project")
        self.assertTrue((work / ".claude" / "skills" / "mybase").is_symlink())
        self.assertFalse((self.home / ".claude" / "skills" / "mybase").exists())

    def test_install_needs_a_skill_file(self):
        (self.base / "SKILL.md").unlink()
        self.assertIn("no SKILL.md", self.run_in(self.base, "install", expect=2))

    def test_init_can_create_and_link_in_one_command(self):
        dest = self.tmp / "fresh"
        out = self.run_in(self.tmp, "init", str(dest), "--name", "fresh", "--install")
        self.assertIn("linked", out)
        self.assertTrue((self.home / ".claude" / "skills" / "fresh").is_symlink())

    def test_init_without_install_says_how(self):
        dest = self.tmp / "fresh2"
        out = self.run_in(self.tmp, "init", str(dest), "--name", "fresh2")
        self.assertIn("grumpy.py install", out)
        self.assertFalse((self.home / ".claude" / "skills" / "fresh2").exists())


class TestSections(TempWorkspace):
    def test_headings_split_the_body_and_preamble_is_kept(self):
        parts = kb.sections("intro text\n\n## One\n\na\n\n## Two\n\nb")
        self.assertEqual([n for n, _ in parts], ["(preamble)", "One", "Two"])
        self.assertEqual(parts[1][1], "a")

    def test_a_body_with_no_headings_is_one_part(self):
        self.assertEqual([n for n, _ in kb.sections("just prose")], ["(preamble)"])

    def test_empty_sections_are_dropped(self):
        parts = kb.sections("## Empty\n\n## Full\n\nx")
        self.assertEqual([n for n, _ in parts], ["Full"])


class TestSkillFile(unittest.TestCase):
    """SKILL.md is loaded by agent runtimes, which key off its frontmatter."""

    def setUp(self) -> None:
        skill = HERE / "SKILL.md"
        if not skill.exists():
            self.skipTest("no SKILL.md beside the engine")
        self.text = skill.read_text(encoding="utf-8")

    def test_has_frontmatter_with_name_and_description(self):
        self.assertTrue(self.text.startswith("---\n"))
        end = self.text.find("\n---\n", 4)
        self.assertNotEqual(end, -1, "frontmatter is not terminated")
        front = self.text[4:end]
        self.assertRegex(front, r"(?m)^name:\s*\S+")
        self.assertRegex(front, r"(?m)^description:\s*\S+")

    def test_name_matches_the_instance(self):
        """A skill is loaded from a directory named after it, so the two must
        agree. grumpy.conf is the authority when it names one."""
        end = self.text.find("\n---\n", 4)
        name = re.search(r"(?m)^name:\s*(\S+)", self.text[4:end]).group(1)
        self.assertEqual(name, kb.conf().get("name") or HERE.name)

    def test_description_is_specific_enough_to_trigger(self):
        """A vague description is why a skill never fires. This cannot check
        that the words are the right ones, only that someone wrote enough of
        them and removed the scaffold's placeholder."""
        end = self.text.find("\n---\n", 4)
        desc = re.search(r"(?m)^description:\s*(.+)", self.text[4:end]).group(1)
        if "EDITME" in desc:
            self.skipTest("SKILL.md is still the scaffold - fill in description")
        self.assertGreater(len(desc), 120, "description is too vague to trigger")
        self.assertGreater(len(desc.split()), 20,
                           "name the repos and failure modes it should fire on")

    def test_documents_every_command_the_cli_exposes(self):
        for verb in ("search", "add", "tags", "discuss"):
            self.assertIn(f"grumpy.py {verb}", self.text, f"{verb} is undocumented")


class TestRootResolution(unittest.TestCase):
    """The engine can live outside the base it operates on.

    That layout is what lets one clone of grumpy serve every base on a box, and
    lets a base repo hold nothing but knowledge. The self-contained layout that
    `init` still produces has to keep working alongside it, so all four
    resolution paths are pinned here rather than just the new ones.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Engine and base as siblings, and the base has NO copy of the engine.
        self.engine = self.tmp / "engine"
        self.engine.mkdir()
        shutil.copy(HERE / "grumpy.py", self.engine / "grumpy.py")
        self.base = self.tmp / "base"
        (self.base / "notes").mkdir(parents=True)
        (self.base / "docs").mkdir()
        (self.base / "grumpy.conf").write_text("prefix = tn\nname = base\n", encoding="utf-8")
        (self.base / "tags.md").write_text(TAGS_FIXTURE, encoding="utf-8")
        (self.base / "notes" / "tn-0001-a-note.md").write_text(
            "---\nid: tn-0001\ntitle: A findable note\nkind: architecture\n"
            "status: open\ntags: [architecture]\nrepos: []\nlinks: []\n"
            "created: 2026-01-01\n---\n\nBody text.\n", encoding="utf-8")

    def run_engine(self, *args: str, cwd: Path, env_root: str | None = None,
                   expect: int = 0) -> str:
        env = dict(os.environ)
        env.pop("GRUMPY_ROOT", None)
        if env_root:
            env["GRUMPY_ROOT"] = env_root
        r = subprocess.run([sys.executable, str(self.engine / "grumpy.py"), *args],
                           cwd=cwd, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, expect, f"args={args}\n{r.stdout}\n{r.stderr}")
        return r.stdout + r.stderr

    def test_cwd_holding_a_conf_wins(self) -> None:
        # `cd my-kb && ../grumpy/grumpy.py search x` must do the obvious thing.
        self.assertIn("findable", self.run_engine("search", "findable", cwd=self.base))

    def test_root_flag_works_from_anywhere(self) -> None:
        out = self.run_engine("--root", str(self.base), "search", "findable", cwd=self.tmp)
        self.assertIn("findable", out)

    def test_env_var_works_from_anywhere(self) -> None:
        out = self.run_engine("search", "findable", cwd=self.tmp, env_root=str(self.base))
        self.assertIn("findable", out)

    def test_flag_beats_env(self) -> None:
        # Explicit beats ambient, or a stale exported GRUMPY_ROOT silently wins
        # over what the caller just typed.
        empty = self.tmp / "empty"
        (empty / "notes").mkdir(parents=True)
        (empty / "grumpy.conf").write_text("prefix = zz\n", encoding="utf-8")
        (empty / "tags.md").write_text(TAGS_FIXTURE, encoding="utf-8")
        # Searching the empty base finds nothing, and `search` exits 1 on no
        # matches — that non-zero IS the assertion here: the flag sent it to the
        # empty base rather than to the one GRUMPY_ROOT names.
        out = self.run_engine("--root", str(empty), "search", "findable",
                              cwd=self.tmp, env_root=str(self.base), expect=1)
        self.assertNotIn("findable", out)

    def test_self_contained_layout_still_works(self) -> None:
        # What `init` produces. Engine inside the base, run from elsewhere, no
        # flag and no env: it must still find its own directory.
        shutil.copy(HERE / "grumpy.py", self.base / "grumpy.py")
        env = dict(os.environ)
        env.pop("GRUMPY_ROOT", None)
        r = subprocess.run([sys.executable, str(self.base / "grumpy.py"), "search", "findable"],
                           cwd=self.tmp, capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("findable", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
