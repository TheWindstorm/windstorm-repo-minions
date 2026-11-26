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
    return ran


OUTCOME_RUNNER = """
import contextlib, json, os, sys, unittest
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
with open(os.devnull, "w") as dn, contextlib.redirect_stdout(dn), contextlib.redirect_stderr(dn):
    suite.run(res)
with open(out, "w") as f:
    json.dump({"passed": res.passed, "skipped": [[t._testMethodName, w] for t, w in res.skipped],
               "run": res.testsRun}, f)
"""


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
        _RAN = names_that_ran()
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
            ran = names_that_ran(d)
            twice = defined_twice(os.path.join(d, "test_probe.py"))
        self.assertEqual(ran, {"test_probe_reached", "test_probe_shadowed"})
        self.assertEqual(twice, ["test_probe_shadowed"])
        self.assertTrue(any("test_probe_skipped (a planted skip)" in x for x in NOT_RUN_HERE[before:]), NOT_RUN_HERE)
        del NOT_RUN_HERE[before:]

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
    env.update(BRANCH="main", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
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
