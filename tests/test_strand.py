"""The strand checks: the plan, PROMISES.json and the tests agree.

Standard library only. Run: python tests/test_strand.py -v

- Every test the plan names exists, and every codon test has a codon in the plan.
- No codon is used for two claims.
- PROMISES.json: a version is refused while a row due in it has no test, and the version after it is refused
  until the row says where it was proven on a real machine.

Until the plan exists (it comes in its own pull request) the plan is read as empty, so these checks pass only
while no row and no codon test exists either.
"""
import ast
import atexit
import collections
import contextlib
import json
import os
import re
import sys
import tempfile
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


def collect(path: str) -> list:
    """The tests the unittest runner collects from one file: what `python <file>` would run."""
    loader = unittest.TestLoader()
    suite = loader.discover(os.path.dirname(path), pattern=os.path.basename(path), top_level_dir=os.path.dirname(path))
    out, todo = [], [suite]
    while todo:
        s = todo.pop()
        for x in s:
            (todo.append(x) if isinstance(x, unittest.TestSuite) else out.append(x))
    return out


def tests_that_ran(tests_dir: str = TESTS) -> set:
    """G9.6: names of the tests the runner collects AND runs to a pass. A name found only as text is not a test.

    This file's own tests are counted when collected: they are running now, and a failure among them fails the run.
    Every other test file is run here, in this process, with its output kept quiet.
    """
    ran = set()
    for name in sorted(os.listdir(tests_dir)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        tests = collect(os.path.join(tests_dir, name))
        if os.path.abspath(os.path.join(tests_dir, name)) == os.path.abspath(__file__):
            ran |= {t._testMethodName for t in tests}
            continue

        class Passed(unittest.TestResult):
            def __init__(self):
                super().__init__()
                self.passed = []

            def addSuccess(self, test):
                super().addSuccess(test)
                self.passed.append(test)

        res = Passed()
        with open(os.devnull, "w") as dn, contextlib.redirect_stdout(dn), contextlib.redirect_stderr(dn):
            unittest.TestSuite(tests).run(res)
        ran |= {t._testMethodName for t in res.passed}
        for test, why in res.skipped:  # an honest skip is "not run here", never a pass
            NOT_RUN_HERE.append(f"{name}: {test._testMethodName} ({why})")
    return ran


NOT_RUN_HERE: list = []


def defined_twice(path: str) -> list:
    """G9.6: test names a file defines more than once. A later twin hides the real body, and its name still passes."""
    seen = collections.Counter(n.name for n in ast.walk(ast.parse(fread(path)))
                               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"))
    return sorted(k for k, c in seen.items() if c > 1)


_RAN = None


def ran_here() -> set:
    global _RAN
    if _RAN is None:
        _RAN = tests_that_ran()
    return _RAN


def v(s):
    return tuple(int(x) for x in s.split("."))


class Promises(unittest.TestCase):
    """A lesson is a gate, not a note."""

    def setUp(self):
        self.doc = jload(os.path.join(ROOT, "PROMISES.json"))
        self.rows = self.doc["rows"]
        self.version = version()
        self.tests = ran_here()
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
                              f"{r['id']} ({r['promise'][:60]}...) is due in {r['ship_in']} and has no test that ran and passed. "
                              "Build it, or have its date moved (see how_to_use in PROMISES.json).")
                self.assertIn(f"`{r['test']}`", self.plan, f"{r['id']}: its test is not named in the plan")

    def test_PROMISES_every_built_row_names_a_test_that_ran_at_any_version(self):
        """A row marked built points at a test that ran and passed today, not only once its version is due."""
        for r in self.rows:
            if r.get("test"):
                self.assertIn(r["test"], self.tests, f"{r['id']}: its test {r['test']} was not collected or did not pass")
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
        self.assertEqual(named - ran_here(), set(), "the plan names tests that were not collected or did not pass")
        self.assertEqual(real - named, set(), "tests with no codon in the plan")

    def test_G9_6_the_gate_counts_only_tests_that_ran(self):
        """The gate's self-test: it plants every way a test can look present and not run, and must refuse each.
        Not counted: a test after a helper's return, one renamed out of collection, one under `if False:`, one
        marked skipped (listed as not run here), one that fails, one whose text sits in a string. A test shadowed by
        a later twin of the same name is counted by name, so the twice-defined check refuses it instead."""
        probe = (
            "import unittest\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_probe_reached(self):\n"
            "        self.assertTrue(True)\n\n"
            "    def helper(self):\n"
            "        return\n\n"
            "        def test_probe_after_a_return(self):\n"
            "            pass\n\n"
            "    def xtest_probe_renamed(self):\n"
            "        pass\n\n"
            "    if False:\n"
            "        def test_probe_under_if_false(self):\n"
            "            pass\n\n"
            "    @unittest.skip('a planted skip')\n"
            "    def test_probe_skipped(self):\n"
            "        pass\n\n"
            "    def test_probe_fails(self):\n"
            "        self.fail('no')\n\n"
            "    def test_probe_shadowed(self):\n"
            "        self.fail('the real body')\n\n"
            "    def test_probe_shadowed(self):\n"
            "        pass\n\n"
            "_OLD = '''\n"
            "    def test_probe_in_a_string(self):\n"
            "        pass\n"
            "'''\n")
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_probe.py"), "w", encoding="utf-8") as f:
                f.write(probe)
            before = len(NOT_RUN_HERE)
            ran = tests_that_ran(d)
            twice = defined_twice(os.path.join(d, "test_probe.py"))
        self.assertEqual(ran, {"test_probe_reached", "test_probe_shadowed"})
        self.assertEqual(twice, ["test_probe_shadowed"])
        self.assertTrue(any("test_probe_skipped (a planted skip)" in x for x in NOT_RUN_HERE[before:]), NOT_RUN_HERE)
        del NOT_RUN_HERE[before:]

    def test_no_test_file_defines_one_name_twice(self):
        for name in sorted(os.listdir(TESTS)):
            if name.startswith("test_") and name.endswith(".py"):
                self.assertEqual(defined_twice(os.path.join(TESTS, name)), [], f"{name} defines a test name twice")

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


@atexit.register
def _say_what_did_not_run():
    for line in NOT_RUN_HERE:
        print(f"not run here: {line}", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
