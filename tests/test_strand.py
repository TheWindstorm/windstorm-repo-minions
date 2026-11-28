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
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import machine_guard  # noqa: E402  rule 17 (R142): first, so no test here reaches this machine
import version_places  # noqa: E402  R194: the gates' reads of a version from a name, one place

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


def names_that_ran(tests_dir: str = TESTS) -> set:
    """G9.6: names of the tests the runner collects AND runs to a pass. A name found only as text is not a test.

    This file's own tests are counted when collected: they are running now, and a failure among them fails the run.
    Every other test file is run here, in this process, with its output kept quiet.
    """
    ran = set()
    for name in sorted(os.listdir(tests_dir)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        if os.path.abspath(os.path.join(tests_dir, name)) == os.path.abspath(__file__):
            ran |= {t._testMethodName for t in collect(os.path.join(tests_dir, name))}
            continue
        # R169 (the gate recipe's ninth case): each other file runs in its OWN process, and what it ran is read from
        # the outcome that process writes only once its whole run has ended. A process that ended early (a test, or
        # the tool under test, calling os._exit(0); a parent leaving after a fork) wrote none: nothing of it counts,
        # and the gate fails, whatever that exit was. Outcomes come from the runner's own result (addSuccess), never
        # from report text a test can write into (the eighth case).
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "outcome.json")
            p = subprocess.run([sys.executable, "-c", OUTCOME_RUNNER, tests_dir, name, out], capture_output=True,
                               text=True, timeout=1800)
            if not os.path.exists(out):
                raise AssertionError(f"{name} ended before its run finished (exit {p.returncode}, no outcome written): "
                                     "an early end is never a pass (R169)")
            got = jload(out)
        ran |= set(got["passed"])
        for test, why in got["skipped"]:  # an honest skip is "not run here", never a pass
            NOT_RUN_HERE.append(f"{name}: {test} ({why})")
        LEFT_OPEN.extend(f"{name}: {x}" for x in got.get("left_open", []))  # R177
        for test, where, text, cut in got.get("failed", []):  # R226: why a test of that file did not pass, kept
            FAILED_THERE.setdefault(test, []).append(f"{name}: {where} did not pass; the last {cut} characters of "
                                                     f"its message:\n{text}")
    return ran


def why_not_passed(names) -> str:
    """R226: for each name that did not pass, what its own file's run said of it (empty where it failed nowhere)."""
    parts = [x for n in sorted(names) for x in FAILED_THERE.get(n, [])]
    return "".join(f"\n{x}" for x in parts)


OUTCOME_RUNNER = """
import contextlib, gc, json, os, sys, unittest, warnings
tests_dir, name, out = sys.argv[1:4]
sys.path.insert(0, tests_dir)
suite = unittest.TestLoader().discover(tests_dir, pattern=name, top_level_dir=tests_dir)
class Passed(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.passed = []
    def addSuccess(self, test):
        super().addSuccess(test)
        self.passed.append(test._testMethodName)
res = Passed()
TAIL = 6000  # R226: one fixed count, from the end of each message
with open(os.devnull, "w") as dn, contextlib.redirect_stdout(dn), contextlib.redirect_stderr(dn), \
        warnings.catch_warnings(record=True) as seen:  # R177: every file a test leaves open is named
    warnings.simplefilter("always", ResourceWarning)
    suite.run(res)
    gc.collect()
left_open = [str(w.message) for w in seen if issubclass(w.category, ResourceWarning)]
def who(t):  # a subtest's failure is its test's; an error outside any test keeps its own description
    case = getattr(t, "test_case", t)
    return getattr(case, "_testMethodName", None) or str(t)
failed = [[who(t), str(t), text[-TAIL:], TAIL] for t, text in res.failures + res.errors]  # R226
with open(out, "w") as f:
    json.dump({"passed": res.passed, "skipped": [[t._testMethodName, w] for t, w in res.skipped],
               "run": res.testsRun, "left_open": left_open, "failed": failed}, f)
"""


NOT_RUN_HERE: list = []
FAILED_THERE: dict = {}  # R226: test name -> what each other file's own run said of it when it failed or errored
LEFT_OPEN: list = []  # R177: the ResourceWarnings each other test file's own run raised


