#!/usr/bin/env python3
"""Tests for grumpy.py. Stdlib only:  python3 -m unittest -v

Unit tests import kb and repoint its module-level paths at a temp directory.
End-to-end tests copy kb.py into a throwaway workspace and drive it as a
subprocess, so they exercise argument parsing, exit codes and stdout the same
way a caller does.
"""

from __future__ import annotations

import importlib
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

    def kb(self, *args: str, expect: int = 0, stdin: str = "") -> str:
        r = subprocess.run([sys.executable, "grumpy.py", *args], cwd=self.tmp,
                           capture_output=True, text=True, input=stdin)
        self.assertEqual(r.returncode, expect,
                         f"args={args}\nstdout={r.stdout}\nstderr={r.stderr}")
        return r.stdout + r.stderr

    # -- add ---------------------------------------------------------------

    def test_add_writes_a_well_formed_note(self):
        self.kb("add", "--title", "Gateway exits 1", "--kind", "known-issue",
                "--tags", "blocker", "--repos", "gateway,scheduler",
                "--body", "the manifest file is missing")
        files = list((self.tmp / "notes").glob("*.md"))
        self.assertEqual(len(files), 1)
        text = files[0].read_text(encoding="utf-8")
        self.assertIn("id: tn-0001", text)
        self.assertIn("title: Gateway exits 1", text)
        self.assertIn("repos: [gateway, scheduler]", text)
        self.assertIn("the manifest file is missing", text)
        self.assertIn("## Discussion", text)
        self.assertTrue(files[0].name.startswith("tn-0001-gateway-exits-1"))

    def test_add_mirrors_kind_into_tags(self):
        self.kb("add", "--title", "T", "--kind", "architecture")
        text = next((self.tmp / "notes").glob("*.md")).read_text(encoding="utf-8")
        self.assertIn("tags: [architecture]", text)

    def test_add_increments_the_id(self):
        self.kb("add", "--title", "One", "--kind", "known-issue")
        self.kb("add", "--title", "Two", "--kind", "known-issue")
        names = sorted(p.name for p in (self.tmp / "notes").glob("*.md"))
        self.assertTrue(names[0].startswith("tn-0001"))
        self.assertTrue(names[1].startswith("tn-0002"))

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

    # -- search ------------------------------------------------------------

    def _seed(self) -> None:
        self.kb("add", "--title", "Gateway config missing", "--kind", "known-issue",
                "--tags", "blocker", "--repos", "gateway", "--body", "stale manifest")
        self.kb("add", "--title", "Client and server split", "--kind", "architecture",
                "--tags", "fyi", "--repos", "service-a", "--body", "deployment boundary")

    def test_search_matches_the_body(self):
        self._seed()
        self.assertIn("tn-0001", self.kb("search", "manifest"))

    def test_search_with_no_query_lists_everything(self):
        self._seed()
        out = self.kb("search")
        self.assertIn("tn-0001", out)
        self.assertIn("tn-0002", out)

    def test_search_multi_word_query_is_or_not_and(self):
        self._seed()
        out = self.kb("search", "manifest boundary")
        self.assertIn("tn-0001", out)
        self.assertIn("tn-0002", out)

    def test_search_by_parent_tag_matches_children(self):
        self._seed()
        out = self.kb("search", "--tag", "kind")
        self.assertIn("tn-0001", out)
        self.assertIn("tn-0002", out)

    def test_search_by_leaf_tag_narrows(self):
        self._seed()
        out = self.kb("search", "--tag", "blocker")
        self.assertIn("tn-0001", out)
        self.assertNotIn("tn-0002", out)

    def test_search_by_repo(self):
        self._seed()
        out = self.kb("search", "--repo", "gateway")
        self.assertIn("tn-0001", out)
        self.assertNotIn("tn-0002", out)

    def test_search_by_kind(self):
        self._seed()
        out = self.kb("search", "--kind", "architecture")
        self.assertIn("tn-0002", out)
        self.assertNotIn("tn-0001", out)

    def test_search_finds_a_cjk_substring(self):
        """unicode61 tokenises an unbroken CJK run as one token, so FTS alone
        cannot match a substring of a Chinese sentence. The fallback must."""
        self.kb("add", "--title", "Expiry", "--kind", "known-issue",
                "--body", "優先處理，今天就去要一把新的簽章金鑰")
        self.assertIn("tn-0001", self.kb("search", "簽章金鑰"))

    def test_search_finds_a_partial_english_word(self):
        self._seed()
        self.assertIn("tn-0001", self.kb("search", "anifes"))

    def test_fts_still_wins_when_it_matches(self):
        """The fallback must not fire when FTS already answered - otherwise
        stemming and ranking are lost."""
        self._seed()
        out = self.kb("search", "manifest")
        self.assertIn("tn-0001", out)
        self.assertNotIn("tn-0002", out)

    # -- graph traversal from search ---------------------------------------

    @staticmethod
    def rows(out: str) -> list[tuple[str, bool]]:
        """(id, was_expanded) for each result row. Parsing rows rather than
        grepping the raw text matters: a `-> tn-0003` neighbour line mentions
        ids that were not themselves returned."""
        found = []
        for line in out.splitlines():
            m = re.match(r"^(\s*)([a-z]+-\d+)  \S", line)
            if m:
                found.append((m.group(2), bool(m.group(1))))
        return found

    def _linked_pair(self) -> None:
        self.kb("add", "--title", "Build pipeline", "--kind", "architecture",
                "--body", "the builder writes output files")
        self.kb("add", "--title", "Local toolchain", "--kind", "decision",
                "--body", "pinned compiler and runtime, see [[tn-0001]] for why")

    def test_search_lists_a_hit_s_neighbours(self):
        self._linked_pair()
        self.assertIn("-> tn-0002", self.kb("search", "builder"))

    def test_neighbours_are_visible_from_either_end(self):
        self._linked_pair()
        self.assertIn("-> tn-0001", self.kb("search", "toolchain"))

    def test_expand_pulls_in_linked_notes_with_attribution(self):
        self._linked_pair()
        out = self.kb("search", "builder", "--expand")
        self.assertEqual(self.rows(out), [("tn-0001", False), ("tn-0002", True)])
        self.assertIn("via tn-0001", out)
        self.assertIn("+1 linked", out)

    def test_expand_does_not_repeat_a_direct_hit(self):
        self._linked_pair()
        out = self.kb("search", "--expand")          # both notes are direct hits
        self.assertEqual(self.rows(out), [("tn-0001", False), ("tn-0002", False)])
        self.assertNotIn("via", out)

    def test_expand_accepts_a_hop_count(self):
        self.kb("add", "--title", "One", "--kind", "architecture",
                "--body", "first note mentioning aardvark")
        self.kb("add", "--title", "Two", "--kind", "architecture",
                "--body", "second note pointing at [[tn-0001]]")
        self.kb("add", "--title", "Three", "--kind", "architecture",
                "--body", "third note pointing at [[tn-0002]]")
        one = [nid for nid, _ in self.rows(self.kb("search", "aardvark", "--expand", "1"))]
        two = [nid for nid, _ in self.rows(self.kb("search", "aardvark", "--expand", "2"))]
        self.assertEqual(one, ["tn-0001", "tn-0002"])
        self.assertEqual(two, ["tn-0001", "tn-0002", "tn-0003"])

    def test_task_search_expands_its_context_by_default(self):
        """A task read without the findings behind it is just a sentence."""
        self.kb("add", "--title", "Background", "--kind", "known-issue",
                "--body", "the aardvark subsystem drops frames on reboot")
        self.kb("add", "--title", "Fix the aardvark", "--kind", "task", "--force",
                "--body", "see [[tn-0001]] for what is actually broken")
        rows = self.rows(self.kb("search", "--kind", "task"))
        self.assertEqual(rows, [("tn-0002", False), ("tn-0001", True)])

    def test_expand_zero_suppresses_the_task_default(self):
        self.kb("add", "--title", "Background", "--kind", "known-issue",
                "--body", "the aardvark subsystem drops frames on reboot")
        self.kb("add", "--title", "Fix the aardvark", "--kind", "task", "--force",
                "--body", "see [[tn-0001]] for what is actually broken")
        rows = self.rows(self.kb("search", "--kind", "task", "--expand", "0"))
        self.assertEqual(rows, [("tn-0002", False)])

    def test_non_task_searches_do_not_expand_on_their_own(self):
        self._linked_pair()
        self.assertEqual(self.rows(self.kb("search", "builder")),
                         [("tn-0001", False)])

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
        self.assertIn("tn-0001", out)
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
        self.assertRegex(out, r"\d+%\s+tn-0001")

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
                      "--body", "the scheduler retries a job whose worker "
                                "vanished, duplicating every side effect")
        self.assertIn("closest existing note:", out)
        self.assertRegex(out, r"\(\d+/2000 chars\)")

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
                "--body", "the scheduler retries a job whose worker vanished, "
                          "duplicating every side effect it had performed")
        self.assertEqual(len(list((self.tmp / "notes").glob("*.md"))), 2)

    # -- lifecycle -----------------------------------------------------------

    def _with_status(self, nid_title, status):
        self.kb("add", "--title", nid_title, "--kind", "known-issue",
                "--status", status, "--body", f"a defect about {nid_title} zebra")

    def test_settled_entries_drop_out_of_search(self):
        self._with_status("Still broken", "open")
        self._with_status("Since fixed", "resolved")
        rows = [nid for nid, _ in self.rows(self.kb("search", "zebra"))]
        self.assertEqual(rows, ["tn-0001"])

    def test_the_count_says_how_many_were_hidden(self):
        self._with_status("Still broken", "open")
        self._with_status("Since fixed", "resolved")
        self.assertIn("1 settled hidden", self.kb("search", "zebra"))

    def test_all_brings_settled_entries_back(self):
        self._with_status("Still broken", "open")
        self._with_status("Since fixed", "resolved")
        rows = [nid for nid, _ in self.rows(self.kb("search", "zebra", "--all"))]
        self.assertEqual(rows, ["tn-0001", "tn-0002"])

    def test_naming_a_settled_status_finds_it_without_all(self):
        self._with_status("Since fixed", "resolved")
        self.assertIn("tn-0001", self.kb("search", "--status", "resolved"))

    def test_a_worked_around_defect_stays_visible(self):
        """The defect is still there and the workaround still needs explaining,
        so this status must not behave like a closed one."""
        self._with_status("Papered over", "workaround-applied")
        out = self.kb("search", "zebra")
        self.assertIn("tn-0001", out)
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
        self.assertIn("tn-0003", self.kb("search", "unmistakable-token"))

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
        self.assertIn("tn-0001", self.kb("search", "--tag", "kind"))

    def test_move_refuses_to_create_a_cycle(self):
        self.assertIn("descendant",
                      self.kb("tags", "--move", "kind", "--parent", "known-issue", expect=2))

    def test_move_rejects_an_unknown_tag(self):
        self.assertIn("no such tag",
                      self.kb("tags", "--move", "nope", "--parent", "kind", expect=2))

    # -- discuss -----------------------------------------------------------

    def test_discuss_appends_under_the_discussion_heading(self):
        self._seed()
        self.kb("discuss", "tn-0001", "-m", "we should report this", "--who", "sam")
        text = next((self.tmp / "notes").glob("tn-0001*.md")).read_text(encoding="utf-8")
        self.assertIn("**sam**", text)
        self.assertIn("we should report this", text)
        self.assertLess(text.index("## Discussion"), text.index("we should report this"))

    def test_discuss_keeps_earlier_entries(self):
        self._seed()
        self.kb("discuss", "tn-0001", "-m", "first", "--who", "a")
        self.kb("discuss", "tn-0001", "-m", "second", "--who", "b")
        text = next((self.tmp / "notes").glob("tn-0001*.md")).read_text(encoding="utf-8")
        self.assertIn("first", text)
        self.assertLess(text.index("first"), text.index("second"))

    def test_discuss_content_is_searchable(self):
        self._seed()
        self.kb("discuss", "tn-0001", "-m", "peculiarphrase", "--who", "a")
        self.assertIn("tn-0001", self.kb("search", "peculiarphrase"))

    def test_discuss_on_a_missing_note_fails(self):
        self.assertIn("no note matching",
                      self.kb("discuss", "tn-9999", "-m", "x", expect=2))


