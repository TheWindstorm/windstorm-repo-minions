"""The strand checks: the plan, PROMISES.json and the tests agree.

Standard library only. Run: python tests/test_strand.py -v

- Every test the plan names exists, and every codon test has a codon in the plan.
- No codon is used for two claims.
- PROMISES.json: a version is refused while a row due in it has no test, and the version after it is refused
  until the row says where it was proven on a real machine.

Until the plan exists (it comes in its own pull request) the plan is read as empty, so these checks pass only
while no row and no codon test exists either.
"""
import collections
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(ROOT, "DNA-STRAND-MASTER-PLAN.md")
PLUGIN = os.path.join(ROOT, ".claude-plugin", "plugin.json")
TESTS = os.path.join(ROOT, "tests")


def fread(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def plan_text():
    return fread(PLAN) if os.path.exists(PLAN) else ""


def version():
    """The version being built: plugin.json's, or 0.0.0 before there is a plugin."""
    raw = jload(PLUGIN)["version"] if os.path.exists(PLUGIN) else "0.0.0"
    return tuple(int(x) for x in raw.split("."))


def all_test_names():
    names = set()
    for name in sorted(os.listdir(TESTS)):
        if name.startswith("test_") and name.endswith(".py"):
            names |= set(re.findall(r"def (test_[A-Za-z0-9_]+)\(", fread(os.path.join(TESTS, name))))
    return names


def v(s):
    return tuple(int(x) for x in s.split("."))


class Promises(unittest.TestCase):
    """A lesson is a gate, not a note."""

    def setUp(self):
        self.doc = jload(os.path.join(ROOT, "PROMISES.json"))
        self.rows = self.doc["rows"]
        self.version = version()
        self.tests = all_test_names()
        self.plan = plan_text()

    def test_PROMISES_every_row_is_whole_and_its_codon_is_in_the_plan(self):
        ids = [r["id"] for r in self.rows]
        self.assertEqual(len(ids), len(set(ids)), "two rows share an id")
        for r in self.rows:
            for k in ("id", "seen", "promise", "codon", "owner", "first_ship_in", "ship_in"):
                self.assertTrue(r.get(k), f"{r.get('id')}: '{k}' is empty")
            self.assertIn(r["owner"], ("person", "maintainer"), r["id"])
            self.assertIn(f"| {r['codon']} |", self.plan, f"{r['id']}: codon {r['codon']} is not in the plan")

    def test_PROMISES_a_lesson_not_taken_is_written_down_with_its_reason(self):
        for d in self.doc.get("declined", []):
            self.assertTrue(d.get("heard") and d.get("why"), "a declined lesson needs what was heard and why it was not taken")

    def test_PROMISES_no_version_ships_while_a_promise_due_in_it_is_not_built(self):
        for r in self.rows:
            if v(r["ship_in"]) <= self.version:
                self.assertIn(r.get("test"), self.tests,
                              f"{r['id']} ({r['promise'][:60]}...) is due in {r['ship_in']} and has no test. "
                              "Build it, or have its date moved (see how_to_use in PROMISES.json).")
                self.assertIn(f"`{r['test']}`", self.plan, f"{r['id']}: its test is not named in the plan")

    def test_PROMISES_the_version_after_needs_proof_on_a_real_machine(self):
        for r in self.rows:
            if v(r["ship_in"]) < self.version:
                self.assertTrue(r.get("proven"), f"{r['id']} shipped in {r['ship_in']} and no real machine has proven it yet")

    def test_PROMISES_a_date_moves_only_with_a_reason_and_a_persons_row_only_by_their_word(self):
        for r in self.rows:
            if r["ship_in"] != r["first_ship_in"]:
                self.assertTrue(r.get("moved_why"), f"{r['id']}: its date moved with no reason written")
                if r["owner"] == "person":
                    self.assertTrue(r.get("moved_by_person"), f"{r['id']}: only the person who saw it can move its date")


class Strand(unittest.TestCase):
    def test_every_proof_named_in_the_plan_is_a_real_test_and_every_test_has_a_codon(self):
        plan = plan_text()
        named = set(re.findall(r"`(test_[A-Za-z0-9_]+)`", plan))
        real = {t for t in all_test_names() if re.match(r"test_(?:G|INV)[0-9]", t)}
        self.assertEqual(named - all_test_names(), set(), "the plan names tests that do not exist")
        self.assertEqual(real - named, set(), "tests with no codon in the plan")

    def test_no_codon_is_used_twice_in_the_plan(self):
        seen = collections.Counter(re.findall(r"^\| ((?:G|INV)[0-9.]+) \|", plan_text(), re.M))
        self.assertEqual([c for c, n in seen.items() if n > 1], [], "a codon is used for two claims")


class Hygiene(unittest.TestCase):
    """Public-repository rules that a machine can check."""

    def test_no_workflow_of_this_public_repository_asks_for_a_self_hosted_runner(self):
        wf = os.path.join(ROOT, ".github", "workflows")
        for name in sorted(os.listdir(wf)):
            text = fread(os.path.join(wf, name))
            for line in re.findall(r"runs-on:.*", text):
                self.assertNotIn("self-hosted", line, f"{name}: {line.strip()}")
                self.assertRegex(line, r"runs-on:\s*(ubuntu|macos|windows)-", f"{name}: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