def defined_twice(path: str) -> list:
    """G9.6: test names a file defines more than once. A later twin hides the real body, and its name still passes."""
    seen = collections.Counter(n.name for n in ast.walk(ast.parse(fread(path)))
                               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"))
    return sorted(k for k, c in seen.items() if c > 1)


_RAN = None


def ran_here() -> set:
    global _RAN
    if _RAN is None:
        _RAN = names_that_ran()
    return _RAN


def v(s):
    return tuple(int(x) for x in s.split("."))


# A filed row (G9.20): kept, due in no version while 'filed' holds text. The one question every check asks of it.
FILED_LEAD = re.compile(r"By the (maintainer|owner)'s word, (\d{4}-\d{2}-\d{2}): ")


def filed(r) -> bool:
    return bool(str(r.get("filed") or "").strip())


def due_not_built(rows, version, ran, plan) -> list:
    """Rows due in this version or before with no test that ran and passed, or whose test the plan does not name.
    An unbuilt filed row is due in no version, whatever 'ship_in' says."""
    out = []
    for r in rows:
        if filed(r) and not r.get("test"):
            continue
        if v(r["ship_in"]) <= version:
            if r.get("test") not in ran:
                out.append(f"{r['id']} ({r['promise'][:60]}...) is due in {r['ship_in']} and has no test that ran and "
                           "passed. Build it, or have its date moved (see how_to_use in PROMISES.json).")
            elif f"`{r['test']}`" not in plan:
                out.append(f"{r['id']}: its test is not named in the plan")
    return out


def built_not_passed(rows, ran, plan) -> list:
    """A row marked built, filed or not, points at a test that ran and passed today and that the plan names."""
    out = []
    for r in rows:
        if r.get("test"):
            if r["test"] not in ran:
                out.append(f"{r['id']}: its test {r['test']} was not collected or did not pass")
            elif f"`{r['test']}`" not in plan:
                out.append(f"{r['id']}: its test is not named in the plan")
    return out


def not_proven_after(rows, version) -> list:
    """Rows of an earlier version with no proven line. A filed row is not asked for one while it is filed."""
    return [f"{r['id']} shipped in {r['ship_in']} and no real machine has proven it yet"
            for r in rows if not filed(r) and v(r["ship_in"]) < version and not r.get("proven")]


def filed_text_faults(rows) -> list:
    """A filed row's text opens with whose word and the day (the gate reads those first words only); a person's
    row is filed only by the owner's word."""
    out = []
    for r in rows:
        if not filed(r):
            continue
        m = FILED_LEAD.match(r["filed"])
        day = None
        if m:
            try:
                day = datetime.date.fromisoformat(m.group(2))
            except ValueError:
                day = None
        if not m or day is None:
            out.append(f"{r['id']}: 'filed' must begin \"By the maintainer's word, YYYY-MM-DD: \" or "
                       "\"By the owner's word, YYYY-MM-DD: \", a real day")
        elif r.get("owner") == "person" and m.group(1) != "owner":
            out.append(f"{r['id']}: a person's row is filed only by the owner's word")
    return out


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
        self.assertEqual(due_not_built(self.rows, self.version, self.tests, self.plan), [],
                         why_not_passed({r.get("test") for r in self.rows} - self.tests))

    def test_PROMISES_every_built_row_names_a_test_that_ran_at_any_version(self):
        """A row marked built points at a test that ran and passed today, not only once its version is due."""
        self.assertEqual(built_not_passed(self.rows, self.tests, self.plan), [],
                         why_not_passed({r.get("test") for r in self.rows} - self.tests))

    def test_PROMISES_the_version_after_needs_proof_on_a_real_machine(self):
        self.assertEqual(not_proven_after(self.rows, self.version), [])

    def test_G9_20_a_filed_row_says_whose_word_and_the_day(self):
        """The file's own rows: every filed text opens with whose word and the day."""
        self.assertEqual(filed_text_faults(self.rows), [])

    def test_G9_20_the_gate_asks_a_filed_row_one_question(self):
        """Planted rows, each case refused first and the same row filed then passes where it should."""
        plan = "`test_built_one`"
        lead = "By the maintainer's word, 2026-11-27: waits for a real report. A what-if from reading."

        def row(**k):
            r = {"id": "R9", "promise": "a promise", "owner": "maintainer", "ship_in": "0.1.1", "first_ship_in": "0.1.1"}
            r.update(k)
            return r
        with self.subTest("1. a filed text without its opening or its date fails"):
            for bad in ("waits for a real report. A what-if.", "By the maintainer's word: waits for a real report. x",
                        "By the maintainer's word, 2026-13-40: waits for a real report. x",
                        "By someone's word, 2026-11-27: waits for a real report. x",
                        "by the maintainer's word, 2026-11-27: waits for a real report. x"):
                self.assertEqual(len(filed_text_faults([row(filed=bad)])), 1, bad)
            self.assertEqual(filed_text_faults([row(filed=lead)]), [])
        with self.subTest("2. a person's row filed by the maintainer's word fails"):
            self.assertEqual(len(filed_text_faults([row(owner="person", filed=lead)])), 1)
            self.assertEqual(filed_text_faults([row(owner="person", filed=lead.replace("maintainer's", "owner's"))]), [])
        with self.subTest("3. an unbuilt row due in the version under test fails; the same row filed passes"):
            self.assertEqual(len(due_not_built([row()], v("0.1.1"), set(), plan)), 1)
            self.assertEqual(len(due_not_built([row(filed="   ")], v("0.1.1"), set(), plan)), 1)
            self.assertEqual(due_not_built([row(filed=lead)], v("0.1.1"), set(), plan), [])
            self.assertEqual(due_not_built([row(filed=lead)], v("0.2.0"), set(), plan), [])
        with self.subTest("4. a built filed row whose test did not pass fails"):
            built = row(filed=lead, test="test_built_one")
            self.assertEqual(len(built_not_passed([built], set(), plan)), 1)              # missing
            self.assertEqual(len(due_not_built([built], v("0.1.1"), set(), plan)), 1)
            self.assertEqual(built_not_passed([built], {"test_built_one"}, plan), [])
            probe = ("import unittest\n\nclass T(unittest.TestCase):\n"
                     "    @unittest.skip('a planted skip')\n    def test_built_one(self):\n        pass\n\n"
                     "    def test_built_two(self):\n        self.fail('the real body')\n\n"
                     "    def test_built_two(self):\n        pass\n")
            with tempfile.TemporaryDirectory() as d:
                with open(os.path.join(d, "test_probe.py"), "w", encoding="utf-8") as f:
                    f.write(probe)
                before = len(NOT_RUN_HERE)
                ran = names_that_ran(d)
                twice = defined_twice(os.path.join(d, "test_probe.py"))
                del NOT_RUN_HERE[before:]
            self.assertEqual(len(built_not_passed([built], ran, plan)), 1)                # skipped
            self.assertEqual(twice, ["test_built_two"])                                     # named twice
        with self.subTest("5. a built row with no proven line where one is asked fails; the same row filed passes"):
            old = row(test="test_built_one", ship_in="0.1.0", first_ship_in="0.1.0")
            self.assertEqual(len(not_proven_after([old], v("0.1.1"))), 1)
            self.assertEqual(not_proven_after([dict(old, filed=lead)], v("0.1.1")), [])
            self.assertEqual(not_proven_after([dict(old, filed=lead)], v("0.2.0")), [])

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
        missing = named - ran_here()
        self.assertEqual(missing, set(), "the plan names tests that were not collected or did not pass"
                         + why_not_passed(missing))
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
            ran = names_that_ran(d)
            twice = defined_twice(os.path.join(d, "test_probe.py"))
        self.assertEqual(ran, {"test_probe_reached", "test_probe_shadowed"})
        self.assertEqual(twice, ["test_probe_shadowed"])
        self.assertTrue(any("test_probe_skipped (a planted skip)" in x for x in NOT_RUN_HERE[before:]), NOT_RUN_HERE)
        del NOT_RUN_HERE[before:]

    def test_G9_6_b_why_a_test_did_not_pass_reaches_the_gates_message(self):
        """R226 (seen once, 2026-11-28, in this repository's own CI on one of four Python versions): in the gate's run
        of another test file a test did not pass that had passed minutes before in the same job, and the gate's
        failure named it with nothing of why. Each file's own run now writes every test that failed or errored with
        the last part of its message, a fixed count it names, and the gate's failure prints them under the names."""
        words = "the planted reason " + "x" * 7000 + " its very last words"
        probe = ("import unittest\n\nclass T(unittest.TestCase):\n"
                 "    def test_probe_fails_with_words(self):\n"
                 f"        self.fail({words!r})\n\n"
                 "    def test_probe_errors(self):\n"
                 "        raise RuntimeError('a planted error')\n\n"
                 "    def test_probe_sub(self):\n"
                 "        with self.subTest('one part'):\n"
                 "            self.assertEqual(1, 2, 'a planted subtest')\n\n"
                 "    def test_probe_passes(self):\n"
                 "        pass\n")
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_probe.py"), "w", encoding="utf-8") as f:
                f.write(probe)
            ran = names_that_ran(d)
        planted = {"test_probe_fails_with_words", "test_probe_errors", "test_probe_sub"}
        try:
            self.assertEqual(ran, {"test_probe_passes"})  # what counts as passed is untouched
            with self.assertRaises(AssertionError) as caught:
                self.assertEqual(planted - ran, set(), "the plan names tests that were not collected or did not pass"
                                 + why_not_passed(planted - ran))
            said = str(caught.exception)
            for name in planted:
                # Python 3.10 names a test "NAME (test_probe.T)", 3.11 and later "NAME (test_probe.T.NAME)"
                self.assertRegex(said, rf"\ntest_probe\.py: {name} \(test_probe\.T(\.{name})?\)( \[one part\])? did not "
                                       r"pass; the last 6000 characters of its message:\n")
            self.assertIn("its very last words", said)
            self.assertNotIn("the planted reason", said)  # cut at the fixed count, and the line says so
            self.assertIn("RuntimeError: a planted error", said)
            self.assertIn("AssertionError: 1 != 2 : a planted subtest", said)
            self.assertEqual(why_not_passed({"test_probe_passes", "test_no_such"}), "")
        finally:
            for name in planted:
                FAILED_THERE.pop(name, None)

    def test_G9_15_the_tests_close_every_file_they_open(self):
        """R177: a full run printed 24 ResourceWarning lines (files the tests opened and left to the collector). Each
        other test file's own run records them (warnings always shown, a collection at its end), and the gate fails
        on any. A planted probe that leaves a file open is named; the same probe closing it is not."""
        ran_here()
        self.assertEqual(LEFT_OPEN, [], "files a test left open (R177)")
        leaky = ("import os, unittest\n\n"
                 "class T(unittest.TestCase):\n"
                 "    def test_probe_leaves_it_open(self):\n"
                 "        self.assertTrue(open(os.devnull).read() == '')\n")
        closed = leaky.replace("open(os.devnull).read() == ''", "True")
        for text, want in ((leaky, 1), (closed, 0)):
            with tempfile.TemporaryDirectory() as d:
                with open(os.path.join(d, "test_probe.py"), "w", encoding="utf-8") as f:
                    f.write(text)
                before = len(LEFT_OPEN)
                self.assertEqual(names_that_ran(d), {"test_probe_leaves_it_open"})
                got = LEFT_OPEN[before:]
                del LEFT_OPEN[before:]
            self.assertEqual(len(got), want, got)
            if want:
                self.assertIn("test_probe.py: unclosed file", got[0])

    def test_G9_14_a_run_that_ends_early_or_prints_a_pass_is_never_green(self):
        """R169 (the gate recipe's eighth and ninth cases, 2026-11-24). Ninth: a test (or the tool under test,
        a parent leaving after a fork) calls os._exit(0): exit 0, no summary, and a gate in the same process never
        ran; seen green on this repository's own CI step before R169. Each other file now runs in its own process
        and counts only from the outcome written at its end: the early end fails the gate. Eighth: a test writes a
        line shaped like the runner's report for a name no file defines: outcomes come from the runner's result,
        never its text. And the CI step is green only with each file's "Ran" and "OK" lines and the gate's own
        last line, which it writes only after its whole run."""
        early = ("import os, unittest\n\n"
                 "class T(unittest.TestCase):\n"
                 "    def test_a_ends_early(self):\n"
                 "        os._exit(0)\n\n"
                 "    def test_b_after(self):\n"
                 "        pass\n")
        printed = ("import sys, unittest\n\n"
                   "class T(unittest.TestCase):\n"
                   "    def test_probe_writes(self):\n"
                   "        sys.stderr.write('test_probe_ghost (test_probe.T.test_probe_ghost) ... ok\\n')\n")
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "test_probe.py"), "w", encoding="utf-8") as f:
                f.write(early)
            with self.assertRaises(AssertionError) as caught:
                names_that_ran(d)
            self.assertIn("ended before its run finished (exit 0, no outcome written)", str(caught.exception))
            with open(os.path.join(d, "test_probe.py"), "w", encoding="utf-8") as f:
                f.write(printed)
            self.assertEqual(names_that_ran(d), {"test_probe_writes"})
        step = fread(os.path.join(ROOT, ".github", "workflows", "tests.yml"))
        for line in ("set -o pipefail", "python \"$t\" -v 2>&1 | tee \"$log\" || exit 1",
                     "grep -qE '^Ran [0-9]+ tests? in ' \"$log\" && grep -qE '^OK( \\(.*\\))?$' \"$log\" || {",
                     "tail -n 1 \"$RUNNER_TEMP/test_strand.py.log\" | grep -qE '^gate: passed: ' || {"):
            self.assertIn(line, step)
        self.assertIn('print(f"gate: {\'passed\' if result.wasSuccessful() else \'FAILED\'}', fread(__file__))

    def test_no_test_file_defines_one_name_twice(self):
        for name in sorted(os.listdir(TESTS)):
            if name.startswith("test_") and name.endswith(".py"):
                self.assertEqual(defined_twice(os.path.join(TESTS, name)), [], f"{name} defines a test name twice")

    def test_G9_19_the_suite_runs_with_the_tag_at_its_head_before_the_push(self):
        # Row R219 (the family's rule of 2026-11-26). The script runs on a scratch repository with a local tag, as it
        # is and with a failing test planted; the release steps name it before the tag is pushed.
        script = os.path.join(ROOT, "scripts", "tag_head_suite.py")
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k in ("GIT_CONFIG_GLOBAL",
                                                                                         "GIT_CONFIG_NOSYSTEM")}
        env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
        files = {
            "test_a.py": "import unittest\nclass A(unittest.TestCase):\n    def test_a(self):\n        pass\n"
                         "if __name__ == '__main__':\n    unittest.main()\n",
            # a stand-in gate: its own last line, as the real one writes it, after its run
            "test_strand.py": "import subprocess, sys, unittest\nclass G(unittest.TestCase):\n"
                              "    def test_tag_at_head(self):\n"
                              "        out = subprocess.run(['git', 'describe', '--exact-match', 'HEAD'],"
                              " capture_output=True, text=True).stdout.strip()\n"
                              "        self.assertEqual(out, 'v9.9.9')\n"
                              "if __name__ == '__main__':\n    r = unittest.main(exit=False).result\n"
                              "    print('gate: ' + ('passed' if r.wasSuccessful() else 'FAILED') + ': 1 test(s)')\n"
                              "    sys.exit(0 if r.wasSuccessful() else 1)\n"}
        with tempfile.TemporaryDirectory() as d:
            run = lambda *a: subprocess.run(a, cwd=d, env=env, capture_output=True, text=True)
            os.mkdir(os.path.join(d, "tests"))
            for name, body in files.items():
                with open(os.path.join(d, "tests", name), "w", encoding="utf-8") as f:
                    f.write(body)
            run("git", "init", "-q"); run("git", "add", "."); run("git", "commit", "-qm", "r")
            run("git", "tag", "v0.0.1")  # a tag the source holds: the fresh clone takes none of them
            good = run("git", "rev-parse", "HEAD").stdout.strip()
            r = run(sys.executable, script, "v9.9.9", "--source", d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("annotated tag v9.9.9 made there only", r.stdout)
            self.assertIn("ok tests/test_a.py: Ran 1 test in 0.", r.stdout)
            self.assertIn("ok the gate: gate: passed: 1 test(s)", r.stdout)
            self.assertTrue(r.stdout.rstrip().splitlines()[-1].startswith("ok: the whole suite passed with v9.9.9"))
            self.assertLess(r.stdout.index("test_a.py"), r.stdout.index("ok the gate"), "every file, then the gate")
            self.assertEqual(run("git", "tag", "--list").stdout, "v0.0.1\n", "the tag is made in the clone only")
            # a failing test planted: it stops the run before the gate, and nothing is tagged here
            with open(os.path.join(d, "tests", "test_b.py"), "w", encoding="utf-8") as f:
                f.write(files["test_a.py"].replace("pass", "self.fail('planted')").replace("class A", "class B"))
            run("git", "add", "."); run("git", "commit", "-qm", "planted")
            r = run(sys.executable, script, "v9.9.9", "--source", d)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            stop = [x for x in r.stdout.splitlines() if x.startswith("STOP: ")]
            self.assertEqual(len(stop), 1, r.stdout)
            self.assertIn("tests/test_b.py failed", stop[0])
            self.assertNotIn("ok the gate", r.stdout)
            self.assertNotIn("ok: the whole suite passed", r.stdout)
            self.assertEqual(run("git", "tag", "--list").stdout, "v0.0.1\n", "a stop tags nothing here either")
            kept = re.search(r"outside the clone (\S+), which is kept", stop[0]).group(1)
            self.assertTrue(os.path.isdir(kept), kept)
            shutil.rmtree(kept)
            # the commit named is the one tagged: the good commit still passes from behind the planted one
            r = run(sys.executable, script, "v9.9.9", good, "--source", d)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn(f"head {good}", r.stdout)
        # the release steps name it, before the tag is pushed
        steps = fread(os.path.join(ROOT, "docs", "release-steps.md"))
        self.assertIn("python3 scripts/tag_head_suite.py vX.Y.Z", steps)
        self.assertLess(steps.index("scripts/tag_head_suite.py"), steps.index("git push origin vX.Y.Z"))

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
                # The family's rule (2026-11-23): a job names its image; a moved "latest" is a change nobody reviewed.
                self.assertNotIn("latest", line, f"{name}: {line.strip()}")

    def test_a_commits_checks_run_once(self):
        # The family's rule 17 (2026-11-23): on pull requests, and on pushes only to main and the release branch
        # (release-x.y.z), so a pull request's commit, a release-sync-N one too, is not checked twice.
        wf = os.path.join(ROOT, ".github", "workflows")
        for name in sorted(os.listdir(wf)):
            text = fread(os.path.join(wf, name))
            on = re.search(r"^on:(.*?)(?=^\S)", text, re.M | re.S).group(1)
            self.assertNotRegex(on, r"\[\s*push", name)
            push = re.search(r"^  push:\s*\n\s+branches:\s*\[(.*)\]\s*$", on, re.M)
            self.assertIsNotNone(push, f"{name}: push names its branches")
            self.assertEqual({b.strip().strip('"') for b in push.group(1).split(",")}, {"main", "release-[0-9]*"}, name)
            # Rule 17 again (11-23): a pull request's newer push cancels its older runs, never on main or a release.
            self.assertIn("group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}", text)
            self.assertIn("cancel-in-progress: ${{ github.event_name == 'pull_request' }}", text)


    def test_G9_10_main_carries_install_lines_only_pinned_to_a_released_tag(self):
        # Rules 15 and 16 (the maintainer's rulings, 2026-11-23): before a release, main's README has no install
        # lines; after it, every clone there names a version tag that exists. Runs the CI step itself, as written.
        url = "https://github.com/TheWindstorm/windstorm-repo-minions"
        cases = [  # (README text, tags on GitHub's copy, passes; None: GitHub's copy could not be read)
            ("No install lines here.\n", [], True),
            (f"`git clone --branch v0.1.0 {url}`\n", [], False),  # the tag does not exist yet
            (f"`git clone --branch v0.1.0 {url}`\n`python3 -I x/minions.py look O/N`\n", ["v0.1.0"], True),
            (f"`git clone {url}`\n", ["v0.1.0"], False),  # hands out main
            (f"`git clone --branch release-0.1.0 {url}`\n", ["v0.1.0"], False),  # a branch moves; main names a tag
            (f"`git clone --branch v0.1.0 {url}`\n`git clone {url}`\n", ["v0.1.0"], False),  # one unpinned
            ("`python3 -I x/minions.py setup O/N`\n", ["v0.1.0"], False),  # an install line with no pinned clone
            (f"`git clone --branch v0.1.0 {url}`\n", None, False),  # rule 6: could not be read is refused, said
        ]
        for readme, tags, ok in cases:
            r = release_place(readme, tags)
            self.assertEqual(r.returncode == 0, ok, f"{readme!r} tags {tags}: {r.stdout}{r.stderr}")
            if tags is None:
                self.assertIn("could not be read", r.stdout)
            elif "--branch v0.1.0" in readme and not tags:
                self.assertIn("not a tag of this repository yet", r.stdout)

    def test_G9_21_a_release_line_differs_from_main_only_in_what_names_its_version(self):
        # Row R223 (the maintainer's ruling of 2026-11-27): the release-place step on a release branch passes a branch
        # whose only differences from main are the README, the changelog, the manifest, the tool's VERSION line and
        # the skill's clone line, each naming the branch's own version; every other difference fails, said. A pull
        # request into a release line is checked as that line, whatever its head is called. Runs the CI step
        # itself, as written, on a made repository with an origin of its own.
        url = "https://github.com/TheWindstorm/windstorm-repo-minions"
        labels = {
            "README.md": f"## Status: 0.1.1, released 2026-12-01\n\n`cd ~ && git clone --branch v0.1.1 {url}`\n",
            "CHANGELOG.md": "# Changelog\n\n## 0.1.1 (2026-12-01)\n\nsecond.\n\n## 0.1.0 (2026-11-26)\n\nfirst.\n",
            "scripts/minions.py": RELEASE_MAIN["scripts/minions.py"].replace('VERSION = "0.1.0"', 'VERSION = "0.1.1"'),
            "skills/repo-minions/SKILL.md": RELEASE_MAIN["skills/repo-minions/SKILL.md"].replace("v0.1.0", "v0.1.1"),
            ".claude-plugin/plugin.json": '{"name": "repo-minions", "version": "0.1.1"}\n',
        }
        tool, skill = labels["scripts/minions.py"], labels["skills/repo-minions/SKILL.md"]
        cases = [  # (what differs from main, passes, words the step says, and how the branch is made)
            ("the labels only", labels, True, "differs from main only in: ", {}),
            ("README and changelog only, as before", {k: labels[k] for k in ("README.md", "CHANGELOG.md")}, True,
             "differs from main only in: ", {}),
            ("a second changed line in the tool", {**labels, "scripts/minions.py": tool.replace("return 0", "return 1")},
             False, "scripts/minions.py differs from main in more than its one version line", {}),
            ("more than the version on the tool's line",
             {**labels, "scripts/minions.py": tool.replace('VERSION = "0.1.1"', 'VERSION = "0.1.1"  # next')},
             False, "is not the tool's VERSION line naming 0.1.1", {}),
            ("another version than the branch's", {**labels, "scripts/minions.py": tool.replace("0.1.1", "0.1.2")},
             False, "is not the tool's VERSION line naming 0.1.1", {}),
            ("the skill's clone naming a branch", {**labels, "skills/repo-minions/SKILL.md":
                                                   skill.replace("--branch v0.1.1", "--branch release-0.1.1")},
             False, "skills/repo-minions/SKILL.md: its one changed line differs", {}),
            ("the skill's line changed in more than the version",
             {**labels, "skills/repo-minions/SKILL.md": skill.replace("home folder", "own folder")},
             False, "skills/repo-minions/SKILL.md: its one changed line differs", {}),
            ("the skill's clone naming no version", {**labels, "skills/repo-minions/SKILL.md":
                                                     skill.replace("--branch v0.1.1", "--branch v@")},
             False, "skills/repo-minions/SKILL.md: its one changed line differs", {}),
            ("a second changed line in the skill",
             {**labels, "skills/repo-minions/SKILL.md": skill.replace("Then look.", "Then look again.")},
             False, "skills/repo-minions/SKILL.md differs from main in more than its one version line", {}),
            ("the README's clone naming the branch",
             {**labels, "README.md": labels["README.md"].replace("--branch v0.1.1", "--branch release-0.1.1")},
             False, "every clone names this version's tag (--branch v0.1.1)", {}),
            ("a third path", {**labels, "tests/x.py": "x = 2\n"}, False, "tests/x.py differs from main", {}),
            ("a mode change", labels, False, "scripts/minions.py changed its mode", {"chmod": "scripts/minions.py"}),
            ("a deleted file", {**labels, "CHANGELOG.md": None}, False, "CHANGELOG.md is deleted", {}),
            ("a renamed file", labels, False, "READ-ME.md is added", {"rename": ("README.md", "READ-ME.md")}),
            ("an added file", {**labels, "docs/new.md": "new\n"}, False, "docs/new.md is added", {}),
            # a pull request into a release line is checked as that line, whatever its head is called
            ("a release-sync-1 head into release-0.1.1, the labels only", labels, True,
             "ok release-0.1.1 differs from main only in: ", {"branch": "release-sync-1", "base": "release-0.1.1"}),
            ("a release-sync-1 head into release-0.1.1 with one code line",
             {**labels, "scripts/minions.py": tool.replace("return 0", "return 1")}, False,
             "scripts/minions.py differs from main in more than its one version line",
             {"branch": "release-sync-1", "base": "release-0.1.1"}),
            ("a head named for nothing into release-0.1.1, the labels only", labels, True,
             "ok release-0.1.1 differs from main only in: ", {"branch": "fix-readme", "base": "release-0.1.1"}),
            ("a branch that names no version", labels, False, "does not name a version",
             {"branch": "release-next"}),
            ("origin not readable, an older copy of main at hand", labels, False, "main could not be read",
             {"origin": False}),
        ]
        for what, changes, ok, words, how in cases:
            with self.subTest(what):
                r = release_line(changes, **how)
                out = r.stdout + r.stderr
                self.assertEqual(r.returncode == 0, ok, f"{what}: {out}")
                self.assertIn(words, out, f"{what}: {out}")
                if ok:  # its ok line names every file that differs
                    said = out.split("differs from main only in: ", 1)[1].split()
                    self.assertEqual(sorted(said), sorted(changes), what)

    def test_G9_16_a_gate_reads_a_version_only_where_it_stands_whole(self):
        # Row R194: three places where a gate took a version, or this repository, from part of a word. Each form is
        # planted where that gate reads it: the CI step on main's README, and the reads test_G9_8 makes of the
        # README and the skill (tests/version_places.py, the one place both read through).
        url = "https://github.com/TheWindstorm/windstorm-repo-minions"
        cases = [  # (README text, tags on GitHub's copy, passes)
            # (1) two clones on one line, one unpinned: a count of lines saw one clone, pinned
            (f"`git clone --branch v0.1.0 {url}` or `git clone {url}`\n", ["v0.1.0"], False),
            (f"`git clone --branch v0.1.0 {url}` or `git clone --branch v0.1.0 {url}`\n", ["v0.1.0"], True),
            # (2) another repository whose name starts with this one's is not a pinned clone of this one
            (f"`git clone --branch v0.1.0 {url}-other`\n", ["v0.1.0"], False),
            (f"`git clone --branch v0.1.0 {url}s`\n", ["v0.1.0"], False),
            (f"`git clone --branch v0.1.0 {url}.git`\n", ["v0.1.0"], True),  # this one's own address, with .git
            (f"git clone --branch v0.1.0 {url}\n", ["v0.1.0"], True),  # the address at the line's end
        ]
        for readme, tags, ok in cases:
            r = release_place(readme, tags)
            self.assertEqual(r.returncode == 0, ok, f"{readme!r} tags {tags}: {r.stdout}{r.stderr}")
        vp = version_places
        self.assertEqual(vp.clone_options(f"`git clone --branch v0.1.0 {url}` or `git clone {url}`"),
                         ["--branch v0.1.0", ""])
        for other in (f"{url}-other", f"{url}s", f"{url}.github.io", f"{url}_x"):
            self.assertEqual(vp.clone_options(f"`git clone --branch v0.1.0 {other}`"), [], other)
            self.assertFalse(vp.clones_ours(f"`git clone {other}`"), other)
        for own in (f"`git clone --branch v0.1.0 {url}`", f"`git clone --branch v0.1.0 {url}.git`",
                    f"git clone --branch v0.1.0 {url}", f"(git clone --branch v0.1.0 {url})"):
            self.assertEqual(vp.clone_options(own), ["--branch v0.1.0"], own)
            self.assertTrue(vp.clones_ours(own), own)
            self.assertEqual(vp.clones_not_read(own), [], own)
        # Every clone has three answers, never two (the maintainer's principle, 2026-11-25): this repository whole, read
        # and checked with the letter case of host, owner and name ignored; an address listed in OTHERS; or NOT READ,
        # and the version test fails naming the line. A sentence's closing marks end the address.
        for form in (f"run git clone --branch v0.1.0 {url}.", f"run git clone --branch v0.1.0 {url}. Then look",
                     f"run git clone --branch v0.1.0 {url}.git.", f"run git clone --branch v0.1.0 {url}... then",
                     "`git clone --branch v0.1.0 https://github.com/thewindstorm/windstorm-repo-minions`",
                     "`git clone --branch v0.1.0 https://GITHUB.COM/TheWindstorm/windstorm-repo-minions`",
                     "`git clone --branch v0.1.0 git@github.com:TheWindstorm/windstorm-repo-minions.git`",
                     "`git clone --branch v0.1.0 ssh://git@github.com/TheWindstorm/windstorm-repo-minions`"):
            self.assertEqual(vp.clone_options(form), ["--branch v0.1.0"], form)
            self.assertTrue(vp.clones_ours(form), form)
            self.assertEqual(vp.clones_not_read(form), [], form)
        # two clones on one line with no code span between them are two, never one
        two = f"run git clone --branch v0.1.0 {url} or git clone {url}"
        self.assertEqual(vp.clone_options(two), ["--branch v0.1.0", ""])
        # not this repository whole, not listed: not read, and named by its line, never skipped
        for other in (f"{url}-other", f"{url}s", f"{url}.github.io", f"{url}_x",
                      "https://github.com/TheWindstorm/windstorm-pit-board", "a git clone of it"):
            text = f"`git clone --branch v0.1.0 {other}`" if "://" in other else other
            self.assertEqual(vp.clone_options(text), [], other)
            self.assertEqual(vp.clones_not_read(text), [text], other)  # the line as it stands, named
        # (3) a status heading names this version, never one that only starts with it
        for heading in ("0.1.0-rc1, not released", "0.1.0+local", "0.1.0a", "0.1.01", "0.1.0.1", "0.1.0rc1"):
            self.assertFalse(vp.heading_names(heading, "0.1.0"), heading)
        for heading in ("0.1.0, released 2026-11-27", "0.1.0 (the first release)", "0.1.0"):
            self.assertTrue(vp.heading_names(heading, "0.1.0"), heading)


def release_place(readme, tags):
    """Run the release-place CI step, as written in tests.yml, on main with this README; tags: those on GitHub's copy,
    None when it cannot be read. A tag v0.1.0 is in the checkout in every case: a tag only there must not count."""
    text = fread(os.path.join(ROOT, ".github", "workflows", "tests.yml"))
    job = text[text.index("\n  release-place:"):]
    lines = job.splitlines()
    i = next(n for n, x in enumerate(lines) if x.strip() == "run: |")
    indent = len(lines[i]) - len(lines[i].lstrip()) + 2
    body = []
    for x in lines[i + 1:]:
        if x.strip() and len(x) - len(x.lstrip()) < indent:
            break
        body.append(x[indent:])
    script = "set -e\n" + "\n".join(body) + "\n"  # GitHub runs a step with no shell named as bash -e
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k in ("GIT_CONFIG_GLOBAL",
                                                                                     "GIT_CONFIG_NOSYSTEM")}
    env.update(BRANCH="main", BASE_REF="", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "README.md"), "w", encoding="utf-8") as f:
            f.write(readme)
        run = lambda *a: subprocess.run(a, cwd=d, env=env, capture_output=True, text=True)
        run("git", "init", "-q"); run("git", "add", "."); run("git", "commit", "-qm", "r")
        run("git", "tag", "v0.1.0")
        origin = os.path.join(d, "origin.git")
        run("git", "init", "-q", "--bare", origin)
        for tag in tags or []:
            run("git", "push", "-q", origin, f"refs/tags/{tag}")
        run("git", "remote", "add", "origin", origin if tags is not None else os.path.join(d, "gone.git"))
        return run("bash", "-c", script)