# ---------------------------------------------------------------------------
# End-to-end - the real knowledge base in this repo
# ---------------------------------------------------------------------------

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
            cited = set(_re.findall(rf"\b{kb.prefix()}-\d{{4}}\b", text))
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

    def kb(self, *args, expect=0):
        r = subprocess.run([sys.executable, "grumpy.py", *args], cwd=self.tmp,
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, expect, r.stdout + r.stderr)
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
        self.assertLess(out.index("tn-0002"), out.index("tn-0003"))

    def test_issues_can_be_narrowed_to_one_severity(self):
        self._seed()
        out = self.kb("issues", "--severity", "blocker")
        self.assertIn("tn-0002", out)
        self.assertNotIn("tn-0001", out)

    def test_issues_hides_settled_ones_unless_asked(self):
        self.kb("add", "--title", "Was broken", "--kind", "known-issue",
                "--tags", "major", "--status", "fixed", "--body", "not any more")
        self.assertIn("no open issues", self.kb("issues", expect=1))
        self.assertIn("tn-0001", self.kb("issues", "--all"))

    def test_issues_ignores_other_kinds(self):
        self._seed()
        self.kb("add", "--title", "A choice", "--kind", "decision",
                "--body", "we went with the second option for these reasons")
        self.assertNotIn("tn-0004", self.kb("issues"))

    def test_context_lists_neighbours_by_kind(self):
        self.kb("add", "--title", "The defect", "--kind", "known-issue",
                "--tags", "major", "--body", "something is wrong in the widget")
        self.kb("add", "--title", "What we did about it", "--kind", "decision",
                "--body", "we chose to work around it, see [[tn-0001]]")
        out = self.kb("read", "tn-0001", "--context")
        self.assertIn("## Context", out)
        self.assertIn("decision", out)
        self.assertIn("tn-0002", out)

    def test_context_is_honest_when_there_is_none(self):
        self.kb("add", "--title", "Lonely", "--kind", "known-issue",
                "--tags", "minor", "--body", "nothing links here at all yet")
        self.assertIn("nothing links to this yet",
                      self.kb("read", "tn-0001", "--context"))

    def test_read_without_context_stays_quiet(self):
        self.kb("add", "--title", "The defect", "--kind", "known-issue",
                "--tags", "major", "--body", "something is wrong in the widget")
        self.assertNotIn("## Context", self.kb("read", "tn-0001"))


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
