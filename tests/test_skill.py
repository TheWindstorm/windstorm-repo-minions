"""The skill file: its format, its order, the person's steps, and the sentences the plan fixes (G7, G8).

Standard library only. Run: python tests/test_skill.py -v
"""
import io
import json
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "repo-minions", "SKILL.md")


def fread(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def text():
    return fread(SKILL)


def flat(s: str) -> str:
    """The file's prose with line breaks and markdown emphasis taken out, so a sentence can be found whole."""
    return re.sub(r"\s+", " ", s.replace("**", ""))


def frontmatter(t: str) -> dict:
    """A small reader for the frontmatter the skill uses: `key: value` lines, values plain or single-quoted."""
    m = re.match(r"---\n(.*?)\n---\n", t, re.S)
    assert m, "no frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        if v.startswith("'") and v.endswith("'"):
            v = v[1:-1].replace("''", "'")
        out[k.strip()] = v
    return out


def section(t: str, heading: str) -> str:
    m = re.search(r"^## " + re.escape(heading) + r"\n(.*?)(?=^## |\Z)", t, re.S | re.M)
    assert m, f"no section {heading}"
    return m.group(1)


class Skill(unittest.TestCase):
    def test_G8_1_the_skill_file_passes_the_validator(self):
        t = text()
        fm = frontmatter(t)
        self.assertEqual(fm.get("name"), "repo-minions")
        self.assertEqual(os.path.basename(os.path.dirname(SKILL)), fm["name"], "the folder is named as the skill")
        self.assertRegex(fm["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertTrue(0 < len(fm.get("description", "")) <= 1024, len(fm.get("description", "")))
        self.assertIn("your own computers do your repo's chores", fm["description"])
        self.assertIn("Use when", fm["description"])
        self.assertLess(len(t.splitlines()), 500)
        # The Agent Skills validator, where this computer's GitHub CLI has it and is logged in (CI runs it too).
        if shutil.which("gh") and subprocess.run(["gh", "skill", "--help"], capture_output=True).returncode == 0 \
                and subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0:
            r = subprocess.run(["gh", "skill", "publish", "--dry-run"], cwd=ROOT, capture_output=True, text=True,
                               timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_G8_2_the_skill_names_the_order_and_the_persons_steps(self):
        t = text()
        order = section(t, "The order, always the same")
        steps = re.findall(r"^\d+\. \*\*(\w+)\*\*", order, re.M)
        self.assertEqual(steps, ["look", "tell", "setup", "move", "verify", "status"])
        persons = flat(section(t, "The person's steps"))
        for must in ("gh auth login", "admin password", "I never see it", "same terminal", "--start-test",
                     "says yes", "never merges", "merge it yourself", "--include-job <file>:<job>",
                     "never press Agree or Accept"):
            self.assertIn(must, persons, must)
        # R81: a computer without git (a stock Debian): installing it is the person's step, named with its command
        self.assertIn("installing it is the person's step", flat(t))
        self.assertIn("sudo apt update && sudo apt install git", flat(t))

    def test_G8_10_a_persons_step_is_never_done_another_way(self):
        persons = flat(section(text(), "The person's steps"))
        self.assertIn("A step that is the person's is never done another way", persons)
        self.assertIn("If you cannot do what a line says, or the command fails, tell the person what it printed and "
                      "ask", persons)
        self.assertIn("a download instead of the named install", persons)

    def test_G8_11_the_tools_words_are_quoted_exactly_or_not_called_its_words(self):
        lines = flat(section(text(), "What the tool prints"))
        self.assertIn("give them exactly as printed: add nothing inside them (no other time zone, no conversion, "
                      "no comment)", lines)
        self.assertIn("Your own explanation is never presented as the tool's", lines)
        self.assertIn("| `tell the person N of M` | words the person must hear | paste the whole block, 1 of M to M of M, "
                      "as printed", lines)
        before = flat(section(text(), "Before setup"))
        # Run B again: a small model shortened the block under the word "verbatim"; the block is pasted whole.
        self.assertIn("Paste them to the person as they are, as one block, all of them, numbers included: never "
                      "shortened, merged, reworded or left out. Add your own words only after the block, never inside "
                      "it.", before)

    def test_G8_8_the_skill_says_waiting_is_not_done(self):
        w = flat(section(text(), "Waiting"))
        self.assertIn("`waiting` is not done", w)
        self.assertIn("Never tell the person it worked while the last line is `waiting`", w)
        self.assertIn("what is being waited for, that nothing is broken, and when the wait ends", w)
        lines = flat(section(text(), "What the tool prints"))
        for kind in ("`ok`", "`warn`", "`stop`", "`need you`", "`waiting`"):
            self.assertIn(f"| {kind} |", lines)
        for code in ("| 0 |", "| 1 |", "| 2 |", "| 3 |", "| 4 |", "| 64 |", "| 130 |"):
            self.assertIn(code, lines)

    def test_G7_1_cost_words_are_read_and_dated_never_promised(self):
        cost = flat(section(text(), "Cost"))
        self.assertIn("Say the block's cost line word for word: what GitHub charges is GitHub's to say, with the "
                      "page's address", cost)
        self.assertIn("I could not read GitHub's page today.", cost)

    def test_G7_2_storage_is_said_to_count_the_same(self):
        self.assertIn("Artifact and cache storage count the same whichever computer ran the job.", flat(text()))

    def test_G7_3_what_a_job_can_reach_is_said_before_setup(self):
        before = flat(section(text(), "Before setup"))
        self.assertIn("the secrets a job can read (names only) and what already runs on this computer", before)
        self.assertIn("Anyone who can push to this repository can run code on this computer.", before)

    def test_G7_4_restart_and_waiting_limits_are_said(self):
        self.assertIn("A restart of this computer cancels the job running on it, a short sleep only delays it, and a "
                      "sleep of more than about 10 minutes fails it on GitHub, which `status` names as cut, not as your "
                      "test failing;", flat(section(text(), "Before the jobs move")))

    def test_G7_9_a_long_sleep_fails_the_job_and_is_said_before_setup(self):
        # Said in the skill, and printed by the tool in move's numbered block, before the merge that moves jobs here
        # (a small model skips a list, G7.10; each line where it matters, G7.14).
        self.assertIn("a sleep of more than about 10 minutes fails it on GitHub", flat(section(text(), "Before the jobs move")))
        import importlib.util
        spec = importlib.util.spec_from_file_location("m", os.path.join(ROOT, "scripts", "minions.py"))
        M = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(M)
        out = io.StringIO()
        rep = M.Report(out)
        M.before_move_words({"battery": False}, rep)
        rep.code()  # the numbered block is printed when the command ends
        said = out.getvalue()
        self.assertIn("a short sleep only delays it, and a sleep of more than about 10 minutes fails it on GitHub",
                      said)

    def test_G7_5_merge_rules_are_said_only_when_read(self):
        t = flat(text())
        self.assertIn("Never tell the person that required checks or branch protection hold their merges unless "
                      "look printed that it read them.", t)
        self.assertEqual(len(re.findall(r"branch protection", t)), 1, "branch protection is named only in the rule")

    def test_G7_7_waiting_checks_are_said_before_setup(self):
        self.assertIn("While this computer is off, asleep or held, your checks wait, and GitHub cancels a check that "
                      "has waited 24 hours.", flat(section(text(), "Before the jobs move")))

    def test_G7_8_the_limits_are_said_in_plain_words_before_setup(self):
        before = flat(section(text(), "Before setup"))
        self.assertIn("at most half of this computer's processors and 60 % of its memory", before)
        self.assertIn("with the real numbers", before)
        self.assertIn("`setup <owner/repo> --limits <processors>,<memory %>`", before)
        self.assertIn('On a computer with one processor the line says "1 of 1" and that everything else there will be '
                      "slow while a job runs: read that too, and let the person decide.", before)

    def test_the_skill_says_which_sleep_locks_are_taken_and_what_they_never_hold(self):
        before = flat(section(text(), "Before the jobs move"))
        self.assertIn("a sleep you ask for, the lid or a low battery still puts it to sleep", before)
        self.assertIn("It never holds the lid switch, the power or sleep keys, a shutdown, or a sleep someone asks "
                      "for.", before)

    def test_G8_17_the_agent_never_looks_into_the_persons_keys_or_saved_logins(self):
        # Row R110 (2026-11-23): a small-model cold start looked into the person's ~/.ssh and git's saved-login
        # settings; the skill says, in plain words, that it does not, and reaches GitHub only as the tool does.
        safety = flat(section(text(), "Safety rules"))
        self.assertIn("Do not look into the person's keys, their `~/.ssh` folder, git's saved logins or any other "
                      "saved login. Reach GitHub only as the tool does: through the GitHub CLI and its own login.", safety)
        with open(os.path.join(ROOT, "skills", "repo-minions", "evals", "evals.json"), encoding="utf-8") as f:
            first = json.load(f)["evals"][0]
        self.assertTrue(any("~/.ssh" in x and x.startswith("Must not:") for x in first["expectations"]), first)

    def test_G6_14_the_places_used_in_the_persons_home_are_named_and_listed_alike(self):
        # Family rule 23 (2026-11-23, row R119): a skill touches nothing in the person's home outside its own named
        # places, and a proof on a stock machine diffs the whole home against them. The globs the proof uses and the
        # places the skill's text names are the same list: no place on one side only.
        with open(os.path.join(ROOT, "docs", "home-named.txt"), encoding="utf-8") as f:
            globs = [x.strip() for x in f if x.strip() and not x.startswith("#")]
        bases = {g.split("/**")[0].rstrip("/") for g in globs}
        said = flat(section(text(), "Safety rules"))
        start = said.index("The person's home. In the person's home folder")
        end = said.find(" - ", start)
        sentence = said[start:end if end > 0 else len(said)]
        named = set(re.findall(r"`~/([^`]+)`", sentence))
        self.assertEqual(named, {"windstorm-repo-minions", ".config/gh", ".minions", "repo-minions-record"})
        for n in named:
            self.assertIn(n, bases, n)
            self.assertIn(n + "/**", globs, n)
        for b in bases:  # every listed place is named, or is the folder that holds a named one
            self.assertTrue(b in named or any(n.startswith(b + "/") for n in named), b)
        self.assertIn("It changes no other file of theirs; neither do you.", sentence)
        # The plugin road (rule 23, 2026-11-23): what Claude Code writes on a bare install is Claude Code's, proved by
        # the one listing pair that brackets the install with the family's shared list; never a glob in this file.
        self.assertIn("Installing the plugin is Claude Code's own act: it writes only Claude Code's plugin places. "
                      "The skill itself makes nothing until the person asks for runners to be set up.", sentence)
        self.assertFalse([g for g in globs if g.startswith(".claude")], globs)

    def test_G8_18_the_skill_never_offers_to_delete_what_uninstall_kept(self):
        # Rule 8 (2026-11-23, row R115): in run I the smallest model offered `rm -r` on the record uninstall keeps.
        safety = flat(section(text(), "Safety rules"))
        self.assertIn("Never delete, and never offer to delete, what an uninstall kept (the person's record of what "
                      "this tool did), unless the person asks for it in their own words in this conversation.", safety)
        with open(os.path.join(ROOT, "skills", "repo-minions", "evals", "evals.json"), encoding="utf-8") as f:
            first = json.load(f)["evals"][0]
        self.assertTrue(any(x.startswith("Must not: delete, or offer to delete, what uninstall kept")
                            for x in first["expectations"]), first)

    def test_G7_14_the_skill_says_each_block_where_it_is_printed(self):
        moving = flat(section(text(), "Before the jobs move"))
        self.assertIn("`move` ends with its own numbered `tell the person` block, just before it asks the person to "
                      "merge.", moving)
        self.assertIn("When `status` names a job this computer's memory limit stopped, it ends with one `tell the "
                      "person` line: paste it.", moving)
        self.assertIn("(5 on most computers, 6 on a laptop)", flat(section(text(), "Before setup")))

    def test_the_skill_carries_the_one_repository_sentence(self):
        self.assertIn("This version serves one repository per computer.", flat(text()))

    def test_the_skill_carries_the_safety_rules(self):
        safety = flat(section(text(), "Safety rules"))
        for must in ("Private repositories only.", "The tool never merges.", "No token on a command line.",
                     "counts their containers and runners and never names them"):
            self.assertIn(must, safety, must)

    def test_the_skill_never_promises_cost_or_says_what_it_cannot_know(self):
        t = text()
        for word in ("free", "no bill", "save money", "saves", "first", "identical", "a law"):
            self.assertNotRegex(t.lower(), r"\b" + re.escape(word) + r"\b", word)
        for name in ("veron", "windy", "eth-", "tailscale", "user1", "/home/"):
            self.assertNotIn(name, t.lower(), name)


if __name__ == "__main__":
    unittest.main()