RELEASE_MAIN = {  # main's files in the made repository of test_G9_21 (row R223)
    "README.md": "## Status: 0.1.0, released 2026-11-26\n\n"
                 "`cd ~ && git clone --branch v0.1.0 https://github.com/TheWindstorm/windstorm-repo-minions`\n",
    "CHANGELOG.md": "# Changelog\n\n## 0.1.0 (2026-11-26)\n\nfirst.\n",
    "scripts/minions.py": '#!/usr/bin/env python3\n"""the tool"""\nimport os\n\nVERSION = "0.1.0"\n\n\n'
                          "def main():\n    return 0\n",
    "skills/repo-minions/SKILL.md": "---\nname: repo-minions\n---\n\n1. `cd ~ && git clone --branch v0.1.0 "
                                    "https://github.com/TheWindstorm/windstorm-repo-minions`, in the person's home "
                                    "folder.\n2. Then look.\n",
    "tests/x.py": "x = 1\n",
}


def release_line(changes, branch="release-0.1.1", base="", origin=True, chmod=None, rename=None):
    """Run the release-place CI step, as written in tests.yml, on a release branch made from RELEASE_MAIN with these
    changes (a path's new text, or None to delete it); main is on an origin of its own and already fetched once, as
    the checkout leaves it, and with origin False that origin can no longer be read. branch is the pull request's
    head or the pushed branch; base is the pull request's base, empty for a push."""
    text = fread(os.path.join(ROOT, ".github", "workflows", "tests.yml"))
    job = text[text.index("\n  release-place:"):]
    lines = job.splitlines()
    i = next(n for n, x in enumerate(lines) if x.strip() == "run: |")
    indent = len(lines[i]) - len(lines[i].lstrip()) + 2
    body = []
    for x in lines[i + 1:]:
        if x.strip() and len(x) - len(x.lstrip()) < indent:
            break
        body.append(x[indent:])
    script = "set -e\n" + "\n".join(body) + "\n"  # GitHub runs a step with no shell named as bash -e
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") or k in ("GIT_CONFIG_GLOBAL",
                                                                                     "GIT_CONFIG_NOSYSTEM")}
    env.update(BRANCH=branch, BASE_REF=base, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid")
    with tempfile.TemporaryDirectory() as d:
        work = os.path.join(d, "work")
        os.makedirs(work)
        run = lambda *a: subprocess.run(a, cwd=work, env=env, capture_output=True, text=True)

        def put(files):
            for rel, body_text in files.items():
                p = os.path.join(work, rel)
                if body_text is None:
                    os.remove(p)
                    continue
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(body_text)
        run("git", "init", "-q", "-b", "main"); run("git", "config", "core.fileMode", "true")
        put(RELEASE_MAIN)
        run("git", "add", "-A"); run("git", "commit", "-qm", "main")
        origin_dir = os.path.join(d, "origin.git")
        run("git", "init", "-q", "--bare", origin_dir)
        run("git", "push", "-q", origin_dir, "main")
        run("git", "remote", "add", "origin", origin_dir)
        run("git", "fetch", "-q", "origin")  # as the checkout does: a copy of main is at hand before the step
        if not origin:
            run("git", "remote", "set-url", "origin", os.path.join(d, "gone.git"))
        run("git", "checkout", "-qb", branch)
        put(changes)
        if chmod:
            os.chmod(os.path.join(work, chmod), 0o755)
        if rename:
            run("git", "mv", *rename)
        run("git", "add", "-A"); run("git", "commit", "-qm", "labels")
        return run("bash", "-c", script)


@atexit.register
def _say_what_did_not_run():
    for line in NOT_RUN_HERE:
        print(f"not run here: {line}", file=sys.stderr)


def tearDownModule():
    machine_guard.check()  # rule 17 (R142): a test that reached this machine fails the run


if __name__ == "__main__":
    # R169 (the gate recipe's ninth case): the gate's own last line, written only after its whole run has ended; the
    # CI job is green only with it (and with each file's own "Ran"/"OK" summary), so a run that ends early is red
    result = unittest.main(exit=False).result
    print(f"gate: {'passed' if result.wasSuccessful() else 'FAILED'}: {result.testsRun} test(s) of the gate, every "
          "other test file run in its own process to its end", flush=True)
    sys.exit(0 if result.wasSuccessful() else 1)
