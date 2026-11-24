"""Tests for scripts/minions.py, one per codon of the plan (DNA-STRAND-MASTER-PLAN.md), plus helpers.

GitHub is stood in for by answers recorded from a real private repository (tests/fixtures/*.jsonl, scrubbed by
tests/fixtures/scrub.py); the computer by FakeHost. Run: python tests/test_minions.py -v
"""
import ast
import atexit
import copy
import importlib.util
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import machine_guard  # noqa: E402  rule 17 (R142): first, so no test here reaches this machine

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import minions as M  # noqa: E402
M.VERSION_ON_DISK = M.VERSION

FIX = os.path.join(ROOT, "tests", "fixtures")
TMP = tempfile.mkdtemp(prefix="minions-test-")
atexit.register(shutil.rmtree, TMP, True)
REPO = "example-org/example-repo"
BRANCH = re.compile(r"minions-start-test-[0-9a-f]{8}")


TELL = re.compile(r"^tell the person (\d+) of (\d+): ")


def tells_of(lines):
    """The tell lines' texts; each block numbered whole, 1 of N to N of N, in order (G7.10)."""
    out, block = [], []
    for x in lines + [""]:
        m = TELL.match(x)
        if m:
            block.append((int(m.group(1)), int(m.group(2)), x[m.end():]))
            continue
        if block:
            n = len(block)
            assert [(i, k) for i, k, _ in block] == [(i, n) for i in range(1, n + 1)], block
            out += [t for _, _, t in block]
            block = []
    return out

def load(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return [json.loads(x) for x in f]


class StandIn(M.GitHub):
    """Answers each call with the next recorded answer for the same method and path (branch names normalised).
    A call with no recorded answer gets 404 and is kept in `misses`."""

    def __init__(self, rows, overrides=None):
        self.rows = [dict(r, path=BRANCH.sub("BRANCH", r["path"])) for r in copy.deepcopy(rows)]
        self.overrides = overrides or {}
        self.calls, self.misses = [], []

    def api(self, method, path, body=None):
        key = (method, BRANCH.sub("BRANCH", path))
        self.calls.append(key)
        if key in self.overrides:
            o = self.overrides[key]
            return o(body) if callable(o) else o
        for i, r in enumerate(self.rows):
            if (r["method"], r["path"]) == key:
                self.rows.pop(i)
                return r["status"], r["data"]
        self.misses.append(key)
        return 404, {"message": "Not Found"}


class FakeHost(M.Host):
    def __init__(self, system="Linux", files=None, runs=None, which=None, uid=1000,
                 groups=("users",), dirs=None, links=None, page="", free=(100 * 1024 ** 3, 500 * 1024 ** 3)):
        self._system, self.files, self.runs = system, dict(files or {}), dict(runs or {})
        # R160: gh has a login it can read with no session bus (a made-up value, one line, as `gh auth token` prints)
        self.runs.setdefault(("gh", "auth", "token"), (0, "made-up-login-value\n"))
        self.runs.setdefault(("gh", "auth", "login", "--hostname", "github.com", "--help"), (0, GH_LOGIN_HELP))
        if which is None:  # every program the tool and its hooks run (G8.9); no container engine
            which = ("gh", "sudo", "systemctl", *M.PROGRAMS, *M.HOOK_PROGRAMS)
        self._which, self._uid, self._groups = set(which), uid, list(groups)
        self.dirs, self.links, self.page, self.free = dict(dirs or {}), dict(links or {}), page, free
        self.ran, self.fed = [], []  # every argv run; (argv, standard input) of every run_in
        self._state = tempfile.mkdtemp(dir=TMP)
        self.home_mode, self.download, self.validated, self.tty = 0o750, b"a stand-in runner archive", 0, True
        self.enter, self.read_enters = True, 0  # G7.15: the person's own terminal; Enter pressed

    def _answer(self, argv, input=None):
        for k in sorted(self.runs, key=len, reverse=True):
            if tuple(argv[:len(k)]) == k:
                a = self.runs[k]
                return a(argv, input) if callable(a) else a
        return 1, ""

    def run_in(self, argv, input, timeout=600):
        self.ran.append(tuple(argv))
        self.fed.append((tuple(argv), input))
        return self._answer(argv, input)

    def state_dir(self):
        return self._state

    def hostname(self):
        return "box-1"

    def fetch_to(self, url, path, timeout=300):
        self.fetched = url
        with open(path, "wb") as f:
            f.write(self.download)

    def slept_s(self):  # R83: seconds suspended since boot; a test moves it with the clock to stand in for a sleep
        return getattr(self, "slept", 0.0)

    def isatty(self):
        return self.tty

    def read_enter(self):
        self.read_enters += 1
        return self.enter

    def sudo_validate(self):
        self.validated += 1
        self.runs[("sudo", "-n", "true")] = (0, "")
        return 0

    def mode_of(self, path):
        return self.home_mode if path == self.home() else 0o700

    def which(self, name):
        return f"/usr/bin/{name}" if name in self._which else None

    def run_own(self, argv, timeout=20):  # R160: the person's own environment, read for where gh keeps a login
        self.ran.append(("own", *argv))
        return self._answer(["own", *argv])

    def run(self, argv, timeout=20):
        self.ran.append(tuple(argv))
        rc, out = self._answer(argv)
        if rc == 0 and argv[:3] == ["sudo", "-n", "install"] and argv[-1] == M.TOOL_COPY:  # G4.10: the copy is there
            with open(argv[-2], encoding="utf-8") as f:
                self.files[M.TOOL_COPY] = f.read()
        return rc, out

    def read(self, path):
        return self.files.get(path)

    def write_text(self, path, text):
        if os.path.dirname(path) == M.SEEN_ROOT and getattr(self, "seen_unwritable", False):
            raise PermissionError(13, "Permission denied", path)
        self.files[path] = text

    def listdir(self, path):
        return self.dirs.get(path, [])

    def system(self):
        return self._system

    def machine(self):
        return "x86_64"

    def cpu_count(self):
        return 8

    def uid(self):
        return self._uid

    def groups(self):
        return self._groups

    def home(self):
        return "/home/person"

    # G6.10: the person's record, kept in the stand-in's files, never in a real home.
    def keep_in_record(self, name, text, append=True):
        if getattr(self, "record_unwritable", False):
            raise OSError(28, "No space left on device")
        path = f"{self.record_dir()}/{name}"
        self.files[path] = (self.files.get(path, "") if append else "") + text

    def record_count(self):
        mine = [v for k, v in self.files.items() if k.startswith(self.record_dir() + "/")]
        return len(mine), sum(len(v.splitlines()) for v in mine)

    def delete_record(self):
        for k in [k for k in self.files if k.startswith(self.record_dir() + "/")]:
            del self.files[k]

    def ask(self, prompt):
        self.asked = prompt
        return getattr(self, "answer", "")

    def listdir_or_none(self, path):
        return self.dirs.get(path, [])

    def there(self, path):
        return path in self.files or path in self.dirs

    def user(self):
        return "person"

    def disk_free(self, path):
        return self.free

    def dev_of(self, path):
        return (259, 2)

    def realpath(self, path):
        return self.links.get(path, path)

    def fetch(self, url, timeout=30):
        if isinstance(self.page, Exception):
            raise self.page
        return self.page

    def sleep(self, seconds):
        pass


SCOPES = "github.com\n  ✓ Logged in to github.com account example-person\n  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'\n"
# R146: `gh api -i user` as Ubuntu's own gh 2.45.0 answered it on a stock virtual machine, 2026-11-24 (names made up)
USER_OK = 'HTTP/2.0 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n\r\n{"login":"example-person","id":1}'
NO_LOGIN = ("To get started with GitHub CLI, please run:  gh auth login\n"
            "Alternatively, populate the GH_TOKEN environment variable with a GitHub API authentication token.\n")
UNREACHABLE = ('Get "https://api.github.com/user": proxyconnect tcp: dial tcp 127.0.0.1:9: connect: connection '
               'refused\n')
MEMINFO = "MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\nSwapTotal:       2097152 kB\nSwapFree:        2097152 kB\n"
ROUTE = "Iface\tDestination\tGateway\nenp3s0\t00000000\t0101A8C0\n"
TCP = ("  sl  local_address rem_address   st\n"
       "   0: 00000000:01BB 00000000:0000 0A\n"
       "   1: 0100007F:1538 00000000:0000 0A\n"
       "   2: 00000000:0016 0A0B0C0D:D431 01\n")


# R160: the line of `gh auth login --help` (gh 2.45.0) that names the flag
GH_LOGIN_HELP = "      --insecure-storage     Save authentication credentials in plain text instead of credential store\n"


def linux_host(**kw):
    files = {"/etc/os-release": 'PRETTY_NAME="Example Linux 1"\nID=ubuntu\nID_LIKE=debian\n',
             "/proc/meminfo": MEMINFO, "/proc/net/route": ROUTE, "/proc/net/tcp": TCP,
             "/sys/devices/nvme0n1/queue/rotational": "0\n"}
    files.update(kw.pop("files", {}))
    runs = {("gh", "auth", "status"): (0, SCOPES), ("gh", "api", "-i", "user"): (0, USER_OK), ("sudo", "-n", "true"): (0, ""),
            ("systemctl", "is-enabled"): (0, "enabled\nenabled\n")}
    runs.update(kw.pop("runs", {}))
    links = {"/sys/dev/block/259:2": "/sys/devices/nvme0n1/nvme0n1p2"}
    with open(os.path.join(FIX, "billing-page-excerpt.html"), encoding="utf-8") as f:
        page = f.read()
    dirs = {M.SYSTEMD_RUN_DIR: []}  # R155: systemd runs this stand-in computer, as it does a stock Ubuntu
    dirs.update(kw.pop("dirs", {}))
    return FakeHost(files=files, runs=runs, links=links, page=kw.pop("page", page), dirs=dirs, **kw)


def look(gh, host, start=False):
    out = io.StringIO()
    code = M.look(REPO, start, host=host, gh=gh, out=out)
    return code, out.getvalue().splitlines()


def with_repo(rows, change):
    rows = copy.deepcopy(rows)
    for r in rows:
        if r["method"] == "GET" and r["path"] == "repos/" + REPO:
            change(r["data"])
    return rows


class G1(unittest.TestCase):
    def setUp(self):
        self.recent = load("look-recent-runs.jsonl")
        self.start = load("look-start-test.jsonl")

    def test_G1_1_look_stops_on_a_repository_that_is_not_private(self):
        rows = with_repo(self.recent, lambda d: d.update(visibility="public", private=False))
        gh = StandIn(rows)
        code, lines = look(gh, linux_host())
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue(lines[-1].startswith("stop ") and "is public" in lines[-1], lines)
        self.assertEqual(gh.calls, [("GET", "repos/" + REPO)], "nothing more is read or written after the stop")

    def test_G1_2_look_names_account_kind_and_runner_scope(self):
        _, lines = look(StandIn(self.recent), linux_host())
        self.assertIn("ok organisation: this version gives this one repository its own runner on this computer; "
                      "runner groups for an organisation's chosen repositories come in the next version", lines)
        rows = with_repo(self.recent, lambda d: d["owner"].update(type="User"))
        _, lines = look(StandIn(rows), linux_host())
        self.assertIn("ok personal account: one runner per repository", lines)

    def test_G1_3_look_names_missing_token_rights(self):
        rows = with_repo(self.recent, lambda d: d["permissions"].update(admin=False))
        host = linux_host(runs={("gh", "auth", "status"): (0, SCOPES.replace(", 'workflow'", ""))})
        _, lines = look(StandIn(rows), host)
        self.assertTrue(any(x.startswith("need you: admin on the repository") for x in lines), lines)
        need = [x for x in lines if x.startswith("need you: your login may not change workflow files")]
        self.assertEqual(len(need), 1, lines)
        self.assertIn(M.GH_SCOPE_DOCS, need[0])
        self.assertNotIn("gh auth refresh", need[0])  # the owner's ruling, G8.16: never run as printed here
        host = linux_host(runs={("gh", "auth", "status"): (0, "  ✓ Logged in to github.com account x\n")})
        _, lines = look(StandIn(self.recent), host)
        self.assertTrue(any("could not read the token's scopes" in x for x in lines), lines)
        # R79: an older gh (2.23, Debian 12's) prints the scopes without quotes; read the same way
        bare = "  ✓ Logged in to github.com as example-person (/home/x/.config/gh/hosts.yml)\n" \
               "  ✓ Token scopes: gist, read:org, repo, workflow\n"
        self.assertEqual(M.token_scopes(linux_host(runs={("gh", "auth", "status"): (0, bare)})),
                         {"gist", "read:org", "repo", "workflow"})
        _, lines = look(StandIn(self.recent), linux_host(runs={("gh", "auth", "status"): (0, bare)}))
        self.assertIn("ok your login has the `workflow` scope (needed to change workflow files)", lines)
        # R97: gh 2.4.0 (Ubuntu 22.04's) prints no scopes; GitHub's own header on one answer gives them.
        old_gh = "github.com\n  ✓ Logged in to github.com as example-person (/home/x/.config/gh/hosts.yml)\n" \
                 "  ✓ Token: *******************\n"
        header = "HTTP/2.0 200 OK\r\nX-Oauth-Scopes: gist, read:org, repo, workflow\r\nX-Github-Media-Type: github.v3\r\n\r\n{}"
        host = linux_host(runs={("gh", "auth", "status"): (0, old_gh), ("gh", "api", "-i", "user"): (0, header)})
        self.assertEqual(M.token_scopes(host), {"gist", "read:org", "repo", "workflow"})
        _, lines = look(StandIn(self.recent), host)
        self.assertIn("ok your login has the `workflow` scope (needed to change workflow files)", lines)
        # No such header (a fine-grained token): still said as not read, never guessed.
        host = linux_host(runs={("gh", "auth", "status"): (0, old_gh),
                                ("gh", "api", "-i", "user"): (0, "HTTP/2.0 200 OK\r\n\r\n{}")})
        self.assertIsNone(M.token_scopes(host))
        # A body that names the header is never read as one.
        host = linux_host(runs={("gh", "auth", "status"): (0, old_gh),
                                ("gh", "api", "-i", "user"): (0, "HTTP/2.0 200 OK\r\n\r\nX-Oauth-Scopes: workflow")})
        self.assertIsNone(M.token_scopes(host))

    def test_G1_4_the_start_test_leaves_nothing_and_names_a_zero_job_startup_failure(self):
        # 1. The recorded real start test: jobs start; the run and the branch are deleted and read back gone.
        gh = StandIn(self.start)
        code, lines = look(gh, linux_host(), start=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn("ok start test: GitHub creates jobs here (1 job, queued); it ran on no machine", lines)
        self.assertIn("ok start test: the run and the branch are deleted, read back gone", lines)
        posted = [p for m, p in gh.calls if m == "POST"]
        self.assertEqual(posted[:4], [f"repos/{REPO}/git/{x}" for x in ("blobs", "trees", "commits", "refs")])
        self.assertIn(("DELETE", f"repos/{REPO}/git/refs/heads/BRANCH"), gh.calls)
        self.assertTrue(any(m == "DELETE" and "/actions/runs/" in p for m, p in gh.calls))
        # The commit has no parent and holds only our file, so none of the person's workflows can run on it.
        bodies = {}
        gh2 = StandIn(self.start, overrides={})
        real_api = gh2.api

        def spy(method, path, body=None):
            if method == "POST":
                bodies[path.rsplit("/", 1)[-1]] = body
            return real_api(method, path, body)
        gh2.api = spy
        look(gh2, linux_host(), start=True)
        self.assertEqual(bodies["commits"]["parents"], [])
        self.assertEqual([x["path"] for x in bodies["trees"]["tree"]], [M.START_FILE])
        self.assertTrue(bodies["refs"]["ref"].startswith("refs/heads/" + M.START_BRANCH_PREFIX))
        self.assertIn(f"runs-on: [{M.START_LABEL}]", bodies["blobs"]["content"])
        # 2. GitHub does not start the run: startup_failure with zero jobs stops setup, and still cleans up.
        rows = copy.deepcopy(self.start)
        for r in rows:
            if r["method"] == "GET" and "/jobs" in r["path"]:
                r["data"] = {"total_count": 0, "jobs": []}
            if r["method"] == "GET" and isinstance(r["data"], dict):
                for run in r["data"].get("workflow_runs", []) + ([r["data"]] if "conclusion" in r["data"] else []):
                    run.update(status="completed", conclusion="startup_failure")
        gh = StandIn(rows)
        code, lines = look(gh, linux_host(), start=True)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue(any(x.startswith("stop start test: " + M.ZERO_JOB_WORDS) for x in lines), lines)
        self.assertIn(("DELETE", f"repos/{REPO}/git/refs/heads/BRANCH"), gh.calls)
        # 3. An error after the branch is made (no run ever appears): the branch is still deleted.
        gh = StandIn(self.start, overrides={("GET", f"repos/{REPO}/actions/runs?branch=BRANCH&per_page=5"):
                                            (500, {"message": "boom"})})
        code, lines = look(gh, linux_host(), start=True)
        self.assertTrue(any("no run appeared within the time limit" in x for x in lines), lines)
        self.assertIn(("DELETE", f"repos/{REPO}/git/refs/heads/BRANCH"), gh.calls)
        # 3b. The run appears only after the branch is gone: it is cancelled and deleted too.
        seen = {"n": 0}

        def late_runs(body):
            seen["n"] += 1
            return (500, {"message": "boom"}) if seen["n"] == 1 else (200, {"workflow_runs": [{"id": 777}]})
        gh = StandIn(self.start, overrides={("GET", f"repos/{REPO}/actions/runs?branch=BRANCH&per_page=5"): late_runs,
                                            ("GET", f"repos/{REPO}/actions/runs/777"): (404, {}),
                                            ("POST", f"repos/{REPO}/actions/runs/777/cancel"): (202, {}),
                                            ("DELETE", f"repos/{REPO}/actions/runs/777"): (204, None)})
        start_wait = M.start_test.__defaults__
        M.start_test.__defaults__ = (5, 5)
        try:
            code, lines = look(gh, linux_host(), start=True)
        finally:
            M.start_test.__defaults__ = start_wait
        self.assertIn(("DELETE", f"repos/{REPO}/actions/runs/777"), gh.calls)
        self.assertIn("ok start test: the run and the branch are deleted, read back gone", lines)
        # 3c. R82: GitHub gives no clear answer on the read-back (a 504, seen on a stock machine): asked again, and a
        # run GitHub never says is there is not called left.
        for answers, want in (([504, 504, 404], "ok start test: the run and the branch are deleted, read back gone"),
                              ([504, 504, 504], "warn start test: deleted run 555, but GitHub gave no clear answer"),
                              ([200, 404, 404], "ok start test: the run and the branch are deleted, read back gone"),
                              ([200, 200, 200], "warn start test: could not remove run 555")):
            later = list(answers)
            first = {"read": True}

            def run_reads(body):  # the first read is before the delete (the run has finished); then the read-backs
                if first.pop("read", False):
                    return 200, {"status": "completed"}
                return later.pop(0), {"message": "x"}
            gh = StandIn([], overrides={("GET", f"repos/{REPO}/actions/runs/555"): run_reads,
                                        ("DELETE", f"repos/{REPO}/actions/runs/555"): (204, None),
                                        ("DELETE", f"repos/{REPO}/git/refs/heads/BRANCH"): (204, None)})
            out = io.StringIO()
            M.cleanup_start_test(gh, linux_host(), REPO, "minions-start-test-abc123", 555, M.Report(out))
            said = out.getvalue()
            self.assertTrue(any(x.startswith(want) for x in said.splitlines()), said)
            if "could not remove" not in want:
                self.assertNotIn("could not remove", said)
        # 4. The branch cannot be pushed (no workflow scope): said, and the recent runs are read instead.
        gh = StandIn(self.recent, overrides={("POST", f"repos/{REPO}/git/blobs"):
                                             (201, {"sha": "a" * 40}),
                                             ("POST", f"repos/{REPO}/git/trees"): (201, {"sha": "b" * 40}),
                                             ("POST", f"repos/{REPO}/git/commits"): (201, {"sha": "c" * 40}),
                                             ("POST", f"repos/{REPO}/git/refs"):
                                             (404, {"message": "refusing to allow an OAuth App to create or update "
                                                               "workflow without `workflow` scope"})})
        code, lines = look(gh, linux_host(), start=True)
        self.assertTrue(any("the `workflow` scope is missing" in x for x in lines), lines)
        self.assertTrue(any(x.startswith("ok jobs start here (last finished run") for x in lines), lines)
        self.assertNotIn(("DELETE", f"repos/{REPO}/git/refs/heads/BRANCH"), gh.calls)

    def test_G1_5_look_names_the_secrets_a_job_could_read(self):
        sec = {"total_count": 2, "secrets": [{"name": "DEPLOY_KEY"}, {"name": "NPM_TOKEN"}]}
        env = {"total_count": 1, "environments": [{"name": "prod"}]}
        envsec = {"total_count": 1, "secrets": [{"name": "PROD_DB"}]}
        gh = StandIn(self.recent, overrides={
            ("GET", f"repos/{REPO}/actions/secrets?per_page=100"): (200, sec),
            ("GET", f"repos/{REPO}/environments?per_page=100"): (200, env),
            ("GET", f"repos/{REPO}/environments/prod/secrets?per_page=100"): (200, envsec),
            ("GET", f"repos/{REPO}/actions/organization-secrets?per_page=100"): (403, {"message": "no"})})
        _, lines = look(gh, linux_host())
        line = [x for x in lines if "secret(s) a job" in x][0]
        for n in ("DEPLOY_KEY", "NPM_TOKEN", "PROD_DB (environment prod)"):
            self.assertIn(n, line)
        self.assertIn("warn could not read: organisation secrets", lines)
        _, lines = look(StandIn(self.recent), linux_host())
        self.assertIn("ok no Actions secrets a job of this repository can read", lines)

    def test_G1_6_look_surveys_the_computer(self):
        host = linux_host(dirs={"/sys/class/power_supply": ["BAT0"]},
                          files={"/sys/class/power_supply/BAT0/type": "Battery\n",
                                 "/proc/meminfo": MEMINFO.replace("SwapFree:        2097152", "SwapFree:         100000")})
        _, lines = look(StandIn(self.recent), host)
        self.assertIn("ok Example Linux 1, x86_64, 8 cores, 15.6 GB, 7.8 GB available", lines)
        self.assertTrue(any("of swap in use now" in x for x in lines), lines)
        self.assertIn("ok disk for the work folders: SSD, 100.0 GB free of 500.0 GB", lines)
        self.assertTrue(any(x.startswith("warn this computer has a battery") for x in lines), lines)
        self.assertIn("ok network: wired or not Wi-Fi", lines)
        self.assertIn("warn sleep: the system may sleep (sleep.target not masked)", lines)
        # What cannot be read is said, never guessed.
        host = linux_host(files={"/proc/meminfo": "", "/proc/net/route": "",
                                 "/sys/devices/nvme0n1/queue/rotational": None},
                          runs={("systemctl", "is-enabled"): (1, "")})
        host.files = {k: v for k, v in host.files.items() if v is not None}
        _, lines = look(StandIn(self.recent), host)
        self.assertIn("warn swap in use: not read", lines)
        self.assertIn("warn network: not read", lines)
        self.assertIn("warn sleep: not read", lines)
        self.assertTrue(any("disk kind not read" in x for x in lines), lines)

    def test_G1_7_look_names_what_already_lives_on_the_computer(self):
        host = linux_host(which=("gh", "sudo", "systemctl", "podman"),
                          runs={("podman", "ps"): (0, "my-db\n")})
        _, lines = look(StandIn(self.recent), host)
        self.assertIn("warn 1 port open to the network here: 443", lines)
        self.assertIn("warn 1 container(s) of yours running: not named here (`look <owner/repo> --show-names` names "
                      "them)", lines)  # G1.16: named only on the person's own flag
        self.assertTrue(any("serves something live" in x for x in lines), lines)
        self.assertEqual(M.listening_ports(TCP), {443}, "loopback and non-listening sockets are not counted")
        host = linux_host(files={"/proc/net/tcp": "  sl local st\n"})
        _, lines = look(StandIn(self.recent), host)
        self.assertIn("ok no port open to the network", lines)
        self.assertFalse(any("serves something live" in x for x in lines))
        # R72: loopback is all of 127.0.0.0/8 (a stock Ubuntu's own name service listens on 127.0.0.53) and 127.x
        # mapped into IPv6; the computer's own upkeep (here remote login) is not something live; any other port is,
        # and the look's record keeps it for setup.
        self.assertEqual(M.listening_ports("  sl\n 0: 3500007F:0035 00000000:0000 0A\n"
                                           " 1: 0000000000000000FFFF00000100007F:1F90 00000000:0000 0A\n"), set())
        host = linux_host(files={"/proc/net/tcp": "  sl\n 0: 00000000:0016 00000000:0000 0A\n"
                                                  " 1: 3500007F:0035 00000000:0000 0A\n"})
        _, lines = look(StandIn(self.recent), host)
        self.assertIn("ok 1 port open to the network for this computer's own upkeep: 22 (remote login); normal on most "
                      "computers, not counted as something live", lines)
        self.assertFalse(any("open to the network here" in x for x in lines), lines)
        # R80: Debian 12's own name resolution (LLMNR, 5355) is upkeep too
        host = linux_host(files={"/proc/net/tcp": "  sl\n 0: 00000000:14EB 00000000:0000 0A\n"})
        _, lines = look(StandIn(self.recent), host)
        self.assertIn("ok 1 port open to the network for this computer's own upkeep: 5355 (local name resolution); "
                      "normal on most computers, not counted as something live", lines)
        self.assertFalse(any("serves something live" in x for x in lines), lines)
        self.assertFalse(any("serves something live" in x for x in lines), lines)
        self.assertFalse(M.load_state(host)["looks"][REPO]["survey"]["serves"])
        host = linux_host(files={"/proc/net/tcp": "  sl\n 0: 00000000:1F98 00000000:0000 0A\n"})
        _, lines = look(StandIn(self.recent), host)
        self.assertTrue(any("serves something live" in x for x in lines), lines)
        s = M.load_state(host)["looks"][REPO]["survey"]
        self.assertEqual((s["serves"], s["serves_what"]), (True, "port(s) open to the network: 8088"))

    def test_G1_16_names_are_printed_only_of_what_the_person_chose(self):
        # The family's rule 18 (2026-11-23): what the person did not choose is counted; its names (which may be the
        # person's projects) are printed only on a flag of their own. The repository they named is theirs to see.
        host = linux_host(which=("gh", "sudo", "systemctl", "podman"),
                          runs={("podman", "ps"): (0, "client-acme-db\n"),
                                ("systemctl", "list-units"): (0, "actions.runner.o-r.box-r.service loaded active running X\n"),
                                ("systemctl", "show", "-p", "User", "--value", "actions.runner.o-r.box-r.service"): (0, "person\n")})
        out = io.StringIO()
        M.main(["look", REPO], host=host, out=out, gh=StandIn(self.recent))
        said = out.getvalue()
        self.assertNotIn("client-acme-db", said)
        self.assertNotIn("box-r", said)
        self.assertIn("1 container(s) of yours running: not named here", said)
        self.assertIn("1 other runner service(s) of yours here: not named here", said)
        out = io.StringIO()
        M.main(["look", REPO, "--show-names"], host=host, out=out, gh=StandIn(self.recent))
        said = out.getvalue()
        self.assertIn("1 container(s) of yours running: client-acme-db (podman, yours)", said)
        self.assertIn("1 other runner service(s) of yours here: actions.runner.o-r.box-r.service", said)

    def test_G1_12_other_peoples_things_are_counted_never_named(self):
        host = linux_host(which=("gh", "sudo", "systemctl", "docker"),
                          runs={("docker", "ps"): (0, "shop-db\nshop-web\n"),
                                ("systemctl", "list-units"): (0, "actions.runner.o-r.box-r.service loaded active running X\n"
                                                                 "actions.runner.o-q.box-q.service loaded active running X\n"),
                                ("systemctl", "show", "-p", "User", "--value", "actions.runner.o-r.box-r.service"): (0, "person\n"),
                                ("systemctl", "show", "-p", "User", "--value", "actions.runner.o-q.box-q.service"): (0, "someone-else\n")})
        _, lines = look(StandIn(self.recent), host)
        self.assertIn("warn 2 container(s) running on this computer's shared container service "
                      "(not named: they may be another person's)", lines)
        self.assertFalse(any("shop-db" in x or "someone-else" in x or "box-q" in x for x in lines), lines)
        self.assertIn("warn 1 other runner service(s) of yours here: not named here (`look <owner/repo> --show-names` "
                      "names them)", lines)
        self.assertIn("warn 1 other runner service(s) here that do not run as you (not named)", lines)
        self.assertTrue(any("serves something live" in x for x in lines), lines)
        # Our own runner, after setup, runs as minions-runner: named as ours, never counted as another (P1a).
        M.save_state(host, {"runners": {"o/q": {"unit": "actions.runner.o-q.box-q.service"}}})
        _, lines = look(StandIn(self.recent), host)
        self.assertIn("ok this tool's own runner here: actions.runner.o-q.box-q.service", lines)
        self.assertFalse(any("do not run as you" in x for x in lines), lines)
        # R78: uninstall's census counts another person's runner service the same way, never naming it; with no
        # runner of ours left, the slice is not said to be kept "for the other runners".
        M.save_state(host, {"runners": {}})
        out = io.StringIO()
        M.census(StandIn(self.recent), host, [], False, M.Report(out))
        said = out.getvalue()
        self.assertNotIn("box-q", said)
        self.assertNotIn("someone-else", said)
        self.assertNotIn("box-r", said)  # G1.16: the person's own runner service is counted, not named
        self.assertIn("ok census: 1 runner service of yours here that this tool did not make (kept; not named: their "
                      "names may be your projects')", said)
        self.assertIn("warn census: 1 runner service here not running as you or as minions-runner (not named: "
                      "they may be another person's; this tool did not make them)", said)
        self.assertNotIn("kept for this tool's other runners", said)

    def test_G1_8_look_lists_each_jobs_needs_against_the_computer(self):
        wf = """name: ci
on: [push]
jobs:
  test:
    runs-on: ubuntu-latest   # hosted now
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
      - run: sudo apt-get install -y libfoo
  ios:
    runs-on: macos-14
    steps:
      - run: xcodebuild
  db:
    runs-on:
      - self-hosted
      - linux
    services:
      postgres:
        image: postgres:16
  pick:
    runs-on: ${{ matrix.os }}
"""
        jobs = M.read_workflow(wf)
        self.assertEqual([j["id"] for j in jobs], ["test", "ios", "db", "pick"])
        self.assertEqual(jobs[0]["runs_on"], ["ubuntu-latest"])
        self.assertEqual(jobs[0]["setup"], ["setup-node"])
        self.assertTrue(jobs[0]["sudo"])
        self.assertEqual(jobs[2]["runs_on"], ["self-hosted", "linux"])
        self.assertTrue(jobs[2]["services"])
        self.assertEqual(jobs[3]["runs_on"], "expression")
        k = [M.judge_job(j, "Linux") for j in jobs]
        self.assertEqual(k[0][0], "warn")
        self.assertIn("uses sudo", k[0][1])
        self.assertIn("downloads its tools (setup-node)", k[0][1])
        self.assertEqual(k[1][0], "stay")
        self.assertIn("stays on GitHub", k[1][1])
        self.assertIn("asks for a runner that is not ours (self-hosted, linux)", k[2][1])
        self.assertIn("chosen when the job runs; not read", k[3][1])
        # Through look, on the recorded repository's four workflows.
        _, lines = look(StandIn(self.recent), linux_host())
        self.assertIn("warn .github/workflows/c3-a-self-hosted-alone.yml job j: asks for a runner that is not ours "
                      "(self-hosted)", lines)
        self.assertIn("warn .github/workflows/c3-c-ours-only.yml job j: asks for a runner that is not ours "
                      "(minions-c3)", lines)

    def test_G1_13_a_virtual_disk_is_not_called_spinning(self):
        def box(link, files, runs=None):
            h = linux_host(files=files, runs=runs or {})
            h.links = {"/sys/dev/block/259:2": link}
            return h
        # The stock VM's virtio disk: rotational 1, device vda. Seen: look called it a spinning disk.
        h = box("/sys/devices/pci0000:00/virtio2/block/vda/vda1",
                {"/sys/devices/pci0000:00/virtio2/block/vda/queue/rotational": "1\n"})
        self.assertEqual(M.disk_kind(h, "/home"), "disk kind not known (virtual)")
        # A sda disk whose vendor is the hypervisor's, and one only systemd-detect-virt knows about.
        sda = "/sys/devices/pci0000:00/block/sda/sda1"
        rot = {"/sys/devices/pci0000:00/block/sda/queue/rotational": "1\n"}
        h = box(sda, {**rot, "/sys/devices/pci0000:00/block/sda/device/vendor": "QEMU    \n"})
        self.assertEqual(M.disk_kind(h, "/home"), "disk kind not known (virtual)")
        h = box(sda, rot, {("systemd-detect-virt", "--vm"): (0, "kvm\n")})
        self.assertEqual(M.disk_kind(h, "/home"), "disk kind not known (virtual)")
        # A real computer's disks are still told apart.
        h = box(sda, rot, {("systemd-detect-virt", "--vm"): (1, "none\n")})
        self.assertEqual(M.disk_kind(h, "/home"), "spinning disk")
        self.assertEqual(M.disk_kind(linux_host(), "/home"), "SSD")

    def test_G1_14_look_offers_only_what_this_setup_does(self):
        _, lines = look(StandIn(self.recent), linux_host())  # the recorded repository is an organisation's
        org = [x for x in lines if x.startswith("ok organisation:")]
        self.assertEqual(len(org), 1, lines)
        self.assertIn("this one repository its own runner", org[0])
        self.assertIn("come in the next version", org[0])
        self.assertFalse([x for x in lines if "runner group limited" in x or "repositories you choose" in x], lines)

    def test_G1_9_look_reads_the_current_cost_rule_with_its_date(self):
        host = linux_host()
        host.now = lambda: M.dt.datetime(2026, 11, 22, 18, 0, tzinfo=M.dt.timezone.utc)
        _, lines = look(StandIn(self.recent), host)
        line = [x for x in lines if "billing page" in x][0]
        self.assertTrue(line.startswith("ok GitHub's billing page, read 2026-11-22 18:00 UTC"), line)
        self.assertIn("Self-hosted runners.", line)
        self.assertIn(M.COST_URL, line)
        for page in (OSError("offline"), "<html><p>Something else entirely.</p></html>"):
            host = linux_host(page=page)
            _, lines = look(StandIn(self.recent), host)
            self.assertTrue(any(x.startswith("warn I could not read GitHub's rule today") for x in lines), lines)
            self.assertIn("I could not read GitHub's page about charges today.", tells_of(lines))
            self.assertFalse(any("lists \"Self-hosted runners\"" in x for x in lines))

    def test_G1_10_no_github_cli_or_login_is_the_first_need_you(self):
        gh = StandIn(self.recent)
        code, lines = look(gh, linux_host(which=("sudo",)))
        self.assertEqual(lines, ["need you: install the GitHub CLI: `sudo apt update && sudo apt install gh`. " + M.GH_ONLY])
        self.assertEqual(code, M.EXIT_NEED)
        self.assertEqual(gh.calls, [])
        host = linux_host(runs={("gh", "auth", "status"): (1, "You are not logged into any GitHub hosts."),
                                ("gh", "api", "-i", "user"): (4, NO_LOGIN)})
        code, lines = look(StandIn(self.recent), host)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("need you: log in to GitHub: run `gh auth login`"), lines)
        self.assertTrue(lines[0].endswith(M.GH_ONLY), lines)
        mac = FakeHost(system="Darwin", which=())
        _, lines = look(StandIn(self.recent), mac)
        self.assertEqual(len(lines), 1)  # rule 7: no command this tool never saw run on a Mac; where to read
        self.assertIn("names none: read https://github.com/cli/cli#installation", lines[0])
        self.assertNotIn("brew", lines[0])
        # R77: the line itself says GitHub is reached only through gh, no key or other saved login is looked
        # for, and the step is the person's
        self.assertIn("no SSH key and no other saved login on this computer is used or looked for", M.GH_ONLY)
        self.assertIn("This step is yours", M.GH_ONLY)

    def test_G1_11_no_admin_rights_on_the_computer_stops_before_anything(self):
        gh = StandIn(self.recent)
        host = linux_host(runs={("sudo", "-n", "true"): (1, "a password is required")})
        code, lines = look(gh, host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue(lines[-2].startswith("need you: this needs an admin of this computer"), lines)
        self.assertTrue(lines[-1].startswith("stop not touched anything"), lines)
        self.assertEqual(gh.calls, [], "no GitHub call before the admin check passes")
        _, lines = look(StandIn(self.recent), linux_host(groups=("users", "sudo"),
                                                          runs={("sudo", "-n", "true"): (1, "")}))
        self.assertTrue(any("member of sudo" in x for x in lines), lines)
        mac = FakeHost(system="Darwin", which=("gh",), groups=("staff", "admin"),
                       runs={("gh", "auth", "status"): (0, SCOPES)})
        out = io.StringIO()
        M.check_admin(mac, M.Report(out))
        self.assertIn("member of admin", out.getvalue())


# ------------------------------------------------------------------------------------------------------------ G2

FOLDER = "/home/minions-runner/runners/example-org-example-repo"
UNIT = "actions.runner.example-org-example-repo.minions-box-1.service"
R_TEST = ("sudo", "-n", "-u", "minions-runner", "--", "test", "-e")
DEPS = "/tmp/minions-download.AbCd1234"  # R127: root's own folder for the checked download
TOKEN = "AB" + "FAKE" + "REGISTRATION" + "TOKEN" + "0123"  # built from pieces, so no recorded file holds it whole


def setup_rows(sha):
    """The recorded downloads list and release notes, with linux-x64's checksum set to the stand-in archive's."""
    rows = copy.deepcopy(load("setup-release.jsonl"))
    real = next(x["sha256_checksum"] for x in rows[0]["data"] if (x["os"], x["architecture"]) == ("linux", "x64"))
    for r in rows:
        r["data"] = json.loads(json.dumps(r["data"]).replace(real, sha))
    return rows


def runners_answer(status="online", busy=False, labels=("minions", "minions-medium")):
    return 200, {"total_count": 1, "runners": [{"id": 7, "name": "minions-box-1", "os": "Linux", "status": status,
                                                "busy": busy, "labels": [{"name": x, "type": "custom"} for x in labels]}]}


DROPIN_NAME = "50-minions.conf"
SHOW = ("Slice=minions.slice\nNice=10\nCPUWeight=50\nRestart=on-failure\n"
        "Wants=network-online.target\nUnitFileState=enabled\nUser=minions-runner\nOOMPolicy=continue\n")


def setup_host(**kw):
    """A Linux computer where every admin step works, as a stand-in for the VM run."""
    extra = kw.pop("runs", {})
    host = linux_host(**kw)
    sha = hashlib.sha256(host.download).hexdigest()
    creds = "".join(f"600 minions-runner {FOLDER}/{n}\n" for n in (".credentials", ".runner", ".credentials_rsaparams"))
    host.runs.update({
        ("id", "-u", "minions-runner"): (1, "id: 'minions-runner': no such user"),
        ("id", "-nG", "minions-runner"): (0, "minions-runner\n"),
        ("sudo", "-n", "useradd"): (0, ""),
        ("sudo", "-n", "chmod"): (0, ""),
        ("sudo", "-n", "stat", "-c", "%a", "/home/minions-runner"): (0, "750\n"),
        ("sudo", "-n", "-u", "minions-runner", "--", "stat", "-c", "%a %U %n"): (0, creds),  # R128: as the runner
        ("sudo", "-n", "-u", "minions-runner", "--", "chmod"): (0, ""),
        ("sudo", "-n", "-u", "minions-runner", "--", "cp"): (0, ""),
        ("sudo", "-n", "-u", "minions-runner", "--", "tee"): (0, ""),
        ("sudo", "-n", "test", "-e"): (1, ""),
        ("sudo", "-n", "install"): (0, ""),
        ("sudo", "-n", "-u", "minions-runner", "--", "test", "-e"): (1, ""),
        ("sudo", "-n", "-u", "minions-runner", "--", "install"): (0, ""),
        ("sudo", "-n", "sha256sum", f"{DEPS}/runner.tgz"): (0, f"{sha}  {DEPS}/runner.tgz\n"),
        ("sudo", "-n", "-u", "minions-runner", "--", "tar"): (0, ""),
        ("sudo", "-n", "-u", "minions-runner", "--", "rm"): (0, ""),
        ("sudo", "-n", "mktemp", "-d"): (0, DEPS + "\n"),  # R127: root's own folder for the checked download
        ("sudo", "-n", "tar"): (0, ""),
        ("sudo", "-n", "bash", f"{DEPS}/root/bin/installdependencies.sh"): (0, "-----\nlibicu found\n"),
        ("sudo", "-n", "rm"): (0, ""),
        ("sudo", "-n", "-u", "minions-runner", "--", *M.RUNNER_ENV, "bash", "-c", M.CONFIG_SCRIPT):
            (0, "√ Connected to GitHub\n√ Runner successfully added\n√ Settings Saved.\n"),
        ("sudo", "-n", "-u", "minions-runner", "--", "cat", f"{FOLDER}/.service"): (0, UNIT + "\n"),
        ("sudo", "-n", "tee"): (0, ""),
        ("sudo", "-n", "systemctl"): (0, ""),
        ("systemctl", "show", UNIT): (0, SHOW),
        ("systemctl", "show", "minions.slice"): (0, "CPUQuotaPerSecUSec=4s\nMemoryMax=10066329600\n"),
        ("systemctl", "is-active", UNIT): (0, "active\n"),
        ("systemctl", "is-active", "minions-watch.timer"): (0, "active\n"),
        ("sudo", "-n", "chown"): (0, ""),
        ("sudo", "-n", "-u", "minions-runner", "--", "cat", f"{FOLDER}/.env"): (0, "LANG=C.UTF-8\n"),
        ("sudo", "-n", "-u", "minions-runner", "--", "tee", f"{FOLDER}/.env"): (0, ""),
        ("sudo", "-n", "stat", "-c", "%a %U", f"{M.hook_dir(REPO)}/job-started.sh"): (0, "755 root\n755 root\n"),
        ("sudo", "-n", "-u", "minions-runner", "--", f"{FOLDER}/bin/Runner.Listener", "--version"): (0, "2.338.2\n"),
    })
    # G5.15: setup's first watch pass runs the real watch, as the timer would, against a GitHub that reads it private.
    def first_pass(argv, inp):
        M.watch(host=host, gh=StatusGH(), out=io.StringIO())
        return 0, ""
    host.runs[("sudo", "-n", "systemctl", "start", "minions-watch.service")] = first_pass
    host.runs.update(extra)
    return host, sha


def made_by_us(host):
    """R129: the state records that this tool made the runner user (as setup writes it after useradd)."""
    d = M.load_state(host)
    d["runner_user_made"] = True
    M.save_state(host, d)


def root_sites(src: str) -> set[tuple[str, str]]:
    """R135 (rule 28's list): every place the tool runs a command as root, as (function, what root runs): a call of
    sudo(host, [...]) (the first word of the list; a systemctl(...) call as "systemctl <verb>"; a list held in a name
    as "(argv)"), a (sudo, [...]) pair chosen in a loop, sudo chosen by a name ("chosen"), and a literal
    ["sudo", "-n", <word>, ...] list. A `sudo(host, ["-u", RUNNER_USER, ...])` is the runner user, not root."""
    def what(node):
        if isinstance(node, ast.List) and node.elts:
            e = node.elts[0]
            if isinstance(e, ast.Constant):
                return None if e.value == "-u" else e.value
            return "?"
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "systemctl":
            a = node.args[0]
            return "systemctl " + (a.value if isinstance(a, ast.Constant) else "?")
        return "(argv)"
    found = set()
    for fn in ast.walk(ast.parse(src)):
        if not isinstance(fn, ast.FunctionDef) or fn.name in ("sudo", "as_runner"):
            continue
        heads = set()
        for n in ast.walk(fn):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "sudo":
                heads.add(id(n.func))
                w = what(n.args[1])
                if w:
                    found.add((fn.name, w))
            elif isinstance(n, ast.Tuple) and n.elts and getattr(n.elts[0], "id", "") == "sudo":
                heads.add(id(n.elts[0]))
                w = what(n.elts[1]) if len(n.elts) > 1 else None
                if w:
                    found.add((fn.name, w))
            elif (isinstance(n, ast.List) and len(n.elts) > 2 and all(isinstance(x, ast.Constant) for x in n.elts[:3])
                  and n.elts[0].value == "sudo" and n.elts[1].value == "-n"):
                found.add((fn.name, n.elts[2].value))
        for n in ast.walk(fn):
            if isinstance(n, ast.Name) and n.id == "sudo" and id(n) not in heads:
                found.add((fn.name, "chosen"))
    return found


def setup_gh(sha, overrides=None):
    o = {("POST", f"repos/{REPO}/actions/runners/registration-token"): (201, {"token": TOKEN,
                                                                           "expires_at": "2026-11-22T23:00:00Z"}),
         ("GET", f"repos/{REPO}/actions/runners?per_page=100"): runners_answer()}
    o.update(overrides or {})
    return StandIn(setup_rows(sha), o)


def fresh_look(host, minutes=5, stopped=False, survey=True):
    when = (M.dt.datetime.now(M.dt.timezone.utc) - M.dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = {"system": "Linux", "machine": "x86_64", "cores": 8, "mem": 16384000 * 1024,
         "free": 100 * 1024 ** 3, "serves": False, "serves_what": ""} if survey else {}
    M.save_state(host, {"looks": {REPO: {"time": when, "stopped": stopped, "survey": s}}})


def no_event():
    """This process's environment without GitHub's event record: in CI, GitHub's runner sets GITHUB_EVENT_PATH for
    this repository's own (public) job, and the hook under test rightly refuses on it (seen 2026-11-23)."""
    env = dict(os.environ)
    env.pop("GITHUB_EVENT_PATH", None)
    return env


def refuse_sudo(host):
    """G9.7: what a system timer run as the person meets: `sudo -n` is refused (no ticket), whatever else is set."""
    answer = host._answer
    host.answer_with_sudo = answer  # for the steps a person runs in a terminal, where sudo can ask

    def no_sudo(argv, input=None):
        if argv[:1] in (["sudo"], ("sudo",)):
            return 1, "sudo: a password is required\n"
        return answer(argv, input)
    host._answer = no_sudo


def fresh_seen(folder, value=None):
    """G5.15: the watch's record that it read the repository private, as a hook test needs it."""
    path = os.path.join(folder, "seen-private")
    with open(path, "w") as f:
        f.write((value if value is not None else str(int(time.time()))) + "\n")
    return path


def run_setup(gh, host, interactive=True):
    out = io.StringIO()
    code = M.setup(REPO, host=host, gh=gh, out=out, interactive=interactive)
    return code, out.getvalue().splitlines()


def admin_steps(host):
    return [a for a in host.ran if a[:1] == ("sudo",) and a[:3] != ("sudo", "-n", "true")]


def doctor_world():
    """A computer after a real setup, and a GitHub that lists our runner online and idle with our labels."""
    host, sha = setup_host()
    fresh_look(host)
    code, lines = run_setup(setup_gh(sha), host)
    assert code == M.EXIT_OK, lines
    lim = M.load_state(host)["runners"][REPO]["limits"]
    host.runs[("systemctl", "show", "minions.slice")] = (0, f"CPUQuotaPerSecUSec={lim['cores']}s\n"
                                                            f"MemoryHigh={lim['MemoryHigh']}\nMemoryMax={lim['MemoryMax']}\n"
                                                            f"MemorySwapMax={lim['MemorySwapMax']}\n")
    host.runs[R_TEST + (FOLDER,)] = (0, "")
    host.runs[("sudo", "-n", "stat", "-c", "%a", "/home/minions-runner")] = (0, "750\n")
    return host, sha


def run_doctor(host, sha, **kw):
    out = io.StringIO()
    code = M.doctor(REPO, host=host, gh=setup_gh(sha), out=out, interactive=kw.pop("interactive", False), **kw)
    return code, out.getvalue().splitlines()


class G2(unittest.TestCase):
    def test_G6_12_the_runner_says_the_skill_was_removed_and_the_way_off(self):
        # The family's rule 8 (2026-11-23, 12:56 PM ET): a runner is what the person set up, so it goes on after
        # Claude Code's own `plugin uninstall`, and its own report says so, with the one command that takes it off.
        # R161 (rule 29 (e), 2026-11-24): a plugin is "removed" only when Claude Code's own list was read and names
        # it nowhere; an update (a new version folder, the old one marked, later gone) says nothing.
        host, sha = doctor_world()
        self.assertEqual(M.load_state(host)["skill_from"], {"clone": ROOT})  # setup ran from a clone: its folder
        self.assertNotIn("skill_folder", M.load_state(host))
        status = lambda: run_cmd(M.status, REPO, host=host, gh=setup_gh(sha))[1]
        way_off = f"`python3 -I {M.TOOL_COPY} uninstall --all`"
        host.dirs[ROOT] = []
        self.assertFalse(any("skill" in x and "uninstall --all" in x for x in status()))  # there: nothing to say
        del host.dirs[ROOT]
        line = next(x for x in status() if "skill's folder" in x)
        self.assertTrue(line.startswith(f"warn the skill's folder ({ROOT}) is gone"), line)
        self.assertIn("go on taking your repository's jobs", line)
        self.assertIn(way_off, line)
        for _ in range(2):  # the watch says it in its own log, once, when it changes
            M.watch(host=host, gh=setup_gh(sha), out=io.StringIO())
        with open(os.path.join(host.state_dir(), "journal.log")) as f:
            self.assertEqual(sum("skill's folder" in x and "is gone" in x for x in f), 1)
        there = host.there
        host.there = lambda path: None  # could not be read is a third answer (rule 6), never "gone"
        line = next(x for x in status() if "skill's folder" in x)
        self.assertIn("could not be read", line)
        self.assertNotIn("is gone", line)
        host.there = there
        # From Claude Code's plugin cache: the list and the name are kept, never the version folder.
        base = "/home/you/.claude"
        old = f"{base}/plugins/cache/the-catalog/repo-minions/0.1.0"
        lst = f"{base}/plugins/installed_plugins.json"
        self.assertEqual(M.skill_from(f"{old}/scripts/minions.py"), {"plugin": "repo-minions@the-catalog", "list": lst})
        d = M.load_state(host)
        d["skill_from"] = M.skill_from(f"{old}/scripts/minions.py")
        M.save_state(host, d)
        lines_now = lambda: [x for x in status() if "repo-minions@the-catalog" in x]
        def listed(version, folder):
            host.files[lst] = json.dumps({"version": 2, "plugins": {"repo-minions@the-catalog": [
                {"scope": "user", "installPath": folder, "version": version}]}})
        # no list there (a record carried to another computer): could not be read, never "names it nowhere"
        self.assertTrue(lines_now() and all("could not be read" in x and "nowhere" not in x for x in lines_now()),
                        lines_now())
        host.files[lst] = "{not json"
        self.assertIn("could not be read", lines_now()[0])
        listed("0.1.0", old)
        host.dirs[old] = []
        self.assertEqual(lines_now(), [])  # installed: nothing to say
        # an update: the row moved to the new version, the old folder marked, then the old folder gone: nothing said
        new = f"{base}/plugins/cache/the-catalog/repo-minions/0.1.1"
        listed("0.1.1", new)
        host.files[f"{old}/.orphaned_at"] = "1795455000000"
        self.assertEqual(lines_now(), [])
        del host.dirs[old]
        del host.files[f"{old}/.orphaned_at"]
        self.assertEqual(lines_now(), [])
        self.assertFalse(any("removed" in x or "is gone" in x for x in status()))
        # an uninstall: the list read, no row for it: the line, with the way off
        host.files[lst] = json.dumps({"version": 2, "plugins": {}})
        line = lines_now()[0]
        self.assertTrue(line.startswith(f"warn Claude Code's own list of plugins ({lst}) names "
                                        "repo-minions@the-catalog nowhere"), line)
        self.assertIn("go on taking your repository's jobs", line)
        self.assertIn(way_off, line)
        self.assertNotIn("/plugins/cache/", line)

    def test_G4_10_doctor_and_status_name_an_older_copy_running_the_watch(self):
        # Found 2026-11-23 (row R101): after the plugin updates, the watch runs the copy setup installed until setup
        # runs again; nothing said so.
        host, sha = doctor_world()
        self.assertIn(f'VERSION = "{M.VERSION}"', host.files[M.TOOL_COPY])  # setup put this version's copy there
        status = lambda: run_cmd(M.status, REPO, host=host, gh=setup_gh(sha))[1]
        code, lines = run_doctor(host, sha)
        self.assertIn(f"ok the watch runs this version of the tool ({M.VERSION})", lines)
        self.assertTrue(lines[-1].startswith("ok every promise"), lines)
        self.assertIn(f"ok the watch runs this version of the tool ({M.VERSION})", status())
        # The plugin updated, the copy did not: doctor names it as a promise not kept, status says it.
        with mock.patch.object(M, "VERSION", "99.0.0"):
            code, lines = run_doctor(host, sha)
            line = next(x for x in lines if "the watch runs version" in x)
            self.assertTrue(line.startswith("warn ") and "the watch runs the older code" in line, line)
            self.assertIn("`setup <owner/repo>` again puts this version's copy in place", line)
            self.assertTrue(lines[-1].startswith("stop ") and "the watch's copy of the tool" in lines[-1], lines[-1])
            self.assertTrue(any("the watch runs the older code" in x for x in status()))
            # The step that line names is true: `setup` again on the installed runner puts this version's copy in
            # place and touches nothing else (no download, no registration, no service change).
            newer = os.path.join(TMP, "minions-99.py")
            with open(os.path.join(ROOT, "scripts", "minions.py"), encoding="utf-8") as f:
                text = f.read().replace(f'VERSION = "{M.VERSION_ON_DISK}"', 'VERSION = "99.0.0"', 1)
            with open(newer, "w", encoding="utf-8") as f:
                f.write(text)
            with mock.patch.object(M, "__file__", newer):
                data = M.load_state(host)
                looks = dict(data.get("looks") or {})
                fresh_look(host)
                looks[REPO] = M.load_state(host)["looks"][REPO]
                data["watch"] = {**(data.get("watch") or {}), "time": "2026-11-22T00:00:00Z"}  # the last pass, earlier
                M.save_state(host, {**data, "looks": looks})  # a fresh look, the runner's record kept
                before = len(host.ran)
                code, lines = run_setup(setup_gh(sha), host)
            self.assertIn('VERSION = "99.0.0"', host.files[M.TOOL_COPY])
            self.assertTrue(any(x.startswith("ok the watch runs this version of the tool (99.0.0)") and
                                "were not touched" in x for x in lines), lines)
            later = host.ran[before:]
            self.assertFalse([a for a in later if "config.sh" in " ".join(a) or "svc.sh" in " ".join(a)], later)
            code, lines = run_doctor(host, sha)
            self.assertTrue(lines[-1].startswith("ok every promise"), lines[-3:])
        # Not read is a third answer, never "the same" (rule 6), and not a broken promise.
        del host.files[M.TOOL_COPY]
        code, lines = run_doctor(host, sha)
        self.assertTrue(any(x.startswith("warn the version of the copy the watch runs") and "could not be read" in x
                            for x in lines), lines)
        self.assertFalse(any("the watch runs this version" in x for x in lines))

    def test_G7_6_version_0_1_says_and_holds_one_repository_per_computer(self):
        host, sha = setup_host()
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn(f"ok this version serves one repository per computer: here, {REPO}", lines)
        # A second repository: stopped before anything is touched, the one served named, and the way on.
        host, sha = setup_host()
        fresh_look(host)
        data = M.load_state(host)
        data["runners"] = {"example-org/other-repo": {"name": "minions-box-1"}}
        M.save_state(host, data)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual((code, len(lines)), (M.EXIT_STOP, 3), lines)  # the look, the GitHub login (R144), the stop
        self.assertTrue(lines[-1].startswith("stop this computer already serves example-org/other-repo"), lines)
        self.assertIn("nothing was changed", lines[-1])
        self.assertIn(f"run `uninstall example-org/other-repo` first, then `setup {REPO}` again", lines[-1])
        self.assertNotIn("next version", lines[-1])  # G7.12: a line the person must hear promises no later version
        self.assertEqual(admin_steps(host), [], "nothing is touched for a second repository")
        skill = open(os.path.join(ROOT, "skills", "repo-minions", "SKILL.md")).read()
        self.assertIn("This version serves one repository per computer.", skill)
        self.assertNotIn("next version", skill)

    def test_G2_1_setup_needs_a_fresh_clean_look(self):
        host, sha = setup_host()
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue(lines[-1].startswith(f"stop no look of {REPO}"), lines)
        for kw, want in ((dict(minutes=61), "minutes old"), (dict(stopped=True), "ended with a `stop`"),
                         (dict(survey=False), "did not reach its end")):
            host, sha = setup_host()
            fresh_look(host, **kw)
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual((code, len(lines)), (M.EXIT_STOP, 1), lines)
            self.assertIn(want, lines[0])
            self.assertEqual(admin_steps(host), [], "nothing is touched before the look is checked")
        # look writes itself down: 0700 folder, 0600 file, the survey kept, nothing secret
        host = linux_host()
        look(StandIn(load("look-recent-runs.jsonl")), host)
        self.assertEqual(os.stat(host.state_dir()).st_mode & 0o777, 0o700)
        path = os.path.join(host.state_dir(), "minions.json")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        with open(path) as f:
            entry = json.load(f)["looks"][REPO]
        self.assertFalse(entry["stopped"])
        self.assertEqual(entry["survey"]["cores"], 8)
        self.assertEqual(set(entry["survey"]), {"system", "machine", "cores", "mem", "disk", "free", "battery", "lid", "os",
                                                "sleep_delay", "serves", "serves_what"})
        # a look of a public repository is a look with a stop: setup refuses it (with G1.1)
        host2, sha = setup_host()
        host2._state = host.state_dir()
        look(StandIn(with_repo(load("look-recent-runs.jsonl"), lambda d: d.update(visibility="public",
                                                                                   private=False))), host2)
        code, lines = run_setup(setup_gh(sha), host2)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("ended with a `stop`", lines[-1])

    def test_G2_2_the_runner_user_has_no_admin_rights(self):
        host, sha = setup_host()
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn(("sudo", "-n", "useradd", "--create-home", "--user-group", "--shell", "/bin/bash",
                       "minions-runner"), host.ran)
        self.assertIn(("sudo", "-n", "chmod", "0750", "/home/minions-runner"), host.ran)
        self.assertTrue(all(a[1] == "-n" for a in admin_steps(host)), "every admin step is sudo -n")
        self.assertFalse(any("-G" in a or "usermod" in a or "sudoers" in " ".join(a) for a in host.ran))
        # the password is asked once, by sudo itself, after a need-you line
        host, sha = setup_host(runs={("sudo", "-n", "true"): (1, "sudo: a password is required")})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host, interactive=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertEqual(host.validated, 1)
        need = [x for x in lines if x.startswith("need you:") and "press Enter" not in x]  # G7.15's key comes first
        self.assertTrue(need[0].startswith("need you: type this computer's admin password once"), lines)
        self.assertLess(lines.index(need[0]), lines.index("ok admin rights ready (asked once)"))
        self.assertEqual(len(need), 1)
        # not on a terminal: a blocking need-you, nothing touched
        host, sha = setup_host(runs={("sudo", "-n", "true"): (1, "sudo: a password is required")})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host, interactive=False)
        self.assertEqual((code, host.validated, admin_steps(host)), (M.EXIT_NEED, 0, []))
        self.assertIn("in your own terminal: run `python3 -I ", lines[-1])  # G7.15: setup is the person's to run
        # a runner user that is in an admin group is refused
        for groups in ("minions-runner sudo", "minions-runner docker", "minions-runner wheel", "minions-runner users"):
            host, sha = setup_host(runs={("id", "-nG", "minions-runner"): (0, groups + "\n"),
                                         ("id", "-u", "minions-runner"): (0, "998\n")})
            fresh_look(host)
            made_by_us(host)  # R129: a user this tool made earlier; one it did not make is never taken (G2.14)
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual(code, M.EXIT_STOP)
            self.assertIn("own group only", lines[-2])
            self.assertFalse(any("useradd" in a for a in host.ran))
        # a home closed to others is read back, and the person's own home must not be open to every user
        host, sha = setup_host(runs={("sudo", "-n", "stat", "-c", "%a", "/home/minions-runner"): (0, "755\n")})
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_STOP)
        host, sha = setup_host()
        host.home_mode = 0o755
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual((code, admin_steps(host)), (M.EXIT_NEED, []))
        self.assertIn("chmod 750 /home/person", lines[-1])

    def test_G2_3_the_runner_release_is_checked_before_use(self):
        rep = M.Report(io.StringIO())
        rel = M.runner_release(StandIn(load("setup-release.jsonl")), REPO, linux_host(), rep)
        self.assertEqual(rel["version"], "2.338.2")
        self.assertEqual(rel["sha256"], "baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40")
        self.assertTrue(rel["url"].startswith("https://github.com/actions/runner/releases/download/v2.338.2/"))
        # the two published checksums disagree: stop before anything is downloaded
        rows = load("setup-release.jsonl")
        rows[1]["data"]["body"] = rows[1]["data"]["body"].replace(rel["sha256"], "0" * 64)
        rep = M.Report(io.StringIO())
        self.assertIsNone(M.runner_release(StandIn(rows), REPO, linux_host(), rep))
        self.assertIn("disagree", rep.lines[-1])
        rows[1]["data"]["body"] = "no markers here"
        rep = M.Report(io.StringIO())
        self.assertIsNone(M.runner_release(StandIn(rows), REPO, linux_host(), rep))
        # the downloaded file is not the published one: deleted, nothing installed
        host, sha = setup_host()
        host.download = b"something else"
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("checksum is not the one GitHub publishes", lines[-2])
        self.assertFalse(any(a[2:3] == ("install",) for a in host.ran))
        self.assertEqual([x for x in os.listdir(host.state_dir()) if x.endswith(".tar.gz")], [])
        # the version is written down
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        self.assertEqual(M.load_state(host)["runners"][REPO]["version"], "2.338.2")

    def test_G2_4_each_runner_has_its_own_folders(self):
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        # R128: made and unpacked as the runner user, from root's own checked copy (R127), which it cannot write
        self.assertIn(("sudo", "-n", "-u", "minions-runner", "--", "install", "-d", "-m", "0700", FOLDER), host.ran)
        self.assertIn(("sudo", "-n", "-u", "minions-runner", "--", "tar", "xzf", f"{DEPS}/runner.tgz", "-C",
                       FOLDER), host.ran)
        self.assertIn('--work _work', M.CONFIG_SCRIPT)
        config = [a for a, _ in host.fed if "bash" in a and M.CONFIG_SCRIPT in a][0]
        self.assertEqual(config[-4], FOLDER)
        self.assertFalse(any(a[3:4] == ("cp",) or "cp" in a[:5] for a in host.ran), "unpacked fresh, never copied")
        # a folder that is there already is never reused
        host, sha = setup_host(runs={R_TEST: (0, "")})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("exists already", lines[-2])
        self.assertFalse(any("tar" in a for a in host.ran))

    def test_G2_5_the_registration_token_goes_by_stdin_only(self):
        host, sha = setup_host()
        echo = host.runs[("sudo", "-n", "-u", "minions-runner", "--", *M.RUNNER_ENV, "bash", "-c", M.CONFIG_SCRIPT)]
        host.runs[("sudo", "-n", "-u", "minions-runner", "--", *M.RUNNER_ENV, "bash", "-c", M.CONFIG_SCRIPT)] = \
            lambda argv, input: (echo[0], echo[1] + f"token was {input.strip()}\n")  # a runner that echoes it
        fresh_look(host)
        out = io.StringIO()
        code = M.setup(REPO, host=host, gh=setup_gh(sha), out=out, interactive=True)
        self.assertEqual(code, M.EXIT_OK, out.getvalue())
        self.assertFalse([a for a in host.ran if any(TOKEN in x for x in a)], "the token is on a command line")
        fed = [(a, i) for a, i in host.fed if i and TOKEN in i]
        self.assertEqual(len(fed), 1)
        self.assertEqual(fed[0][1], TOKEN + "\n")
        self.assertIn("read -r T", fed[0][0][7 + len(M.RUNNER_ENV)])
        self.assertNotIn("--token", " ".join(fed[0][0]))
        self.assertFalse(re.search(r"\benv\b", M.CONFIG_SCRIPT))
        self.assertNotIn(TOKEN, out.getvalue())
        for name in os.listdir(host.state_dir()):
            with open(os.path.join(host.state_dir(), name), errors="replace") as f:
                self.assertNotIn(TOKEN, f.read(), name)

    def test_G1_7_setup_asks_before_jobs_run_beside_something_live(self):
        # Seen on a stock VM (2026-11-23): look warned "setup will ask", and setup never asked (row R72).
        host, sha = setup_host()
        fresh_look(host)
        data = M.load_state(host)
        data["looks"][REPO]["survey"].update(serves=True, serves_what="port(s) open to the network: 8088")
        M.save_state(host, data)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_NEED, lines)
        self.assertTrue(lines[-1].startswith("need you: this computer serves something live (port(s) open to the "
                                             "network: 8088)"), lines)
        self.assertIn("Nothing was changed", lines[-1])
        self.assertIn(f"`setup {REPO} --beside-live-too`", lines[-1])
        self.assertEqual(admin_steps(host), [])
        self.assertFalse(host.validated)
        # The person's yes in words: setup goes on, and says what it runs beside.
        self.assertTrue(M.parser().parse_args(["setup", REPO, "--beside-live-too"]).beside_live)
        self.assertFalse(M.parser().parse_args(["setup", REPO]).beside_live)
        out = io.StringIO()
        code = M.setup(REPO, host=host, gh=setup_gh(sha), out=out, beside_live=True)
        lines = out.getvalue().splitlines()
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn("ok this computer serves something live (port(s) open to the network: 8088); you said yes to "
                      "jobs beside it, and they get only the limits below", lines)
        # A look from before this version did not record it: a new look, never a silent no.
        host, sha = setup_host()
        fresh_look(host)
        data = M.load_state(host)
        del data["looks"][REPO]["survey"]["serves"]
        M.save_state(host, data)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn(f"run `look {REPO}` again", lines[-1])

    def test_G2_13_root_runs_only_what_root_wrote_and_checked(self):
        # R127 (rule 28, ROOT RUNS ONLY WHAT ROOT OWNS; CHECKED AND USED ARE THE SAME COPY): root never runs the
        # runner's own scripts (svc.sh); the unit is this tool's own writing; the dependency script runs from root's
        # own copy of the download, the copy whose checksum was read, never from the runner's folder.
        host, sha = setup_host()
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertFalse([a for a in host.ran if any(x == "svc.sh" or x.endswith("/svc.sh") for x in a)],
                         "nothing runs svc.sh")
        root = [a[2:] for a in admin_steps(host) if a[2:3] != ("-u",)]
        runner = [a[5:] for a in admin_steps(host) if a[2:3] == ("-u",)]
        made = [a for a in root if a[:1] == ("mktemp",)]
        self.assertEqual(made, [("mktemp", "-d", "/tmp/minions-download.XXXXXXXX")])
        i_copy = next(i for i, a in enumerate(root) if a[:7] == ("install", "-o", "root", "-g", "root", "-m", "0644")
                      and a[-1] == f"{DEPS}/runner.tgz" and a[-2].startswith(host.state_dir()))
        i_sum = root.index(("sha256sum", f"{DEPS}/runner.tgz"))
        i_deps = root.index(("bash", f"{DEPS}/root/bin/installdependencies.sh"))
        self.assertLess(i_copy, i_sum)
        self.assertLess(i_sum, i_deps)
        self.assertIn(("tar", "xzf", f"{DEPS}/runner.tgz", "-C", FOLDER), runner)  # same copy
        ran_scripts = [a for a in root if a[:1] in (("bash",), ("sh",)) or (a and a[0].endswith(".sh"))]
        self.assertEqual(ran_scripts, [("bash", f"{DEPS}/root/bin/installdependencies.sh")])
        unit = [inp for argv, inp in host.fed if argv[2:4] == ("tee", f"/etc/systemd/system/{UNIT}")]
        self.assertEqual(unit, [M.unit_text(FOLDER, UNIT)])
        self.assertIn(f"User={M.RUNNER_USER}\n", unit[0])
        self.assertIn(("rm", "-rf", "--one-file-system", DEPS), root)  # root's copy goes when it is used
        # a copy whose checksum does not match is never used
        host, sha = setup_host(runs={("sudo", "-n", "sha256sum", f"{DEPS}/runner.tgz"): (0, f"{'0' * 64}  x\n")})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertFalse([a for a in host.ran if "installdependencies.sh" in " ".join(a) or a[5:6] == ("tar",)])

    def test_G2_14_setup_takes_only_a_runner_user_it_made(self):
        # R129 (rule 28): a minions-runner this tool did not make (no record here says it made it), or one whose
        # answer could not be read, stops setup before any admin step; uninstall never removes a user it did not make.
        for answer in ((0, "1001\n"), (2, "id: cannot read the user database\n")):
            host, sha = setup_host(runs={("id", "-u", "minions-runner"): answer})
            fresh_look(host)
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertEqual(admin_steps(host), [], "nothing is touched")
            self.assertIn(M.NOT_OURS_USER if answer[0] == 0 else "could not read whether the user", lines[-1])
        host, sha = setup_host(runs={("id", "-u", "minions-runner"): (0, "1001\n")})
        fresh_look(host)
        made_by_us(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)  # its own user, made earlier, is used again
        self.assertFalse([a for a in host.ran if "useradd" in a])
        # useradd is recorded at once, so a setup cut after it still knows the user is ours
        host, sha = setup_host()
        fresh_look(host)
        run_setup(setup_gh(sha), host)
        self.assertTrue(M.load_state(host).get("runner_user_made"))
        # uninstall: a user with no record that this tool made it stays, named, never removed
        gh, world, host = remove_setup()
        d = M.load_state(host)
        d.pop("runner_user_made")
        M.save_state(host, d)
        code, lines = run_uninstall(gh, host, everything=True)
        self.assertTrue(world.user, lines)
        self.assertNotIn("userdel", world.order)
        self.assertFalse([a for a in host.ran if a[-1:] == ("minions-runner",) and a[2:3] in (("crontab",), ("loginctl",))])

    def test_G2_17_root_follows_nothing_in_the_runner_users_home(self):
        # R128 (rule 28): every act on the runner user's home (unpack, read, write, chmod, kill, delete) runs as the
        # runner user; root's only steps there are on the home folder itself, an entry in root's /home.
        home = "/home/minions-runner"
        for run in ("setup", "uninstall"):
            if run == "setup":
                host, sha = setup_host()
                fresh_look(host)
                self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
            else:
                gh, world, host = remove_setup()
                run_uninstall(gh, host, everything=True)
            root = [a[2:] for a in admin_steps(host) if a[2:3] != ("-u",)]
            inside = [a for a in root if any(x.startswith(home + "/") for x in a)]
            self.assertEqual(inside, [], f"{run}: root acts inside the runner user's home")
            self.assertTrue([a for a in admin_steps(host) if a[2:5] == ("-u", "minions-runner", "--")
                             and any(x.startswith(home + "/") for x in a)], run)

    def test_G2_18_a_stop_ends_what_a_job_left_running(self):
        # R131: our unit says KillMode=mixed: at a stop the runner alone gets SIGTERM (it ends its job its own way),
        # then whatever a job left running in the service is ended. With KillMode=process (GitHub's svc.sh template),
        # a job's background process outlives the service.
        text = M.unit_text(FOLDER, UNIT)
        svc = dict(x.split("=", 1) for x in text.split("[Service]\n")[1].split("\n\n")[0].splitlines())
        self.assertEqual((svc["KillMode"], svc["KillSignal"], svc["User"]), ("mixed", "SIGTERM", M.RUNNER_USER))
        self.assertEqual(text.count("KillMode="), 1)

    def test_G2_20_setup_records_the_system_packages_the_dependency_script_added(self):
        # R139 (rule 8, A LEAVE LEAVES THE HOME AS IT FOUND IT; the owner's ruling 2026-11-24): the runner's own
        # dependency script may install system packages (on a stock Ubuntu 24.04.5: liblttng-ust1t64 and two it
        # needs). Setup reads the system's package list, as the person, before and after that script, keeps what it
        # added in its notes and journal, and says they stay when the tool leaves; uninstall --all names them in the
        # census as kept, with why, and no command. This tool never removes a system package.
        base = "installed bash\ninstalled python3\nconfig-files oldpkg\n"
        def lists(added):
            calls = []
            def answer(argv, inp=None):
                calls.append(argv)
                return 0, base + "".join(f"installed {p}\n" for p in (added if len(calls) > 1 else []))
            return answer
        new = ["liblttng-ust-common1t64", "liblttng-ust1t64"]
        host, sha = setup_host(runs={("dpkg-query",): lists(new)})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        ran = list(host.ran)
        reads = [i for i, a in enumerate(ran) if a[:1] == ("dpkg-query",)]
        deps = ran.index(("sudo", "-n", "bash", f"{DEPS}/root/bin/installdependencies.sh"))
        self.assertEqual(len(reads), 2)
        self.assertLess(reads[0], deps)
        self.assertLess(deps, reads[1])
        # What was read is said, not who did it: a list before and after tell what was added while it ran.
        self.assertIn("ok the runner's own dependency script ran (from root's checked copy of the download); these "
                      "system packages were added while it ran: liblttng-ust-common1t64, liblttng-ust1t64; they stay "
                      "on this computer when this tool leaves, because another program may come to use them", lines)
        self.assertFalse([x for x in lines if "it installed" in x], lines)
        self.assertEqual(M.load_state(host)["dependency_packages"], new)
        with open(os.path.join(host.state_dir(), "journal.log")) as f:
            self.assertIn("added to the system while the runner's dependency script ran: liblttng-ust-common1t64, "
                          "liblttng-ust1t64", f.read())
        # none added; and a list that could not be read is said as such, never as none
        for runs, said in (({("dpkg-query",): lists([])}, "no system packages were added while it ran"),
                           ({("dpkg-query",): (1, "dpkg-query: error")}, "it may add system packages, which stay "
                            "on this computer when this tool leaves; which were added while it ran could not be read "
                            "here")):
            host, sha = setup_host(runs=runs)
            fresh_look(host)
            code, lines = run_setup(setup_gh(sha), host)
            self.assertTrue(any(x.startswith("ok the runner's own dependency script ran") and x.endswith(said)
                                for x in lines), lines)
        self.assertTrue(M.load_state(host)["dependency_packages_unread"])
        # a script that never started added nothing: said so, nothing read, nothing marked unread
        host, sha = setup_host(runs={("dpkg-query",): lists(new), ("sudo", "-n", "tar"): (2, "tar: error")})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertTrue(any(x.startswith("warn the runner's own dependency script did not start: ")
                            and x.endswith("; it added nothing to the system") for x in lines), lines)
        self.assertFalse([a for a in host.ran if a[:1] == ("dpkg-query",)])
        self.assertNotIn("dependency_packages_unread", M.load_state(host))
        # uninstall --all names them, kept, with why; nothing removes a package
        # A list not read at one setup is never lost when a later setup reads one: both are said (an error is
        # never an absence).
        for data, unread, said in (
                (["liblttng-ust1t64"], None, "ok census: system packages added while the runner's dependency script "
                 "ran: liblttng-ust1t64 (kept: installed for the whole system, and another program may use them now)"),
                (None, None, "ok census: system packages added while the runner's dependency script ran: not recorded "
                 "at setup (kept, whatever they are: installed for the whole system, and another program may use "
                 "them now)"),
                (["liblttng-ust1t64"], True, "ok census: system packages added while the runner's dependency script "
                 "ran: liblttng-ust1t64; at one setup the list could not be read, so there may be more (kept, "
                 "whatever they are: installed for the whole system, and another program may use them now)")):
            gh, world, host = remove_setup()
            d = M.load_state(host)
            d["dependency_packages"] = data
            if unread:
                d["dependency_packages_unread"] = True
            M.save_state(host, d)
            code, lines = run_uninstall(gh, host, everything=True)
            self.assertIn(said, lines)
            self.assertFalse([a for a in host.ran if {"apt", "apt-get", "dpkg", "rpm", "dnf", "yum", "zypper"} & set(a)])
            self.assertTrue(lines[-1].startswith("ok census: your record of what this tool did"), lines[-1])

    def test_G2_15_every_root_step_is_on_the_list(self):
        # R135 (rule 28): docs/root-steps.md lists every place the tool runs a command as root, each with its answers
        # (runs a job's file? a job's path? what it creates, with mode and owner). A root step not on the list, or a
        # row the tool no longer has, fails here.
        src = open(os.path.join(ROOT, "scripts", "minions.py")).read()
        code = root_sites(src)
        doc = open(os.path.join(ROOT, "docs", "root-steps.md")).read()
        rows = set()
        for line in doc.splitlines():
            cells = [c.strip().strip("`") for c in line.strip().strip("|").split("|")]
            if line.startswith("| `") and len(cells) >= 5:
                rows.add((cells[0], cells[1]))
                self.assertTrue(all(cells[2:5]), line)  # every answer written
                self.assertRegex(cells[2], r"^(no|yes)\b", line)
                self.assertRegex(cells[3], r"^(no|yes)\b", line)
        self.assertEqual(sorted(code - rows), [], "root steps in the tool that are not on docs/root-steps.md")
        self.assertEqual(sorted(rows - code), [], "rows on docs/root-steps.md the tool no longer has")
        self.assertIn("nothing a job can write is ever run, sourced or followed by root", " ".join(doc.split()))

    def test_G2_16_setup_under_umask_000_makes_nothing_another_account_can_write(self):
        # R134 (rule 28, NOTHING A SKILL MAKES IS WRITABLE BY ANOTHER ACCOUNT): a launcher may start the tool with
        # umask 000. main raises it to at least 022 before anything else; every root step that makes something names
        # its mode and owner; the files the tool makes itself are not writable by group or others.
        main_ = next(n for n in ast.parse(open(os.path.join(ROOT, "scripts", "minions.py")).read()).body
                     if isinstance(n, ast.FunctionDef) and n.name == "main")
        first_call = next(c for s in main_.body for c in ast.walk(s) if isinstance(c, ast.Call)
                          and isinstance(c.func, ast.Name) and c.func.id not in ("parser", "Host"))
        self.assertEqual(getattr(first_call.func, "id", ""), "umask_floor")
        given = os.umask(0)
        try:
            self.assertEqual(M.umask_floor(), 0o022)
            self.assertEqual(os.umask(0o022), 0o022)
            os.umask(0o002)
            self.assertEqual(M.umask_floor(), 0o022)
            os.umask(0o077)
            self.assertEqual(M.umask_floor(), 0o077)  # a stricter umask is kept
            os.umask(0)
            M.umask_floor()
            host, sha = setup_host()
            fresh_look(host)
            code, lines = run_setup(setup_gh(sha), host)
        finally:
            os.umask(given)
        self.assertEqual(code, M.EXIT_OK, lines)
        steps = [a[2:] for a in admin_steps(host) if a[2:3] != ("-u",)]
        makes = {"install", "tee", "mktemp", "useradd", "mkdir", "cp", "touch", "tar", "mv", "ln"}
        self.assertLessEqual({"install", "tee", "mktemp", "useradd", "tar"}, {a[0] for a in steps})  # all were read
        for i, a in enumerate(steps):
            if a[0] not in makes:
                continue
            if a[0] == "install":
                self.assertTrue({"-m", "-o", "-g"} <= set(a), a)
            elif a[0] == "tee":
                rest = steps[i + 1:i + 3]
                self.assertEqual(rest[0][:2], ("chown", "root:root"), a)
                self.assertEqual(rest[0][-1], a[-1])
                self.assertRegex(rest[1][1], r"^0[0-7]{3}$")
                self.assertFalse(int(rest[1][1], 8) & 0o022, a)
                self.assertEqual(rest[1][-1], a[-1])
            elif a[0] == "mktemp":
                self.assertEqual(a[1], "-d", a)  # mktemp makes its folder 0700 whatever the umask
            elif a[0] == "useradd":
                self.assertIn(("chmod", "0750", "/home/minions-runner"), steps[i + 1:])
            elif a[0] == "tar":
                self.assertIn("--wildcards", a)  # only the dependency script, into root's 0700 folder
                d = a[a.index("-C") + 1]
                self.assertIn(("install", "-d", "-o", "root", "-g", "root", "-m", "0700", d), steps[:i])
            else:
                self.fail(f"a root step makes something without naming its mode and owner: {a}")
        for top, _dirs, names in os.walk(host.state_dir()):
            for p in [top] + [os.path.join(top, n) for n in names]:
                self.assertFalse(os.stat(p).st_mode & 0o022, p)

    def test_G2_12_admin_steps_stay_on_the_terminal_that_was_asked(self):
        # Asked once, in this terminal; every admin step after it is `sudo -n` (it never asks again by itself).
        host, sha = setup_host()
        fresh_look(host)
        host.runs[("sudo", "-n", "true")] = (1, "sudo: a password is required\n")
        code, lines = run_setup(setup_gh(sha), host, interactive=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertEqual(host.validated, 1)
        self.assertFalse([a for a in host.ran if a[:1] == ("sudo",) and a[1:2] != ("-n",)], "only sudo -n")
        # Cut part-way (the permission ran out, or another terminal): the stop says to run it again from the same
        # terminal, and that nothing is done twice.
        host, sha = setup_host()
        fresh_look(host)
        host.runs[("sudo", "-n", "useradd")] = (1, "sudo: a password is required\n")
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn("run the same command again from the terminal where you typed the password", lines[-2])
        self.assertIn("never done twice", lines[-2])
        # R73: `resume` and `drain --stop` ask for the password themselves before their admin step, never sending
        # the person back to a terminal where nothing was asked.
        host = setup_host(runs={("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner."): (1, ""),
                                ("systemctl", "is-active", UNIT): (3, "inactive\n")})[0]
        held_state(host)
        host.runs[("sudo", "-n", "true")] = (1, "sudo: a password is required\n")
        for run, cmd in ((lambda **k: M.resume(REPO, host=host, gh=DrainGH(busy=(False,)), **k), f"`resume {REPO}`"),
                         (lambda **k: M.drain(REPO, stop=True, host=host, gh=DrainGH(busy=(False,)), **k),
                          f"`drain {REPO} --stop`")):
            host.ran.clear()
            out = io.StringIO()
            self.assertEqual(run(out=out, interactive=False), M.EXIT_NEED, out.getvalue())
            last = out.getvalue().splitlines()[-1]
            self.assertEqual(last, f"need you: this needs this computer's admin password once: run `sudo -v` in your "
                                   f"terminal, then run {cmd} again from that same terminal")
            self.assertFalse([a for a in host.ran if "systemctl" in a and ("start" in a or "stop" in a)], host.ran)
        # Asked in this terminal: the password once, then the service started.
        host.ran.clear()
        out = io.StringIO()
        self.assertEqual(M.resume(REPO, host=host, gh=DrainGH(busy=(False,)), out=out, interactive=True), M.EXIT_OK,
                         out.getvalue())
        self.assertEqual(host.validated, 1)
        self.assertIn(("sudo", "-n", "systemctl", "start", UNIT), [tuple(a[:5]) for a in host.ran])

    def test_G2_6_a_runner_serves_only_chosen_private_repositories(self):
        host, sha = setup_host()
        fresh_look(host)
        gh = setup_gh(sha)
        self.assertEqual(run_setup(gh, host)[0], M.EXIT_OK)
        config = [a for a, _ in host.fed if M.CONFIG_SCRIPT in a][0]
        self.assertEqual(config[-3], f"https://github.com/{REPO}")
        self.assertIn(("POST", f"repos/{REPO}/actions/runners/registration-token"), gh.calls)
        self.assertFalse([c for c in gh.calls if c[1].startswith("orgs/")], "never an organisation runner")
        self.assertIn(REPO, M.load_state(host)["runners"])

    def test_G2_7_runners_carry_only_our_labels(self):
        gbs = 1024 ** 3
        self.assertEqual(M.size_label({"cores": 2, "mem": 4 * gbs}), "minions-small")
        self.assertEqual(M.size_label({"cores": 8, "mem": 16384000 * 1024}), "minions-medium")
        self.assertEqual(M.size_label({"cores": 32, "mem": 64 * gbs}), "minions-large")
        self.assertEqual(M.size_label({"cores": 32, "mem": 4 * gbs}), "minions-small")
        self.assertEqual(M.size_label({}), "minions-small")
        self.assertEqual({r[0] for r in M.SIZE_RULES}, {"minions-small", "minions-medium", "minions-large"})
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        config = [a for a, _ in host.fed if M.CONFIG_SCRIPT in a][0]
        self.assertEqual(config[-1], "minions,minions-medium")
        self.assertIn("--no-default-labels", M.CONFIG_SCRIPT)

    def test_G2_8_credential_files_are_the_runner_users_only(self):
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        # R128: closed and read back as the runner user: root never follows a path in the runner's home
        RU = ("sudo", "-n", "-u", "minions-runner", "--")
        chmod = host.ran.index(RU + ("chmod", "0600", f"{FOLDER}/.credentials", f"{FOLDER}/.runner",
                                     f"{FOLDER}/.credentials_rsaparams"))
        readback = [i for i, a in enumerate(host.ran) if a[:8] == RU + ("stat", "-c", "%a %U %n")]
        self.assertTrue(readback and readback[0] > chmod)
        self.assertFalse([a for a in admin_steps(host) if a[2] in ("chmod", "stat", "cat") and "/runners/" in " ".join(a)])
        # the runner's own 664, as seen (C3), left as it was: stop
        host, sha = setup_host(runs={RU + ("stat", "-c", "%a %U %n"): (
            0, f"664 minions-runner {FOLDER}/.credentials\n664 minions-runner {FOLDER}/.runner\n"
               f"600 minions-runner {FOLDER}/.credentials_rsaparams\n")})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("credential files are not its own only", lines[-2])
        self.assertFalse(any("svc.sh" in " ".join(a) or UNIT in " ".join(a) for a in host.ran),
                         "no service before the files are closed")

    def test_G2_9_the_runner_is_a_boot_service_started_after_its_limits(self):
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        tees = {a[-1]: i for a, i in host.fed if a[:3] == ("sudo", "-n", "tee")}
        dropin = tees[f"/etc/systemd/system/{UNIT}.d/50-minions.conf"]
        for line in ("Wants=network-online.target", "Slice=minions.slice", "Nice=10", "CPUWeight=50",
                     "Restart=on-failure", "RestartSec=30", "OOMPolicy=continue"):
            self.assertIn(line + "\n", dropin)
        sl = tees["/etc/systemd/system/minions.slice"]
        self.assertIn("CPUQuota=400%\n", sl)  # half of 8 cores
        self.assertIn(f"MemoryMax={int(16384000 * 1024 * 0.6)}\n", sl)
        order = [" ".join(a) for a in host.ran]
        svc = order.index(f"sudo -n tee /etc/systemd/system/{UNIT}")  # R127: the unit is this tool's own writing
        self.assertFalse([a for a in order if "/svc.sh" in a])  # runsvc.sh is copied as the runner; svc.sh never runs
        tee = max(i for i, a in enumerate(order) if a.startswith("sudo -n tee") and ("minions.slice" in a or DROPIN_NAME in a))
        reload_ = order.index("sudo -n systemctl daemon-reload --no-pager")
        enable = order.index(f"sudo -n systemctl enable {UNIT} --no-pager")
        start = order.index(f"sudo -n systemctl start {UNIT} --no-pager")
        show = next(i for i, a in enumerate(order) if a.startswith(f"systemctl show {UNIT}"))
        self.assertTrue(svc < tee < reload_ < enable < start < show, order)
        # read back not as written: stop
        host, sha = setup_host(runs={("systemctl", "show", UNIT): (0, SHOW.replace("Slice=minions.slice",
                                                                                   "Slice=system.slice"))})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("Slice='system.slice'", lines[-2])
        self.assertNotIn("runners", M.load_state(host))

    def test_G2_10_setup_ends_with_both_sides_read(self):
        host, sha = setup_host()
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK)
        self.assertTrue(lines[-1].startswith("ok both sides: GitHub lists minions-box-1 online and idle"), lines)
        for gh_says, active in ((runners_answer(status="offline"), "active\n"),
                                ((200, {"total_count": 0, "runners": []}), "active\n"),
                                (runners_answer(), "activating\n")):
            host, sha = setup_host(runs={("systemctl", "is-active", UNIT): (0, active)})
            fresh_look(host)
            code, lines = run_setup(setup_gh(sha, {("GET", f"repos/{REPO}/actions/runners?per_page=100"): gh_says}),
                                    host)
            self.assertEqual(code, M.EXIT_WAIT, lines)
            self.assertTrue(lines[-1].startswith("waiting "), lines)
        # the journal has each step, written before it ran
        with open(os.path.join(host.state_dir(), "journal.log")) as f:
            steps = [x.split(" ", 2)[1] for x in f]
        for s in ("useradd", "download", "unpack", "dependencies", "register", "chmod", "write"):  # R127: no svc.sh
            self.assertIn(s, steps)
        self.assertNotIn("svc.sh", steps)

    def test_G2_11_a_job_asking_only_for_self_hosted_never_lands_on_ours(self):
        for s in ({"cores": 1}, {"cores": 8, "mem": 16 * 1024 ** 3}, {"cores": 64, "mem": 256 * 1024 ** 3}):
            labels = M.runner_labels(s)
            self.assertEqual(labels[0], "minions")
            self.assertFalse(set(labels) & {"self-hosted", "linux", "Linux", "x64", "X64", "macos", "arm64"})
        host, sha = setup_host()
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha, {("GET", f"repos/{REPO}/actions/runners?per_page=100"):
                                               runners_answer(labels=("self-hosted", "Linux", "X64", "minions",
                                                                      "minions-medium"))}), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("no job asking for `self-hosted` can land on it", lines[-1])

    def test_G2_23_what_will_refuse_is_asked_before_the_first_write_and_a_later_stop_names_what_it_left(self):
        # Rule 11, row R155: read in the code and walked here (2026-11-24). Cut at the runner downloads list, at
        # the registration token, at config.sh, at systemd: each stop after the first write named nothing it left;
        # systemd's stop came after 48 admin steps and a registration on GitHub, with no next step at all.
        listing = ("GET", f"repos/{REPO}/actions/runners?per_page=100")
        none = (200, {"total_count": 0, "runners": []})
        next_step = G8Words.NEXT
        # 1. systemd does not run this computer: setup stops before its first write, here and on GitHub.
        host, sha = setup_host()
        host.dirs.pop(M.SYSTEMD_RUN_DIR)
        fresh_look(host)
        gh = setup_gh(sha)
        code, lines = run_setup(gh, host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertTrue(lines[-1].startswith("stop systemd does not run this computer: `systemctl` is here, but "
                                             f"{M.SYSTEMD_RUN_DIR} is not"), lines)
        self.assertIn("Nothing was changed, on this computer or on GitHub. Your next step:", lines[-1])
        self.assertEqual(admin_steps(host), [])
        self.assertFalse([c for c in gh.calls if c[0] != "GET"], gh.calls)
        self.assertNotIn("setting_up", M.load_state(host))
        # look says it too, before setup is asked; an answer that could not be read is not a "no" (rule 11).
        rep = M.Report(io.StringIO())
        M.survey(host, rep)
        self.assertTrue([x for x in rep.lines if x.startswith("warn systemd does not run this computer")], rep.lines)
        host, sha = setup_host()
        fresh_look(host)
        host.there = lambda path: None if path == M.SYSTEMD_RUN_DIR else FakeHost.there(host, path)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        # 2. every admin step of a whole setup, refused in turn, and the two GitHub answers that can refuse after
        # the first write: the last line names what is left (or that nothing was found made) and the next step.
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        steps = list(dict.fromkeys(tuple(a) for a in admin_steps(host)))
        self.assertEqual(steps[0][:3], ("sudo", "-n", "useradd"))  # the first write
        cuts = [("admin", s) for s in steps] + [("github", ("GET", f"repos/{REPO}/actions/runners/downloads")),
                                                ("github", ("POST", f"repos/{REPO}/actions/runners/registration-token"))]
        stopped = 0
        for kind, cut in cuts:
            host, sha = setup_host()
            fresh_look(host)
            over = {listing: none}
            if kind == "admin":
                host.runs[cut] = (1, "refused by the stand-in\n")
            else:
                over[cut] = (403, {"message": "Resource not accessible by integration"})
            code, lines = run_setup(setup_gh(sha, over), host)
            if not [x for x in lines if x.startswith("stop ")]:
                continue  # a step whose failure the tool reads past (a clean-up, a read-back that reads again)
            stopped += 1
            last = lines[-1]
            self.assertTrue(last.startswith(f"stop setup of {REPO} stopped"), (cut, lines[-3:]))
            self.assertIn(f"`setup {REPO}`", last, cut)
            if "part-way" in last:
                self.assertIn(f"`uninstall {REPO}` takes it away", last, cut)
            step = [x for x in lines[:-1] if x.startswith("stop ")]
            self.assertTrue(step, (cut, lines[-3:]))
            self.assertTrue(next_step.search(re.sub(r"^stop\s*`[^`]*`", "", step[-1])), (cut, step[-1]))
        self.assertGreaterEqual(stopped, 15, stopped)
        # 3. status after a cut says what is true: a setup cut part-way, what is left, and the two commands.
        host, sha = setup_host()
        fresh_look(host)
        config = ("sudo", "-n", "-u", "minions-runner", "--", *M.RUNNER_ENV, "bash", "-c", M.CONFIG_SCRIPT)
        host.runs[config] = (1, "Http response code: NotFound\n")
        gh = setup_gh(sha, {listing: none})
        self.assertEqual(run_setup(gh, host)[0], M.EXIT_STOP)
        code, lines = run_cmd(M.status, REPO, host=host, gh=gh)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertTrue(lines[0].startswith(f"stop a setup of {REPO} here was cut part-way, and this is left of it: "),
                        lines)
        self.assertIn(f"`setup {REPO}` finishes it; or `uninstall {REPO}` takes it away", lines[0])
        self.assertFalse([x for x in lines if "no runner of ours" in x], lines)
        # 4. sudo's words are true for both: a password taken with no ticket kept, and a password not taken.
        for took, words in ((0, "stop sudo took the password, but this computer does not keep admin rights open"),
                            (1, "stop sudo did not take the password (or it was not typed): nothing was touched")):
            host, sha = setup_host()
            fresh_look(host)
            host.runs[("sudo", "-n", "true")] = (1, "sudo: a password is required\n")
            host.sudo_validate = lambda took=took: took
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertTrue(lines[-1].startswith(words), lines[-1])
            self.assertEqual(admin_steps(host), [])


# ------------------------------------------------------------------------------------------------------------ G3

CI_YML = """name: ci
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  test:
    name: Unit tests
    runs-on: ubuntu-latest   # the person's own comment
    steps:
      - uses: actions/checkout@v4
      - run: python3 -m unittest -v
  build:
    runs-on:
      - ubuntu-latest-8-cores
    steps:
      - run: make
  huge:
    runs-on: ubuntu-latest-32-cores
    steps:
      - run: make all
  mac:
    runs-on: macos-latest
    steps:
      - run: xcodebuild
  cuda:
    runs-on: [ubuntu-latest, gpu]
    steps:
      - run: nvidia-smi
  matrix:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - run: echo hi
  db:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
    steps:
      - run: psql --version
"""
MAC_YML = "name: mac only\non: workflow_dispatch\njobs:\n  mac:\n    runs-on: macos-latest\n    steps:\n      - run: sw_vers\n"
RELEASE_YML = """name: release
on:
  push:
    tags:
      - 'v*'
jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - run: echo release
"""
DEPLOY_YML = """name: deploy
on: [push, workflow_dispatch]
jobs:
  site:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - run: ./deploy.sh
  npm:
    runs-on: ubuntu-latest
    steps:
      - run: npm publish
  pypi:
    runs-on: ubuntu-latest
    steps:
      - run: ./upload.sh
        env:
          TOKEN: ${{ secrets.PYPI_API_TOKEN }}
  lint:
    runs-on: ubuntu-latest
    steps:
      - run: ./lint.sh
        env:
          GH: ${{ secrets.GITHUB_TOKEN }}
"""
OURS = "minions-box-1"


class MoveGH(StandIn):
    """GitHub for `move`: the workflow files, our runner, the Git Data API, pull requests and runs, with every
    write kept in `writes` as (method, path, body)."""

    def __init__(self, files, runner_status="online", pr_state="open", merged_at=None, runs=None, jobs=None):
        super().__init__([])
        self.files, self.writes = dict(files), []
        self.pr = {"number": 12, "state": pr_state, "merged_at": merged_at,
                   "html_url": f"https://github.com/{REPO}/pull/12"}
        self.runs, self.jobs = runs if runs is not None else [], jobs or {}
        self.runner_status = runner_status
        self.others = []  # G3.7: other self-hosted runners GitHub lists for the repository

    def api(self, method, path, body=None):
        self.calls.append((method, path))
        if method != "GET":
            self.writes.append((method, path, body))
        base = f"repos/{REPO}"
        if (method, path) == ("GET", base):
            return 200, {"default_branch": "main", "private": True, "visibility": "private"}
        if method == "GET" and path.split("?")[0] == f"{base}/actions/runners":
            return 200, {"total_count": 1, "runners": [{"id": 7, "name": OURS, "status": self.runner_status,
                                                        "busy": False, "os": "unknown",
                                                        "labels": [{"name": "minions"}, {"name": "minions-medium"}]}]
                         + self.others}
        if method == "GET" and path.split("?")[0] == f"{base}/contents/.github/workflows":
            return 200, [{"name": p.rsplit("/", 1)[1], "path": p} for p in sorted(self.files)]
        if method == "GET" and path.startswith(f"{base}/contents/"):
            p = path[len(f"{base}/contents/"):].split("?")[0]
            return 200, {"content": M.base64.b64encode(self.files[p].encode()).decode()}
        if (method, path) == ("GET", f"{base}/git/ref/heads/main"):
            return 200, {"object": {"sha": "tip0"}}
        if (method, path) == ("GET", f"{base}/git/commits/tip0"):
            return 200, {"tree": {"sha": "tree0"}}
        if (method, path) == ("POST", f"{base}/git/blobs"):
            return 201, {"sha": f"blob{len(self.writes)}"}
        if (method, path) == ("POST", f"{base}/git/trees"):
            return 201, {"sha": "tree1"}
        if (method, path) == ("POST", f"{base}/git/commits"):
            return 201, {"sha": "commit1"}
        if (method, path) == ("POST", f"{base}/git/refs"):
            return 201, {"ref": body["ref"]}
        if (method, path) == ("POST", f"{base}/pulls"):
            return 201, {"number": 12, "html_url": self.pr["html_url"]}
        if (method, path) == ("GET", f"{base}/pulls/12"):
            return 200, self.pr
        if method == "GET" and "/actions/workflows/" in path and "/runs" in path:
            wf = path.split("/actions/workflows/")[1].split("/")[0]
            return 200, {"workflow_runs": [r for r in self.runs if r["wf"] == wf]}
        if method == "POST" and path.endswith("/dispatches"):
            wf = path.split("/actions/workflows/")[1].split("/")[0]
            self.runs.append({"id": 99, "wf": wf, "status": "completed", "created_at": "2026-11-22T23:59:00Z"})
            return 200, {"workflow_run_id": 99}
        if method == "GET" and path.endswith("/jobs"):
            rid = int(path.split("/runs/")[1].split("/")[0])
            return 200, {"jobs": self.jobs.get(rid, [])}
        self.misses.append((method, path))
        return 404, {"message": "Not Found"}

    def blobs(self):
        return [b["content"] for m, p, b in self.writes if p.endswith("/git/blobs")]


def move_host(runner=True):
    host = linux_host()
    fresh_look(host)
    if runner:
        d = M.load_state(host)
        d["runners"] = {REPO: {"name": OURS, "labels": ["minions", "minions-medium"], "unit": UNIT}}
        M.save_state(host, d)
    return host


def run_move(gh, host, **kw):
    out = io.StringIO()
    code = M.move(REPO, host=host, gh=gh, out=out, **kw)
    return code, out.getvalue().splitlines()


def pr_body_of(gh):
    return next(b["body"] for m, p, b in gh.writes if p.endswith("/pulls"))


class G3(unittest.TestCase):
    FILES = {".github/workflows/ci.yml": CI_YML, ".github/workflows/mac.yml": MAC_YML,
             ".github/workflows/release.yml": RELEASE_YML, ".github/workflows/deploy.yml": DEPLOY_YML}

    def test_G3_1_move_opens_a_pull_request_and_never_merges(self):
        host, gh = move_host(), MoveGH(self.FILES)
        code, lines = run_move(gh, host)
        self.assertEqual(code, M.EXIT_NEED, lines)
        self.assertTrue(lines[-1].startswith("need you: read and merge pull request #12 yourself"), lines)
        self.assertIn("never merges", lines[-1])
        # one branch, one commit on the tip, one pull request; never a merge, never a write to the default branch
        kinds = [p.rsplit("/", 1)[-1] if not p.endswith("/git/refs") else "refs" for m, p, b in gh.writes]
        self.assertEqual([k for k in kinds if k != "blobs"], ["trees", "commits", "refs", "pulls"], gh.writes)
        self.assertFalse([w for w in gh.writes if "merge" in w[1] or w[0] in ("PUT", "PATCH")])
        ref = next(b for m, p, b in gh.writes if p.endswith("/git/refs"))
        self.assertRegex(ref["ref"], r"^refs/heads/minions-move-[0-9a-f]{8}$")
        commit = next(b for m, p, b in gh.writes if p.endswith("/git/commits"))
        self.assertEqual(commit["parents"], ["tip0"])
        # only runs-on lines change; comments and layout stay; never self-hosted
        (new,) = [c for c in gh.blobs() if c.startswith("name: ci")]
        old_l, new_l = CI_YML.splitlines(), new.splitlines()
        diff = [(a, b) for a, b in zip(old_l, new_l) if a != b]
        self.assertEqual(diff[0], ("    runs-on: ubuntu-latest   # the person's own comment",
                                   "    runs-on: [minions]   # the person's own comment"))
        self.assertIn("    runs-on: [minions, minions-medium]", new_l)  # the 8-core job needs the size label
        self.assertNotIn("      - ubuntu-latest-8-cores", new_l)
        self.assertEqual(len(old_l) - len(new_l), 1)  # the list form became one line; nothing else moved
        self.assertTrue(all(a == b or "runs-on:" in a for a, b in zip(old_l[:16], new_l[:16])))
        for c in gh.blobs():
            self.assertNotIn("self-hosted", c)
        # a second move while that pull request is open opens no second one
        gh2 = MoveGH(self.FILES)
        code, lines = run_move(gh2, host)
        self.assertEqual(code, M.EXIT_NEED)
        self.assertIn("still open", lines[-1])
        self.assertFalse([w for w in gh2.writes if w[1].endswith("/pulls")])
        # no runner of ours online, or no fresh look: stop, and nothing is written
        for h, g, want in ((move_host(runner=False), MoveGH(self.FILES), "run `setup"),
                           (move_host(), MoveGH(self.FILES, runner_status="offline"), "is offline")):
            code, lines = run_move(g, h)
            self.assertEqual(code, M.EXIT_STOP)
            self.assertIn(want, lines[-1])
            self.assertEqual(g.writes, [])
        h = linux_host()
        code, lines = run_move(MoveGH(self.FILES), h)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("no look of", lines[-1])

    def test_G1_15_look_and_move_judge_jobs_alike(self):
        files = dict(self.FILES)
        files[".github/workflows/theirs.yml"] = ("name: theirs\non: workflow_dispatch\njobs:\n  alone:\n"
                                                 "    runs-on: self-hosted\n    steps:\n      - run: x\n"
                                                 "  team:\n    runs-on: [team-box]\n    steps:\n      - run: x\n")
        s = {"system": "Linux", "machine": "x86_64", "cores": 8, "mem": 16384000 * 1024}
        ours = M.runner_labels(s)
        for path, text in files.items():
            trig = M.read_triggers(text)
            for job in M.read_workflow(text):
                kind, words, _ = M.plan_job(path, trig, job, "Linux", "x86_64", ours, set())
                lk, lw = M.look_job_line(path, trig, job, s)
                self.assertEqual(lk == "ok", kind in ("move", "here"), (path, job["id"], kind, lw))
                self.assertIn(words, lw, (path, job["id"]))
                if kind == "publish":
                    self.assertTrue(lw.startswith("publishes or deploys ("), lw)
                    self.assertNotIn("can move here", lw)
        # The release job on a pushed tag: look no longer says it can move.
        trig = M.read_triggers(RELEASE_YML)
        (job,) = M.read_workflow(RELEASE_YML)
        lk, lw = M.look_job_line(".github/workflows/release.yml", trig, job, s)
        self.assertEqual(lk, "warn")
        self.assertIn("starts on a pushed tag", lw)
        self.assertIn("--include-job release.yml:publish", lw)
        # A runner that is not ours: the same words in look's line and under its own heading in move's PR.
        trig = M.read_triggers(files[".github/workflows/theirs.yml"])
        for job in M.read_workflow(files[".github/workflows/theirs.yml"]):
            self.assertTrue(M.look_job_line("theirs.yml", trig, job, s)[1].startswith("asks for a runner that is not ours"))
        gh = MoveGH(files)
        run_move(gh, move_host())
        body = pr_body_of(gh)
        theirs = body.split("## Left as they are (they ask for a runner that is not ours)")[1].split("##")[0]
        self.assertIn("job `alone`: asks for a runner that is not ours (self-hosted)", theirs)
        self.assertIn("job `team`: asks for a runner that is not ours (team-box)", theirs)
        cannot = body.split("## Left as they are (they cannot run on this computer)")[1].split("##")[0]
        self.assertNotIn("not ours", cannot)

    def test_G3_2_jobs_that_cannot_move_are_left_and_named(self):
        host, gh = move_host(), MoveGH(self.FILES)
        code, lines = run_move(gh, host)
        body = pr_body_of(gh)
        left = body.split("## Left as they are (they cannot run on this computer)")[1].split("##")[0]
        for job, why in (("huge", "asks for 32 cores"), ("mac", "macos-latest"), ("cuda", "GPU"),
                         ("matrix", "chosen when the job runs"), ("db", "containers")):
            self.assertRegex(left, rf"job `{job}`: [^\n]*{re.escape(why)}", body)
        (ci,) = [c for c in gh.blobs() if c.startswith("name: ci")]
        for keep in ("    runs-on: ubuntu-latest-32-cores", "    runs-on: macos-latest", "    runs-on: [ubuntu-latest, gpu]",
                     "    runs-on: ${{ matrix.os }}"):
            self.assertIn(keep, ci.splitlines())
        self.assertEqual(ci.count("runs-on: ubuntu-latest\n"), 1)  # the db job's line is as it was
        # a file whose every job stays is not written at all
        self.assertFalse([c for c in gh.blobs() if c.startswith("name: mac only")])
        self.assertIn("job `mac`", body)
        # nothing at all can move: no branch, no pull request
        gh2 = MoveGH({".github/workflows/mac.yml": MAC_YML})
        code, lines = run_move(gh2, move_host())
        self.assertEqual(code, M.EXIT_OK)
        self.assertIn("nothing was changed", lines[-1])
        self.assertEqual(gh2.writes, [])

    def test_G3_3_publishing_jobs_are_never_moved_silently(self):
        host, gh = move_host(), MoveGH(self.FILES)
        run_move(gh, host)
        body = pr_body_of(gh)
        pub = body.split("## Left as they are (they publish or deploy)")[1].split("## ")[0]
        for job, why in (("publish", "starts on a pushed tag"), ("site", "deploys to an environment"),
                         ("npm", "npm publish"), ("pypi", "publishing secret (PYPI_API_TOKEN)")):
            self.assertRegex(pub, rf"job `{job}`: [^\n]*{re.escape(why)}", body)
        self.assertIn("--include-job", pub)
        self.assertFalse([c for c in gh.blobs() if c.startswith("name: release")])
        (dep,) = [c for c in gh.blobs() if c.startswith("name: deploy")]
        self.assertEqual(dep.count("runs-on: [minions]"), 1)  # lint only: GITHUB_TOKEN is not a publishing secret
        self.assertIn("  lint:\n    runs-on: [minions]\n", dep)
        # the person's word for one job moves that job only
        host, gh = move_host(), MoveGH(self.FILES)
        run_move(gh, host, include=["release.yml:publish"])
        (rel,) = [c for c in gh.blobs() if c.startswith("name: release")]
        self.assertIn("  publish:\n    runs-on: [minions]\n", rel)
        self.assertIn("with your word for this job", pr_body_of(gh))
        (dep,) = [c for c in gh.blobs() if c.startswith("name: deploy")]
        self.assertEqual(dep.count("runs-on: [minions]"), 1)

    def test_G3_7_verify_counts_a_job_on_another_computer_of_ours(self):
        # Seen 2026-11-23 (run I, row R114): two computers of ours served one repository; the other one took the
        # moved job and verify said it did not move. A runner of ours elsewhere counts, and is named; one GitHub no
        # longer lists is the third answer; a runner that is not ours is still "did not move".
        files = {".github/workflows/ci.yml": CI_YML}
        host, gh = move_host(), MoveGH(files)
        run_move(gh, host)
        merged = {p: next(c for c in gh.blobs() if c.splitlines()[0] == text.splitlines()[0]) for p, text in files.items()}
        runs = [{"id": 5, "wf": "ci.yml", "status": "completed", "created_at": "2026-11-22T23:10:00Z"}]
        job = lambda name, runner: {"name": name, "runner_name": runner, "labels": ["minions"], "conclusion": "success",
                                    "html_url": "https://example.invalid/j", "runner_group_name": "Default"}
        theirs = lambda name, labels: {"id": 9, "name": name, "status": "online", "busy": False, "os": "Linux",
                                       "labels": [{"name": x} for x in labels]}
        def verify(runner, others):
            h = move_host()
            d = M.load_state(host)
            d["moves"][REPO].pop("verified", None)
            M.save_state(h, d)
            g = MoveGH(merged, pr_state="closed", merged_at="2026-11-22T23:00:00Z", runs=list(runs),
                       jobs={5: [job("Unit tests", runner), job("build", OURS)]})
            g.others = others
            return run_move(g, h, verify=True)
        code, lines = verify("minions-laptop", [theirs("minions-laptop", ["minions", "minions-small"])])
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn("ok moved: .github/workflows/ci.yml job test ran on minions-laptop (another computer of yours "
                      "with this tool's runner) and succeeded (run 5)", lines)
        self.assertIn(f"ok moved: .github/workflows/ci.yml job build ran on {OURS} (this computer) and succeeded (run 5)",
                      lines)
        self.assertEqual(lines[-1], f"ok moved: every moved job of {REPO} ran on a computer of yours with this tool's "
                                    "runner (here, or minions-laptop)")
        # no longer listed: the tool cannot tell, and says so; never "moved", never "did not move"
        code, lines = verify("minions-gone", [])
        self.assertEqual(code, M.EXIT_STOP, lines)
        stop = [x for x in lines if x.startswith("stop ")]
        self.assertEqual(len(stop), 1, lines)
        self.assertIn("cannot tell whether it is one of yours", stop[0])
        self.assertNotIn("did not move", stop[0])
        self.assertFalse([x for x in lines if x.startswith("ok moved: every")], lines)
        # a runner that carries GitHub's default labels is not one this tool registered: it did not move
        code, lines = verify("build-box", [theirs("build-box", ["self-hosted", "Linux", "minions"])])
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertTrue([x for x in lines if x.startswith("stop ") and "did not move" in x], lines)

    def test_G3_6_moved_is_said_only_after_a_real_job_ran(self):
        files = {".github/workflows/ci.yml": CI_YML, ".github/workflows/lint.yml":
                 "name: lint\non: [push, workflow_dispatch]\njobs:\n  lint:\n    runs-on: ubuntu-latest\n    steps:\n"
                 "      - run: ./lint.sh\n"}
        host, gh = move_host(), MoveGH(files)
        run_move(gh, host)
        merged = {p: next(c for c in gh.blobs() if c.splitlines()[0] == text.splitlines()[0]) for p, text in files.items()}
        # not merged yet: need you, and nothing is called moved
        code, lines = run_move(MoveGH(merged), host, verify=True)
        self.assertEqual(code, M.EXIT_NEED)
        self.assertIn("not merged yet", lines[-1])
        self.assertFalse([x for x in lines if x.startswith("ok moved")])
        job = lambda name, runner, labels, c="success": {"name": name, "runner_name": runner, "labels": labels,
                                                          "conclusion": c, "html_url": "https://example.invalid/j"}
        runs = [{"id": 5, "wf": "ci.yml", "status": "completed", "created_at": "2026-11-22T23:10:00Z"},
                {"id": 4, "wf": "ci.yml", "status": "completed", "created_at": "2026-11-22T22:00:00Z"}]
        jobs = {5: [job("Unit tests", OURS, ["minions"]), job("build", OURS, ["minions", "minions-medium"]),
                    job("mac", "GitHub Actions 3", ["macos-latest"])],
                4: [job("Unit tests", "GitHub Actions 1", ["ubuntu-latest"])],
                99: [job("lint", OURS, ["minions"])]}
        gh = MoveGH(merged, pr_state="closed", merged_at="2026-11-22T23:00:00Z", runs=runs, jobs=jobs)
        code, lines = run_move(gh, host, verify=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn(f"ok moved: .github/workflows/ci.yml job test ran on {OURS} (this computer) and succeeded (run 5)", lines)
        self.assertIn(f"ok moved: every moved job of {REPO} ran on this computer", lines[-1])
        # lint.yml had no run since the merge, every one of its jobs moved and it can be started by hand: started
        self.assertIn(("POST", f"repos/{REPO}/actions/workflows/lint.yml/dispatches"), gh.calls)
        # ci.yml has a job that stays on GitHub's machines: never started by the tool
        self.assertNotIn(("POST", f"repos/{REPO}/actions/workflows/ci.yml/dispatches"), gh.calls)
        # a moved job that ran on another runner is a stop
        jobs2 = {**jobs, 5: [job("Unit tests", "GitHub Actions 2", ["ubuntu-latest"])]}
        host2 = move_host()
        d = M.load_state(host)
        d["moves"][REPO].pop("verified", None)
        M.save_state(host2, d)
        code, lines = run_move(MoveGH(merged, pr_state="closed", merged_at="2026-11-22T23:00:00Z", runs=list(runs),
                                      jobs=jobs2), host2, verify=True)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue([x for x in lines if x.startswith("stop ") and "did not move" in x], lines)
        # no run yet of a workflow the tool may not start: waiting, exit 4, the deadline-free reason and what next
        code, lines = run_move(MoveGH(merged, pr_state="closed", merged_at="2026-11-22T23:00:00Z", runs=[],
                                      jobs=jobs), host2, verify=True)
        self.assertEqual(code, M.EXIT_WAIT, lines)
        self.assertTrue(lines[-1].startswith("waiting "), lines)
        self.assertIn(f"`move {REPO} --verify`", lines[-1])
        self.assertFalse([x for x in lines if x.startswith("ok moved: every")])
        self.assertTrue([x for x in lines if x.startswith("waiting .github/workflows/ci.yml: no run since the merge")])
        # a run still going at the deadline: the deadline is said, then waiting
        slow = [{"id": 6, "wf": "ci.yml", "status": "in_progress", "created_at": "2026-11-22T23:10:00Z"},
                {"id": 7, "wf": "lint.yml", "status": "queued", "created_at": "2026-11-22T23:10:00Z"}]
        code, lines = run_move(MoveGH(merged, pr_state="closed", merged_at="2026-11-22T23:00:00Z", runs=slow,
                                      jobs=jobs), host2, verify=True, wait_s=30, poll_s=10)
        self.assertEqual(code, M.EXIT_WAIT, lines)
        self.assertTrue([x for x in lines if re.search(r"until \d\d:\d\dZ at the latest", x)], lines)
        self.assertTrue([x for x in lines if "still in_progress at the deadline" in x], lines)
        self.assertIn("--verify", lines[-1])
        # R76: the same wait while GitHub shows our runner offline: said as that, with `resume` named first
        gh = MoveGH(merged, pr_state="closed", merged_at="2026-11-22T23:00:00Z", runs=slow, jobs=jobs)
        gh.runner_status = "offline"
        code, lines = run_move(gh, host2, verify=True, wait_s=30, poll_s=10)
        self.assertEqual(code, M.EXIT_WAIT, lines)
        self.assertEqual(lines[-1], f"waiting on our runner {OURS}: GitHub shows it offline, so the moved jobs wait for it; "
                                    f"nothing is called moved before one has run here. Next: `resume {REPO}`, then "
                                    f"`move {REPO} --verify` again")
        self.assertFalse([x for x in lines if x.startswith("ok moved")])


FAKE_INHIBIT = """#!/bin/bash
# a stand-in for systemd-inhibit: writes down its options, then runs the rest as the real one does
opts=()
while [ "${1#--}" != "$1" ]; do opts+=("$1"); shift; done
printf '%s\\n' "${opts[*]}" >> "$MINIONS_TEST_LOG"
exec "$@"
"""


def held_state(host, labels=("minions", "minions-medium")):
    M.save_state(host, {"runners": {REPO: {"name": "minions-box-1", "folder": FOLDER, "unit": UNIT,
                                          "labels": list(labels)}}})


class DrainGH(StandIn):
    """GitHub for drain and resume: the runner's busy flag follows `busy` (one value per read), labels follow PUTs."""

    def __init__(self, busy=(False,), status="online"):
        super().__init__([])
        self.busy, self.status, self.labels, self.puts = list(busy), status, ["minions", "minions-medium"], []

    def api(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path == f"repos/{REPO}/actions/runners?per_page=100":
            b = self.busy.pop(0) if len(self.busy) > 1 else self.busy[0]
            return runners_answer(status=self.status, busy=b, labels=self.labels)
        if method == "PUT" and path == f"repos/{REPO}/actions/runners/7/labels":
            self.puts.append(list(body["labels"]))
            self.labels = list(body["labels"])
            return 200, {"labels": [{"name": x} for x in self.labels]}
        return 404, {"message": "Not Found"}


def run_drain(gh, host, **kw):
    out = io.StringIO()
    code = M.drain(REPO, host=host, gh=gh, out=out, **kw)
    return code, out.getvalue().splitlines()


class G4(unittest.TestCase):
    def test_G4_1_one_job_at_a_time_per_runner(self):
        # The runner takes one job at a time; setup never registers it in a way that changes that, and a second
        # runner for the same repository is not made on top of the first (more runners come only with G12).
        for opt in ("--ephemeral", "--jitconfig", "--disableupdate"):
            self.assertNotIn(opt, M.CONFIG_SCRIPT)
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        configs = [a for a in host.ran if M.CONFIG_SCRIPT in a]
        self.assertEqual(len(configs), 1)
        self.assertEqual(list(M.load_state(host)["runners"]), [REPO])
        host2, sha = setup_host(runs={R_TEST: (0, "")})  # the runner's folder is there (read as the runner, R128)
        fresh_look(host2)
        code, lines = run_setup(setup_gh(sha), host2)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertFalse(any(M.CONFIG_SCRIPT in a for a in host2.ran))

    def test_G4_3_the_disk_never_fills_from_jobs(self):
        # setup names both hooks in the runner's .env, root-owned and 755, read back.
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        envtext = next(x for a, x in host.fed if a[-1] == f"{FOLDER}/.env")
        self.assertIn("LANG=C.UTF-8\n", envtext)
        self.assertIn(f"ACTIONS_RUNNER_HOOK_JOB_COMPLETED={M.hook_dir(REPO)}/job-completed.sh\n", envtext)
        self.assertTrue(any(a[:4] == ("sudo", "-n", "chown", "root:root") for a in host.ran))
        # The completed hook, run for real: it empties this job's work folder and nothing outside it.
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            folder = os.path.realpath(d)
            ws = os.path.join(folder, "_work", "repo", "repo")
            os.makedirs(os.path.join(ws, "sub"))
            with open(os.path.join(ws, "sub", "build.out"), "w") as f:
                f.write("x")
            outside = os.path.join(folder, "keep.txt")
            with open(outside, "w") as f:
                f.write("mine")
            acts = os.path.join(folder, "_work", "_actions")
            script = os.path.join(folder, "done.sh")
            for floor, low in ((0, False), (1 << 62, True)):
                with mock.patch.object(M, "DISK_FLOOR", floor):
                    with open(script, "w") as f:
                        f.write(M.job_completed_script(folder))
                os.makedirs(os.path.join(acts, "someone", "an-action", "v1"), exist_ok=True)
                open(os.path.join(acts, "someone", "an-action", "v1.completed"), "w").close()
                env = dict(os.environ, GITHUB_WORKSPACE=ws)
                r = subprocess.run(["bash", script], env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(os.listdir(ws), [])
                self.assertTrue(os.path.exists(outside))
                self.assertEqual(os.path.exists(os.path.join(folder, "_minions", "disk-low")), low)
                self.assertEqual(os.listdir(acts), [], "the actions a job fetched are deleted (G4.3)")
                self.assertIn("minions: the actions this job fetched were deleted", r.stdout)
            # A workspace outside the runner's work folder, or one that climbs out of it, is never touched.
            for bad in (folder, os.path.join(folder, "_work", "..")):
                r = subprocess.run(["bash", script], env=dict(os.environ, GITHUB_WORKSPACE=bad), capture_output=True,
                                   text=True, timeout=30)
                self.assertTrue(os.path.exists(outside))
        # Under the floor: setup sets nothing up, and resume does not release a held runner.
        host, sha = setup_host(free=(1024 ** 3, 500 * 1024 ** 3))  # read at setup's time, not the look's
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("under the 5.0 GB floor", lines[-1])
        self.assertEqual(admin_steps(host), [])
        host = setup_host(free=(1024 ** 3, 500 * 1024 ** 3))[0]
        held_state(host)
        gh = DrainGH()
        out = io.StringIO()
        self.assertEqual(M.resume(REPO, host=host, gh=gh, out=out), M.EXIT_STOP)
        self.assertEqual(gh.puts, [])

    def _hooks_for_real(self, folder, log):
        bindir = os.path.join(folder, "bin-stand-in")
        os.makedirs(bindir)
        with open(os.path.join(bindir, "systemd-inhibit"), "w") as f:
            f.write(FAKE_INHIBIT)
        os.chmod(os.path.join(bindir, "systemd-inhibit"), 0o755)
        env = dict(no_event(), PATH=bindir + os.pathsep + os.environ.get("PATH", ""), MINIONS_TEST_LOG=log,
                   RUNNER_TRACKING_ID="github_job_1", GITHUB_WORKSPACE=os.path.join(folder, "_work", "r", "r"))
        seen = fresh_seen(folder)
        for name, text in (("start.sh", M.job_started_script(folder, seen=seen)), ("done.sh", M.job_completed_script(folder))):
            with open(os.path.join(folder, name), "w") as f:
                f.write(text)
        return env

    def test_G4_4_no_sleep_during_a_job(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            folder = os.path.realpath(d)
            log = os.path.join(folder, "inhibit.log")
            env = self._hooks_for_real(folder, log)
            pid = None
            try:
                r = subprocess.run(["bash", os.path.join(folder, "start.sh")], env=env, capture_output=True,
                                   text=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("will not fall asleep by itself", r.stdout)
                with open(os.path.join(folder, "_minions", "inhibit.pid")) as f:
                    pid = int(f.read())
                os.kill(pid, 0)  # the locks are held while the job runs
                for _ in range(50):
                    if os.path.exists(log) and len(open(log).read().splitlines()) == 2:
                        break
                    M.time.sleep(0.1)
                self.assertEqual(open(log).read().splitlines(),
                                 [" ".join(M.INHIBIT_IDLE[1:]), " ".join(M.INHIBIT_SLEEP_DELAY[1:])])
                with open(f"/proc/{pid}/environ", "rb") as f:
                    self.assertNotIn(b"RUNNER_TRACKING_ID=", f.read())  # its own, ended by our hook
                self.assertEqual(os.getpgid(pid), pid)  # its own process group, so the hook ends all of it
                r = subprocess.run(["bash", os.path.join(folder, "done.sh")], env=env, capture_output=True,
                                   text=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)
                for _ in range(50):
                    try:
                        os.kill(pid, 0)
                        if open(f"/proc/{pid}/stat").read().split()[2] == "Z":
                            break
                    except (ProcessLookupError, FileNotFoundError):
                        break
                    M.time.sleep(0.1)
                else:
                    self.fail("the lock outlived the job")
                self.assertFalse(os.path.exists(os.path.join(folder, "_minions", "inhibit.pid")))
                pid = None
            finally:
                if pid:
                    os.kill(pid, 15)
        # R89 (seen on a stock machine): a job whose end hook never ran, because its service stopped mid-job, left the
        # hold for up to 6 hours. The next job's start hook ends the old hold first, and the service's own stop ends
        # it too (ExecStopPost in our drop-in).
        def gone(p):
            for _ in range(50):
                try:
                    os.kill(p, 0)
                    if open(f"/proc/{p}/stat").read().split()[2] == "Z":
                        return True
                except (ProcessLookupError, FileNotFoundError):
                    return True
                M.time.sleep(0.1)
            return False
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            folder = os.path.realpath(d)
            env = self._hooks_for_real(folder, os.path.join(folder, "inhibit.log"))
            pidfile = os.path.join(folder, "_minions", "inhibit.pid")
            pids = []
            try:
                for _ in range(2):
                    r = subprocess.run(["bash", os.path.join(folder, "start.sh")], env=env, capture_output=True,
                                       text=True, timeout=30)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    with open(pidfile) as f:
                        pids.append(int(f.read()))
                self.assertTrue(gone(pids[0]), "the next job's start ends an earlier hold")
                post = next(x for x in M.dropin_text(folder).splitlines() if x.startswith("ExecStopPost="))
                r = subprocess.run(shlex.split(post.split("=", 1)[1].lstrip("-")), capture_output=True, text=True,
                                   timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue(gone(pids[1]), "the service's stop ends the hold")
                self.assertFalse(os.path.exists(pidfile))
                pids = []
            finally:
                for p in pids:
                    try:
                        os.kill(p, 15)
                    except ProcessLookupError:
                        pass
        # The lock ends by itself after GitHub's longest default job, if a runner dies before its hook runs.
        self.assertEqual(M.inhibitor_argv()[-2:], ["sleep", str(6 * 3600)])

    def test_G4_9_the_inhibitor_never_holds_a_sleep_the_person_or_hardware_asks_for(self):
        argv = M.inhibitor_argv()
        locks = []
        for i, a in enumerate(argv):
            if a == "systemd-inhibit":
                opts = {}
                for x in argv[i + 1:]:
                    if not x.startswith("--"):
                        break
                    k, _, v = x[2:].partition("=")
                    opts[k] = v
                locks.append(opts)
        self.assertEqual([(o["what"], o["mode"]) for o in locks], [("idle", "block"), ("sleep", "delay")])
        for o in locks:
            whats = o["what"].split(":")
            self.assertFalse(any(w.startswith("handle-") or w == "shutdown" for w in whats), o)
            self.assertFalse("sleep" in whats and o["mode"] == "block", o)
        script = M.job_started_script(FOLDER)
        for word in M.FORBIDDEN_INHIBIT:
            self.assertNotIn(word, script)
        self.assertNotIn("--mode=block --who", " ".join(M.INHIBIT_SLEEP_DELAY))
        self.assertEqual(script.count("'systemd-inhibit'"), 2)  # the two locks it starts, nothing else

    def test_G11_3_sleep_is_put_off_during_a_job_where_the_os_allows(self):
        # Linux: during a job, a delay lock on sleep (never a block), taken by the job-start hook and let go with the
        # job; the skill names what is not put off beyond that delay: a sleep asked for, the lid, a low battery.
        script = M.job_started_script(FOLDER)
        self.assertIn(M.sh_quote(M.INHIBIT_SLEEP_DELAY[:3]), script)
        self.assertIn("--what=sleep", M.INHIBIT_SLEEP_DELAY)
        self.assertIn("--mode=delay", M.INHIBIT_SLEEP_DELAY)
        with open(os.path.join(ROOT, "skills", "repo-minions", "SKILL.md")) as f:
            skill = re.sub(r"\s+", " ", f.read())
        self.assertIn("a sleep you ask for, the lid or a low battery still puts it to sleep, about N seconds later.",
                      skill)
        self.assertIn("one that puts off a requested sleep by the short delay the system allows", skill)

    def test_G4_5_runner_self_update_stays_on_and_is_reported(self):
        self.assertNotIn("--disableupdate", M.CONFIG_SCRIPT)
        host, sha = setup_host()
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK)
        self.assertIn("ok runner version 2.338.2; GitHub's own self-update stays on", lines)
        self.assertEqual(M.load_state(host)["runners"][REPO]["version"], "2.338.2")

    def test_G4_6_the_service_is_stopped_only_after_a_drain(self):
        stops = lambda h: [a for a in h.ran if "stop" in a and "systemctl" in a]
        # A job runs past the deadline: held, waiting with its deadline and the next command, exit 4, nothing stopped.
        host = setup_host(runs={("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner.Worker"): (0, "4321\n")})[0]
        held_state(host)
        gh = DrainGH(busy=(True,))
        code, lines = run_drain(gh, host, stop=True, wait_s=0)
        self.assertEqual(code, M.EXIT_WAIT)
        self.assertEqual(gh.puts, [["minions-held"]])
        self.assertTrue(lines[-1].startswith("waiting for the running job to end (GitHub says busy, a job process"),
                        lines)
        self.assertIn(f"Next: `drain {REPO}` again", lines[-1])
        self.assertEqual(stops(host), [])
        self.assertEqual(M.load_state(host)["runners"][REPO]["labels_before_hold"], ["minions", "minions-medium"])
        # Both sides idle after a while: drained, then (asked) stopped; the deadline was said in the line.
        answers = iter([(0, "4321\n"), (1, "")])
        host = setup_host(runs={("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner.Worker"):
                                lambda a, i: next(answers, (1, "")),
                                ("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner."): (1, ""),
                                ("systemctl", "is-active", UNIT): (3, "inactive\n")})[0]
        held_state(host)
        gh = DrainGH(busy=(True, False))
        code, lines = run_drain(gh, host, stop=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertTrue(any(re.search(r"waiting for it to end, until \d\d:\d\dZ at the latest", x) for x in lines))
        self.assertIn("ok drained: online and idle: minions-box-1 is held, GitHub says online and not busy, and no job "
                      "process runs here", lines)
        self.assertEqual(len(stops(host)), 1)
        i_drained = next(i for i, x in enumerate(lines) if x.startswith("ok drained"))
        i_stopped = next(i for i, x in enumerate(lines) if x.startswith("ok the runner is stopped"))
        self.assertLess(i_drained, i_stopped)
        # resume: started again only with no process of the runner left, then our labels back.
        code = M.resume(REPO, host=host, gh=gh, out=io.StringIO())
        self.assertEqual(code, M.EXIT_OK)
        self.assertEqual(gh.puts[-1], ["minions", "minions-medium"])
        self.assertNotIn("labels_before_hold", M.load_state(host)["runners"][REPO])
        # And the stop helper itself refuses without a drain.
        rep = M.Report(io.StringIO())
        self.assertFalse(M.stop_runner(host, UNIT, FOLDER, rep, drained=False))
        self.assertIn("only after a drain", rep.lines[-1])

    def test_G4_7_a_stop_is_sigterm_and_read_back_never_two_copies(self):
        left = {"n": 0}

        def pgrep(argv, _):
            return (0, "777\n") if left["n"] == 0 else (1, "")

        def kill(argv, _):
            left["n"] += 1
            return 0, ""

        host = setup_host(runs={("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner."): pgrep,
                                ("sudo", "-n", "-u", "minions-runner", "--", "kill", "-TERM"): kill,
                                ("systemctl", "is-active", UNIT): (3, "inactive\n")})[0]
        rep = M.Report(io.StringIO())
        self.assertTrue(M.stop_runner(host, UNIT, FOLDER, rep, drained=True))
        # by exact PID, SIGTERM, never SIGINT; as the runner user, who can end only its own (R128)
        self.assertIn(("sudo", "-n", "-u", "minions-runner", "--", "kill", "-TERM", "777"), host.ran)
        self.assertFalse(any("-INT" in a or "-9" in a or "pkill" in a or "killall" in a for a in host.ran))
        self.assertEqual(rep.lines[-1], "ok the runner is stopped: the service is inactive, and no process of it is left")
        # A process that does not go: stop, and nothing starts the runner again (never two copies).
        host = setup_host(runs={("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner."): (0, "777\n"),
                                ("systemctl", "is-active", UNIT): (3, "inactive\n")})[0]
        rep = M.Report(io.StringIO())
        self.assertFalse(M.stop_runner(host, UNIT, FOLDER, rep, drained=True))
        rep = M.Report(io.StringIO())
        self.assertFalse(M.start_runner(host, UNIT, FOLDER, rep))
        self.assertIn("two copies never run", rep.lines[-1])
        self.assertFalse(any(a[:4] == ("sudo", "-n", "systemctl", "start") for a in host.ran))

    def test_G4_2_the_runner_runs_low_and_capped(self):
        gb = 1024 ** 3
        # From the survey: half the processors (never less than one), memory max 60 %, no high mark and no swap for
        # jobs: a job that outgrows the max fails at once, by name, never crawls through swap (run26).
        lim = M.slice_limits({"cores": 8, "mem": 16 * gb})
        self.assertEqual((lim["cores"], lim["CPUQuota"]), (4, "400%"))
        self.assertEqual((lim["MemoryHigh"], int(lim["MemoryMax"]), lim["MemorySwapMax"]),
                         ("infinity", int(16 * gb * 0.6), "0"))
        self.assertEqual(M.slice_limits({"cores": 1, "mem": 8 * gb})["cores"], 1)
        self.assertEqual(M.slice_limits({"cores": 3, "mem": 8 * gb})["cores"], 1)
        # The person's own choice (G7.8) within bounds: never more processors than there are, never under one.
        self.assertEqual(M.slice_limits({"cores": 4, "mem": 8 * gb}, (9, 60))["cores"], 4)
        self.assertEqual(M.slice_limits({"cores": 4, "mem": 8 * gb}, (0, 60))["cores"], 1)
        # Low priority on the runner's service, inside our slice; the slice carries the limits.
        drop = M.dropin_text()
        for line in (f"Slice={M.SLICE}", "Nice=10", "CPUWeight=50"):
            self.assertIn(line + "\n", drop)
        sl = M.slice_text(lim)
        for line in ("CPUQuota=400%", "MemoryHigh=infinity", f"MemoryMax={lim['MemoryMax']}", "MemorySwapMax=0"):
            self.assertIn(line + "\n", sl)
        # setup installs both from the survey and reads them back from systemd.
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        fed = {a[-1]: x for a, x in host.fed if a[:3] == ("sudo", "-n", "tee") or "tee" in a}
        self.assertTrue(any("CPUQuota=400%" in (x or "") for x in fed.values()), list(fed))
        self.assertTrue([a for a in host.ran if "show" in a and "CPUWeight" in a], "read back")
        # R94: kept out of swap is read from the running slice, three answers; only "kept" is said or recorded so.
        for swap_max, kind, said in (("0\n", "ok", "jobs are kept out of this computer's swap, read back"),
                                     ("max\n", "warn", "this computer did not keep jobs out of swap: the running "
                                                       "slice reads max for its swap cap, not 0"),
                                     (None, "warn", "could not read whether jobs are kept out of swap")):
            host, sha = setup_host()
            fresh_look(host)
            if swap_max is not None:
                host.files[M.SWAP_MAX_FILE] = swap_max
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual(code, M.EXIT_OK, lines)
            self.assertTrue(any(x.startswith(f"{kind} {said}") for x in lines), (swap_max, lines))
            if kind == "warn":
                self.assertFalse(any("kept out of this computer's swap" in x for x in lines), lines)
                self.assertTrue(any("instead of failing by name. Next step:" in x for x in lines), lines)
            rec = M.load_state(host)["runners"][REPO]["limits"]
            self.assertEqual(rec.get("swap_kept"), True if kind == "ok" else None)
        # doctor: kept at setup, then not held: a promise not kept, named, with the command that writes it again.
        host, sha = setup_host()
        fresh_look(host)
        host.files[M.SWAP_MAX_FILE] = "0\n"
        run_setup(setup_gh(sha), host)
        lim = M.load_state(host)["runners"][REPO]["limits"]
        host.runs[("systemctl", "show", "minions.slice")] = (0, f"CPUQuotaPerSecUSec={lim['cores']}s\nMemoryHigh=infinity\n"
                                                                f"MemoryMax={lim['MemoryMax']}\nMemorySwapMax=0\n")
        host.files[M.SWAP_MAX_FILE] = "max\n"
        code, lines = run_doctor(host, sha)
        self.assertTrue(any("did not keep jobs out of swap" in x and "though setup read it kept" in x for x in lines),
                        lines)

    def test_G11_1_a_laptop_is_named_and_its_limits_said_before_setup(self):
        page = load("look-recent-runs.jsonl")
        cases = (({"/sys/class/power_supply": ["BAT0"]}, {"/sys/class/power_supply/BAT0/type": "Battery\n"},
                  "battery"),
                 ({"/proc/acpi/button/lid": ["LID0"]}, {}, "lid"),
                 ({}, {}, None))
        for dirs, files, what in cases:
            host = linux_host(dirs=dirs)
            host.files.update(files)
            lines = look(StandIn(page), host)[1]
            tells = tells_of(lines)
            if what:
                self.assertTrue(any(x.startswith(f"warn this computer has a {what}: " + M.LAPTOP_WORDS) for x in lines),
                                (what, lines))
                self.assertIn(f"This computer has a {what}: " + M.LAPTOP_WORDS + ".", tells)
            else:
                self.assertIn("ok no battery and no lid: not a laptop", lines)
                self.assertFalse([x for x in tells if "laptop" in x])
        self.assertEqual(M.LAPTOP_WORDS, "a laptop takes jobs only while it is awake, on the network and (by default) "
                                         "on mains power; `resume <owner/repo> --on-battery-too` lets it take jobs "
                                         "on battery as well")

    def test_G11_2_on_battery_the_runners_are_held(self):
        def laptop(host, plugged):
            host.dirs["/sys/class/power_supply"] = ["AC", "BAT0"]
            host.files["/sys/class/power_supply/AC/type"] = "Mains\n"
            host.files["/sys/class/power_supply/AC/online"] = "1\n" if plugged else "0\n"
            host.files["/sys/class/power_supply/BAT0/type"] = "Battery\n"
        host, clock = g5_host()
        refuse_sudo(host)
        gh = StatusGH()
        laptop(host, plugged=False)
        self.assertEqual(M.power_source(host), "battery")
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(gh.puts, [(REPO, ["minions-held"])])
        self.assertEqual(M.load_state(host)["runners"][REPO]["held_for"], "on battery")
        self.assertTrue(any("held for on battery since" in x for x in run_cmd(M.status, REPO, host=host, gh=gh)[1]))
        # resume on battery without the person's word: stays held, and says the two ways on.
        code, lines = run_cmd(M.resume, REPO, host=host, gh=gh)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn(f"`resume {REPO} --on-battery-too`", lines[-1])
        # Plugged in: the next pass lifts it by itself.
        laptop(host, plugged=True)
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(gh.puts[-1], (REPO, ["minions", "minions-medium"]))
        self.assertNotIn("held_for", M.load_state(host)["runners"][REPO])
        # The person's word, by name: jobs on battery too; the watch then holds nothing on battery.
        laptop(host, plugged=False)
        code, lines = run_cmd(M.resume, REPO, host=host, gh=gh, on_battery="too")
        self.assertTrue(any("jobs land here on battery too, by your word" in x for x in lines), lines)
        n = len(gh.puts)
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(len(gh.puts), n)
        # A hold for another reason (a drain) is left as it is, on battery or on mains.
        data = M.load_state(host)
        data["runners"][REPO].update(held_for="drain", jobs_on_battery=False)
        M.save_state(host, data)
        gh.r[REPO]["labels"] = ["minions-held"]
        n = len(gh.puts)
        for plugged in (False, True):
            laptop(host, plugged)
            run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(len(gh.puts), n)
        # A computer with no battery is never held for it.
        host, clock = g5_host()
        self.assertEqual(M.power_source(host), "mains")

    def test_G4_8_limits_have_floors_and_too_little_is_said_before_setup(self):
        gib = 1024 ** 3
        # Never less than one processor.
        self.assertEqual(M.slice_limits({"cores": 1, "mem": 16 * gib})["CPUQuota"], "100%")
        self.assertEqual(M.slice_limits({"cores": 4, "mem": 16 * gib}, (0, 50))["CPUQuota"], "100%")
        # A 3 GB computer: 60 % is under 2 GiB, measured against the memory MAX. look says so; setup stops.
        small = {"system": "Linux", "machine": "x86_64", "cores": 2, "mem": 3 * gib, "free": 100 * gib}
        lim = M.slice_limits(small)
        self.assertLess(int(lim["MemoryMax"]), M.MEMORY_FLOOR)
        self.assertGreaterEqual(int(M.slice_limits(small, (1, 70))["MemoryMax"]), M.MEMORY_FLOOR)
        host, sha = setup_host()
        M.save_state(host, {"looks": {REPO: {"time": M.dt.datetime.now(M.dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"), "stopped": False, "survey": dict(small, serves=False)}}})
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertEqual(admin_steps(host), [])
        self.assertIn("a job would likely be killed by the limit", lines[-1])
        self.assertIn(f"`setup {REPO} --limits 1,67`", lines[-1])
        self.assertIn("less memory is left for your own work", lines[-1])
        # The way out works: --limits 1,70 sets up, and the slice has those numbers.
        out = io.StringIO()
        small_gh = setup_gh(sha, {("GET", f"repos/{REPO}/actions/runners?per_page=100"):
                                  runners_answer(labels=("minions", "minions-small"))})
        code = M.setup(REPO, host=host, gh=small_gh, out=out, limits=(1, 70))
        self.assertEqual(code, M.EXIT_OK, out.getvalue())
        sl = next(x for a, x in host.fed if a[-1] == "/etc/systemd/system/minions.slice")
        self.assertIn(f"MemoryMax={int(3 * gib * 0.7)}\n", sl)
        self.assertIn("CPUQuota=100%\n", sl)
        # Too little memory in all: no share is offered.
        self.assertIn("it cannot be a minion", M.memory_floor_line(REPO, M.slice_limits({"cores": 2, "mem": 2 * gib})))
        # look says it before setup, in plain words with the way out; and the parser takes --limits.
        self.assertIn("--limits", M.memory_floor_line(REPO, lim))
        self.assertEqual(M.parse_limits("2,70"), (2, 70))
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as e:
            M.main(["setup", REPO, "--limits", "0,95"], host=linux_host())
        self.assertEqual(e.exception.code, M.EXIT_USAGE)
        words = M.limits_in_words(M.slice_limits({"cores": 8, "mem": 16 * gib}))
        self.assertEqual(words, "jobs here get at most 4 of this computer's 8 processors and 60 % of its memory "
                                "(9.6 GB); the one way to change that is `setup <owner/repo> --limits "
                                "<processors>,<memory %>`")
        # One processor (the owner's ruling, 2026-11-23): "1 of 1", said plainly, never refused.
        words = M.limits_in_words(M.slice_limits({"cores": 1, "mem": 8 * gib}))
        self.assertTrue(words.startswith("jobs here get at most 1 of this computer's 1 processor and 60 % "), words)
        self.assertTrue(words.endswith("; this computer has one processor: while a job runs it uses all of it, at "
                                       "low priority, and everything else here will be slow"), words)
        self.assertNotIn("one processor", M.limits_in_words(M.slice_limits({"cores": 2, "mem": 8 * gib})))


class Clock:
    """A host's clock that only moves when the tool sleeps."""

    def __init__(self, host, at="2026-11-22T23:00:00Z"):
        self.t = M.parse_stamp(at)
        host.now = lambda: self.t
        host.sleep = self.sleep

    def sleep(self, s):
        self.t += M.dt.timedelta(seconds=s)


class StatusGH(StandIn):
    """GitHub for status and watch: runners (by repository), the repository's privacy, queued jobs, label PUTs."""

    def __init__(self, repos=(REPO,), private=True, status="online", busy=False, queued=(), listed=True):
        super().__init__([])
        self.private, self.listed, self.queued = private, listed, list(queued)
        self.r = {rp: {"id": 7 + i, "name": "minions-box-1", "os": "unknown", "status": status, "busy": busy,
                       "labels": ["minions", "minions-medium"]} for i, rp in enumerate(repos)}
        self.puts, self.jobs, self.annotations, self.reruns = [], {}, {}, []

    def api(self, method, path, body=None):
        self.calls.append((method, path))
        for rp, r in self.r.items():
            if method == "GET" and path == f"repos/{rp}":
                return 200, {"full_name": rp, "private": self.private, "visibility": "private" if self.private else "public"}
            if method == "GET" and path == f"repos/{rp}/actions/runners?per_page=100":
                b = r["busy"].pop(0) if isinstance(r["busy"], list) and len(r["busy"]) > 1 else (
                    r["busy"][0] if isinstance(r["busy"], list) else r["busy"])
                one = dict(r, busy=b, labels=[{"name": x, "type": "custom"} for x in r["labels"]])
                return 200, {"total_count": int(self.listed), "runners": [one] if self.listed else []}
            if method == "PUT" and path == f"repos/{rp}/actions/runners/{r['id']}/labels":
                self.puts.append((rp, list(body["labels"])))
                r["labels"] = list(body["labels"])
                return 200, {"labels": [{"name": x} for x in r["labels"]]}
            if method == "GET" and path == f"repos/{rp}/actions/runs?status=queued&per_page=50":
                return 200, {"workflow_runs": [{"id": 900, "name": "CI"}] if self.queued else []}
            if method == "GET" and path == f"repos/{rp}/actions/runs/900/jobs?per_page=100":
                return 200, {"jobs": self.queued}
            m = re.fullmatch(rf"repos/{re.escape(rp)}/actions/runs/(\d+)/jobs\?filter=all&per_page=100", path)
            if method == "GET" and m:
                return 200, {"jobs": self.jobs.get(int(m.group(1)), [])}
            m = re.fullmatch(rf"repos/{re.escape(rp)}/check-runs/(\d+)/annotations", path)
            if method == "GET" and m:
                return 200, self.annotations.get(int(m.group(1)), [])
            if method == "DELETE" and path == f"repos/{rp}/actions/runners/{r['id']}":
                b = r["busy"][0] if isinstance(r["busy"], list) else r["busy"]
                if b:  # seen on GitHub: a runner running a job cannot be removed
                    return 422, {"message": "Runner is currently running a job and cannot be deleted."}
                self.listed = False
                return 204, None
            m = re.fullmatch(rf"repos/{re.escape(rp)}/actions/jobs/(\d+)/rerun", path)
            if method == "POST" and m:
                self.reruns.append(int(m.group(1)))
                return 201, {}
            m = re.fullmatch(rf"repos/{re.escape(rp)}/actions/runs/(\d+)", path)
            if method == "GET" and m and getattr(self, "workflow_path", ".github/workflows/ci.yml"):
                return 200, {"id": int(m.group(1)), "path": getattr(self, "workflow_path", ".github/workflows/ci.yml")}
        return 404, {"message": "Not Found"}


def dt_hms(t):
    return M.hms(M.dt.datetime.fromtimestamp(t, M.dt.timezone.utc))


def g5_host(runs=None, repos=(REPO,), worker=False):
    """A computer with our runner set up for each repository, the service active, and a survey."""
    r = {("systemctl", "is-active"): (0, "active\n"),
         ("pgrep", "-u", "minions-runner", "-f"): (0, "4321\n") if worker else (1, ""),
         ("sudo", "-n", "systemctl"): (0, ""), ("sudo", "-n", "-u", "minions-runner", "--", "kill"): (0, "")}
    r.update(runs or {})
    host = linux_host(runs=r)
    clock = Clock(host)
    survey = {"system": "Linux", "machine": "x86_64", "os": "Example Linux 1", "cores": 8, "mem": 16 * 1024 ** 3}
    data = {"looks": {}, "runners": {}, "watch": {"time": "2026-11-22T22:58:00Z", "every_s": 300}}
    for rp in repos:
        o, n = rp.split("/")
        data["looks"][rp] = {"time": "2026-11-22T22:30:00Z", "stopped": False, "survey": survey}
        data["runners"][rp] = {"name": "minions-box-1", "folder": f"/home/minions-runner/runners/{o}-{n}",
                               "unit": f"actions.runner.{o}-{n}.minions-box-1.service",
                               "labels": ["minions", "minions-medium"]}
    M.save_state(host, data)
    return host, clock


def run_cmd(fn, *a, **kw):
    out = io.StringIO()
    code = fn(*a, out=out, **kw)
    return code, out.getvalue().splitlines()


def printed_system_commands():
    """G8.16: every backticked system command in a string the tool can print (docstrings left out), its f-string
    fields read as {}."""
    import ast
    tree = ast.parse(open(os.path.join(ROOT, "scripts", "minions.py"), encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body and \
                isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            docs.add(id(node.body[0].value))
    texts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            texts.append("".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            texts.append(node.value)
    found = set()
    for t in texts:
        for m in re.finditer(r"`((?:sudo|gh|chmod|journalctl|python3) [^`]*)`", t):
            found.add(re.sub(r"\{[^`]*?\}", "{}", m.group(1)))
    return found


class G9Version(unittest.TestCase):
    def test_G8_16_every_printed_command_is_registered_with_where_it_was_run_as_printed(self):
        # The family's rule 7 (2026-11-23): a command a tool prints for a person has been run exactly as printed on
        # that kind of system, its prompts answered by hand; where none has, the line says what is missing and
        # where to read (as the GitHub CLI's install line now does off Debian and Ubuntu).
        found = printed_system_commands() | set(M.INSTALL_GH.values())
        self.assertEqual(found - set(M.PRINTED_COMMANDS), set(), "printed, not in PRINTED_COMMANDS")
        self.assertEqual(set(M.PRINTED_COMMANDS) - found, set(), "in PRINTED_COMMANDS, no longer printed")
        self.assertNotIn("brew install gh", found)
        self.assertNotIn("gh auth refresh -s workflow", found)  # the owner's ruling (11-23): what is missing + where
        if M.VERSION != "0.0.0":  # a release: every one was run as printed somewhere real
            self.assertEqual([c for c, where in M.PRINTED_COMMANDS.items() if not where], [],
                             "printed for the person and never run as printed")

    def test_G9_9_the_evals_are_the_cold_start_cases_and_each_is_whole(self):
        # Rule 13 (row R103): the skill's evals, in the skill-creator layout, are the cold-start runs written as
        # cases: the sentence, what must be said, what must not happen.
        skill_dir = os.path.join(ROOT, "skills", "repo-minions")
        with open(os.path.join(skill_dir, "evals", "evals.json"), encoding="utf-8") as f:
            ev = json.load(f)
        with open(os.path.join(skill_dir, "SKILL.md"), encoding="utf-8") as f:
            name = re.search(r"^name: *(\S+)", f.read(), re.M).group(1)
        self.assertEqual(ev["skill_name"], name)
        ids = [e["id"] for e in ev["evals"]]
        self.assertEqual(len(ids), len(set(ids)))
        for e in ev["evals"]:
            self.assertIsInstance(e["id"], int)
            self.assertTrue(e["prompt"].strip() and e["expected_output"].strip(), e["id"])
            self.assertIsInstance(e.get("files"), list, e["id"])
            self.assertTrue(e["expectations"] and all(isinstance(x, str) and x.strip() for x in e["expectations"]))
            self.assertTrue(any(x.startswith("Must not:") for x in e["expectations"]), f"case {e['id']}: no must-not")
        # Every line look can tell the person is named by a case, by its opening words.
        said = " ".join(x for e in ev["evals"] for x in e["expectations"])
        host = linux_host(files={"/sys/class/power_supply/BAT0/type": "Battery\n"},
                          dirs={"/sys/class/power_supply": ["BAT0"]})
        for t in tells_of(look(StandIn(load("look-recent-runs.jsonl")), host)[1]):
            opening = " ".join(t.replace(REPO, "OWNER/NAME").split()[:4])
            self.assertIn(opening, said, f"no eval case names the tell line: {t[:80]}")

    def test_G9_8_one_version_is_named_everywhere(self):
        # The tool, the manifest (where it exists: on a release branch) and the changelog's top entry (once it is a
        # version) name one version; a half-bumped branch fails here, before a tag.
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        self.assertRegex(M.VERSION, r"^\d+\.\d+\.\d+$")
        manifest = os.path.join(root, ".claude-plugin", "plugin.json")
        if os.path.exists(manifest):
            with open(manifest, encoding="utf-8") as f:
                self.assertEqual(json.load(f).get("version"), M.VERSION, "the manifest and the tool")
        with open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8") as f:
            top = next(x for x in f if x.startswith("## "))[3:].strip()
        if top != "Unreleased":
            self.assertEqual(top.split()[0], M.VERSION, "the changelog's top entry and the tool")
        # The owner's ruling (2026-11-23, rule 16 as corrected): a clone of this repository named in the README or
        # the skill names its branch, never main; once a version is released it is that version's tag (or, before
        # the tag, its release branch), so the line cannot drift at the next release.
        for rel in ("README.md", os.path.join("skills", "repo-minions", "SKILL.md")):
            with open(os.path.join(root, rel), encoding="utf-8") as f:
                clones = re.findall(r"git clone ([^`]*?)https://github\.com/TheWindstorm/windstorm-repo-minions", f.read())
            for opts in clones:
                if M.VERSION == "0.0.0":
                    continue  # before the first release: main's README has no install lines (rule 15)
                self.assertIn(opts.strip(), (f"--branch v{M.VERSION}", f"--branch release-{M.VERSION}"),
                              f"{rel}: a clone line names this version's tag or release branch, never main")
        # Rule 10 (the family's one version): the README's own status heading is one of the places. A README that
        # carries install lines says, in its status heading, the version they install; so 0.1.1 cannot ship with a
        # README that still says 0.1.0, or "not released" (found 2026-11-24 in the release README before the tag).
        with open(os.path.join(root, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        if M.VERSION != "0.0.0" and re.search(r"git clone [^`]*https://github\.com/TheWindstorm/windstorm-repo-minions", readme):
            status = re.search(r"^## Status: (.*)$", readme, re.M)
            self.assertIsNotNone(status, "README: a README with install lines has a status heading")
            self.assertRegex(status.group(1), rf"^{re.escape(M.VERSION)}(?![0-9.])",
                             "README: the status heading names the version its install lines install")


class G5(unittest.TestCase):
    def test_G5_20_no_line_says_the_hook_holds_without_its_caveat(self):
        # R133: a job can blank the start hook in the runner's own .env (seen on a stock VM, 2026-11-24), so no line,
        # shown or written, says the hook refuses a job without saying "unless a job that already ran here switched
        # that hook off"; the README says the watch is the gate and names the window.
        src = open(os.path.join(ROOT, "scripts", "minions.py")).read().splitlines()
        for i, line in enumerate(src):
            if re.search(r"hook here refuses", line) and not line.lstrip().startswith("#"):
                self.assertIn("HOOK_CAVEAT", " ".join(src[i:i + 3]), f"scripts/minions.py:{i + 1}")
        docs = [os.path.join(ROOT, "docs", "root-steps.md")]
        readme = " ".join(open(os.path.join(ROOT, "README.md")).read().split())
        if "nothing can be installed yet" not in readme:  # the release branch's README (main's has no install lines)
            docs.append(os.path.join(ROOT, "README.md"))
            self.assertIn("nothing a job can write is ever run, sourced or followed by root", readme)
        for p in docs:
            text = " ".join(open(p).read().split())
            for words in ("switched the hook off", "up to five minutes, longer while GitHub cannot be reached",
                          "nothing on this computer refuses a job, and a job already running finishes"):
                self.assertIn(words, text, p)

    def test_G5_17_status_says_when_github_last_handed_this_runner_a_job(self):
        # Seen once, on one new private repository (2026-11-23, row R105): a 10-minute schedule did not fire once in
        # an hour, with the runner connected and waiting. A job GitHub never hands over is GitHub's side.
        host, clock = g5_host()
        jobs = M.records_dir_of(f"/home/minions-runner/runners/example-org-example-repo") + "/jobs.log"
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
        line = next(x for x in lines if "GitHub has not handed this runner a job" in x)
        self.assertTrue(line.startswith("ok ") and "on GitHub's side" in line and "connected and waiting" in line)
        t = int(host.now().timestamp()) - 25 * 60
        host.files[jobs] = f"start {t - 600} 11 1 0 build\nend {t - 500} 11 1 0 build\nstart {t} 12 1 0 soak\n"
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
        want = dt_hms(t)
        self.assertIn(f"ok the last job GitHub handed this runner started at {want}, 25 min ago ('soak' of {REPO})",
                      lines)
        # Not read is a third answer (rule 6), never "not handed a job yet".
        host.files.pop(jobs)
        host.there = lambda path: None
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
        self.assertTrue(any(x.startswith("warn when GitHub last handed a job") and "could not be read" in x
                            for x in lines))
        self.assertFalse(any("has not handed" in x for x in lines))

    def test_G5_1_status_reads_both_sides_and_the_queue(self):
        host, clock = g5_host()
        gh = StatusGH(queued=[{"id": 1, "name": "check", "status": "queued", "labels": ["minions"],
                               "created_at": "2026-11-22T22:56:00Z"},
                              {"id": 2, "name": "macos", "status": "queued", "labels": ["macos-latest"],
                               "created_at": "2026-11-22T22:00:00Z"}])
        code, lines = run_cmd(M.status, REPO, host=host, gh=gh)
        self.assertEqual(code, M.EXIT_OK, lines)
        line = next(x for x in lines if x.startswith("ok minions-box-1 for"))
        self.assertIn("GitHub says online, not busy (read 23:00:00Z)", line)
        self.assertIn("this computer: service active, no job process (read 23:00:00Z)", line)
        # The OS from this computer's own survey, though GitHub's list says `unknown` (C7).
        self.assertIn("Example Linux 1 x86_64 (from this computer's own survey)", line)
        self.assertNotIn("unknown", line)
        # The queue: only jobs asking for our labels count; the oldest's age.
        self.assertIn(f"ok queue for {REPO}: 1 job(s) waiting for our runner, the oldest for 4 min", lines)
        gh = StatusGH()
        self.assertIn(f"ok queue for {REPO}: no job waiting for our runner", run_cmd(M.status, REPO, host=host, gh=gh)[1])

    def test_G5_2_idle_only_when_both_sides_agree(self):
        cases = [(False, False, "idle"), (True, False, "the two sides disagree"),
                 (False, True, "the two sides disagree"), (True, True, "busy")]
        for gh_busy, worker, word in cases:
            host, _ = g5_host(worker=worker)
            code, lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(busy=gh_busy))
            kind = "ok" if word in ("idle", "busy") else "warn"  # the first word is the worst true thing
            line = next(x for x in lines if x.startswith(f"{kind} minions-box-1 for"))
            self.assertIn(f"for {REPO}: {word}.", line, (gh_busy, worker))
            self.assertIn("GitHub says online, " + ("busy" if gh_busy else "not busy"), line)
            self.assertIn("a job process runs (4321)" if worker else "no job process", line)
        # GitHub offline: never "idle", even with no job process here.
        host, _ = g5_host()
        line = next(x for x in run_cmd(M.status, REPO, host=host, gh=StatusGH(status="offline"))[1]
                    if x.startswith("warn minions-box-1 for"))
        self.assertIn(": not ready.", line)
        # R84 (seen with the runner's listener paused during a job): GitHub says offline and busy while a job process
        # runs here: the two sides disagree, as a warn, never "ok ... busy".
        host, _ = g5_host(worker=True)
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(status="offline", busy=True))[1]
        line = next(x for x in lines if "minions-box-1 for" in x)
        self.assertTrue(line.startswith(f"warn minions-box-1 for {REPO}: the two sides disagree."), line)
        self.assertIn("GitHub says offline, busy", line)
        self.assertIn("a job process runs (4321)", line)

    def test_G5_1_status_never_begins_with_ok_while_a_job_waits_on_a_runner_that_cannot_take_it(self):
        # P2, 2026-11-23: the runner offline, a job waiting, and status began `ok minions-stock-vm ...: not ready`.
        job = {"id": 1, "name": "check", "status": "queued", "labels": ["minions"],
               "created_at": (M.dt.datetime.now(M.dt.timezone.utc) - M.dt.timedelta(minutes=2)).strftime(
                   "%Y-%m-%dT%H:%M:%SZ")}
        host, _ = g5_host()
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(status="offline", queued=[job]))[1]
        self.assertTrue(lines[0].startswith(f"warn queue for {REPO}: 1 job(s) waiting for our runner"), lines)
        self.assertIn(f"behind our runner being offline or asleep. Start this computer's runner: `resume {REPO}` (it "
                      "asks for the admin password once", lines[0])
        self.assertFalse([x for x in lines if x.startswith(f"ok queue for {REPO}")])
        self.assertTrue(any(x.startswith("warn minions-box-1 for") and ": not ready." in x for x in lines))
        # Held: the queue line says what holds it and the way on; nothing before it says `ok`.
        host, _ = g5_host()
        data = M.load_state(host)
        data["runners"][REPO].update(held_for="low disk", held_at="2026-11-23T05:31:41Z")
        M.save_state(host, data)
        gh = StatusGH(queued=[job])
        gh.r[REPO]["labels"] = ["minions-held"]
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertTrue(lines[0].startswith("warn queue for"), lines)
        self.assertIn("behind our hold (low disk). The hold lifts by itself", lines[0])
        # Online and idle with a job just queued: plain `ok`, nothing to warn of.
        host, _ = g5_host()
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(queued=[job]))[1]
        self.assertTrue(lines[0].startswith("ok "), lines)

    def test_G5_3_the_watch_runs_and_its_silence_is_noticed(self):
        # The units: a system timer every 5 minutes; its service runs as the person, the tool's root-owned copy.
        svc, tmr = M.watch_units("person", M.WATCH_EVERY_S)
        self.assertIn("User=person\n", svc)
        self.assertIn(f"ExecStart=/usr/bin/python3 -I {M.TOOL_COPY} watch\n", svc)
        self.assertIn("OnUnitActiveSec=300s\n", tmr)
        self.assertIn("WantedBy=timers.target\n", tmr)
        # setup installs it and reads it back.
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        self.assertIn(("sudo", "-n", "systemctl", "enable", "--now", "minions-watch.timer", "--no-pager"), host.ran)
        # One pass: a heartbeat in minions.json and one line in the journal.
        host, clock = g5_host()
        self.assertEqual(run_cmd(M.watch, host=host, gh=StatusGH())[0], M.EXIT_OK)
        self.assertEqual(M.load_state(host)["watch"]["time"], "2026-11-22T23:00:00Z")
        with open(os.path.join(host.state_dir(), "journal.log")) as f:
            last = f.read().splitlines()[-1]
        self.assertEqual(last, f"2026-11-22T23:00:00Z watch {REPO}: online, not busy")
        # Within three intervals: ok. Older: "the watch is not running", as a warn.
        clock.sleep(14 * 60)
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
        self.assertIn("ok the watch ran 14 min ago, at 23:00:00Z (every 5 min)", lines)
        clock.sleep(2 * 60)
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
        self.assertIn("warn the watch is not running: its last heartbeat was 16 min ago, at 23:00:00Z (it runs every "
                      "5 min)", lines)
        # R83: the same 16 minutes, 12 of them asleep (seen on a stock machine): the timer counts only waking time,
        # so the watch is not called stopped, and no stop line comes first.
        host.slept = 12 * 60
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
        self.assertIn("ok the watch ran 16 min ago, at 23:00:00Z; this computer slept 12 min since, and the watch's "
                      "timer counts only waking time: its next pass comes within 5 min of waking time", lines)
        self.assertFalse([x for x in lines if "the watch is not running" in x], lines)
        # Asleep only 30 s of the 16 minutes: 15.5 minutes awake with no pass is still a stopped watch.
        host.slept = 30
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
        self.assertTrue(lines[0].startswith("stop the watch is not running"), lines)

    def test_G5_4_a_repository_no_longer_private_gets_no_job_here_and_our_runner_leaves_it(self):
        # Plan 2.0: the watch has no admin rights (G9.7). A job still runs: it finishes (GitHub refuses to remove a
        # busy runner, 422, seen); the record says `public` at once (the hook refuses every later job, G5.15), our
        # labels come off, and once idle our runner is removed on GitHub's side with the person's own rights.
        host, clock = g5_host(worker=True)
        refuse_sudo(host)
        gh = StatusGH(private=False, busy=True)
        code, lines = run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(gh.puts, [(REPO, ["minions-held"])])
        self.assertEqual(host.files[M.seen_path(FOLDER)].strip(), "public")
        self.assertFalse([a for a in host.ran if a[:1] == ("sudo",)], "the watch asks for no admin rights")
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn(f"the job hook here refuses each job of it before its first step {M.HOOK_CAVEAT}; our "
                      "runner is taken off the "
                      "repository on GitHub's side as soon as no job runs on it (each pass tries again); a job already "
                      "running finishes", lines[-1])
        self.assertNotIn(("DELETE", f"repos/{REPO}/actions/runners/7"), gh.calls, "never while a job runs")
        self.assertFalse(M.load_state(host)["runners"][REPO]["public_stop"]["removed"])
        # The next pass, the job done: removed on GitHub; said; the record stays `public`.
        gh.r[REPO]["busy"] = False
        run_cmd(M.watch, host=host, gh=gh)
        self.assertIn(("DELETE", f"repos/{REPO}/actions/runners/7"), gh.calls)
        self.assertTrue(M.load_state(host)["runners"][REPO]["public_stop"]["removed"])
        self.assertEqual(host.files[M.seen_path(FOLDER)].strip(), "public")
        self.assertFalse([a for a in host.ran if a[:1] == ("sudo",)], "the watch asks for no admin rights")
        n = len(gh.calls)
        run_cmd(M.watch, host=host, gh=gh)
        self.assertNotIn(("DELETE", f"repos/{REPO}/actions/runners/7"), gh.calls[n:], "removed once")
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(private=False, listed=False))[1]
        self.assertIn("our runner is off the repository on GitHub's side, so GitHub hands this computer no job of it"
                      "; its service here is active (a removed runner's listener can wait idle", lines[0])
        self.assertNotIn("stopped its own service", lines[0])  # it may not have (seen): status reads the service
        host.runs[("systemctl", "is-active")] = (3, "inactive\n")
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(private=False, listed=False))[1]
        self.assertIn("; its service here is inactive; a job already running", lines[0])
        host.runs[("systemctl", "is-active")] = (0, "active\n")
        # Coming back after the removal: the person's word, GitHub says private, then `setup` registers again.
        host._answer = host.answer_with_sudo
        code, lines = run_cmd(M.resume, REPO, host=host, gh=StatusGH(private=True, listed=False), private_again=True)
        self.assertEqual(code, M.EXIT_NEED, lines)
        self.assertIn(f"need you: run `setup {REPO}`: it registers the runner again in place", lines[-1])
        st = M.load_state(host)["runners"][REPO]
        self.assertNotIn("public_stop", st)
        self.assertTrue(st.get("gone_on_github"))  # setup's G5.12 path registers it again (seen on a real machine)
        self.assertTrue(host.files[M.seen_path(FOLDER)].strip().isdigit())
        # GitHub not reachable for the labels: said, and the next pass tries again; the record stays `public`.
        host4, _ = g5_host()
        refuse_sudo(host4)

        class RunnersUnread(StatusGH):  # the repository read public, then GitHub not reached for the runners
            def api(self, method, path, body=None):
                return (0, None) if "/actions/runners" in path else super().api(method, path, body)
        run_cmd(M.watch, host=host4, gh=RunnersUnread(private=False))
        self.assertFalse(M.load_state(host4)["runners"][REPO]["public_stop"]["labels_off"])
        self.assertFalse(M.load_state(host4)["runners"][REPO]["public_stop"]["removed"])
        gh4 = StatusGH(private=False)
        run_cmd(M.watch, host=host4, gh=gh4)
        self.assertEqual(gh4.puts, [(REPO, ["minions-held"])])
        self.assertTrue(M.load_state(host4)["runners"][REPO]["public_stop"]["labels_off"])
        self.assertTrue(M.load_state(host4)["runners"][REPO]["public_stop"]["removed"])
        self.assertEqual(host4.files[M.seen_path(FOLDER)].strip(), "public")
        # status says why, first.
        host._answer = host.answer_with_sudo  # status and resume run in the person's terminal
        host2 = host
        host2 = g5_host(worker=True)[0]
        refuse_sudo(host2)
        run_cmd(M.watch, host=host2, gh=StatusGH(private=False, busy=True))
        host2._answer = host2.answer_with_sudo
        host = host2
        lines = run_cmd(M.status, REPO, host=host2, gh=StatusGH(private=False, busy=True))[1]
        self.assertTrue(lines[0].startswith(f"stop {REPO} is no longer private (read "), lines)
        self.assertIn("Anyone's fork can ask a public repository's runner for a job", lines[0])
        # resume: refused without the person's word, refused while GitHub says public, works once private again.
        code, lines = run_cmd(M.resume, REPO, host=host, gh=StatusGH(private=False))
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("--private-again", lines[-1])
        code, lines = run_cmd(M.resume, REPO, host=host, gh=StatusGH(private=False), private_again=True)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("this computer keeps refusing its jobs", lines[-1])
        self.assertTrue(M.load_state(host)["runners"][REPO].get("public_stop"))
        host3 = host
        host3.runs[("systemctl", "is-active")] = (3, "inactive\n")
        host3.runs[("pgrep", "-u", "minions-runner", "-f")] = (1, "")
        gh = StatusGH(private=True)
        gh.r[REPO]["labels"] = ["minions-held"]
        code, lines = run_cmd(M.resume, REPO, host=host3, gh=gh, private_again=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn(("sudo", "-n", "systemctl", "enable", "actions.runner.example-org-example-repo.minions-box-1.service",
                       "--no-pager"), host3.ran)
        self.assertEqual(gh.puts[-1], (REPO, ["minions", "minions-medium"]))
        self.assertNotIn("public_stop", M.load_state(host3)["runners"][REPO])
        self.assertTrue(host3.files[M.seen_path(FOLDER)].strip().isdigit())  # resume read it private: a fresh time

    def test_G5_4_what_github_took_is_read_back_never_assumed(self):
        # Seen: GitHub answered the label change with an error (HTTP 502) and had taken it. The hold reads back.
        class ErrorButTaken(StatusGH):
            def api(self, method, path, body=None):
                st, d = super().api(method, path, body)
                return (502, {"message": "Server Error"}) if method == "PUT" else (st, d)
        host, clock = g5_host(worker=True)
        refuse_sudo(host)
        gh = ErrorButTaken(private=False, busy=True)
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(gh.r[REPO]["labels"], ["minions-held"])
        self.assertTrue(M.load_state(host)["runners"][REPO]["public_stop"]["labels_off"])
        # status reads GitHub now, not the watch's record: the record says "not taken off", GitHub lists it held.
        data = M.load_state(host)
        data["runners"][REPO]["public_stop"]["labels_off"] = False
        M.save_state(host, data)
        host._answer = host.answer_with_sudo
        gh2 = StatusGH(private=False, busy=True)
        gh2.r[REPO]["labels"] = ["minions-held"]
        lines = run_cmd(M.status, REPO, host=host, gh=gh2)[1]
        self.assertNotIn("could not be taken off", lines[0])
        gh3 = StatusGH(private=False, busy=True)  # GitHub lists our labels on it: said
        self.assertIn("our labels could not be taken off yet", run_cmd(M.status, REPO, host=host, gh=gh3)[1][0])
        # Idle and still listed: the removal is owed, said as such (seen when the watch could not reach GitHub).
        line = run_cmd(M.status, REPO, host=host, gh=StatusGH(private=False))[1][0]
        self.assertIn("our runner is still on the repository on GitHub's side, with no job on it: the watch could not "
                      "take it off yet (each pass tries again)", line)
        # GitHub not reached: the watch's record, and said so.
        class Unread(StatusGH):
            def api(self, method, path, body=None):
                return (0, None) if "/actions/runners" in path else super().api(method, path, body)
        self.assertIn("(GitHub not read now (HTTP 0): from the watch's pass)",
                      run_cmd(M.status, REPO, host=host, gh=Unread(private=False))[1][0])

    def test_G5_6_drain_ends_with_online_and_idle_read(self):
        host, clock = g5_host()
        code, lines = run_cmd(M.drain, REPO, host=host, gh=StatusGH())
        self.assertEqual(code, M.EXIT_OK)
        self.assertIn("ok drained: online and idle: minions-box-1 is held, GitHub says online and not busy, and no job "
                      "process runs here", lines)
        # Offline on GitHub's side: not "online and idle"; said plainly.
        host, clock = g5_host()
        lines = run_cmd(M.drain, REPO, host=host, gh=StatusGH(status="offline"))[1]
        self.assertFalse(any("online and idle" in x for x in lines), lines)
        self.assertTrue(any(x.startswith("warn drained: no job process runs here, but GitHub says") for x in lines))
        # Still running at the deadline: says what is still running, and the next command.
        host, clock = g5_host(worker=True)
        code, lines = run_cmd(M.drain, REPO, host=host, gh=StatusGH(busy=True), wait_s=60)
        self.assertEqual(code, M.EXIT_WAIT)
        self.assertIn("GitHub says busy, a job process runs here (4321)", lines[-1])

    def test_G5_11_drain_before_a_planned_restart_lifts_after_the_boot(self):
        other = "example-org/other-repo"
        host, clock = g5_host(repos=(REPO, other))
        host.files["/proc/sys/kernel/random/boot_id"] = "boot-one\n"
        gh = StatusGH(repos=(REPO, other))
        code, lines = run_cmd(M.drain, None, host=host, gh=gh, restart=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertEqual(sorted(gh.puts), [(REPO, ["minions-held"]), (other, ["minions-held"])])
        self.assertTrue(lines[-1].startswith("ok online and idle: safe to restart now"), lines)
        self.assertEqual(M.load_state(host)["restart_hold"]["boot_id"], "boot-one")
        # The same boot: the watch keeps the hold.
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(len(gh.puts), 2)
        # After the restart (a new boot id) and GitHub reading the runners online: lifted by itself.
        host.files["/proc/sys/kernel/random/boot_id"] = "boot-two\n"
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(sorted(gh.puts[2:]), [(REPO, ["minions", "minions-medium"]), (other, ["minions", "minions-medium"])])
        self.assertNotIn("restart_hold", M.load_state(host))
        # A job still running: no "safe to restart", and what still runs is named.
        host, clock = g5_host(worker=True)
        code, lines = run_cmd(M.drain, None, host=host, gh=StatusGH(busy=True), restart=True, wait_s=60)
        self.assertEqual(code, M.EXIT_WAIT)
        self.assertFalse(any("safe to restart" in x for x in lines))
        self.assertIn("Next: `drain --restart` again", lines[-1])

    def test_G5_24_a_restart_hold_ends_when_the_person_resumes_or_drains_by_name(self):
        # R154 (found 2026-11-24 reading rule 24's lines): `drain --restart`, then `resume` without restarting, left the
        # restart hold kept; `status` still said "held for a restart ... lifts by itself", and a later `drain REPO`
        # was lifted by the watch after any reboot, so jobs landed on a runner the person had held.
        host, clock = g5_host()
        host.files["/proc/sys/kernel/random/boot_id"] = "boot-one\n"
        gh = StatusGH()
        self.assertEqual(run_cmd(M.drain, None, host=host, gh=gh, restart=True)[0], M.EXIT_OK)
        self.assertEqual(run_cmd(M.resume, REPO, host=host, gh=gh)[0], M.EXIT_OK)
        self.assertNotIn("restart_hold", M.load_state(host))
        self.assertFalse([x for x in run_cmd(M.status, REPO, host=host, gh=gh)[1] if "held for a restart" in x])
        self.assertEqual(run_cmd(M.drain, REPO, host=host, gh=gh)[0], M.EXIT_OK)
        host.files["/proc/sys/kernel/random/boot_id"] = "boot-two\n"  # a restart, any time later
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(gh.puts[-1], (REPO, ["minions-held"]))  # the person's hold stays
        self.assertEqual(M.load_state(host)["runners"][REPO]["held_for"], "drain")
        # `drain REPO` straight after `drain --restart`: the hold is the person's now, said as a drain, kept by a restart
        host, clock = g5_host()
        host.files["/proc/sys/kernel/random/boot_id"] = "boot-one\n"
        gh = StatusGH()
        run_cmd(M.drain, None, host=host, gh=gh, restart=True)
        run_cmd(M.drain, REPO, host=host, gh=gh)
        self.assertNotIn("restart_hold", M.load_state(host))
        host.files["/proc/sys/kernel/random/boot_id"] = "boot-two\n"
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(gh.puts[-1], (REPO, ["minions-held"]))
        self.assertTrue([x for x in run_cmd(M.status, REPO, host=host, gh=gh)[1] if "held for drain since" in x])
        with open(os.path.join(host.state_dir(), "journal.log")) as f:
            self.assertIn("restart hold ended", f.read())
        # The reverse order (the maintainer's reading): `drain A` first, then `drain --restart`, then the restart. The
        # restart's lift undoes only what the restart itself held; A's own hold is said as already held and stays.
        other = "example-org/other-repo"
        host, clock = g5_host(repos=(REPO, other))
        host.files["/proc/sys/kernel/random/boot_id"] = "boot-one\n"
        gh = StatusGH(repos=(REPO, other))
        run_cmd(M.drain, REPO, host=host, gh=gh)
        code, lines = run_cmd(M.drain, None, host=host, gh=gh, restart=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn(f"ok minions-box-1 for {REPO} was already held (for drain), read from GitHub now: it stays held "
                      f"after the restart; `resume {REPO}` lifts it", lines)
        self.assertEqual(M.load_state(host)["restart_hold"]["repos"], [other])
        self.assertIn(f"The hold on {other} lifts by itself", lines[-1])
        self.assertTrue([x for x in run_cmd(M.status, None, host=host, gh=gh)[1]
                         if f"held for a restart since" in x and f"({other})" in x])
        host.files["/proc/sys/kernel/random/boot_id"] = "boot-two\n"
        run_cmd(M.watch, host=host, gh=gh)
        self.assertIn((other, ["minions", "minions-medium"]), gh.puts)  # the restart's own hold lifted
        self.assertEqual([x for x in gh.puts if x[0] == REPO], [(REPO, ["minions-held"])])  # the person's stays
        # Every runner already held: no restart hold is kept, and the line says they stay held.
        host, clock = g5_host()
        host.files["/proc/sys/kernel/random/boot_id"] = "boot-one\n"
        gh = StatusGH()
        run_cmd(M.drain, REPO, host=host, gh=gh)
        code, lines = run_cmd(M.drain, None, host=host, gh=gh, restart=True)
        self.assertNotIn("restart_hold", M.load_state(host))
        self.assertTrue(lines[-1].endswith("Every runner here was already held before this, and stays held after the "
                                           "restart until you `resume` it"), lines[-1])
        # A `drain --restart` that had to wait, run again as its line says: the hold it set is still its own.
        host, clock = g5_host(worker=True)
        host.files["/proc/sys/kernel/random/boot_id"] = "boot-one\n"
        gh = StatusGH(busy=True)
        self.assertEqual(run_cmd(M.drain, None, host=host, gh=gh, restart=True, wait_s=60)[0], M.EXIT_WAIT)
        host.runs[("pgrep", "-u", "minions-runner", "-f")] = (1, "")
        gh.r[REPO]["busy"] = False
        self.assertEqual(run_cmd(M.drain, None, host=host, gh=gh, restart=True)[0], M.EXIT_OK)
        self.assertEqual(M.load_state(host)["restart_hold"]["repos"], [REPO])
        # The third road: `drain --restart`, then `uninstall REPO` (then `setup REPO` again, G6.22) before any restart:
        # the removal ends that repository's restart hold, so status never says a live runner is held for a restart.
        gh, world, host = remove_setup(moved=False)
        d = M.load_state(host)
        d["restart_hold"] = {"time": "2026-11-22T23:00:00Z", "boot_id": "boot-one", "repos": [REPO]}
        M.save_state(host, d)
        self.assertEqual(run_uninstall(gh, host)[0], M.EXIT_OK)
        self.assertNotIn("restart_hold", M.load_state(host))

    def test_G4_3_below_the_floor_the_watch_holds_and_lifts_by_itself(self):
        # P4 (seen 2026-11-23: 4 GB free and the watch said only "online, not busy"): held before the disk fills.
        host, clock = g5_host()
        refuse_sudo(host)  # the watch has no admin rights; the hold is GitHub's labels, taken with the person's login
        gh = StatusGH()
        host.free = (4 * 1024 ** 3, 30 * 1024 ** 3)
        self.assertEqual(run_cmd(M.watch, host=host, gh=gh)[0], M.EXIT_OK)
        self.assertEqual(gh.puts, [(REPO, ["minions-held"])])
        mine = M.load_state(host)["runners"][REPO]
        self.assertEqual(mine["held_for"], "low disk")
        with open(os.path.join(host.state_dir(), "journal.log")) as f:
            j = f.read()
        self.assertIn("held: 4.0 GB free, under the 5.0 GB floor", j)
        self.assertIn("held for low disk since", run_cmd(M.status, REPO, host=host, gh=gh)[1][0])
        # Just above the floor: still held (no flapping); a pass takes no second hold.
        host.free = (int(5.5 * 1024 ** 3), 30 * 1024 ** 3)
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(len(gh.puts), 1)
        # Space back: lifted by itself, the labels as they were.
        host.free = (20 * 1024 ** 3, 30 * 1024 ** 3)
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(gh.puts[-1], (REPO, ["minions", "minions-medium"]))
        self.assertNotIn("held_for", M.load_state(host)["runners"][REPO])
        # A hold for another reason (a drain) is never lifted by space coming back.
        data = M.load_state(host)
        data["runners"][REPO].update(held_for="drain", labels_before_hold=["minions", "minions-medium"])
        M.save_state(host, data)
        gh.r[REPO]["labels"] = ["minions-held"]
        n = len(gh.puts)
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(len(gh.puts), n)
        self.assertFalse([a for a in host.ran if a[:1] == ("sudo",)], "the watch asks for no admin rights")

    def test_G5_10_a_change_in_the_cost_rule_is_told(self):
        host, clock = g5_host()
        refuse_sudo(host)
        gh = StatusGH()
        run_cmd(M.watch, host=host, gh=gh)  # no earlier read: this one is kept, nothing is called a change
        first = M.load_state(host)["cost"]
        self.assertTrue(any(x.startswith("Self-hosted runners") for x in first["items"]))
        self.assertNotIn("cost_change", M.load_state(host))
        # Within the week: no read at all, whatever the page says now.
        host.page = host.page.replace("Self-hosted runners", "Self-hosted runner minutes")
        clock.sleep(6 * 24 * 3600)
        run_cmd(M.watch, host=host, gh=gh)
        self.assertNotIn("cost_change", M.load_state(host))
        # A week on, the page reads differently: kept with both versions and dates, told once at the next status.
        clock.sleep(2 * 24 * 3600)
        run_cmd(M.watch, host=host, gh=gh)
        c = M.load_state(host)["cost_change"]
        self.assertEqual((c["old"], c["old_time"]), (first["items"], first["time"]))
        self.assertTrue(any(x.startswith("Self-hosted runner minutes") for x in c["new"]))
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertTrue(lines[0].startswith("warn GitHub's billing page changed what it says about self-hosted "
                                            f"runners: read {first['time']}, it said"), lines[0])
        self.assertIn(M.COST_URL, lines[0])
        self.assertFalse(any("billing page changed" in x for x in run_cmd(M.status, REPO, host=host, gh=gh)[1]))
        # A page that cannot be read is journaled, never taken for a change.
        data = M.load_state(host)
        data.pop("cost_change")
        M.save_state(host, data)
        host.page = ""
        clock.sleep(8 * 24 * 3600)
        run_cmd(M.watch, host=host, gh=gh)
        self.assertNotIn("cost_change", M.load_state(host))
        with open(os.path.join(host.state_dir(), "journal.log")) as f:
            self.assertIn("cost rule GitHub's page could not be read", f.read())

    def test_G5_12_a_runner_github_removed_is_named_and_registered_again(self):
        host, clock = g5_host()
        refuse_sudo(host)  # G9.7: the watch is a system timer run as the person, with no sudo ticket
        gh = StatusGH(listed=False)
        code, lines = run_cmd(M.watch, host=host, gh=gh)
        # GitHub's runner program stops its own service once its registration is gone (seen): the watch only reads.
        self.assertFalse([a for a in host.ran if a[:1] == ("sudo",)], "the watch asks for no admin rights")
        with open(os.path.join(host.state_dir(), "journal.log")) as f:
            self.assertIn("gone on GitHub example-org/example-repo minions-box-1: its service is active", f.read())
        self.assertTrue(M.load_state(host)["runners"][REPO]["gone_on_github"])
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertTrue(lines[0].startswith("warn GitHub no longer lists our runner minions-box-1"), lines)
        self.assertIn("more than 14 days", lines[0])
        self.assertIn(f"Run `setup {REPO}` again", lines[0])
        # setup again: registered in place with the person's login, folders and labels kept, nothing unpacked.
        host2, sha = setup_host()
        fresh_look(host2)
        data = M.load_state(host2)
        data["runners"] = {REPO: dict(M.load_state(host)["runners"][REPO], folder=FOLDER, unit=UNIT)}
        M.save_state(host2, data)
        code, lines = run_setup(setup_gh(sha), host2)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn(("sudo", "-n", "-u", "minions-runner", "--", "rm", "-f", f"{FOLDER}/.runner",
                       f"{FOLDER}/.credentials", f"{FOLDER}/.credentials_rsaparams"), host2.ran)
        configs = [a for a in host2.ran if M.CONFIG_SCRIPT in a]
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0][-2:], ("minions-box-1", "minions,minions-medium"))
        # The old listener can still run, idle, after GitHub dropped it (seen): stopped before the new registration,
        # or it keeps the service and the new registration never connects (seen); started after.
        stop = ("sudo", "-n", "systemctl", "stop", UNIT, "--no-pager")
        start = ("sudo", "-n", "systemctl", "start", UNIT, "--no-pager")
        self.assertIn(stop, host2.ran)
        self.assertLess(host2.ran.index(stop), host2.ran.index(configs[0]))
        self.assertLess(host2.ran.index(configs[0]), len(host2.ran) - 1 - host2.ran[::-1].index(start))
        self.assertFalse(any("tar" in a for a in host2.ran))
        self.assertNotIn("gone_on_github", M.load_state(host2)["runners"][REPO])
        self.assertTrue(lines[-1].startswith("ok both sides"), lines)

    def test_G5_13_a_job_waiting_behind_our_hold_is_warned(self):
        job = {"id": 1, "name": "check", "status": "queued", "labels": ["minions"], "created_at": "2026-11-22T22:13:00Z"}
        host, clock = g5_host()
        data = M.load_state(host)
        data["runners"][REPO].update(held_for="drain", held_at="2026-11-22T22:10:00Z")
        M.save_state(host, data)
        gh = StatusGH(queued=[job])
        gh.r[REPO]["labels"] = ["minions-held"]
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertEqual(lines[0], f"warn job 'check' (CI) of {REPO} has waited 47 min behind our hold (drain); "
                                   f"GitHub cancels a job that waits 24 hours. `resume {REPO}` lets it land here")
        # Under the threshold: no warn.
        lines = run_cmd(M.status, REPO, host=host, gh=gh, wait_warn_s=3600)[1]
        self.assertFalse(any(x.startswith("warn job") for x in lines), lines)
        # Behind an offline runner: warned too, with what to do.
        gh = StatusGH(queued=[job], status="offline")
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertTrue(lines[0].startswith("warn job 'check'") and "behind our runner being offline" in lines[0], lines)


    # ---- G5.7 and G5.14: naming a job a cut or our limit stopped (one path)

    def _named(self, records, job, here_runs=None, notes=(), worker=False, files=None):
        t0 = int(M.parse_stamp("2026-11-22T22:40:00Z").timestamp())
        runs = {("systemctl", "show", "actions.runner.example-org-example-repo.minions-box-1.service", "-p",
                 "ActiveEnterTimestamp"): (0, "ActiveEnterTimestamp=@%d\nInactiveEnterTimestamp=\n" % (t0 - 3600))}
        runs.update(here_runs or {})
        host, clock = g5_host(runs=runs, worker=worker)
        host.files[M.records_dir_of("/home/minions-runner/runners/example-org-example-repo") + "/jobs.log"] = records
        host.files["/proc/stat"] = "cpu 1 2 3\nbtime %d\n" % (t0 - 7200)
        host.files.update(files or {})
        data = M.load_state(host)
        data["runners"][REPO]["limits"] = {"MemoryMax": str(4 * 1024 ** 3)}
        M.save_state(host, data)
        gh = StatusGH()
        gh.jobs[555] = [dict({"id": 9001, "name": "check", "runner_name": "minions-box-1", "run_attempt": 1,
                              "started_at": "2026-11-22T22:39:58Z"}, **job)]
        gh.annotations[9001] = [{"message": m} for m in notes]
        return host, gh, t0

    CUT_STEPS = [{"name": "Set up job", "conclusion": "success"}, {"name": "Run tests", "conclusion": "cancelled"}]

    def test_G5_7_a_job_cut_by_a_restart_is_named_and_offered_again(self):
        t0 = int(M.parse_stamp("2026-11-22T22:40:00Z").timestamp())
        start = f"start {t0} 555 1 0 check\n"
        cut = {"conclusion": "failure", "steps": self.CUT_STEPS}
        # A stop of the runner's service during the job, no end recorded here: both sides agree, named as cut.
        stopped = {("systemctl", "show", "actions.runner.example-org-example-repo.minions-box-1.service", "-p",
                    "ActiveEnterTimestamp"): (0, "ActiveEnterTimestamp=@%d\nInactiveEnterTimestamp=@%d\n" % (t0 + 30, t0 + 20))}
        host, gh, _ = self._named(start, cut, stopped, notes=[M.CANCELED])
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        named = [x for x in lines if x.startswith("warn job 'check'")]
        self.assertEqual(named, ["warn job 'check' of .github/workflows/ci.yml (run 555, started 22:40:00Z) was cut by a stop of this computer's "
                                 "runner service, not a failing test of yours; GitHub shows it as failed. To run it "
                                 f"again, once: `status {REPO} --rerun 9001`."])
        # The stop is only in the unit's journal (systemd's own start and stop times were replaced since, as a
        # reboot does): still named. And once named, it is kept: a later read without this computer's record of the
        # stop still names it (the watch names and keeps them).
        later = {("systemctl", "show", "actions.runner.example-org-example-repo.minions-box-1.service", "-p",
                  "ActiveEnterTimestamp"): (0, "ActiveEnterTimestamp=@%d\nInactiveEnterTimestamp=@%d\n" % (t0 + 9000, t0 + 8990)),
                 ("journalctl", "-u", "actions.runner.example-org-example-repo.minions-box-1.service"):
                 (0, "%d.1 box-1 systemd[1]: Stopping actions.runner...\n" % (t0 + 20))}
        host, gh, _ = self._named(start, cut, later, notes=[M.CANCELED])
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(M.load_state(host)["named"]["9001"]["why"], "a stop of this computer's runner service")
        host.runs.pop(("journalctl", "-u", "actions.runner.example-org-example-repo.minions-box-1.service"))
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertTrue(any("was cut by a stop of this computer's runner service" in x for x in lines), lines)
        # A restart of the computer during the job.
        host, gh, _ = self._named(start, cut, files={"/proc/stat": "btime %d\n" % (t0 + 60)})
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertTrue(any("was cut by a restart of this computer" in x for x in lines), lines)
        # A sleep during the job (end recorded): cut by sleep.
        sleep = {("journalctl", "-u", "systemd-suspend.service"):
                 (0, "%d.5 box-1 systemd-sleep[9]: Entering sleep state 'suspend'...\n" % (t0 + 40))}
        host, gh, _ = self._named(start + f"end {t0 + 900} 555 1 0 check\n", cut, sleep)
        self.assertTrue(any("was cut by this computer going to sleep" in x for x in run_cmd(M.status, REPO, host=host, gh=gh)[1]))
        # systemd 255 (Ubuntu 24.04) writes the same moment in other words (seen on a stock virtual machine).
        sleep255 = {("journalctl", "-u", "systemd-suspend.service"):
                    (0, "%d.8 box-1 systemd-sleep[9]: Performing sleep operation 'suspend'...\n" % (t0 + 40))}
        host, gh, _ = self._named(start + f"end {t0 + 900} 555 1 0 check\n", cut, sleep255)
        self.assertTrue(any("was cut by this computer going to sleep" in x for x in run_cmd(M.status, REPO, host=host, gh=gh)[1]))
        # Not named: a job that came through a sleep (GitHub: success); the person's own failing test (no step
        # cancelled), even with a stop here; GitHub's cut with nothing on this computer's side.
        for job, runs, rec in (({"conclusion": "success", "steps": []}, sleep, start + f"end {t0 + 900} 555 1 0 check\n"),
                               ({"conclusion": "failure", "steps": [{"name": "Run tests", "conclusion": "failure"}]},
                                stopped, start),
                               (cut, None, start + f"end {t0 + 900} 555 1 0 check\n")):
            host, gh, _ = self._named(rec, job, runs)
            lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
            self.assertFalse(any(x.startswith("warn job 'check'") for x in lines), (job, lines))
        # Run again once, only on the person's word; never a second time; never a job we did not name.
        host, gh, _ = self._named(start, cut, stopped, notes=[M.CANCELED])
        self.assertEqual(gh.reruns, [])
        code, lines = run_cmd(M.status, REPO, host=host, gh=gh, rerun=9001)
        self.assertEqual(gh.reruns, [9001])
        self.assertIn("ok asked GitHub to run job 'check' again, once; it lands here when this computer is free", lines)
        code, lines = run_cmd(M.status, REPO, host=host, gh=gh, rerun=9001)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertEqual(gh.reruns, [9001])
        self.assertTrue(any("It was run again once already." in x for x in lines))
        code, lines = run_cmd(M.status, REPO, host=host, gh=gh, rerun=1234)
        self.assertEqual((code, gh.reruns), (M.EXIT_STOP, [9001]))
        self.assertIn("stop job 1234 is not one this computer cut or its limit stopped: nothing was run again", lines)

    def test_G11_4_after_a_wake_or_network_change_the_runner_is_seen_back(self):
        # The watch writes down since when GitHub has shown our runner offline while its service runs here with no
        # job process; it restarts nothing (no admin rights). Past two intervals, status names it first, with the
        # way on. Back online, it is cleared. (By ruling, 2026-11-23: seen coming back by itself in a VM.)
        host, clock = g5_host()
        run_cmd(M.watch, host=host, gh=StatusGH(status="offline"))
        mine = M.load_state(host)["runners"][REPO]
        self.assertEqual(mine["offline_since"], "2026-11-22T23:00:00Z")
        self.assertFalse([a for a in host.ran if a[:2] == ("sudo", "-n")], "the watch restarts nothing")
        clock.sleep(240)  # under two intervals (2 x 300 s): only the usual not-ready line
        run_cmd(M.watch, host=host, gh=StatusGH(status="offline"))
        self.assertEqual(M.load_state(host)["runners"][REPO]["offline_since"], "2026-11-22T23:00:00Z")
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(status="offline"))[1]
        self.assertFalse([x for x in lines if "has shown it offline at" in x], lines)
        clock.sleep(420)  # 11 min: past two intervals
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(status="offline"))[1]
        self.assertEqual(lines[0], "warn minions-box-1 for example-org/example-repo: GitHub has shown it offline at "
                                   "every watch pass since 23:00:00Z (11 min), and now, while its service runs here, "
                                   "so no job lands on it. A runner "
                                   "usually comes back by itself within a few minutes of a wake or of the network "
                                   "returning; this one has not. `drain example-org/example-repo --stop`, then "
                                   "`resume example-org/example-repo`, starts it fresh (each asks for the admin "
                                   "password once)")
        # R85 (seen on a stock machine): the same 11 minutes, 8 of them asleep, is 3 minutes awake: not named; a
        # runner is still reconnecting just after a wake.
        host.slept = 8 * 60
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(status="offline"))[1]
        self.assertFalse([x for x in lines if "has shown it offline at" in x], lines)
        clock.sleep(8 * 60)  # 19 min, 11 of them awake: named, with the awake part said
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(status="offline"))[1]
        self.assertTrue(lines[0].startswith("warn minions-box-1 for example-org/example-repo: GitHub has shown it "
                                            "offline at every watch pass since 23:00:00Z (19 min, 11 min of it "
                                            "awake), and now"), lines[0])
        host.slept = 0
        # R86 (seen in a steered run on a stock machine): the watch reads GitHub once a pass, so the line says each
        # pass and now; it never says GitHub showed it offline for the whole span.
        self.assertNotIn("offline since", lines[0])
        # R85: setup's (and resume's) both-sides check, once GitHub lists it online, ends an offline record written
        # before it came online (seen: setup's own first watch pass ran before GitHub listed the new runner).
        d = M.load_state(host)
        unit = d["runners"][REPO]["unit"]
        self.assertTrue(M.both_sides(StatusGH(status="online"), host, REPO, "minions-box-1", unit,
                                     ["minions", "minions-medium"], M.Report(io.StringIO()), wait_s=0))
        self.assertNotIn("offline_since", M.load_state(host)["runners"][REPO])
        run_cmd(M.watch, host=host, gh=StatusGH(status="offline"))  # offline again, for the lines below
        # Back online: cleared, and not named.
        run_cmd(M.watch, host=host, gh=StatusGH(status="online"))
        self.assertNotIn("offline_since", M.load_state(host)["runners"][REPO])
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(status="online"))[1]
        self.assertFalse([x for x in lines if "has shown it offline at" in x], lines)
        # A job process runs, or the service is stopped: not this case.
        for kw, runs in (({"worker": True}, None), ({}, {("systemctl", "is-active"): (3, "inactive\n")})):
            host, clock = g5_host(runs=runs, **kw)
            run_cmd(M.watch, host=host, gh=StatusGH(status="offline"))
            self.assertNotIn("offline_since", M.load_state(host)["runners"][REPO], kw)

    def test_G11_5_a_job_cut_by_sleep_is_named_and_offered_again(self):
        # After a wake: a job the sleep cut is named as cut by the sleep (both sides agree), offered once, and run
        # again once on the person's word (as G5.7).
        t0 = int(M.parse_stamp("2026-11-22T22:40:00Z").timestamp())
        rec = f"start {t0} 555 1 0 check\nend {t0 + 900} 555 1 0 check\n"
        cut = {"conclusion": "failure", "steps": self.CUT_STEPS}
        slept = {("journalctl", "-u", "systemd-suspend.service"):
                 (0, "%d.8 box-1 systemd-sleep[9]: Performing sleep operation 'suspend'...\n" % (t0 + 40))}
        host, gh, _ = self._named(rec, cut, slept)
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertIn("warn job 'check' of .github/workflows/ci.yml (run 555, started 22:40:00Z) was cut by this computer going to sleep, not a "
                      "failing test of yours; GitHub shows it as failed. To run it again, once: "
                      f"`status {REPO} --rerun 9001`.", lines)
        code, lines = run_cmd(M.status, REPO, host=host, gh=gh, rerun=9001)
        self.assertEqual(gh.reruns, [9001])
        code, lines = run_cmd(M.status, REPO, host=host, gh=gh, rerun=9001)
        self.assertEqual((code, gh.reruns), (M.EXIT_STOP, [9001]))
        # A job that came through the same sleep (GitHub: success) is not named.
        host, gh, _ = self._named(rec, {"conclusion": "success", "steps": []}, slept)
        self.assertFalse([x for x in run_cmd(M.status, REPO, host=host, gh=gh)[1] if x.startswith("warn job")])

    def test_G5_18_a_stopped_job_is_named_by_its_workflow_file(self):
        # Seen 2026-11-23 (run K, row R118): status named a job killed at the cap only as 'j' and its run; the agent
        # could not say which of the person's workflows it was, since several name their job `j`.
        t0 = int(M.parse_stamp("2026-11-22T22:40:00Z").timestamp())
        failed = {"conclusion": "failure", "steps": [{"name": "Build", "conclusion": "failure"}]}
        host, gh, _ = self._named(f"start {t0} 555 1 0 check\nend {t0 + 70} 555 1 1 check\n", failed,
                                  notes=["Process completed with exit code 137."])
        gh.workflow_path = ".github/workflows/mem-big.yml"
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        named = [x for x in lines if x.startswith("warn job 'check'")]
        self.assertEqual(len(named), 1, lines)
        self.assertTrue(named[0].startswith("warn job 'check' of .github/workflows/mem-big.yml (run 555, started "
                                            "22:40:00Z) was stopped by this computer's memory limit"), named[0])
        # Read once, then kept: a later status needs no read of the run.
        gh.workflow_path = None
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertTrue(any("of .github/workflows/mem-big.yml (run 555" in x for x in lines), lines)
        # Where it cannot be read, the line says so, never a guess.
        host, gh, _ = self._named(f"start {t0} 555 1 0 check\nend {t0 + 70} 555 1 1 check\n", failed,
                                  notes=["Process completed with exit code 137."])
        gh.workflow_path = None
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        named = [x for x in lines if x.startswith("warn job 'check'")]
        self.assertTrue(named and "which workflow it came from could not be read" in named[0], lines)

    def test_G5_14_a_job_killed_by_our_limit_is_named_as_such(self):
        t0 = int(M.parse_stamp("2026-11-22T22:40:00Z").timestamp())
        failed = {"conclusion": "failure", "steps": [{"name": "Build", "conclusion": "failure"}]}
        # The slice's kill count rose during the job: named as our limit, with the one way to change it.
        host, gh, _ = self._named(f"start {t0} 555 1 0 check\nend {t0 + 70} 555 1 1 check\n", failed,
                                  notes=["Process completed with exit code 137."])
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        named = [x for x in lines if x.startswith("warn job 'check'")]
        self.assertEqual(named, ["warn job 'check' of .github/workflows/ci.yml (run 555, started 22:40:00Z) was stopped by this computer's memory "
                                 "limit (4.0 GB), not by your code failing; GitHub shows it as failed. The one way to "
                                 f"change the limit: `setup {REPO} --limits <processors>,<memory %>`. To run it again, once: "
                                 f"`status {REPO} --rerun 9001`."])
        # R93: the limit said is the one it was killed under; limits changed after that never rewrite it.
        d = M.load_state(host)
        d["runners"][REPO]["limits"]["MemoryMax"] = str(2 * 1024 ** 3)
        M.save_state(host, d)
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertTrue(any(x.startswith("warn job 'check'") and "memory limit (4.0 GB)" in x for x in lines), lines)
        # A job named before the limit was kept: no number, rather than today's.
        d = M.load_state(host)
        d["named"]["9001"].pop("limit")
        M.save_state(host, d)
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertTrue(any(x.startswith("warn job 'check'") and "memory limit, not by your" in x for x in lines), lines)
        # No kill counted here: the person's own failure, never named.
        host, gh, _ = self._named(f"start {t0} 555 1 1 check\nend {t0 + 70} 555 1 1 check\n", failed)
        self.assertFalse(any(x.startswith("warn job") for x in run_cmd(M.status, REPO, host=host, gh=gh)[1]))
        # No end here (the runner itself was killed): the count read now decides.
        host, gh, _ = self._named(f"start {t0} 555 1 0 check\n", failed, files={M.MEMORY_EVENTS: "oom 2\noom_kill 2\n"})
        self.assertTrue(any("stopped by this computer's memory limit" in x for x in run_cmd(M.status, REPO, host=host, gh=gh)[1]))
        # The hooks themselves write the records, with the slice's kill count, for real.
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            events = os.path.join(d, "memory.events")
            with open(events, "w") as f:
                f.write("low 0\nhigh 3\nmax 2\noom 1\noom_kill 1\n")
            recs = os.path.join(d, "records")
            os.makedirs(recs)
            env = dict(no_event(), GITHUB_RUN_ID="555", GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="check",
                       GITHUB_WORKSPACE=os.path.join(d, "nowhere"), PATH=machine_guard.path("/usr/bin:/bin"))
            with mock.patch.object(M, "MEMORY_EVENTS", events):
                seen = fresh_seen(d)
                for name, fn in (("s.sh", lambda f, r: M.job_started_script(f, r, seen=seen)),
                                 ("e.sh", M.job_completed_script)):
                    with open(os.path.join(d, name), "w") as f:
                        f.write(fn(os.path.join(d, "runner"), recs).replace("command -v systemd-inhibit", "false"))
                    r = subprocess.run(["bash", os.path.join(d, name)], env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(r.returncode, 0, r.stderr)
            with open(os.path.join(recs, "jobs.log")) as f:
                got = [x.split() for x in f.read().splitlines()]
            self.assertEqual([g[0] for g in got], ["start", "end"])
            self.assertEqual([g[2:] for g in got], [["555", "1", "1", "check"], ["555", "1", "1", "check"]])
        # setup makes the records folder: the runner writes, you read.
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        self.assertIn(("sudo", "-n", "install", "-d", "-o", "minions-runner", "-g", "person", "-m", "2750",
                       f"{M.RECORDS_ROOT}/example-org-example-repo"), host.ran)


# ------------------------------------------------------------------------------------------------------------ G6

RM_TOKEN = "fake-" + "remove-" + "token-" + "2b8e"  # built from pieces: never a real token, never in any argv
SLICE_FILE = f"/etc/systemd/system/{M.SLICE}"
WATCH_FILES = (f"/etc/systemd/system/{M.WATCH_UNIT}.timer", f"/etc/systemd/system/{M.WATCH_UNIT}.service")


class World:
    """A computer with our runner on it, changed only by the commands the tool runs (through sudo and systemctl).
    It behaves as seen on a real one: `config.sh remove` refuses while the service is installed, and a slice whose
    file is deleted stays loaded and active after a reload until it is stopped (Part 7 C7)."""

    def __init__(self, gh, own_removal_works=True, interrupt_on=None):
        self.gh, self.own_removal_works, self.interrupt_on = gh, own_removal_works, interrupt_on
        self.paths = {FOLDER, f"{FOLDER}/.runner", f"{FOLDER}/.service", f"/etc/systemd/system/{UNIT}",
                      f"/etc/systemd/system/{UNIT}.d", SLICE_FILE, *WATCH_FILES, M.HOOKS_ROOT, M.hook_dir(REPO),
                      M.RECORDS_ROOT, M.records_dir_of(FOLDER), "/home/minions-runner"}
        self.active = {UNIT: "active", M.SLICE: "active", f"{M.WATCH_UNIT}.timer": "active"}
        self.loaded = set(self.active) | {f"{M.WATCH_UNIT}.service"}
        self.user, self.listener, self.worker, self.order = True, True, False, []
        self.files, self.links = {}, set()  # remove_setup shares the host's files; links a job planted (R128)

    def gone(self, path):
        self.paths = {p for p in self.paths if p != path and not p.startswith(path.rstrip("/") + "/")}

    def answer(self, argv, input=None):
        a = list(argv)
        if self.interrupt_on and self.interrupt_on(a):
            self.interrupt_on = None
            raise KeyboardInterrupt
        if a[:2] == ["sudo", "-n"]:
            a = a[2:]
            if a[:3] == ["-u", "minions-runner", "--"] and a[3:5] != ["bash", "-c"] and a[3:5] != ["env", "-i"]:
                a = a[3:]  # R128: the runner user's own steps (read, rm, find, kill) in its own folders
                if a[:3] == ["stat", "-c", "%F"]:  # R128: what kind of thing a job may have left there
                    if a[3] in self.links:
                        return 0, "symbolic link\n"
                    if a[3] in self.files or a[3] in self.paths:
                        return 0, "regular file\n"
                    return 1, f"stat: cannot statx '{a[3]}': No such file or directory"
                if a[:1] == ["cat"]:
                    if a[1] in self.files:
                        return 0, self.files[a[1]]
                    return (0, UNIT + "\n") if a[1] in self.paths else (1, f"cat: {a[1]}: No such file or directory")
                if a[:1] == ["find"] and a[1] == "/tmp":  # R130: what the runner user owns in /tmp etc.
                    self.order.append(f"find {a[-1]} in /tmp")
                    return 0, ""
                if a[:2] == ["kill", "-KILL"]:
                    self.order.append("kill -KILL -1 (as the runner)")
                    return 0, ""
                if a[:1] == ["find"]:
                    self.order.append(f"empty {a[1]}")
                    self.paths = {p for p in self.paths if not p.startswith(a[1].rstrip("/") + "/")}
                    return 0, ""
            elif a[:3] == ["-u", "minions-runner", "--"]:
                self.order.append("config.sh remove")
                if f"{FOLDER}/.service" in self.paths:
                    return 1, "Uninstall service first"
                if not self.own_removal_works or input != RM_TOKEN + "\n":
                    return 1, "Failed: removing runner from the server"
                self.gh.runner = None
                self.gone(f"{FOLDER}/.runner")
                return 0, "Runner removed successfully"
        if a == ["true"] and argv[:2] == ("sudo", "-n") or list(argv[:3]) == ["sudo", "-n", "true"]:
            return 0, ""
        if a[:2] == ["test", "-e"]:
            return (0 if a[2] in self.paths else 1), ""
        if a[:1] == ["rm"]:
            self.order.append(f"rm {a[-1]}")
            self.gone(a[-1])
            return 0, ""
        if a[:1] == ["rmdir"]:
            if any(p.startswith(a[1].rstrip("/") + "/") for p in self.paths):
                return 1, f"rmdir: failed to remove '{a[1]}': Directory not empty"
            self.order.append(f"rmdir {a[1]}")
            self.gone(a[1])
            return 0, ""
        if a[:1] == ["userdel"]:
            self.order.append("userdel")
            self.user = False
            self.gone("/home/minions-runner")
            return 0, ""
        if a[:1] == ["systemctl"] and argv[0] == "sudo":
            verb, rest = a[1], [x for x in a[2:] if not x.startswith("--")]
            if verb == "stop" or (verb == "disable" and "--now" in a):
                for u in rest:
                    self.order.append(f"stop {u}")
                    self.active[u] = "inactive"
                    if u == UNIT:
                        self.listener = False
            if verb == "daemon-reload":
                for u in list(self.loaded):
                    if f"/etc/systemd/system/{u}" not in self.paths and self.active.get(u) != "active":
                        self.loaded.discard(u)
            return 0, ""
        if a[:2] == ["systemctl", "show"]:
            u = a[2]
            # As systemd 255 answers: a slice always reads "loaded" (systemd makes any slice from its name); what
            # tells a loaded file is FragmentPath.
            frag = f"/etc/systemd/system/{u}" if u in self.loaded else ""
            return 0, (f"LoadState={'loaded' if u in self.loaded or u.endswith('.slice') else 'not-found'}\n"
                       f"ActiveState={self.active.get(u, 'inactive') if u in self.loaded else 'inactive'}\n"
                       f"FragmentPath={frag}\nDropInPaths=\n")
        if a[:2] == ["systemctl", "is-active"]:
            st = self.active.get(a[2], "inactive")
            return (0 if st == "active" else 3), st + "\n"
        if a[:2] == ["systemctl", "list-units"]:
            return 0, "".join(f"{u} loaded {self.active.get(u, 'inactive')}\n" for u in sorted(self.loaded))
        if a[:2] == ["pgrep", "-u"]:
            if a[-1].endswith("Runner.Worker"):
                return (0, "3333\n") if self.worker else (1, "")
            return (0, "2222\n") if self.listener else (1, "")
        if a[:1] == ["id"]:
            if a[1:2] == ["-u"]:
                return (0, "1001\n") if self.user else (1, "id: 'minions-runner': no such user")
            return (0, "uid=1001(minions-runner)") if self.user else (1, "no such user")
        if a[:1] == ["crontab"]:  # R130: as on a stock Ubuntu: no crontab of that user
            self.order.append(" ".join(a[:2]))
            return 1, "no crontab for minions-runner\n"
        if a[:1] == ["atq"]:
            return 0, ""
        if a == ["sudo", "-n", "true"]:
            return 0, ""
        return 1, ""


class RemoveGH(MoveGH):
    """GitHub for remove: our runner until it is removed, the removal token, DELETE, label PUTs, and (from MoveGH)
    the workflow files and the Git Data API for the put-back pull request."""

    def __init__(self, files=None, busy=(False,)):
        super().__init__(files or {})
        self.runner = {"id": 7, "name": OURS, "status": "online", "labels": [{"name": "minions"},
                                                                           {"name": "minions-medium"}]}
        self.busy = list(busy)

    def api(self, method, path, body=None):
        base = f"repos/{REPO}"
        if method == "GET" and path == f"{base}/actions/runners?per_page=100":
            self.calls.append((method, path))
            if self.runner is None:
                return 200, {"total_count": 0, "runners": []}
            b = self.busy.pop(0) if len(self.busy) > 1 else self.busy[0]
            return 200, {"total_count": 1, "runners": [dict(self.runner, busy=b)]}
        if (method, path) == ("POST", f"{base}/actions/runners/remove-token"):
            self.calls.append((method, path))
            return 201, {"token": RM_TOKEN, "expires_at": "2026-11-23T01:00:00Z"}
        if (method, path) == ("DELETE", f"{base}/actions/runners/7"):
            self.calls.append((method, path))
            self.writes.append((method, path, body))
            self.runner = None
            return 204, None
        if (method, path) == ("PUT", f"{base}/actions/runners/7/labels"):
            self.calls.append((method, path))
            self.runner["labels"] = [{"name": x} for x in body["labels"]]
            return 200, {"labels": self.runner["labels"]}
        return super().api(method, path, body)


MOVED_CI = CI_YML.replace("runs-on: ubuntu-latest   # the person's own comment",
                          "runs-on: [minions]   # the person's own comment")


def remove_setup(own_removal_works=True, interrupt_on=None, busy=(False,), moved=True):
    gh = RemoveGH({".github/workflows/ci.yml": MOVED_CI}, busy=busy)
    world = World(gh, own_removal_works, interrupt_on)
    host = linux_host()
    host._answer = world.answer
    world.files = host.files
    # A folder lists what the world holds under it (G6.10: the records left after a copy that failed).
    host.listdir_or_none = lambda path: (sorted({p[len(path) + 1:].split("/")[0] for p in world.paths
                                                 if p.startswith(path.rstrip("/") + "/")}) if path in world.paths else [])
    Clock(host)
    data = {"runners": {REPO: {"name": OURS, "folder": FOLDER, "unit": UNIT, "labels": ["minions", "minions-medium"]}},
            "runner_user_made": True,  # R129: setup made the runner user, and its record says so
            "dependency_packages": []}  # R139: setup read the package list; the dependency script added none
    if moved:
        data["moves"] = {REPO: {"pr": 12, "branch": "minions-move-1", "base": "main", "files": {
            ".github/workflows/ci.yml": [{"id": "test", "name": "Unit tests",
                                          "runs_on": "    runs-on: ubuntu-latest   # the person's own comment\n"}]}}}
    M.save_state(host, data)
    return gh, world, host


def run_uninstall(gh, host, repo=REPO, **kw):
    out = io.StringIO()
    code = M.uninstall(repo, host=host, gh=gh, out=out, interactive=True, **kw)
    return code, out.getvalue().splitlines()


class G6(unittest.TestCase):
    def test_G6_17_the_last_uninstall_leaves_nothing_of_the_runner_user_alive_or_scheduled(self):
        # R130 (rule 28, the one-after-another hole): when the last runner goes, its user goes in this order: the
        # service stopped; no process of that user, read back; its crontab and at jobs off; linger off; its files in
        # /tmp, /var/tmp and /dev/shm removed as that user; then userdel -r. What could not be removed is named.
        gh, world, host = remove_setup()
        code, lines = run_uninstall(gh, host)
        self.assertEqual(code, M.EXIT_OK, lines)
        o = world.order
        steps = [f"stop {UNIT}", "rmdir " + M.records_dir_of(FOLDER), "crontab -r", "crontab -l", "find -delete in /tmp",
                 "find -print in /tmp", "empty /home/minions-runner", "userdel"]
        self.assertEqual([x for x in o if x in steps], steps, o)
        ran = list(host.ran)
        procs = [i for i, a in enumerate(ran) if a == ("pgrep", "-u", "minions-runner")]
        cron = ran.index(("sudo", "-n", "crontab", "-r", "-u", "minions-runner"))
        self.assertTrue([i for i in procs if i < cron], "no process of that user is read back before its crontab goes")
        self.assertTrue(any("nothing of minions-runner is left running or scheduled" in x for x in lines), lines)
        ran = [a[5:] for a in host.ran if a[2:5] == ("-u", "minions-runner", "--") and a[5:6] == ("find",)]
        self.assertIn(("find", *M.PUBLIC_TMP, "-xdev", "-user", "minions-runner", "-delete"), ran)
        # a process that outlives SIGKILL stops the uninstall before userdel, and is named
        gh, world, host = remove_setup()
        real = host._answer
        host._answer = lambda argv, input=None: ((0, "4444\n") if list(argv) == ["pgrep", "-u", "minions-runner"]
                                                  and "rmdir " + M.records_dir_of(FOLDER) in world.order
                                                  else real(argv, input))
        code, lines = run_uninstall(gh, host)
        self.assertNotEqual(code, M.EXIT_OK)
        self.assertTrue(world.user)
        self.assertNotIn("userdel", world.order)
        self.assertIn("still run after being ended with SIGKILL", " ".join(lines))
        # a file of that user left in /tmp after the delete is named, and the user stays
        gh, world, host = remove_setup()
        real2 = host._answer
        def left(argv, input=None):
            if list(argv[5:6]) == ["find"] and argv[-1] == "-print":
                return 0, "/tmp/kept-by-a-job\n"
            return real2(argv, input)
        host._answer = left
        code, lines = run_uninstall(gh, host)
        self.assertTrue(world.user)
        self.assertIn("/tmp/kept-by-a-job", " ".join(lines))

    def test_G6_18_a_unit_that_failed_is_not_left_loaded_after_uninstall(self):
        # R132: a failed oneshot unit stays loaded as "failed" after its file is deleted, until reset-failed (seen on
        # a stock Ubuntu 24.04 virtual machine, 2026-11-24): uninstall resets the units it deleted after the reload.
        gh, world, host = remove_setup()
        code, lines = run_uninstall(gh, host, everything=True)
        self.assertIn("ok census: the slice and the watch (scheduled checks): none", lines)
        ran = [a[2:] for a in host.ran if a[2:3] == ("systemctl",)]
        for units in ((UNIT,), (f"{M.WATCH_UNIT}.timer", f"{M.WATCH_UNIT}.service")):
            reset = [i for i, a in enumerate(ran) if a[1] == "reset-failed" and set(units) <= set(a)]
            self.assertTrue(reset, units)
            self.assertTrue(any(a[1] == "daemon-reload" for a in ran[:reset[0]]), units)

    def test_G6_19_a_foreign_file_does_not_stop_a_leave(self):
        # R136 (rule 8, A FOREIGN FILE DOES NOT STOP A LEAVE): at uninstall, what this tool did not make (a file of
        # the person's in its state folder or record folder, a minions-runner it did not make) stays with the folders
        # above it, named in the census as the person's with why, and no command; the leave goes to its end and exits
        # with success; the last line says the tool is off this computer and what stayed is named above.
        gh, world, host = remove_setup(moved=False)
        with open(os.path.join(host.state_dir(), "my-notes.txt"), "w") as f:
            f.write("the person's own")
        d = M.load_state(host)
        d.pop("runner_user_made")
        M.save_state(host, d)
        code, lines = run_uninstall(gh, host, everything=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertFalse([x for x in lines if x.startswith(("stop ", "warn the user"))], lines)
        self.assertTrue(world.user)
        self.assertNotIn("userdel", world.order)
        self.assertIsNone(gh.runner)  # the leave went to its end: GitHub's side, the service, the watch, the slice
        census = [x for x in lines if x.startswith(("ok census:", "warn census:"))]
        self.assertIn("ok census: the runner user: minions-runner (kept: its record here does not say this tool made "
                      "it, so it is yours)", census)
        self.assertIn("ok census: the folder /home/minions-runner: kept: it is the home of the user minions-runner, "
                      "which this tool did not make; it is yours", census)
        self.assertIn(f"ok census: this tool's state folder: {host.state_dir()} (kept: it holds my-notes.txt, which "
                      "this tool did not write; that and the folder are yours)", census)
        self.assertIn("ok census: the slice and the watch (scheduled checks): none", census)
        self.assertTrue(os.path.exists(os.path.join(host.state_dir(), "my-notes.txt")))
        self.assertEqual(sorted(os.listdir(host.state_dir())), ["my-notes.txt"])  # ours went, by name
        for x in census:
            if "kept:" in x and "your record of what this tool did" not in x:
                self.assertNotIn("`", x, "no command for what is the person's")
        self.assertEqual(lines[-1], "ok this tool is off this computer; what stayed is yours, named above with why "
                                    "each stayed")
        # nothing foreign: the census stays last, as before (G6.10)
        gh, world, host = remove_setup(moved=False)
        code, lines = run_uninstall(gh, host, everything=True)
        self.assertEqual(code, M.EXIT_OK)
        self.assertTrue(lines[-1].startswith("ok census: your record of what this tool did"), lines[-1])
        # setup is where a foreign thing stops (G2.14)
        host, sha = setup_host(runs={("id", "-u", "minions-runner"): (0, "1001\n")})
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_STOP)

    def test_G6_22_the_way_back_in_is_walked(self):
        # R149 (rule 8, THE WAY BACK IN IS WALKED TOO): for each form of leaving, setup again meets what that leave
        # kept, decides by what is there now, ends on its own check of both sides with exit 0, and a watch pass after
        # it works. `uninstall REPO` keeps the state folder (no runner in it), the watch, the slice and the tool's
        # copy; `uninstall --all` keeps only the person's record, outside the state folder.
        gh, world, host = remove_setup(moved=False)
        self.assertEqual(run_uninstall(gh, host)[0], M.EXIT_OK)
        kept = M.load_state(host)
        self.assertEqual(kept.get("runners"), {})
        self.assertNotIn("runner_user_made", kept)  # the user it made went with the last runner (R130)
        with open(os.path.join(host.state_dir(), "journal.log"), encoding="utf-8") as f:
            journal_kept = f.read()
        gh, world, host = remove_setup(moved=False)
        self.assertEqual(run_uninstall(gh, host, everything=True)[0], M.EXIT_OK)
        self.assertFalse(os.path.isdir(host.state_dir()))
        for form, state in (("uninstall REPO", kept), ("uninstall --all", None)):
            h, sha = setup_host()
            fresh_look(h)
            if state is not None:
                d = M.load_state(h)
                d.update(state)
                M.save_state(h, d)
                with open(os.path.join(h.state_dir(), "journal.log"), "a", encoding="utf-8") as f:
                    f.write(journal_kept)
            code, lines = run_setup(setup_gh(sha), h)
            self.assertEqual(code, M.EXIT_OK, (form, lines))
            self.assertTrue(lines[-1].startswith("ok both sides: GitHub lists "), (form, lines[-1]))
            self.assertFalse([x for x in lines if x.startswith(("stop ", "need you: log in"))], (form, lines))
            self.assertIn(REPO, M.load_state(h)["runners"])
            self.assertEqual(M.watch(host=h, gh=StatusGH(), out=io.StringIO()), M.EXIT_OK, form)

    def test_G6_10_uninstall_keeps_the_persons_record_in_one_folder_they_own(self):
        # Rule 8 and INV6 (plan 3.0, row R99): the record of what the tool did is the person's. It is moved into one
        # plain-text folder they own, named last by the census, and deleted only on a flag typed at a terminal.
        jobs = M.records_dir_of(FOLDER) + "/jobs.log"
        lines_ = "start 1795440000 4521 1 0 build\nend 1795440090 4521 1 0 build\n"
        def world_with_jobs():
            gh, world, host = remove_setup()
            world.paths.add(jobs)
            host.files[jobs] = lines_
            return gh, world, host
        gh, world, host = world_with_jobs()
        code, out = run_uninstall(gh, host, repo=None, everything=True)
        rec = host.record_dir()
        self.assertEqual(rec, "/home/person/repo-minions-record")
        self.assertEqual(host.files[f"{rec}/jobs-{os.path.basename(FOLDER)}.log"], lines_)
        self.assertIn(" delete ", host.files[f"{rec}/journal.log"])  # the journal, to its last step
        self.assertIn("Nothing secret is in here", host.files[f"{rec}/README.txt"])
        self.assertFalse(any(RM_TOKEN in v for k, v in host.files.items() if k.startswith(rec)))  # INV2
        self.assertNotIn(M.RECORDS_ROOT, world.paths)  # moved, then deleted: nothing of the tool left installed
        self.assertFalse(os.path.isdir(host.state_dir()))
        n = host.record_count()
        self.assertEqual(out[-1], f"ok census: your record of what this tool did: {rec}, {n[1]} lines in 3 files, "
                                  "kept: it is yours. Delete it whenever you like, or run `uninstall --all "
                                  "--delete-record` in your own terminal")
        # Run twice, it is safe, and the record is still there.
        M.main(["uninstall", "--all"], host=host, out=io.StringIO(), gh=gh)
        self.assertEqual(host.files[f"{rec}/jobs-{os.path.basename(FOLDER)}.log"], lines_)
        # The record cannot be written: the jobs' records and the journal stay where they are, never deleted on a
        # failed copy (rule 6), and the person still leaves: everything else is taken.
        gh, world, host = world_with_jobs()
        host.record_unwritable = True
        code, out = run_uninstall(gh, host, repo=None, everything=True)
        self.assertNotEqual(code, M.EXIT_STOP, out)
        self.assertIn(M.records_dir_of(FOLDER), world.paths)
        self.assertNotIn(M.HOOKS_ROOT, world.paths)
        self.assertFalse(world.user)
        self.assertTrue(any(x.startswith("warn the jobs' records in") and "kept where they are" in x for x in out))
        self.assertTrue(os.path.isfile(os.path.join(host.state_dir(), "journal.log")))
        self.assertIn("census:", out[-1])
        # Deleting it is a separate choice typed at a terminal: an agent with no terminal is told it is the person's.
        gh, world, host = world_with_jobs()
        host.tty = False  # the record's deletion asks the terminal itself; the uninstall was confirmed with Enter
        o = io.StringIO()
        M.uninstall(None, everything=True, host=host, gh=gh, out=o, interactive=True, delete_record=True)
        self.assertNotEqual(host.record_count(), (0, 0))
        self.assertTrue(any(x.startswith("need you: deleting your record") and "your own terminal" in x
                            for x in o.getvalue().splitlines()))
        self.assertTrue(o.getvalue().splitlines()[-1].startswith("ok census: your record"))
        for typed, kept in (("", True), ("yes", True), ("delete", False)):
            gh, world, host = world_with_jobs()
            host.tty, host.answer = True, typed
            o = io.StringIO()
            M.uninstall(None, everything=True, host=host, gh=gh, out=o, interactive=True, delete_record=True)
            self.assertIn("Type delete", host.asked)
            self.assertEqual(host.record_count() != (0, 0), kept, typed)
            self.assertIn("census:", o.getvalue().splitlines()[-1])

    def test_G6_11_the_tools_own_copy_alone_runs_uninstall_all_to_the_end(self):
        # Rule 8 (2026-11-23): a person may leave by Claude Code's own `plugin uninstall`, which leaves our runner
        # and watch running by design. The one command that takes them off runs from the copy setup installed,
        # with the plugin's folder gone: a lone file, standard library only.
        src = os.path.join(ROOT, "scripts", "minions.py")
        with tempfile.TemporaryDirectory(dir=TMP) as alone:
            copy = os.path.join(alone, "minions.py")
            shutil.copy(src, copy)
            for argv in (["--help"], ["uninstall", "--help"]):
                p = subprocess.run([sys.executable, "-I", copy, *argv], cwd=alone, capture_output=True, text=True,
                                   timeout=60)
                self.assertEqual(p.returncode, 0, p.stderr)
            spec = importlib.util.spec_from_file_location("minions_copy", copy)
            M2 = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(M2)
            self.assertEqual(M2.TOOL_COPY, "/usr/local/lib/windstorm-repo-minions/minions.py")
            gh, world, host = remove_setup()
            out = io.StringIO()
            code = M2.uninstall(None, everything=True, host=host, gh=gh, out=out, interactive=True)
            lines = out.getvalue().splitlines()
            self.assertEqual(code, M.EXIT_NEED, lines)  # to the end: only the put-back pull request is the person's
            self.assertTrue(lines[-1].startswith("ok census:"), lines[-3:])
            self.assertIsNone(gh.runner)
        with open(os.path.join(ROOT, "skills", "repo-minions", "SKILL.md"), encoding="utf-8") as f:
            skill = " ".join(f.read().split())
        self.assertIn("plugin uninstall", skill)
        self.assertIn(f"`python3 -I {M.TOOL_COPY} uninstall --all`", skill)
        # The release branch's README says it too (main's says nothing can be installed yet).
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            readme = " ".join(f.read().split())
        if "nothing can be installed yet" not in readme:
            self.assertIn("plugin uninstall", readme)
            self.assertIn(f"python3 -I {M.TOOL_COPY} uninstall --all", readme)

    def test_G6_1_uninstall_takes_a_runner_away_on_both_sides(self):
        gh, world, host = remove_setup(busy=(True, True, True, False))
        code, lines = run_uninstall(gh, host, wait_s=600, poll_s=10)
        self.assertEqual(code, M.EXIT_OK, lines)
        # Drained first: held, waited for the running job, then nothing ran when the runner was stopped.
        self.assertTrue(any(x.startswith("ok held:") for x in lines), lines)
        self.assertTrue(any("a job is running" in x and "at the latest" in x for x in lines), lines)
        self.assertIsNone(gh.runner)
        for p in (FOLDER, f"/etc/systemd/system/{UNIT}", f"/etc/systemd/system/{UNIT}.d", M.hook_dir(REPO),
                  M.records_dir_of(FOLDER)):
            self.assertNotIn(p, world.paths)
        self.assertNotIn(REPO, M.load_state(host).get("runners", {}))
        # The token reached the runner's removal only on standard input; never on a command line or in the output.
        self.assertFalse(any(RM_TOKEN in " ".join(a) for a in host.ran))
        self.assertIn(RM_TOKEN + "\n", [i for a, i in host.fed if M.REMOVE_SCRIPT in a])
        self.assertFalse(any(RM_TOKEN in x for x in lines))
        with open(os.path.join(host.state_dir(), "journal.log")) as f:
            j = f.read()
        self.assertNotIn(RM_TOKEN, j)
        for step in ("remove ", "hold ", "stop ", "remove unit ", "config.sh remove", "delete "):
            self.assertIn(step, j)
        self.assertNotIn("svc.sh", j)  # R127: root never runs the runner's own scripts
        # A job still running at the deadline: `waiting`, exit 4, the next command named, nothing taken away.
        gh, world, host = remove_setup(busy=(True,))
        code, lines = run_uninstall(gh, host, wait_s=60, poll_s=10)
        self.assertEqual(code, M.EXIT_WAIT)
        self.assertTrue(lines[-1].startswith("waiting") and "`uninstall example-org/example-repo` again" in lines[-1])
        self.assertIsNotNone(gh.runner)
        self.assertIn(FOLDER, world.paths)

    def test_G6_1_an_error_answer_from_github_is_never_taken_for_gone(self):
        # Seen: GitHub answered errors (HTTP 502) while it still listed our runner, and an uninstall that took an
        # error for "not listed" deleted everything here and left the runner on GitHub. From any read on, errors:
        # nothing here is deleted while GitHub lists the runner, and the line says why.
        class ErrorsFrom(RemoveGH):
            def __init__(self, n):
                super().__init__({".github/workflows/ci.yml": MOVED_CI})
                self.n, self.reads = n, 0

            def api(self, method, path, body=None):
                if method == "GET" and path.endswith("/actions/runners?per_page=100"):
                    self.reads += 1
                    if self.reads >= self.n:
                        return 502, {"message": "Server Error"}
                return super().api(method, path, body)
        for n in range(1, 8):
            gh, world, host = remove_setup()
            gh = ErrorsFrom(n)
            world.gh = gh
            code, lines = run_uninstall(gh, host, wait_s=60, poll_s=10)
            if gh.runner is not None:
                self.assertNotEqual(code, M.EXIT_OK, (n, lines))
                self.assertIn(FOLDER, world.paths, (n, lines))
                self.assertFalse(any("no longer lists" in x for x in lines), (n, lines))
                self.assertIn("could not be read", lines[-1], (n, lines))
        # status: an error answer is said as not read, never as "GitHub no longer lists our runner".
        host, clock = g5_host()

        class Unread(StatusGH):
            def api(self, method, path, body=None):
                return (502, None) if "/actions/runners" in path else super().api(method, path, body)
        lines = run_cmd(M.status, REPO, host=host, gh=Unread())[1]
        self.assertFalse(any("no longer lists" in x for x in lines), lines)
        self.assertIn("GitHub could not be read for example-org/example-repo's runners now (HTTP 502)", lines[0])

    def test_G6_6_uninstall_runs_as_the_runner_and_github_goes_first(self):
        gh, world, host = remove_setup()
        self.assertEqual(run_uninstall(gh, host, everything=True)[0], M.EXIT_NEED)  # the put-back PR is the person's
        # The runner's own removal ran as the runner user, after its service note and unit were taken out by this
        # tool (it refuses while the note is there; R127: never through svc.sh) and before any folder was deleted;
        # the runner user went last.
        o = world.order
        self.assertLess(o.index(f"rm {FOLDER}/.service"), o.index("config.sh remove"))
        self.assertLess(o.index(f"rm /etc/systemd/system/{UNIT}"), o.index("config.sh remove"))
        first_delete = min(i for i, x in enumerate(o) if x.startswith(("rm ", "empty ")) and not x.endswith(
            (".service", UNIT, ".d")))
        self.assertLess(o.index("config.sh remove"), first_delete)
        self.assertEqual(o[-1], "userdel")
        removal = next(a for a in host.ran if M.REMOVE_SCRIPT in a)
        self.assertEqual(removal[:5], ("sudo", "-n", "-u", "minions-runner", "--"))
        # Nothing the person runs enters the runner's home: every command that names it goes through sudo.
        for a in host.ran:
            if any("/home/minions-runner" in x for x in a):
                self.assertIn(a[0], ("sudo", "pgrep"), a)  # pgrep only lists processes by their path
        # The runner's own removal cannot run: GitHub's API deletes the runner, read back, before anything else.
        gh, world, host = remove_setup(own_removal_works=False)
        code, lines = run_uninstall(gh, host)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn(("DELETE", f"repos/{REPO}/actions/runners/7", None), gh.writes)
        self.assertTrue(any("removed through GitHub's API" in x and "read back" in x for x in lines), lines)
        # GitHub refuses both: nothing on this computer is deleted.
        gh, world, host = remove_setup(own_removal_works=False)
        gh.api_real = gh.api
        gh.api = lambda m, p, b=None: (403, {"message": "no"}) if m == "DELETE" else gh.api_real(m, p, b)
        code, lines = run_uninstall(gh, host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("nothing on this computer is deleted yet", lines[-1])
        self.assertIn(FOLDER, world.paths)
        # only the service is taken out before GitHub's side (the runner's removal refuses while it is there)
        self.assertFalse(any(x.startswith(("rm ", "empty ", "rmdir ")) and not x.endswith((".service", ".d"))
                             for x in world.order), world.order)

    def test_G6_5_uninstall_stops_the_slice_before_deleting_it(self):
        gh, world, host = remove_setup()
        code, lines = run_uninstall(gh, host, everything=True)
        o = world.order
        self.assertLess(o.index(f"stop {M.SLICE}"), o.index(f"rm {SLICE_FILE}"))
        self.assertNotIn(M.SLICE, world.loaded)
        self.assertTrue(any("was stopped before its file was deleted, and is gone, read back" in x for x in lines))
        # A slice still loaded from its file after its removal is a `stop`, never "gone".
        gh, world, host = remove_setup()
        host._answer = (lambda real: lambda a, i=None: (0, f"LoadState=loaded\nActiveState=inactive\nFragmentPath={SLICE_FILE}\n")
                        if list(a[:3]) == ["systemctl", "show", M.SLICE] else real(a, i))(world.answer)
        code, lines = run_uninstall(gh, host, everything=True)
        self.assertEqual(code, M.EXIT_STOP)
        # And one still active, too.
        gh, world, host = remove_setup()
        host._answer = (lambda real: lambda a, i=None: (0, "LoadState=loaded\nActiveState=active\n")
                        if list(a[:3]) == ["systemctl", "show", M.SLICE] else real(a, i))(world.answer)
        code, lines = run_uninstall(gh, host, everything=True)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn(f"{M.SLICE} is still loaded or active after its removal", lines[-1])

    def test_G6_2_uninstall_all_leaves_nothing_and_offers_the_workflows_back(self):
        gh, world, host = remove_setup()
        state = host.state_dir()
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_NEED, lines)
        self.assertEqual(world.paths, set())
        self.assertFalse(world.user)
        self.assertFalse(world.loaded)
        self.assertFalse(os.path.exists(state))
        # One pull request puts the moved line back exactly as it was, comment kept; the tool never merges it.
        self.assertEqual(gh.blobs(), [CI_YML])
        pr = next(b for m, p, b in gh.writes if p.endswith("/pulls"))
        self.assertEqual(pr["title"], "Run these jobs where they ran before")
        self.assertTrue(pr["head"].startswith(M.PUT_BACK_PREFIX))
        self.assertIn("runs-on: ubuntu-latest   # the person's own comment", pr["body"])
        self.assertFalse(any(p.endswith("/merge") for m, p, b in gh.writes))
        self.assertTrue(any(x.startswith("need you: read and merge pull request #12 yourself") for x in lines), lines)
        # A move recorded before its lines were kept: named, never guessed.
        gh, world, host = remove_setup()
        d = M.load_state(host)
        d["moves"][REPO]["files"][".github/workflows/ci.yml"][0].pop("runs_on")
        M.save_state(host, d)
        code, lines = run_uninstall(gh, host, everything=True)
        self.assertTrue(any("was not recorded" in x and "put it back by hand" in x for x in lines), lines)
        self.assertEqual(gh.blobs(), [])

    def test_G6_13_the_census_names_the_folder_the_skill_was_downloaded_into(self):
        # Seen 2026-11-23 (run J, row R117): the census said nothing of the clone, and the agent filled the silence
        # by offering to delete it. After `uninstall --all` the census names it: there and whose, gone, or not known.
        # R161 (rule 29 (e)): a plugin is named by its name and what Claude Code's own list says, never a version path.
        clone = "/home/person/windstorm-repo-minions"
        lst = "/home/you/.claude/plugins/installed_plugins.json"
        plugin = {"plugin": "repo-minions@the-catalog", "list": lst}
        row = json.dumps({"version": 2, "plugins": {"repo-minions@the-catalog": [{"version": "0.1.1"}]}})
        for src, files, there, said in (
                ({"clone": clone}, {}, True, f"ok census: the folder this skill was downloaded into: {clone}, still "
                                             "there; it is yours to keep or delete, and this tool does not touch it"),
                ({"clone": clone}, {}, False, f"ok census: the folder this skill was downloaded into ({clone}): gone"),
                (plugin, {lst: row}, None, "ok census: the skill repo-minions@the-catalog: still installed in Claude "
                                           "Code (its own list names it); Claude Code's own `plugin uninstall` takes it"),
                (plugin, {lst: json.dumps({"version": 2, "plugins": {}})}, None,
                 "ok census: the skill repo-minions@the-catalog: Claude Code's own list names it nowhere"),
                (plugin, {}, None, "warn census: whether the skill repo-minions@the-catalog is still installed in "
                                   f"Claude Code: not read (its own list, {lst}, could not be read)"),
                ({}, {}, None, "not known on this computer")):
            gh, world, host = remove_setup()
            d = M.load_state(host)
            d["skill_from"] = src
            d.pop("skill_folder", None)
            M.save_state(host, d)
            host.files.update(files)
            if there:
                host.dirs[clone] = ["skills"]
            code, lines = run_uninstall(gh, host, everything=True)
            line = next(x for x in lines if "census: the folder this skill" in x or "census: the skill " in x
                        or "census: whether the skill" in x)
            self.assertIn(said, line)
            self.assertNotIn("/plugins/cache/", line)
            self.assertTrue(lines[-1].startswith("ok census: your record"), lines[-2:])  # the record stays last
            self.assertNotIn("rm ", line)  # no delete command is printed for it

    def test_G6_26_nothing_setup_writes_names_a_plugin_cache_version_folder(self):
        # R161 (family rule 29 (a), 2026-11-24: AN ANSWER TO (a) IS THE TEST'S OUTCOME, NEVER A READING). The skill
        # copied into a made-up plugins/cache/<catalog>/<plugin>/<version>/ folder, setup run from there; every
        # value of every record and every file setup wrote is read, byte by byte, for that folder's path, and for
        # folders another updater owns (rule 29 (f) (iii): Xcode's, the command line tools', Homebrew's Cellar).
        base = tempfile.mkdtemp(dir=TMP)
        version = os.path.join(base, "plugins", "cache", "the-catalog", "repo-minions", "0.1.0")
        os.makedirs(os.path.join(version, "scripts"))
        planted = os.path.join(version, "scripts", "minions.py")
        shutil.copyfile(M.__file__, planted)
        host, sha = setup_host()
        fresh_look(host)
        with mock.patch.object(M, "__file__", planted):
            code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        # what setup wrote: every file (the tool's own copy aside: it is this program), every text given to a root
        # write on its standard input, and every file in the tool's state folder on disk
        written = [(f"file {k}", v) for k, v in host.files.items() if k != M.TOOL_COPY]
        written += [("stdin of " + " ".join(a), i) for a, i in host.fed if i]
        for root, _, names in os.walk(host.state_dir()):
            for n in names:
                with open(os.path.join(root, n), "rb") as f:
                    written.append((f"kept {n}", f.read().decode("utf-8", "replace")))
        self.assertTrue(any(w.startswith("stdin of sudo -n tee /etc/systemd/system/") for w, _ in written), written)
        refused = (version, "/plugins/cache/", "Xcode.app/Contents/Developer", "CommandLineTools/usr/bin", "/Cellar/")
        hits = [(w, r) for w, text in written for r in refused if r in str(text)]
        self.assertEqual(hits, [])
        self.assertEqual(M.load_state(host)["skill_from"],
                         {"plugin": "repo-minions@the-catalog", "list": os.path.join(base, "plugins", "installed_plugins.json")})

    def test_G6_3_uninstall_ends_with_a_census(self):
        gh, world, host = remove_setup()
        code, lines = run_uninstall(gh, host)
        census = [x for x in lines if "census:" in x]
        self.assertIn(f"ok census: runners on GitHub for {REPO}: none", census)
        self.assertIn("ok census: runner services: none", census)
        # R130: the last runner went, so its user went with it, read back
        self.assertIn("ok census: the runner user: none", census)
        self.assertFalse(world.user)
        self.assertTrue(any(x.startswith("ok census: the slice and the watch") and "kept" in x for x in census))
        gh, world, host = remove_setup()
        code, lines = run_uninstall(gh, host, everything=True)
        census = [x for x in lines if "census:" in x]
        self.assertEqual(len(census), 9, census)  # G6.13: the skill's own folder is named too (here: not known);
        # R139: and the system packages added while the runner's dependency script ran (here: none)
        self.assertTrue(all(x.startswith("ok census:") and x.endswith(": none") for x in census[:-2]), census)
        self.assertTrue(census[-2].startswith("warn census: the folder this skill was downloaded into"), census)
        # Rule 8: the census is the last thing printed; it names this tool's state folder, and last the person's
        # record, kept (G6.10).
        self.assertIn("ok census: this tool's state folder: none", census)
        self.assertTrue(lines[-1].startswith("ok census: your record of what this tool did:"), lines[-3:])
        # Something left is named as a `warn`, never "none".
        gh, world, host = remove_setup()
        gh.runner = dict(gh.runner, name="someone-elses-runner")
        world.user = True
        M.census(gh, host, [REPO], True, M.Report(io.StringIO()))
        out = io.StringIO()
        M.census(gh, host, [REPO], True, M.Report(out))
        self.assertIn(f"warn census: runners on GitHub for {REPO}: someone-elses-runner (not ours)", out.getvalue())
        self.assertIn("warn census: the runner user: minions-runner", out.getvalue())

    def test_G6_4_uninstall_again_finishes_an_interrupted_removal(self):
        # Cut right after the GitHub side was removed, at the first delete.
        gh, world, host = remove_setup(interrupt_on=lambda a: a[:7] == ["sudo", "-n", "-u", "minions-runner", "--", "rm", "-rf"])
        code = M.main(["uninstall", "--all"], host=host, out=io.StringIO(), gh=gh)
        self.assertEqual(code, M.EXIT_INTERRUPTED)
        self.assertIsNone(gh.runner)
        self.assertIn(REPO, M.load_state(host)["runners"])
        before = list(world.order)
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_NEED, lines)
        again = world.order[len(before):]
        self.assertNotIn("config.sh remove", again)  # a step already done is not run again, and does not fail
        self.assertNotIn("svc.sh uninstall", again)
        self.assertTrue(any(f"GitHub no longer lists {OURS} for {REPO}" in x for x in lines), lines)
        self.assertEqual(world.paths, set())
        self.assertFalse(world.user)
        # And once more, with nothing left: it still ends well.
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertIn(code, (M.EXIT_OK, M.EXIT_NEED), lines)


    def test_G5_19_a_watch_pass_in_the_same_second_as_the_last_is_still_seen(self):
        # Seen in CI (2026-11-23): the watch's time is to the second, and a setup whose first pass fell in the same
        # second as the pass before read as "the watch's first pass wrote nothing". On a real computer the timer's
        # own pass can fall in that second too. Each pass is counted, and setup reads the count, never the time.
        host, sha = setup_host()
        t = M.dt.datetime.now(M.dt.timezone.utc).replace(microsecond=0)
        host.now = lambda: t  # this clock does not move: every pass below falls in one second
        fresh_look(host)
        listing = ("GET", f"repos/{REPO}/actions/runners?per_page=100")
        token = ("POST", f"repos/{REPO}/actions/runners/registration-token")
        gh = setup_gh(sha)
        gh.overrides[listing] = lambda body: runners_answer() if token in gh.calls else (200, {"total_count": 0,
                                                                                              "runners": []})
        code, lines = run_setup(gh, host)
        self.assertEqual(code, M.EXIT_OK, lines)
        first = M.load_state(host)["watch"]
        host.runs[R_TEST + (FOLDER,)] = (0, "")
        code, lines = run_setup(setup_gh(sha), host)  # the same second, by the clock above
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertFalse([x for x in lines if "wrote nothing" in x], lines)
        after = M.load_state(host)["watch"]
        self.assertEqual(after["time"], first["time"])
        self.assertEqual(after["pass"], first["pass"] + 1)

    def test_G2_12_a_setup_cut_part_way_is_found_again(self):
        # Seen on a stock VM (2026-11-23): sudo's permission ran out during the runner's configuration; `setup` again
        # stopped on the half-made folder and sent to `uninstall`, which said there was nothing to uninstall.
        host, sha = setup_host()
        fresh_look(host)
        config = ("sudo", "-n", "-u", "minions-runner", "--", *M.RUNNER_ENV, "bash", "-c", M.CONFIG_SCRIPT)
        good = host.runs[config]
        host.runs[config] = (1, "sudo: a password is required\n")
        listing = ("GET", f"repos/{REPO}/actions/runners?per_page=100")
        none = (200, {"total_count": 0, "runners": []})
        gh = setup_gh(sha, {listing: none})
        code, lines = run_setup(gh, host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn("run the same command again from the terminal where you typed the password", lines[-2])
        self.assertEqual(M.load_state(host)["setting_up"][REPO]["folder"], FOLDER)
        # `uninstall` finds it, never "nothing to uninstall", and takes it away, read back.
        out = io.StringIO()
        M.uninstall(REPO, host=host, gh=gh, out=out, interactive=True)
        lines = out.getvalue().splitlines()
        self.assertFalse([x for x in lines if "nothing to uninstall" in x], lines)
        self.assertIn(f"ok uninstalled: {REPO} has no runner on this computer now", lines)
        state = M.load_state(host)
        self.assertNotIn("setting_up", state)
        self.assertNotIn(REPO, state.get("runners") or {})
        # Cut again; then the same command again, as the stop says: setup takes the half-made runner away first,
        # read back, and ends set up.
        self.assertEqual(run_setup(setup_gh(sha, {listing: none}), host)[0], M.EXIT_STOP)
        # (Seen on the VM: an `uninstall` that stopped to ask for the password before it removed anything still
        # leaves the cut setup to be found by `setup` again.)
        sudo_true = host.runs[("sudo", "-n", "true")]
        host.runs[("sudo", "-n", "true")] = (1, "sudo: a password is required\n")
        self.assertEqual(M.uninstall(REPO, host=host, gh=gh, out=io.StringIO(), interactive=False), M.EXIT_NEED)
        host.runs[("sudo", "-n", "true")] = sudo_true
        host.runs[config] = good
        token = ("POST", f"repos/{REPO}/actions/runners/registration-token")
        gh = setup_gh(sha)
        gh.overrides[listing] = lambda body: runners_answer() if token in gh.calls else none
        code, lines = run_setup(gh, host)
        self.assertEqual(code, M.EXIT_OK, lines)
        cut = [i for i, x in enumerate(lines) if x.startswith(f"ok a setup or removal of {REPO} here was cut part-way")]
        gone = lines.index(f"ok uninstalled: {REPO} has no runner on this computer now")
        self.assertTrue(cut and cut[0] < gone, lines)
        state = M.load_state(host)
        self.assertNotIn("setting_up", state)
        self.assertEqual(state["runners"][REPO]["folder"], FOLDER)
        # A setup of a runner that is set up and whole is not taken away: it renews the watch's copy and touches
        # nothing else (G4.10).
        host.runs[R_TEST + (FOLDER,)] = (0, "")
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertTrue(lines[-1].endswith("the runner, its service, its limits and a running job were not touched"),
                        lines)
        self.assertFalse([x for x in lines if "cut part-way" in x or "uninstalled" in x], lines)
        self.assertIn(REPO, M.load_state(host)["runners"])
        # A read as root that sudo refused is said as not read, never taken for none.
        refuse_sudo(host)
        rep = M.Report(io.StringIO())
        M.census(gh, host, [REPO], False, rep)
        self.assertFalse([x for x in rep.lines if x.startswith("ok census: folders: none")], rep.lines)
        self.assertTrue([x for x in rep.lines if x.startswith("warn census: folders: not read, because admin rights "
                                                             "are not open in this terminal: ") and M.HOOKS_ROOT in x],
                        rep.lines)
        self.assertTrue(M.exists_as_root(host, FOLDER))  # a refused read never reads as gone


class G8(unittest.TestCase):
    def test_G8_15_below_its_python_floor_the_tool_stops_before_any_step(self):
        # Seen on a stock Ubuntu 20.04 VM (2026-11-23, Python 3.8): setup made the runner, then failed on a 3.9+
        # operator. Now every command stops first, nothing run, and the file still reads in 3.8's grammar.
        import ast
        from unittest import mock
        src = open(M.__file__).read()
        ast.parse(src, feature_version=(3, 8))
        host = FakeHost()
        out = io.StringIO()
        with mock.patch.object(M.sys, "version_info", (3, 8, 10, "final", 0)):
            code = M.main(["look", REPO], host=host, out=out)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue(out.getvalue().startswith("stop this tool needs Python 3.10 or newer, and this computer's "
                                                  "python3 is 3.8.10; nothing was changed."), out.getvalue())
        self.assertIn("Ubuntu 22.04 or newer, Debian 12 or newer", out.getvalue())
        self.assertEqual(host.ran, [])
        self.assertNotIn(" | {", src.replace(" ", " "))  # no dict union operator (3.9+) in the tool

    def test_G8_4_doctor_checks_install_and_finishes_half_done_steps(self):
        # Every promise of G2 and G4 read back as kept, one line each, and nothing changed.
        host, sha = doctor_world()
        host.ran.clear()
        code, lines = run_doctor(host, sha)
        self.assertEqual(code, M.EXIT_OK, lines)
        for want in ("is in its own group only, no admin rights", "the credential files are 600",
                     "the boot service is enabled, in minions.slice", "the limits read back: at most 4 processors and 60 % of",
                     "the job hooks are root's and read-only to the runner", "free for the work folders, over the",
                     "runner version 2.338.2", "both sides: GitHub lists minions-box-1 online, idle",
                     "the watch's timer is active"):
            self.assertTrue(any(want in x for x in lines), (want, lines))
        self.assertEqual(lines[-1], "ok every promise of the install read back as kept")
        changed = [a for a in admin_steps(host) if a[2] not in ("stat", "test", "cat", "-u")]
        self.assertEqual(changed, [], "doctor changes nothing it was not asked to finish")
        # The swap cap is read back too: jobs given swap again (by hand, or by an older setup) is said, by name.
        h2, sha2 = doctor_world()
        show = h2.runs[("systemctl", "show", "minions.slice")][1]
        h2.runs[("systemctl", "show", "minions.slice")] = (0, show.replace("MemorySwapMax=0", "MemorySwapMax=infinity"))
        _, l2 = run_doctor(h2, sha2)
        self.assertTrue(any("limits read MemorySwapMax='infinity'" in x for x in l2), l2)
        # A promise not kept: named with its way on; the last line counts them, exit 2; still nothing changed.
        host.runs[("sudo", "-n", "-u", "minions-runner", "--", "stat", "-c", "%a %U %n")] = (0, f"644 minions-runner {FOLDER}/.credentials\n"
                                                                    f"600 minions-runner {FOLDER}/.runner\n"
                                                                    f"600 minions-runner {FOLDER}/.credentials_rsaparams\n")
        host.runs[("id", "-nG", "minions-runner")] = (0, "minions-runner docker\n")
        host.ran.clear()
        code, lines = run_doctor(host, sha)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertTrue(any("credential files are not 600" in x for x in lines), lines)
        self.assertTrue(any("minions-runner is in other groups (docker)" in x for x in lines), lines)
        self.assertTrue(lines[-1].startswith(f"stop 2 promise(s) not kept here: {REPO} runner user, {REPO} credential "
                                             "files. Nothing was changed"), lines)
        self.assertFalse([a for a in admin_steps(host) if a[2] in ("chmod", "gpasswd", "rm")])
        # No admin rights open and no terminal to ask: what only an admin reads is "not read", never "kept".
        host, sha = doctor_world()
        refuse_sudo(host)
        code, lines = run_doctor(host, sha)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertTrue(lines[0].startswith("warn admin rights are not open here and no terminal can ask"), lines)
        self.assertIn(f"warn {REPO}: the credential files: not read", lines)
        self.assertIn(f"warn {REPO}: the job hooks: not read", lines)
        self.assertFalse([x for x in lines if "credential files are 600" in x], lines)
        self.assertEqual(lines[-1], "ok every promise of the install read back as kept, of those that could be read")
        # Nothing of ours here: nothing to check.
        code, lines = run_doctor(setup_host()[0], sha)
        self.assertEqual((code, lines), (M.EXIT_OK, [f"ok no runner of ours serves {REPO} on this computer: nothing "
                                                     "to check"]))

    def test_G8_3_commands_and_line_kinds_are_exactly_these(self):
        # The commands built so far are the plan's own, in its order (a subsequence of it); more come with genes.
        plan = ("look", "setup", "move", "lint", "status", "watch", "hold", "resume", "drain", "doctor", "uninstall",
                "self-update")
        sub = next(a for a in M.parser()._actions if isinstance(a, M.argparse._SubParsersAction))
        self.assertEqual(tuple(sub.choices), M.COMMANDS)
        self.assertTrue(set(M.COMMANDS) <= set(plan), M.COMMANDS)
        self.assertEqual(list(M.COMMANDS), sorted(M.COMMANDS, key=plan.index))
        # Six line kinds; the worst decides the code: 0 ok/warn/tell, 2 stop, 3 need you, 4 waiting.
        self.assertEqual(M.LINE_KINDS, ("ok", "warn", "stop", "need you", "waiting", "tell the person"))
        cases = [((), 0), (("warn",), 0), (("tell",), 0), (("wait",), 4), (("wait", "need"), 3),
                 (("wait", "need", "stop"), 2)]
        for kinds, code in cases:
            rep = M.Report(io.StringIO())
            rep.ok("x")
            for k in kinds:
                getattr(rep, k)("x")
            self.assertEqual(rep.code(), code, kinds)
            for line in rep.lines:  # a block's head and foot lines (G7.16) begin with -----
                self.assertTrue(any(line.startswith(k) for k in (*M.LINE_KINDS, "----- ")), line)
        # P1a: every need-you line is exit 3, also one that lets the steps after it run (an agent trusting exit
        # codes went on past an ask); only a password typed at once, in this same run, is not an open ask.
        for kw, code in (({"blocks": False}, M.EXIT_NEED), ({"blocks": False, "answered_here": True}, M.EXIT_OK)):
            rep = M.Report(io.StringIO())
            rep.need("x", **kw)
            rep.ok("y")
            self.assertEqual(rep.code(), code, kw)
        # The whole table: every way a command ends has its own code.
        self.assertEqual((M.EXIT_OK, M.EXIT_FAILED, M.EXIT_STOP, M.EXIT_NEED, M.EXIT_WAIT, M.EXIT_USAGE,
                          M.EXIT_INTERRUPTED), (0, 1, 2, 3, 4, 64, 130))
        # 64: typed wrong (an unknown command, a missing name, an unknown option), never 2.
        for argv in (["nonsense"], ["look"], ["setup", "o/r", "--no-such-option"], []):
            with mock.patch("sys.stderr", io.StringIO()) as err, self.assertRaises(SystemExit) as e:
                M.main(argv, host=linux_host())
            self.assertEqual(e.exception.code, 64, argv)
            self.assertIn("typed wrong", err.getvalue())
        # A command's own code passes through.
        host = linux_host()
        with mock.patch.object(M, "look", return_value=M.EXIT_NEED):
            self.assertEqual(M.main(["look", REPO], host=host, out=io.StringIO()), 3)
        # 1: the tool failed. One stop line names the error and the journal's last step.
        M.journal(host, "register", "o/r as box-1")
        out = io.StringIO()
        with mock.patch.object(M, "setup", side_effect=RuntimeError("a stand-in surprise")):
            self.assertEqual(M.main(["setup", REPO], host=host, out=out), 1)
        line = out.getvalue().strip()
        self.assertTrue(line.startswith("stop the tool itself failed: RuntimeError: a stand-in surprise"), line)
        self.assertIn("The journal's last step: register o/r as box-1", line)
        # 130: interrupted. The journal says which step it was on.
        out = io.StringIO()
        with mock.patch.object(M, "move", side_effect=KeyboardInterrupt):
            self.assertEqual(M.main(["move", REPO], host=host, out=out), 130)
        self.assertIn("interrupted by you during: register o/r as box-1", out.getvalue())
        with open(os.path.join(host.state_dir(), "journal.log"), encoding="utf-8") as f:
            self.assertIn("interrupted by you, during: register o/r as box-1", f.read().splitlines()[-1])

    def test_G8_5_no_system_call_waits_on_a_pager(self):
        host, sha = setup_host()
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        lh = linux_host(which=("gh", "sudo", "systemctl", "docker"))
        look(StandIn(load("look-recent-runs.jsonl")), lh)
        calls = [a for a in host.ran + lh.ran if "systemctl" in a or "journalctl" in a]
        self.assertTrue(calls)
        for a in calls:
            self.assertIn("--no-pager", a, a)
        # And in the source: systemctl and journalctl are only ever called through the two helpers.
        with open(M.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertEqual(re.findall(r'\[\s*"(?:systemctl|journalctl)"', src), ['["systemctl"', '["journalctl"'])


class BlindGH(StatusGH):
    """GitHub as a watch sees it when `gh` cannot be run: no HTTP answer at all, and why."""

    def __init__(self, why="the GitHub CLI (gh) could not be found with this PATH: /usr/bin:/bin", **kw):
        super().__init__(**kw)
        self.blind_why = why

    def api(self, method, path, body=None):
        self.calls.append((method, path))
        self.why = self.blind_why
        return 0, {"message": self.blind_why}


def hook_run(folder, seen_value, wait_s=0, event=None):
    """Runs the job-started hook for real, with a stand-in for its sleep locks; returns (exit, its output).
    `event`: GitHub's record of the event, as the runner gives it to the job (None: none given)."""
    seen = fresh_seen(folder, seen_value)
    recs = os.path.join(folder, "records")
    os.makedirs(recs, exist_ok=True)
    text = M.job_started_script(folder, recs, seen=seen, hold_wait_s=wait_s).replace("command -v systemd-inhibit",
                                                                                     "false")
    path = os.path.join(folder, "start.sh")
    with open(path, "w") as f:
        f.write(text)
    env = dict(no_event(), PATH=machine_guard.path("/usr/bin:/bin"), GITHUB_RUN_ID="77", GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="j")
    if event is not None:
        env["GITHUB_EVENT_PATH"] = os.path.join(folder, "event.json")
        with open(env["GITHUB_EVENT_PATH"], "w") as f:
            json.dump(event, f)
    r = subprocess.run(["bash", path], env=env, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


class P1a(unittest.TestCase):
    """What the two cold starts (P1a, 2026-11-22) found: each fix and its test."""

    def test_G4_9_the_delay_said_is_the_one_this_computer_gives(self):
        # R48: the words name logind's own delay, read without admin rights, never a fixed number.
        def move_words(host):  # G7.14: look reads the delay; move says it, from look's record of this computer
            lines = look(StandIn(load("look-recent-runs.jsonl")), host)[1]
            survey = next(iter(M.load_state(host)["looks"].values()))["survey"]
            rep = M.Report(io.StringIO())
            M.before_move_words(survey, rep)
            rep.code()
            return lines, " ".join(tells_of(rep.lines))
        host = linux_host(runs={tuple(M.LOGIND_DELAY): (0, "t 30000000\n")})
        lines, said = move_words(host)
        self.assertIn("ok a sleep asked while a job runs is put off by at most 30 s (logind's delay)", lines)
        self.assertIn("still puts it to sleep, about 30 seconds later (this computer's own delay).", said)
        self.assertFalse([a for a in host.ran if a[:1] == ("sudo",) and "busctl" in a])
        for answer, words in (((1, "Failed to get property\n"), "a little later (how much later could not be read on "
                                                                  "this computer)"),
                              ((0, "t 0\n"), "still puts it to sleep, at once.")):
            self.assertIn(words, move_words(linux_host(runs={tuple(M.LOGIND_DELAY): answer}))[1])
        self.assertIn("warn the delay a sleep gets while a job runs: not read",
                      look(StandIn(load("look-recent-runs.jsonl")), linux_host())[1])

    def test_G7_10_look_prints_what_to_tell_the_person_before_setup(self):
        code, lines = look(StandIn(load("look-recent-runs.jsonl")), linux_host())
        tells = tells_of(lines)
        # One block, numbered 1 of N to N of N, so a line dropped or merged in a relay shows (run B again).
        self.assertEqual([TELL.match(x).groups() for x in lines if TELL.match(x)],
                         [(str(i), str(len(tells))) for i in range(1, len(tells) + 1)])
        said = " ".join(tells)
        for words in ("Anyone who can push to example-org/example-repo can run code on this computer",
                      "This is for a private repository only",
                      "This version serves one repository per computer",
                      M.COST_POINTER):
            self.assertIn(words, said)
        # G7.14 (R111): what matters only once jobs move, or once one is stopped at the cap, is said there instead.
        for later in ("your checks wait", "A restart of this computer", M.JOB_TOOLS, M.MEMORY_TRADE_OFF):
            self.assertNotIn(later, said)
        limits = next(x for x in lines if x.startswith("ok jobs here get at most"))
        self.assertIn(limits[len("ok "):], tells)  # the limits, word for word as look printed them
        self.assertFalse(any(x.startswith("This computer has a battery") for x in tells))  # no laptop sentence
        # A laptop: its sentence is said too.
        host = linux_host(files={"/sys/class/power_supply/BAT0/type": "Battery\n"},
                          dirs={"/sys/class/power_supply": ["BAT0"]})
        lines = look(StandIn(load("look-recent-runs.jsonl")), host)[1]
        self.assertIn("This computer has a battery: " + M.LAPTOP_WORDS + ".", tells_of(lines))
        # They come after the reading, all together, so an agent says them before setup.
        first = min(i for i, x in enumerate(lines) if TELL.match(x))
        self.assertTrue(all(TELL.match(x) for x in lines[first:-1]), lines[first:])
        self.assertTrue(lines[first - 1].startswith("----- ") and lines[-1].startswith("----- end"), lines)  # G7.16

    def test_G7_13_the_block_points_to_githubs_page_for_cost_and_claims_none(self):
        # Seen 2026-11-23 (row R109): a cold start relayed the block's cost sentence as "lists self-hosted runners as
        # free", dropping its date, the storage clause and the source. The block now says whose it is to say and
        # where to read; look's own line keeps the sentence read, with its date.
        host = linux_host()
        lines = look(StandIn(load("look-recent-runs.jsonl")), host)[1]
        tells = tells_of(lines)
        self.assertEqual([x for x in tells if "GitHub" in x and "charge" in x], [M.COST_POINTER])
        self.assertIn(M.COST_URL, M.COST_POINTER)
        said = " ".join(tells).lower()
        for never in ("free", "billing page, read", "does not charge", "cheaper", "no bill", "postponed"):
            self.assertNotIn(never, said)
        self.assertEqual(len([x for x in lines if x.startswith("ok GitHub's billing page, read ")]), 1, lines)

    def test_G7_15_setup_and_uninstall_show_the_persons_lines_and_wait_for_a_key(self):
        # Rule 7 (2026-11-23, row R112): in run I the smallest model reworded a five-, a three- and a one-line block,
        # so the lines a person must hear before an act are shown by the command the person runs, in their own
        # terminal, before anything changes, and it goes on only when they press Enter.
        host, sha = setup_host()
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        head = next(i for i, x in enumerate(lines) if x.startswith("----- read these "))
        tells = []
        for x in lines[head + 1:]:
            if not TELL.match(x):
                break
            tells.append(x)
        self.assertEqual(lines[head], f"----- read these {len(tells)} lines before anything changes -----")
        self.assertEqual([TELL.match(x).groups() for x in tells],
                         [(str(i), str(len(tells))) for i in range(1, len(tells) + 1)])
        said = " ".join(TELL.sub("", x) for x in tells)
        limits = next(x for x in lines if x.startswith("ok jobs here get at most"))[len("ok "):]
        for words in ("Anyone who can push to example-org/example-repo can run code on this computer",
                      "This is for a private repository only", limits, M.MEMORY_BEFORE_SETUP,
                      "While this computer is off, asleep or held, your checks wait",
                      "A restart of this computer cancels the job running on it", M.JOB_TOOLS,
                      "This version serves one repository per computer", M.ADMIN_MAKES, M.LEAVING_KEEPS,
                      M.COST_POINTER):
            self.assertIn(words, said)
        after = lines[head + 1 + len(tells)]
        self.assertTrue(after.startswith("need you: read the lines above, then press Enter here"), after)
        self.assertEqual(host.read_enters, 1)
        self.assertLess(head, min(i for i, x in enumerate(lines) if x.startswith("ok admin rights")))
        # No Enter: nothing changed, no admin step, the password never asked.
        host, sha = setup_host(runs={("sudo", "-n", "true"): (1, "sudo: a password is required")})
        fresh_look(host)
        host.enter = False
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual((code, host.validated, admin_steps(host)), (M.EXIT_STOP, 0, []), lines)
        self.assertIn("nothing was changed", lines[-1])
        self.assertNotIn(REPO, M.load_state(host).get("runners") or {})
        # Where nobody can press a key (an agent's shell): nothing shown, nothing changed, the person's command printed
        # exactly, with the flags they gave.
        host, sha = setup_host()
        fresh_look(host)
        out = io.StringIO()
        code = M.setup(REPO, host=host, gh=setup_gh(sha), out=out, interactive=False, limits=(2, 50))
        lines = out.getvalue().splitlines()
        self.assertEqual((code, host.read_enters, admin_steps(host)), (M.EXIT_NEED, 0, []), lines)
        self.assertFalse([x for x in lines if TELL.match(x)], lines)
        self.assertIn(f"in your own terminal: run `python3 -I {M.tool_path()} setup {REPO} --limits 2,50` there",
                      lines[-1])
        # uninstall: what it takes, that the record is kept, the put-back pull request; Enter, or nothing changes.
        gh, world, host = remove_setup()
        host.enter = False
        code, lines = run_uninstall(gh, host, None, everything=True)
        self.assertEqual(code, M.EXIT_STOP, lines)
        said = " ".join(tells_of(lines))
        for words in ("This takes off this computer every runner of this tool", "Your record of what this tool did "
                      "is kept in", "Then it opens a pull request that puts each moved job back"):
            self.assertIn(words, said)
        self.assertIn(REPO, M.load_state(host).get("runners") or {})
        self.assertEqual(admin_steps(host), [])
        out = io.StringIO()
        self.assertEqual(M.uninstall(None, everything=True, host=host, gh=gh, out=out, interactive=False), M.EXIT_NEED)
        self.assertIn(f"run `python3 -I {M.tool_path()} uninstall --all` there", out.getvalue().splitlines()[-1])

    def test_G7_15_each_line_on_the_persons_screen_is_one_sentence_and_the_count_is_bound(self):
        # The owner's word (2026-11-23): twelve lines is a lot to read, so each is one plain sentence, and the count
        # grows only with a row: eleven, twelve on a laptop.
        lim = M.slice_limits({"cores": 1, "mem": 8 * 1024 ** 3})  # the longest limits line, one processor
        for s, n in (({"battery": False}, 11), ({"battery": True}, 12), ({"lid": True}, 12)):
            lines = M.setup_lines(REPO, {**s, "sleep_delay": 30}, lim)
            self.assertEqual(len(lines), n, lines)
            gh, world, host = remove_setup()
            for every in (True, False):
                lines += M.uninstall_lines(host, REPO, every, M.load_state(host))
            for x in lines:
                self.assertIsNone(re.search(r"[.!?] +[A-Z`(]", x), f"more than one sentence: {x}")

    def test_G7_17_the_move_pull_request_opens_with_moves_lines(self):
        # The owner's ruling (2026-11-23, row R116): the page the person reads when they decide carries move's own
        # lines, word for word from one source, above anything else.
        host, gh = move_host(), MoveGH(G3.FILES)
        lines = run_move(gh, host)[1]
        body = next(b for m, p, b in gh.writes if m == "POST" and p.endswith("/pulls"))["body"]
        said = tells_of(lines)
        self.assertEqual(len(said), 3, lines)
        self.assertTrue(body.startswith("## Before you merge\n\n" + "".join(f"- {x}\n" for x in said)), body)
        self.assertLess(body.index(said[-1]), body.index("This pull request changes only"))

    def test_G7_16_a_relayed_block_has_a_head_and_a_foot_line(self):
        # The owner's ruling (2026-11-23, row R113): the instruction travels in the tool's own output, at the head
        # and the foot of each block an agent relays, not only in the skill file.
        for n in (1, 3):
            rep = M.Report(io.StringIO())
            rep.ok("before")
            for i in range(n):
                rep.tell(f"line {i + 1}")
            rep.ok("after")
            body = rep.lines[1:-1]
            self.assertEqual(len(body), n + 2, rep.lines)
            self.assertTrue(body[0].startswith(f"----- the {n} numbered line{'s' if n > 1 else ''} below go to the "
                                               "person whole and word for word, numbers included"), body[0])
            span = f"1 of {n} to {n} of {n}" if n > 1 else "1 of 1"
            self.assertEqual(body[-1], f"----- end of the lines for the person: paste {span} whole and unchanged; your "
                                       "own words come only after this line -----")
            self.assertEqual(rep.code(), M.EXIT_OK)
        # look's block, as the agent reads it
        lines = look(StandIn(load("look-recent-runs.jsonl")), linux_host())[1]
        first = min(i for i, x in enumerate(lines) if TELL.match(x))
        self.assertTrue(lines[first - 1].startswith("----- the "), lines[first - 1])
        self.assertTrue(lines[-1].startswith("----- end of the lines for the person"), lines[-1])

    def test_G7_14_each_line_for_the_person_is_said_at_the_stop_where_it_matters(self):
        # The owner's ruling (2026-11-23, rule 7, row R111): nine lines before setup were pasted whole in one small-model
        # run of three; what matters only once jobs move is move's own block, the last thing before the merge it asks
        # for; the memory cap's trade-off is said where status names a job the cap stopped.
        host, gh = move_host(), MoveGH(G3.FILES)
        lines = run_move(gh, host)[1]
        tells = [x for x in lines if TELL.match(x)]
        self.assertEqual([TELL.match(x).groups() for x in tells], [("1", "3"), ("2", "3"), ("3", "3")], lines)
        said = " ".join(tells_of(lines))
        for words in ("While this computer is off, asleep or held, your checks wait, and GitHub cancels a check that "
                      "has waited 24 hours.", "A restart of this computer cancels the job running on it", M.JOB_TOOLS):
            self.assertIn(words, said)
        last_tell = max(i for i, x in enumerate(lines) if TELL.match(x))
        self.assertEqual(last_tell, len(lines) - 3, lines)
        self.assertTrue(lines[-2].startswith("----- end of the lines for the person"), lines)  # G7.16
        self.assertTrue(lines[-1].startswith("need you: read and merge pull request #12 yourself"), lines)
        # status: a job the cap stopped is named, then the trade-off, as its own one-line block at the end
        t0 = int(M.parse_stamp("2026-11-22T22:40:00Z").timestamp())
        failed = {"conclusion": "failure", "steps": [{"name": "Build", "conclusion": "failure"}]}
        host, gh, _ = G5._named(G5(), f"start {t0} 555 1 0 check\nend {t0 + 70} 555 1 1 check\n", failed,
                                     notes=["Process completed with exit code 137."])
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertEqual(lines[-2], "tell the person 1 of 1: " + M.MEMORY_TRADE_OFF, lines)
        self.assertTrue(lines[-1].startswith("----- end of the lines for the person"), lines)
        self.assertNotIn("than that", M.MEMORY_TRADE_OFF)  # it stands alone now, away from the limits line

    def test_G7_12_the_tell_block_carries_only_what_the_person_must_hear_within_a_bound(self):
        # Seen 2026-11-23 (row R104): the smallest model kept all nine lines in meaning but reworded seven and lost
        # three clauses, each only good to know. What is only good to know lives in the README; the block is bound.
        for host in (linux_host(), linux_host(files={"/sys/class/power_supply/BAT0/type": "Battery\n"},
                                              dirs={"/sys/class/power_supply": ["BAT0"]})):
            tells = tells_of(look(StandIn(load("look-recent-runs.jsonl")), host)[1])
            self.assertLessEqual(len(tells), 6, tells)  # G7.14 (R111): six at most before setup, laptop included
            rest = [x for x in tells if x != M.COST_POINTER]  # G7.13 holds its words
            self.assertEqual(len(rest), len(tells) - 1, "the cost pointer is one line of the block")
            self.assertLessEqual(max(len(x) for x in rest), 420, rest)
            self.assertLessEqual(sum(len(x) for x in rest), 1700, rest)
            said = " ".join(tells)
            for never in ("next version", "(seen)", "GitHub's own computers come with"):
                self.assertNotIn(never, said)

    def test_G7_11_setup_limits_changes_an_installed_runner_in_place(self):
        # Seen on a stock VM (2026-11-23): the kill line named `setup --limits` the one way to change the limit, and
        # on a runner set up here it stopped with "exists already", nothing changed.
        host, sha = doctor_world()
        def show(argv, _):
            text = [i for a, i in host.fed if a[:3] == ("sudo", "-n", "tee") and a[3].endswith("minions.slice")][-1]
            sl = dict(x.split("=", 1) for x in text.splitlines() if "=" in x)
            cores = int(sl["CPUQuota"].rstrip("%")) // 100
            return 0, (f"CPUQuotaPerSecUSec={cores}s\nMemoryHigh={sl['MemoryHigh']}\nMemoryMax={sl['MemoryMax']}\n"
                       f"MemorySwapMax={sl.get('MemorySwapMax', 'infinity')}\n")
        host.runs[("systemctl", "show", "minions.slice")] = show
        def look_again():  # a fresh look, the runner's record kept
            d = M.load_state(host)
            d["looks"][REPO]["time"] = M.dt.datetime.now(M.dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            M.save_state(host, d)
        look_again()
        host.ran.clear()
        out = io.StringIO()
        code = M.setup(REPO, host=host, gh=setup_gh(sha), out=out, limits=(2, 40))
        lines = out.getvalue().splitlines()
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertTrue(any(x.startswith(f"ok the limits of {REPO}'s runner are changed, read back from systemd: jobs "
                                         "here get at most 2 of") and "40 % of its memory" in x for x in lines), lines)
        lim = M.load_state(host)["runners"][REPO]["limits"]
        self.assertEqual((lim["cores"], lim["pct"]), (2, 40))
        # The slice only: no runner unpacked, registered, or restarted; no service stopped.
        self.assertFalse([a for a in host.ran if any(w in " ".join(a) for w in ("config.sh", "svc.sh", "stop", "restart",
                                                                                   "tar"))], host.ran)
        # Read back, or said: systemd reading other numbers is a stop, never an ok.
        host.runs[("systemctl", "show", "minions.slice")] = (0, "CPUQuotaPerSecUSec=4s\nMemoryMax=1\nMemoryHigh=1\n")
        look_again()
        out = io.StringIO()
        code = M.setup(REPO, host=host, gh=setup_gh(sha), out=out, limits=(2, 40))
        self.assertEqual(code, M.EXIT_STOP, out.getvalue())
        self.assertIn("the new limits are not in force", out.getvalue())

    def test_G6_9_a_runner_whose_setup_was_cut_before_its_unit_was_recorded_is_stopped_by_name(self):
        # Seen on a stock VM (2026-11-23): uninstall after a setup cut before it recorded the unit printed
        # "the service is Failed to mangle name: Invalid argument" inside an ok line.
        unit = "actions.runner.example-org-example-repo.minions-box-1.service"
        host = FakeHost(runs={("sudo", "-n", "-u", "minions-runner", "--", "cat", f"{FOLDER}/.service"): (0, unit + "\n"),
                              ("systemctl", "is-active", unit): (3, "inactive\n")})
        out = io.StringIO()
        self.assertTrue(M.stop_runner(host, "", FOLDER, M.Report(out), True))
        self.assertIn(("sudo", "-n", "systemctl", "stop", unit, "--no-pager"), host.ran)
        self.assertIn("ok the runner is stopped: the service is inactive", out.getvalue())
        self.assertFalse([a for a in host.ran if "" in a[1:]], host.ran)  # never an empty unit name
        # No service file either: said as such, and nothing run against an empty name.
        host = FakeHost()
        out = io.StringIO()
        self.assertTrue(M.stop_runner(host, "", FOLDER, M.Report(out), True))
        self.assertIn("ok the runner had no service here", out.getvalue())
        self.assertFalse([a for a in host.ran if a[:1] == ("systemctl",) or "systemctl" in a])

    def test_G6_8_uninstall_all_ends_our_leftover_sleep_holds_and_names_anything_else(self):
        # Seen on a stock VM (2026-11-23): a job's sleep hold outlived a service stopped mid-job; `uninstall --all`
        # then stopped on "processes of minions-runner still run" with no way out, though the processes were ours.
        def holds(world_pids, other=()):
            gh, world, host = remove_setup()
            alive = set(world_pids) | set(other)
            def answer(argv, input=None):
                if list(argv) == ["pgrep", "-u", "minions-runner"]:
                    return (0, "".join(f"{p}\n" for p in sorted(alive, key=int))) if alive else (1, "")
                if list(argv[:7]) == ["sudo", "-n", "-u", "minions-runner", "--", "kill", "-TERM"]:
                    alive.difference_update(argv[7:])  # R128: as the runner user
                    return 0, ""
                return world.answer(argv, input)
            host._answer = answer
            who = "--who=Windstorm Repo Minions"
            for pid, (argv, parent) in {"100": (["/usr/bin/systemd-inhibit", "--what=idle", who, "sleep", "21600"], "1"),
                                        "101": (["sleep", "21600"], "100"),
                                        "200": (["/usr/bin/systemd-inhibit", "--what=idle", "--who=someone else",
                                                 "sleep", "9"], "1"),
                                        "201": (["sleep", "9"], "200"), "300": (["python3", "x.py"], "1")}.items():
                host.files[f"/proc/{pid}/cmdline"] = "\0".join(argv) + "\0"
                host.files[f"/proc/{pid}/stat"] = f"{pid} ({os.path.basename(argv[0])[:15]}) S {parent} 1 1"
                host.files[f"/proc/{pid}/comm"] = os.path.basename(argv[0])[:15] + "\n"
            return gh, world, host, alive
        gh, world, host, alive = holds(["100", "101"])
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_NEED, lines)
        self.assertIn(("sudo", "-n", "-u", "minions-runner", "--", "kill", "-TERM", "100", "101"), host.ran)  # exact PIDs
        self.assertFalse(any(a[:2] in (("pkill",), ("killall",)) or a[:1] in (("pkill",), ("killall",)) for a in host.ran))
        self.assertTrue(any("sleep hold left behind" in x and "100, 101" in x for x in lines), lines)
        self.assertFalse(world.user)
        # Anything not made by our holds is never ended: it is named with its name, and the way out is said.
        gh, world, host, alive = holds(["100", "101"], other=["200", "201", "300"])
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn(("sudo", "-n", "-u", "minions-runner", "--", "kill", "-TERM", "100", "101"), host.ran)
        self.assertEqual(alive, {"200", "201", "300"})
        stop = next(x for x in lines if "still run" in x)
        self.assertIn("200 (systemd-inhibit), 201 (sleep), 300 (python3)", stop)
        self.assertIn("run the same command again", stop)
        self.assertTrue(world.user)

    def test_G6_7_uninstall_says_the_put_back_checks_before_its_pull_request(self):
        gh, world, host = remove_setup()
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        tell = next(i for i, x in enumerate(lines) if x.startswith("tell the person 1 of 1: The pull request that "
                                                                    "puts 1 job(s) back starts its own checks"))
        ask = next(i for i, x in enumerate(lines) if x.startswith("need you: read and merge pull request #12"))
        self.assertLess(tell, ask)
        self.assertIn("runs there once for it, before you merge", lines[tell])

    def test_G5_16_a_job_held_at_our_memory_limit_is_named(self):
        # R87 (seen on a stock machine): with the default marks a job that outgrows the limit is not killed; the
        # kernel holds it at the high mark and it barely moves. The watch finds it so pass by pass, from the slice's
        # own files; status names it first, with the job and the person's two ways out, after HELD_PASSES passes.
        host, clock = g5_host(worker=True)
        d = M.load_state(host)
        d["runners"][REPO]["limits"] = {"MemoryHigh": str(4 * 1024 ** 3), "MemoryMax": str(5 * 1024 ** 3)}
        M.save_state(host, d)
        host.files[M.records_dir_of(d["runners"][REPO]["folder"]) + "/jobs.log"] = "start 1795400000 45100 1 0 build\n"
        stalled = "some avg10=80.00 avg60=75.00 avg300=30.00 total=1\nfull avg10=70.00 avg60=62.00 avg300=25.00 total=1\n"
        host.files[M.MEMORY_PRESSURE] = stalled
        for n, high in ((1, 900), (2, 1900)):
            host.files[M.MEMORY_EVENTS] = f"low 0\nhigh {high}\nmax 0\noom 0\noom_kill 0\n"
            run_cmd(M.watch, host=host, gh=StatusGH(busy=True))
            self.assertEqual(M.load_state(host)["runners"][REPO]["memory_held"]["passes"], n)
            lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(busy=True))[1]
            named = [x for x in lines if "held at this computer's memory limit" in x]
            self.assertEqual(bool(named), n >= M.HELD_PASSES, lines)
            clock.sleep(300)
        self.assertEqual(lines[0], "warn job 'build' (run 45100) has been held at this computer's memory limit for "
                                   "jobs (4.0 GB) since 23:00:00Z, 2 watch passes in a row: it needs more memory "
                                   "than jobs here may use, so it barely moves; its tasks all stood still 62 % of "
                                   "the last minute, and it may go on like this until GitHub's own time limit. Your "
                                   "two ways out: cancel it (on GitHub, from the run's page: "
                                   "https://github.com/example-org/example-repo/actions/runs/45100), or "
                                   "give jobs more memory: `setup example-org/example-repo --limits <processors>,"
                                   "<memory %>`")
        # The held count stops rising (the job got its memory, or ended): no longer held, not named.
        run_cmd(M.watch, host=host, gh=StatusGH(busy=True))
        self.assertNotIn("memory_held", M.load_state(host)["runners"][REPO])
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH(busy=True))[1]
        self.assertFalse([x for x in lines if "memory limit for jobs" in x], lines)
        # Little stall: a job brushing the high mark but moving is not named.
        host.files[M.MEMORY_EVENTS] = "high 2500\n"
        host.files[M.MEMORY_PRESSURE] = "some avg10=5.00 avg60=4.00 avg300=1.00 total=1\nfull avg10=2.00 avg60=1.50 avg300=0.50 total=1\n"
        run_cmd(M.watch, host=host, gh=StatusGH(busy=True))
        self.assertNotIn("memory_held", M.load_state(host)["runners"][REPO])
        # No pressure file (a kernel without it): the job's memory sitting at the high mark counts instead.
        del host.files[M.MEMORY_PRESSURE]
        host.files[M.MEMORY_EVENTS] = "high 3000\n"
        host.files[M.MEMORY_CURRENT], host.files[M.MEMORY_HIGH] = str(4 * 1024 ** 3 - 1000), str(4 * 1024 ** 3)
        run_cmd(M.watch, host=host, gh=StatusGH(busy=True))
        self.assertEqual(M.load_state(host)["runners"][REPO]["memory_held"]["passes"], 1)
        # No job process: never held.
        host, clock = g5_host(worker=False)
        host.files[M.MEMORY_EVENTS], host.files[M.MEMORY_PRESSURE] = "high 900\n", stalled
        run_cmd(M.watch, host=host, gh=StatusGH())
        self.assertNotIn("memory_held", M.load_state(host)["runners"][REPO])

    def test_G5_15_a_blind_watch_says_so_and_holds_after_three_passes(self):
        host, clock = g5_host()
        seen = M.seen_path(FOLDER)
        # It reads the repository private: the time goes where the hook reads it.
        self.assertEqual(run_cmd(M.watch, host=host, gh=StatusGH())[0], M.EXIT_OK)
        self.assertEqual(host.files[seen].strip(), str(int(clock.t.timestamp())))
        # Blind (gh not found): each pass ends `stop`, with the count and why, never in silence.
        for n in (1, 2, 3):
            clock.sleep(300)
            code, lines = run_cmd(M.watch, host=host, gh=BlindGH())
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertIn(f"{n} pass(es) in a row", lines[-1])
            self.assertIn("could not be found with this PATH", lines[-1])
        self.assertIn(f"the job hook here refuses each job of it before its first step {M.HOOK_CAVEAT}", lines[-1])
        self.assertIn("run `setup` again from a terminal where `gh` works", lines[-1])
        self.assertEqual(host.files[seen].strip(), str(int(clock.t.timestamp()) - 900))  # never refreshed blind
        # status: a stop line, first; never "ok" for the watch alone.
        code, lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue(lines[0].startswith(f"stop the watch could not read whether {REPO} is still private, 3 pass"))
        # A logged-out gh gets its own way out.
        host2, _ = g5_host()
        lines = run_cmd(M.watch, host=host2, gh=BlindGH(why="GitHub gave no answer: To get started with GitHub CLI, "
                                                            "please run:  gh auth login"))[1]
        # R160: its step keeps the login where the watch can read it (`gh auth login` alone can put it back in a
        # keyring, and the watch goes blind again), with `look` first to say what that keeps where
        self.assertIn("Your way out: in your own terminal, `gh auth login --insecure-storage` (run `look "
                      f"{REPO}` first", lines[-1])
        # Reading it private again ends the count and refreshes the time.
        clock.sleep(300)
        self.assertEqual(run_cmd(M.watch, host=host, gh=StatusGH())[0], M.EXIT_OK)
        self.assertNotIn("blind", M.load_state(host)["watch"])
        self.assertEqual(host.files[seen].strip(), str(int(clock.t.timestamp())))
        # Public: "public" at once, whatever else the watch can do.
        host3, _ = g5_host()
        run_cmd(M.watch, host=host3, gh=StatusGH(private=False))
        self.assertEqual(host3.files[seen].strip(), "public")
        # A record it cannot write is blind too.
        host4, _ = g5_host()
        host4.seen_unwritable = True
        code, lines = run_cmd(M.watch, host=host4, gh=StatusGH())
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("could not write", lines[-1])

    def test_G5_15_the_job_hook_refuses_a_job_without_a_fresh_private_read(self):
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            folder = os.path.realpath(d)
            now = int(time.time())
            self.assertEqual(hook_run(folder, str(now))[0], 0)
            self.assertEqual(hook_run(folder, str(now - 14 * 60))[0], 0)
            for value in (str(now - 16 * 60), "public", "", "garbage"):
                code, out = hook_run(folder, value)
                self.assertEqual(code, 1, value)
                self.assertIn("::error::Windstorm Repo Minions refused this job before any of its steps", out)
            self.assertIn("no longer private", hook_run(folder, "public")[1])
            self.assertEqual(hook_run(folder, str(now + 3600))[0], 1)  # a time in the future is not fresh
            # GitHub's own record of the event: "not private" refuses at once, even with a fresh read.
            private = {"repository": {"full_name": REPO, "private": True, "visibility": "private"}}
            public = {"repository": {"full_name": REPO, "private": False, "visibility": "public"}}
            self.assertEqual(hook_run(folder, str(now), event=private)[0], 0)
            code, out = hook_run(folder, str(now), event=public)
            self.assertEqual(code, 1)
            self.assertIn("GitHub's own record of this event says the repository is not private", out)
            self.assertEqual(hook_run(folder, str(now), event={"schedule": "0 * * * *"})[0], 0)  # no repository named
            self.assertEqual(hook_run(folder, str(now - 3600), event={"schedule": "0 * * * *"})[0], 1)
            # Each refusal is written down, so status can name it (both sides, with GitHub's failure).
            with open(os.path.join(folder, "records", "jobs.log")) as f:
                kinds = [x.split()[0] for x in f.read().splitlines()]
            self.assertIn("refused-public", kinds)
            self.assertIn("refused-stale", kinds)
            # Stale, then made fresh while the hook waits: the job goes on.
            seen = fresh_seen(folder, str(now - 3600))
            text = M.job_started_script(folder, seen=seen, hold_wait_s=20).replace("command -v systemd-inhibit", "false")
            with open(os.path.join(folder, "s.sh"), "w") as f:
                f.write(text)
            p = subprocess.Popen(["bash", os.path.join(folder, "s.sh")], env=dict(no_event(), PATH=machine_guard.path("/usr/bin:/bin")),
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            time.sleep(2)
            fresh_seen(folder)
            out = p.communicate(timeout=60)[0]
            self.assertEqual(p.returncode, 0, out)
            self.assertIn("has not read this repository private in the last 15 minutes; waiting up to", out)
        # It is checked before anything else the hook starts (the sleep locks), so no step of the job runs first.
        script = M.job_started_script(FOLDER)
        for check in ("event=\"$(python3", "refused-public", "refused-stale"):
            self.assertLess(script.index(check), script.index("setsid"), check)

    def test_G5_15_status_names_a_refused_job_as_refused(self):
        t0 = int(M.parse_stamp("2026-11-22T22:40:00Z").timestamp())
        failed = {"conclusion": "failure", "steps": [{"name": "Set up job", "conclusion": "failure"}]}
        note = ["Windstorm Repo Minions refused this job before any of its steps: the watch on that computer has not "
                "read the repository private in the last 15 minutes"]
        for kind, why in (("stale", "the watch not having read the repository private in the last 15 minutes"),
                          ("public", "GitHub's own record of the event, or the watch, saying the repository is not "
                                     "private")):
            host, gh, _ = G5._named(self, f"start {t0} 555 1 0 check\nrefused-{kind} {t0 + 360} 555 1 0 check\n",
                                    failed, notes=note)
            lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
            named = [x for x in lines if x.startswith("warn job 'check'")]
            self.assertEqual(named, [f"warn job 'check' of .github/workflows/ci.yml (run 555, started 22:40:00Z) was refused before its first step "
                                     f"by this computer's guard: {why}. No step of it ran here; GitHub shows it as "
                                     f"failed, not a failing test of yours. To run it again, once: `status {REPO} "
                                     "--rerun 9001`."])
        # Without this computer's record of a refusal, a failed job is the person's own: never named.
        host, gh, _ = G5._named(self, f"start {t0} 555 1 0 check\nend {t0 + 60} 555 1 0 check\n", failed, notes=note)
        self.assertFalse(any(x.startswith("warn job") for x in run_cmd(M.status, REPO, host=host, gh=gh)[1]))

    def test_G5_15_setup_reads_back_one_real_watch_pass(self):
        host, sha = setup_host()
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn(("sudo", "-n", "systemctl", "start", "minions-watch.service", "--no-pager"), host.ran)
        self.assertTrue(any(x.startswith(f"ok the watch's first pass ran now, as the timer runs it, and read {REPO} "
                                         "private (read back)") for x in lines), lines)
        # A first pass that cannot read GitHub: setup stops and says why.
        host, sha = setup_host()
        fresh_look(host)

        def blind_pass(argv, inp):
            M.watch(host=host, gh=BlindGH(), out=io.StringIO())
            return 0, ""
        host.runs[("sudo", "-n", "systemctl", "start", "minions-watch.service")] = blind_pass
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue(lines[-2].startswith("stop the watch's first pass could not read whether the repository is "
                                             "private: the GitHub CLI (gh) could not be found"), lines[-1])

    def test_G8_9_every_program_a_service_runs_by_name_is_listed_and_resolved(self):
        import ast
        with open(M.__file__, encoding="utf-8") as f:
            tree = ast.parse(f.read())

        def first_word(node):
            if isinstance(node, ast.BinOp):
                node = node.left
            if isinstance(node, ast.List) and node.elts and isinstance(node.elts[0], ast.Constant) \
                    and isinstance(node.elts[0].value, str) and not node.elts[0].value.startswith("-"):
                return node.elts[0].value
            return None
        names = set()
        for node in ast.walk(tree):
            cands = []
            if isinstance(node, ast.Call):
                fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if fn in ("run", "run_in", "Popen", "check_output", "check_call") and node.args:
                    cands.append(node.args[0])
                if fn in ("sudo", "as_runner") and len(node.args) > 1:
                    cands.append(node.args[1])
            if isinstance(node, ast.FunctionDef) and node.name in ("systemctl", "journalctl", "inhibitor_argv"):
                cands += [r.value for r in ast.walk(node) if isinstance(r, ast.Return) and r.value is not None]
            if isinstance(node, ast.Assign) and any(isinstance(x, ast.Name) and (x.id in ("argv", "full", "cmd")
                                                                               or x.id.startswith("INHIBIT"))
                                                    for x in node.targets):
                cands.append(node.value)
            names |= {w for w in map(first_word, cands) if w}
        self.assertGreater(len(names), 15, names)  # the scan finds them (a scan that finds nothing proves nothing)
        unlisted = {n for n in names if n not in M.PROGRAMS and n not in M.DARWIN_ONLY}
        self.assertEqual(unlisted, set(), "a program run by name must be listed in PROGRAMS (resolved at setup)")
        # The hooks: every command they run is in HOOK_PROGRAMS, and they run with exactly those folders.
        builtins = {"if", "then", "else", "elif", "fi", "for", "do", "done", "case", "esac", "echo", "exit",
                    "printf", "return", "export", "seen_ok", "in", "break", "while", "true", "false", "local"}
        for script in (M.job_started_script(FOLDER, path="/usr/bin:/bin"), M.job_completed_script(FOLDER, path="/usr/bin:/bin")):
            self.assertIn("\nexport PATH='/usr/bin:/bin'\n", script)
            words = set()
            script = script.replace(M.EVENT_PY, "")  # the event reader is Python, run by python3 (listed)
            for line in script.splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "echo ")):
                    continue
                line = re.sub(r"'[^']*'", "Q", line.replace("'\\''", ""))  # quoted words are arguments
                line = re.sub(r"^[A-Za-z_]+=", "", line)  # an assignment: scan what it runs
                for part in re.split(r"\$\(|;|&&|\|\||\||\bthen\b|\bdo\b|\belse\b", line):
                    w = part.strip().split(" ")[0].strip("'\"")
                    if re.fullmatch(r"[a-z][a-z0-9-]*", w) and w not in builtins:
                        words.add(w)
            self.assertEqual(words - set(M.HOOK_PROGRAMS), set(), script)
        # The watch's unit: the folders where your PATH found them, in its order; a gh in your own folder included.
        host = linux_host()
        host.which = lambda n: "/home/person/.local/bin/gh" if n == "gh" else f"/usr/bin/{n}"
        found, missing = M.resolve_programs(host, sorted(M.PROGRAMS))
        self.assertEqual(missing, [])
        with mock.patch.dict(os.environ, {"PATH": "/home/person/.local/bin:/usr/bin"}):
            svc, _ = M.watch_units("person", 300, M.path_for(found))
        self.assertIn('Environment="PATH=/home/person/.local/bin:/usr/bin"\n', svc)
        # A hook program found only in your home folder: setup stops (the runner's user cannot enter it).
        host, sha = setup_host()
        host.home = lambda: "/home/person"
        real = host.which
        host.which = lambda n: "/home/person/bin/awk" if n == "awk" else real(n)
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn(f"awk ({host.home()}/bin/awk)", lines[-2])


class G9(unittest.TestCase):
    def test_G9_3_the_github_stand_in_answers_as_recorded(self):
        # Every recorded call is answered exactly as GitHub answered it, in the order it was recorded, once; a call
        # nobody recorded gets 404 and is kept, never a made-up answer.
        for name in sorted(os.listdir(FIX)):
            if not name.endswith(".jsonl"):
                continue
            rows = load(name)
            gh = StandIn(rows)
            for r in rows:
                self.assertEqual(gh.api(r["method"], r["path"], r.get("body")), (r["status"], r["data"]),
                                 (name, r["method"], r["path"]))
            self.assertEqual(gh.misses, [], name)
            self.assertEqual(gh.api(rows[0]["method"], rows[0]["path"])[0], 404, name)
            self.assertEqual(len(gh.misses), 1, name)
        # The hand-made answers the runner tests use carry only what a recorded answer carries (runner-life.jsonl:
        # a real runner registered, idle, busy, held by drain, resumed, held again and removed, 2026-11-23).
        life = load("runner-life.jsonl")
        lists = [r["data"] for r in life if r["method"] == "GET" and "/actions/runners?" in r["path"]]
        real = [x for d in lists for x in d["runners"]]
        keys, label_keys = set().union(*map(set, real)), set().union(*(set(y) for x in real for y in x["labels"]))
        put_keys = set().union(*(set(r["data"]) for r in life if r["method"] == "PUT"))
        made = [runners_answer()[1], StatusGH().api("GET", f"repos/{REPO}/actions/runners?per_page=100")[1],
                DrainGH().api("GET", f"repos/{REPO}/actions/runners?per_page=100")[1]]
        for d in made:
            self.assertEqual(set(d) - {"total_count", "runners"}, set(), d)
            for x in d["runners"]:
                self.assertEqual(set(x) - keys, set(), x)
                for y in x["labels"]:
                    self.assertEqual(set(y if isinstance(y, dict) else {"name": y}) - label_keys, set(), y)
        g = DrainGH()
        self.assertEqual(set(g.api("PUT", f"repos/{REPO}/actions/runners/7/labels", {"labels": ["x"]})[1]) - put_keys,
                         set())
        # The states the stand-ins play are states a real runner was seen in, and the hold reads as recorded.
        seen = {(x["status"], x["busy"]) for x in real}
        self.assertTrue({("online", False), ("online", True), ("offline", False)} <= seen, seen)
        held = [sorted(y["name"] for y in r["data"]["labels"]) for r in life if r["method"] == "PUT"]
        self.assertEqual(held[0], [M.HELD_LABEL])
        self.assertEqual(held[1], ["minions", "minions-medium"])
        tok = [r["data"] for r in life if r["path"].endswith("/registration-token")][0]
        self.assertEqual(set(tok), {"token", "expires_at"})
        self.assertEqual(tok["token"], "RECORDED-TOKEN-REMOVED")

    def test_G9_7_a_stand_in_grants_nothing_a_real_machine_refuses(self):
        # The watch's passes, on a stand-in that refuses `sudo -n` as a system timer does: private, blind, public,
        # and kept public. None asks for admin rights, and each still does its work (the record, the labels).
        host, clock = g5_host(worker=True)
        refuse_sudo(host)
        for gh in (StatusGH(), BlindGH(), StatusGH(private=False), StatusGH(private=False)):
            clock.sleep(300)
            run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual([a for a in host.ran if a[:1] == ("sudo",)], [])
        self.assertEqual(host.files[M.seen_path(FOLDER)].strip(), "public")
        # The stand-in itself: refusing is what it does when asked.
        self.assertEqual(host.run(["sudo", "-n", "true"])[0], 1)


class G8Limit(unittest.TestCase):
    def test_G8_14_a_spent_api_limit_is_named_and_never_polled_into(self):
        # Seen 2026-11-23: the fleet's login spent its 5000 calls; every call then was refused with HTTP 403 until
        # GitHub's reset, and the watch's words said to check the network.
        now = M.parse_stamp("2026-11-23T05:13:28Z")
        head = ("HTTP/2.0 403 Forbidden\r\nX-Ratelimit-Limit: 5000\r\nX-Ratelimit-Remaining: 0\r\n"
                "X-Ratelimit-Reset: {}\r\n\r\n".format(int(M.parse_stamp("2026-11-23T05:20:27Z").timestamp())))
        spent = head + '{"message": "API rate limit exceeded for user ID 1."}'
        self.assertEqual(M.rate_limited_until(spent, 403, {"message": "API rate limit exceeded"}, now),
                         M.parse_stamp("2026-11-23T05:20:27Z"))
        self.assertEqual(M.rate_limited_until("HTTP/2.0 429 Too Many\r\nRetry-After: 60\r\n\r\n{}", 429, {}, now),
                         now + M.dt.timedelta(seconds=60))
        self.assertIsNone(M.rate_limited_until("HTTP/2.0 403 Forbidden\r\n\r\n{}", 403,
                                               {"message": "Resource not accessible by integration"}, now))
        self.assertIsNone(M.rate_limited_until("HTTP/2.0 200 OK\r\n\r\n{}", 200, {}, now))
        # The wrapper: one refused call, then no call at all until the reset, then calls again.
        host = linux_host()
        clock = Clock(host, "2026-11-23T05:13:28Z")
        gh = M.GitHub(host)
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            out = spent if len(calls) == 1 else "HTTP/2.0 200 OK\r\n\r\n{\"private\": true}"
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
        orig = M.subprocess.run
        M.subprocess.run = run
        try:
            self.assertEqual(gh.api("GET", f"repos/{REPO}")[0], 403)
            s, d = gh.api("GET", f"repos/{REPO}")
            self.assertEqual((s, len(calls)), (429, 1))
            self.assertIn("GitHub's API limit for this login is spent until 05:20Z", d["message"])
            self.assertIn("this tool's watch uses about 24 calls an hour", d["message"])
            clock.sleep(420)
            self.assertEqual(gh.api("GET", f"repos/{REPO}")[0], 200)
            self.assertEqual(len(calls), 2)
        finally:
            M.subprocess.run = orig
        # The watch's words: the limit and its time, and that it comes back by itself, never "check the network".
        b = {"why": M.limit_words(M.parse_stamp("2026-11-23T05:20:27Z")), "passes": 1, "since": "2026-11-23T05:13:28Z"}
        words = M.blind_words(REPO, b, 300)
        self.assertIn("spent until 05:20Z", words)
        self.assertIn("it comes back by itself at the time named", words)
        self.assertNotIn("network", words)
        # drain's wait ends at once with a `waiting` line instead of polling into the limit.
        host, clock = g5_host(worker=True)
        gh = StatusGH(busy=True)
        gh.host = host  # the stand-in reads the limit by this test's clock, never the real one (it failed after 23:59Z)
        gh.limited_until = M.parse_stamp("2026-11-23T23:59:00Z")
        gh.why = M.limit_words(gh.limited_until)
        code, lines = run_cmd(M.drain, REPO, host=host, gh=gh)
        self.assertEqual(code, M.EXIT_WAIT, lines)
        self.assertIn("GitHub's API limit for this login is spent until 23:59Z", lines[-1])
        self.assertIn("Held, nothing stopped", lines[-1])
        self.assertLess(clock.t - M.parse_stamp("2026-11-22T23:00:00Z"), M.dt.timedelta(seconds=60))
        # R90 (seen on a stock machine): one watch pass read every finished job in this computer's records again, each
        # pass (16 calls with 13 records, against the 2 said). Now a finished job is read once; a pass after that reads
        # the repository and its runners only, and the words say the true spend.
        host, clock = g5_host()
        d = M.load_state(host)
        folder = d["runners"][REPO]["folder"]
        recs = "".join(f"start {1795400000 + 60 * k} {4500 + k} 1 0 j\nend {1795400030 + 60 * k} {4500 + k} 1 0 j\n"
                       for k in range(13))
        host.files[M.records_dir_of(folder) + "/jobs.log"] = recs
        gh = StatusGH()
        gh.jobs = {4500 + k: [{"id": 9000 + k, "name": "j", "runner_name": "minions-box-1", "status": "completed",
                               "conclusion": "success", "run_attempt": 1,
                               "started_at": M.stamp(M.dt.datetime.fromtimestamp(1795400000 + 60 * k, M.dt.timezone.utc))}]
                   for k in range(13)}
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(len(gh.calls), 2 + 13)
        gh.calls.clear()
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(len(gh.calls), 2, gh.calls)
        self.assertIn(f"and at most {M.WATCH_CALLS_PER_JOB} more for each job that ends here", M.limit_words(host.now()))


class INV(unittest.TestCase):
    def test_INV1_never_a_runner_on_a_public_repository(self):
        # Every door, for every visibility that is not private.
        for vis in ("public", "internal"):
            # look stops before anything is installed, and setup then refuses (it needs a look with no stop).
            gh = StandIn(load("look-recent-runs.jsonl"), overrides={
                ("GET", f"repos/{REPO}"): (200, {"full_name": REPO, "private": False, "visibility": vis})})
            host = linux_host()
            code, lines = look(gh, host)
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertTrue([x for x in lines if x.startswith(f"stop {REPO} is {vis}: Repo Minions never puts a "
                                                               "runner on a repository that is not private")], lines)
            sha = setup_host()[1]
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual(code, M.EXIT_STOP)
            self.assertIn("ended with a `stop`", lines[-1])
        # On the minion: the job hook refuses before any step once the record says public, or GitHub's own record
        # of the event says not private (the scheduled-run exception is bounded by G5.15's fresh read).
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            folder = os.path.realpath(d)
            now = int(time.time())
            self.assertEqual(hook_run(folder, "public")[0], 1)
            public = {"repository": {"full_name": REPO, "private": False, "visibility": "public"}}
            self.assertEqual(hook_run(folder, str(now), event=public)[0], 1)
            self.assertEqual(hook_run(folder, str(now - 16 * 60), event={"schedule": "0 * * * *"})[0], 1)
        # The watch: the record says public at once, our labels come off, our runner leaves GitHub's side once idle.
        host, clock = g5_host()
        refuse_sudo(host)
        gh = StatusGH(private=False)
        run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(host.files[M.seen_path(FOLDER)].strip(), "public")
        self.assertEqual(gh.puts, [(REPO, ["minions-held"])])
        self.assertIn(("DELETE", f"repos/{REPO}/actions/runners/7"), gh.calls)
        # And nothing brings it back while it is not private: resume refuses.
        code, lines = run_cmd(M.resume, REPO, host=host, gh=gh)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn("was not private", lines[-1])


    def test_INV8_an_interrupted_step_is_finished_or_undone(self):
        # A setup cut part-way is undone by doctor, read back; it says how to set up again.
        host, sha = setup_host()
        fresh_look(host)
        config = ("sudo", "-n", "-u", "minions-runner", "--", *M.RUNNER_ENV, "bash", "-c", M.CONFIG_SCRIPT)
        host.runs[config] = (1, "sudo: a password is required\n")
        none = (200, {"total_count": 0, "runners": []})
        listing = ("GET", f"repos/{REPO}/actions/runners?per_page=100")
        self.assertEqual(run_setup(setup_gh(sha, {listing: none}), host)[0], M.EXIT_STOP)
        self.assertIn(REPO, M.load_state(host)["setting_up"])
        # Without a terminal or admin rights, doctor touches nothing and says how.
        host.runs[("sudo", "-n", "true")] = (1, "sudo: a password is required\n")
        out = io.StringIO()
        code = M.doctor(REPO, host=host, gh=setup_gh(sha, {listing: none}), out=out, interactive=False)
        self.assertEqual(code, M.EXIT_NEED, out.getvalue())
        self.assertIn(f"then run `doctor {REPO}` again", out.getvalue())
        self.assertIn(REPO, M.load_state(host)["setting_up"])
        host.runs[("sudo", "-n", "true")] = (0, "")
        out = io.StringIO()
        code = M.doctor(REPO, host=host, gh=setup_gh(sha, {listing: none}), out=out, interactive=False)
        lines = out.getvalue().splitlines()
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertTrue(any(x.startswith(f"ok a setup of {REPO} here was cut part-way: what it made is taken away")
                            for x in lines), lines)
        self.assertIn(f"ok uninstalled: {REPO} has no runner on this computer now", lines)
        self.assertIn(f"ok `setup {REPO}` sets it up again, fresh, when you want it", lines)
        state = M.load_state(host)
        self.assertNotIn("setting_up", state)
        self.assertNotIn(REPO, state.get("runners") or {})
        # A removal cut part-way (the record says removing) is finished, read back.
        gh, world, host = remove_setup(interrupt_on=lambda a: a[:7] == ["sudo", "-n", "-u", "minions-runner", "--", "rm", "-rf"])
        self.assertEqual(M.main(["uninstall", REPO], host=host, out=io.StringIO(), gh=gh), M.EXIT_INTERRUPTED)
        self.assertTrue(M.load_state(host)["runners"][REPO]["removing"])
        out = io.StringIO()
        code = M.doctor(REPO, host=host, gh=gh, out=out, interactive=False)
        lines = out.getvalue().splitlines()
        self.assertTrue(any(x.startswith(f"ok a removal of {REPO} here was cut part-way") for x in lines), lines)
        self.assertIn(f"ok uninstalled: {REPO} has no runner on this computer now", lines)
        self.assertNotIn(REPO, M.load_state(host).get("runners") or {})

    def test_INV4_never_kill_a_running_job(self):
        # Every command that could stop the runner, run while a job runs: it holds and waits, and neither stops the
        # runner's service nor sends any process a signal. (0.1.0 has no `uninstall --now`: no exception exists.)
        def kills(host):
            return [a for a in host.ran if "kill" in " ".join(a) or
                    (a[:1] == ("sudo",) and "systemctl" in a and ("stop" in a or "restart" in a) and UNIT in a)]
        for stop, restart in ((False, False), (True, False), (False, True)):
            host, clock = g5_host(worker=True)
            code, lines = run_cmd(M.drain, REPO, host=host, gh=StatusGH(busy=True), stop=stop, restart=restart,
                                  wait_s=60, poll_s=10)
            self.assertEqual(code, M.EXIT_WAIT, (stop, restart, lines))
            self.assertEqual(kills(host), [], (stop, restart))
        gh, world, host = remove_setup(busy=(True,))
        code, lines = run_uninstall(gh, host, wait_s=60, poll_s=10)
        self.assertEqual(code, M.EXIT_WAIT, lines)
        self.assertEqual(kills(host), [])
        # setup again for a runner GitHub dropped, while a job process still runs here: nothing is stopped.
        host, clock = g5_host(worker=True)
        data = M.load_state(host)
        data["runners"][REPO]["gone_on_github"] = "2026-11-22T22:00:00Z"
        M.save_state(host, data)
        lines = []
        out = io.StringIO()
        M.reregister(StatusGH(listed=False), host, REPO, M.load_state(host), M.Report(out))
        self.assertEqual(kills(host), [])
        self.assertIn("a job process of minions-box-1 still runs here; nothing is changed", out.getvalue())
        # The watch, for a repository no longer private, with a job running: no admin step at all.
        host, clock = g5_host(worker=True)
        refuse_sudo(host)
        run_cmd(M.watch, host=host, gh=StatusGH(private=False, busy=True))
        self.assertFalse([a for a in host.ran if a[:1] == ("sudo",)])


class G8Waits(unittest.TestCase):
    def test_G8_6_no_command_hangs_on_waiting(self):
        start = M.parse_stamp("2026-11-22T23:00:00Z")
        # drain, with a job that never ends: the deadline said, the command ends when it passes.
        host, clock = g5_host(worker=True)
        code, lines = run_cmd(M.drain, REPO, host=host, gh=StatusGH(busy=True))
        self.assertEqual(code, M.EXIT_WAIT, lines)
        self.assertTrue([x for x in lines if re.search(r"until \d\d:\d\dZ at the latest", x)], lines)
        self.assertLessEqual((clock.t - start).total_seconds(), M.DRAIN_WAIT_S + 10)
        # resume, while GitHub reads the runner offline: the same.
        host, clock = g5_host()
        data = M.load_state(host)
        data["runners"][REPO].update(held_for="drain", labels_before_hold=["minions", "minions-medium"])
        M.save_state(host, data)
        gh = StatusGH(status="offline")
        gh.r[REPO]["labels"] = ["minions-held"]
        code, lines = run_cmd(M.resume, REPO, host=host, gh=gh)
        self.assertEqual(code, M.EXIT_WAIT, lines)
        self.assertTrue([x for x in lines if re.search(r"until \d\d:\d\d:\d\dZ at the latest", x)], lines)
        self.assertLessEqual((clock.t - start).total_seconds(), 60)

    def test_G8_7_waiting_exits_4_and_names_what_and_what_next(self):
        host, clock = g5_host(worker=True)
        code, lines = run_cmd(M.drain, REPO, host=host, gh=StatusGH(busy=True))
        self.assertEqual(code, 4)
        self.assertTrue(lines[-1].startswith("waiting for the running job to end"), lines[-1])
        self.assertIn(f"Next: `drain {REPO}` again", lines[-1])
        # Every `waiting` line in the tool names the command to run next, or is one workflow's line that `move
        # --verify`'s own last line follows with it.
        import ast
        with open(os.path.join(ROOT, "scripts", "minions.py"), encoding="utf-8") as f:
            src = f.read()
        bad = []
        for n in ast.walk(ast.parse(src)):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "wait"
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "rep" and n.args):
                text = ast.unparse(n.args[0])
                per_path = isinstance(n.args[0], ast.JoinedStr) and text.startswith("f'{path}:") or \
                    text.startswith('f"{path}:')
                if "`" not in text and not per_path:
                    bad.append((n.lineno, text[:80]))
        self.assertEqual(bad, [])


class G8Words(unittest.TestCase):
    NEXT = re.compile(r"`|\bagain\b|\bfirst\b|\bcheck\b|\bwrite owner|\bno step to take\b|\bopen an issue\b|\bask\b|"
                      r"\btype\b|\bpress Enter\b|\bFree some space\b|\bmerge\b|\bsettle\b|\bway out\b|\brun\b|\binstall\b", re.I)

    def test_G8_12_every_stop_and_need_you_line_names_what_happened_and_the_next_step(self):
        # Run B again: `move` stopped on "could not read it, so nothing is moved", with no answer and no way out, and a
        # cold agent went into the tool's source. Every stop and need-you line carries its next step; one that is
        # built elsewhere is checked where it is built.
        with open(os.path.join(ROOT, "scripts", "minions.py")) as f:
            src = f.read()
        lacking = []
        import ast
        for n in ast.walk(ast.parse(src)):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("stop", "need")
                    and isinstance(n.func.value, ast.Name) and n.func.value.id == "rep" and n.args):
                a = n.args[0]
                if isinstance(a, (ast.Name, ast.Call)):
                    continue  # public_words, blind_words, the memory floor: checked below
                words = " ".join(x.value for x in ast.walk(a) if isinstance(x, ast.Constant) and isinstance(x.value, str))
                if words.strip() == "start test:":
                    continue  # its words are ZERO_JOB_WORDS, checked below
                # R155: a line that opens with another program's command in backticks (`systemctl daemon-reload`
                # failed: ...) named no step, yet its backtick passed as one. The command it quotes is not a step.
                if not self.NEXT.search(re.sub(r"^\s*`[^`]*`", "", words)):
                    lacking.append((n.lineno, words[:80]))
        self.assertEqual(lacking, [])
        for built in (M.ZERO_JOB_WORDS, M.public_words(REPO, {"time": "t"}),
                      M.memory_floor_line(REPO, {"MemoryMax": 1, "pct": 60, "mem": 2 * 1024 ** 3, "cores": 1}),
                      M.memory_floor_line(REPO, {"MemoryMax": 1, "pct": 60, "mem": 16 * 1024 ** 3, "cores": 1})):
            self.assertTrue(self.NEXT.search(built), built)
        # GitHub's own answer is named: move's stop on a workflow file it could not read (seen in run B again).
        class FileErr(StandIn):
            def api(self, method, path, body=None):
                if method == "GET" and path.endswith("/contents/.github/workflows"):
                    return 200, [{"name": "mac.yml", "path": ".github/workflows/mac.yml"}]
                return 502, {"message": "Server Error"}
        rep = M.Report(out=io.StringIO())
        self.assertIsNone(M.read_workflow_files(FileErr([]), REPO, None, rep))
        self.assertEqual(rep.lines[-1], ".github/workflows/mac.yml: could not read it (GitHub answered HTTP 502); "
                                        "nothing is changed. Run the same command again; if it stops here again, "
                                        "GitHub's answer named here is the reason".join(["stop ", ""]))

    def test_G8_13_a_reader_that_stops_reading_never_cuts_a_command_short(self):
        # Seen: `look ... | head -2` ended in a BrokenPipeError traceback. A closed pipe stops the printing only: the
        # command finishes its work and its exit code, with no traceback.
        r, w = os.pipe()
        os.close(r)
        out = os.fdopen(w, "w")
        rep = M.Report(out=out)
        rep.ok("one")
        rep.tell("two")
        rep.stop("three: run it again")
        self.assertEqual(rep.code(), M.EXIT_STOP)
        self.assertTrue(rep.out_closed)
        self.assertEqual([x for x in rep.lines if not x.startswith("----- ")],
                         ["ok one", "tell the person 1 of 1: two", "stop three: run it again"])
        script = os.path.join(ROOT, "scripts", "minions.py")
        r = subprocess.run(["bash", "-c", f"python3 -I {script} look not-a-name 2>&1 | head -c 1 >/dev/null; "
                            f"python3 -I {script} look not-a-name 2>&1 | head -c 0"],
                           capture_output=True, text=True, timeout=60)
        self.assertNotIn("Traceback", r.stdout + r.stderr)


class RuleTwo(unittest.TestCase):
    """Rule 2 (A PERSON'S MACHINE IS FULL WHERE A STOCK ONE IS EMPTY): what of the person's shell reaches the programs
    the tool starts (R143) and the road of the tool's own gh (R144)."""
    LIVED = {"GH_TOKEN": "made-up-token-value", "GITHUB_TOKEN": "made-up-too", "GH_HOST": "ghe.invalid",
             "GH_ENTERPRISE_TOKEN": "made-up-ent", "HTTPS_PROXY": "http://proxy.invalid:3128",
             "SSL_CERT_FILE": "/etc/ssl/person.pem", "LANG": "de_DE.UTF-8", "HOME": "/home/person"}

    def test_G2_21_the_runners_configuration_gets_an_environment_built_by_name(self):
        # R143: config.sh, at registration and at removal, runs under `env -i` with six names and nothing else, so
        # nothing of the person's shell crosses, whatever sudo here keeps; the locale every job gets is the tool's.
        self.assertEqual(M.RUNNER_ENV[:2], ("env", "-i"))
        self.assertEqual([x.split("=", 1)[0] for x in M.RUNNER_ENV[2:]],
                         ["HOME", "USER", "LOGNAME", "SHELL", "PATH", "LANG"])
        self.assertIn("LANG=C.UTF-8", M.RUNNER_ENV)
        self.assertNotIn(os.path.expanduser("~"), " ".join(M.RUNNER_ENV))
        host, sha = setup_host()
        # the runner's own env.sh had saved a person's locale into .env: the tool's line replaces it
        host.runs[("sudo", "-n", "-u", "minions-runner", "--", "cat", f"{FOLDER}/.env")] = \
            (0, "LANG=de_DE.UTF-8\nLC_ALL=de_DE.UTF-8\nLANGUAGE=de_DE:de\nJAVA_HOME=/opt/java\n")
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        config = [a for a in host.ran if M.CONFIG_SCRIPT in a]
        self.assertEqual(len(config), 1)
        self.assertEqual(config[0][:5 + len(M.RUNNER_ENV) + 2],
                         ("sudo", "-n", "-u", "minions-runner", "--", *M.RUNNER_ENV, "bash", "-c"))
        envtext = next(x for a, x in host.fed if a[-1] == f"{FOLDER}/.env")
        self.assertTrue(envtext.startswith("LANG=C.UTF-8\n"), envtext)
        self.assertNotIn("de_DE", envtext)
        self.assertNotRegex(envtext, r"(?m)^LC_")
        self.assertIn("JAVA_HOME=/opt/java\n", envtext)  # what the runner itself saved, not a locale, stays
        gh, world, host = remove_setup()
        run_uninstall(gh, host, everything=True)
        removal = [a for a in host.ran if M.REMOVE_SCRIPT in a]
        self.assertTrue(removal)
        self.assertTrue(all(a[5:5 + len(M.RUNNER_ENV)] == M.RUNNER_ENV for a in removal), removal)

    def test_G1_17_the_tools_gh_goes_to_github_com_as_the_saved_login(self):
        # R144: every gh call of the tool names github.com and runs without the shell's token and host settings;
        # proxy and certificate settings stay; setup and the watch read the saved login from the same place.
        env = M.gh_environ(self.LIVED, "/home/person/.config/gh")
        for name in M.GH_NOT_HANDED:
            self.assertNotIn(name, env)
        self.assertEqual(env["HTTPS_PROXY"], "http://proxy.invalid:3128")
        self.assertEqual(env["SSL_CERT_FILE"], "/etc/ssl/person.pem")
        self.assertEqual(env["GH_CONFIG_DIR"], "/home/person/.config/gh")
        self.assertEqual(M.gh_config_dir({}, "/home/p"), "/home/p/.config/gh")
        self.assertEqual(M.gh_config_dir({"XDG_CONFIG_HOME": "/x"}, "/home/p"), "/x/gh")
        self.assertEqual(M.gh_config_dir({"XDG_CONFIG_HOME": "/x", "GH_CONFIG_DIR": "/g"}, "/home/p"), "/g")
        # every gh argv in the tool's source names github.com
        with open(M.__file__, encoding="utf-8") as f:
            src = f.read()
        argvs = re.findall(r'\["gh", .*?\]', src, re.S)
        self.assertGreaterEqual(len(argvs), 3)
        for a in argvs:
            self.assertIn('"--hostname", "github.com"', a, a)
        # the real Host and GitHub hand gh that environment (nothing is started: subprocess.run is stood in for)
        seen = []

        def fake_run(argv, **kw):
            seen.append((list(argv), kw.get("env")))
            return subprocess.CompletedProcess(argv, 0, "HTTP/2.0 200 OK\r\n\r\n{}", "")
        host = M.Host()
        with mock.patch.dict(os.environ, self.LIVED), mock.patch.object(M.subprocess, "run", fake_run):
            host.run(["gh", "auth", "status", "--hostname", "github.com"])
            host.run_in(["gh", "api", "--hostname", "github.com", "x"], "{}")
            M.GitHub(host).api("GET", "repos/o/r")
            host.run(["true"])
        for argv, e in seen[:3]:
            self.assertIsNotNone(e, argv)
            self.assertFalse([n for n in M.GH_NOT_HANDED if n in e], argv)
            self.assertEqual(e.get("HTTPS_PROXY"), "http://proxy.invalid:3128")
        self.assertEqual(seen[2][0][-2:], ["--hostname", "github.com"])
        self.assertIsNone(seen[3][1], "a program other than gh runs with the environment as it is")
        # look, from a shell carrying a token and a host of its own: it names the account and what it does not use
        host = linux_host()
        host.environ = lambda: dict(self.LIVED, XDG_CONFIG_HOME="/home/person/.lived")
        code, lines = look(StandIn(load("look-recent-runs.jsonl")), host)
        self.assertIn("ok GitHub CLI installed and logged in: this tool acts as example-person, the login gh saved "
                      "for github.com, read from /home/person/.lived/gh (your shell's XDG_CONFIG_HOME; the watch reads "
                      "it there too)", lines)
        warn = [x for x in lines if x.startswith("warn Found in your shell and not used by this tool:")]
        self.assertEqual(len(warn), 1, lines)
        self.assertIn("GH_TOKEN, GITHUB_TOKEN, GH_HOST, GH_ENTERPRISE_TOKEN", warn[0])
        self.assertIn("The watch runs without your shell", warn[0])
        self.assertNotIn("made-up", "\n".join(lines))
        # the watch, run without the shell, reads the saved login where setup's look found it
        host.environ = lambda: {"HOME": "/home/person", "PATH": "/usr/bin"}
        self.assertEqual(host.gh_env()["GH_CONFIG_DIR"], "/home/person/.lived/gh")
        # no saved login for github.com: need you, and the shell's token is said as not used
        host = linux_host(runs={("gh", "api", "-i", "user"): (4, NO_LOGIN)})
        host.environ = lambda: dict(self.LIVED)
        code, lines = look(StandIn(load("look-recent-runs.jsonl")), host)
        self.assertEqual(code, M.EXIT_NEED)
        self.assertTrue(lines[0].startswith("need you: log in to GitHub: run `gh auth login`"), lines)
        self.assertIn("Found in your shell and not used by this tool: GH_TOKEN", lines[0])
        # logged in, the name not readable from GitHub's answer
        host = linux_host(runs={("gh", "api", "-i", "user"): (0, "HTTP/2.0 200 OK\r\n\r\n{}")})
        _, lines = look(StandIn(load("look-recent-runs.jsonl")), host)
        self.assertTrue([x for x in lines if "its name could not be read from gh's answer" in x], lines)
        # setup says it too, in its own lines
        host, sha = setup_host()
        fresh_look(host)
        host.environ = lambda: dict(self.LIVED)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertTrue(lines[1].startswith("ok GitHub CLI installed and logged in: this tool acts as example-person"),
                        lines)
        self.assertTrue(lines[2].startswith("warn Found in your shell and not used by this tool: GH_TOKEN"), lines)

    def test_G1_19_a_login_the_watch_cannot_read_stops_before_the_first_write(self):
        # R160 (owner's ruling 11-24, option A). Seen 2026-11-24 on a stock virtual machine with a keyring started by
        # hand: a login gh kept only in the keyring was read by setup's shell and never by the watch (a system timer),
        # so setup made everything, then stopped at the watch's first pass, which read the private repository as 404.
        no_token = (1, "no oauth token found for github.com\n")
        keyring = (0, "github.com\n  ✓ Logged in to github.com account example-person (keyring)\n"
                      "  - Token scopes: 'gist', 'read:org', 'repo', 'workflow'\n")
        # look: the stop, where gh keeps it, the one step, and five things to hear, before GitHub is asked anything
        host = linux_host(runs={("gh", "auth", "token"): no_token, ("own", "gh", "auth", "status"): keyring})
        gh = StandIn(load("look-recent-runs.jsonl"))
        code, lines = look(gh, host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        stop = next(x for x in lines if x.startswith("stop "))
        self.assertTrue(stop.startswith(f"stop {M.NO_LOGIN} the way this computer's check every 5 minutes will "
                                        "read it, when nobody is logged in: gh keeps it in this computer's keyring"), stop)
        self.assertIn("This tool made nothing, on this computer or on GitHub", stop)
        self.assertIn(f"`gh auth login --insecure-storage`, then `look {REPO}` again", stop)
        tells = [x for x in lines if x.startswith("tell the person ")]
        self.assertEqual(len(tells), 5, lines)
        said = " ".join(tells)
        hosts = os.path.join(host.gh_env()["GH_CONFIG_DIR"], "hosts.yml")
        for must in (f"keeps your GitHub login in {hosts}, as plain text", "your account and this computer's admins",
                     "(its scopes: gist, read:org, repo, workflow), on every repository of yours, not only this one",
                     "every copy or backup of your home folder", "if the disk is not encrypted",
                     "runs when nobody is logged in",
                     "If you would rather not, stop here: this tool made nothing, and this computer will not run jobs"):
            self.assertIn(must, said, must)
        self.assertNotRegex(said, r"same is true of the keyring|keyring is (also|as)")  # not read by us: not said
        self.assertNotIn("othing was made", " ".join(lines))  # gh's own files are gh's: only what this tool made is said
        self.assertEqual(gh.calls, [])
        self.assertNotIn(("gh", "api", "-i", "user", "--hostname", "github.com"), host.ran)
        self.assertEqual([a for a in host.ran if a[:1] == ("sudo",)], [])
        # "this tool made nothing" is true (owner's finding 11-24): on a home that has never held the tool's record, the
        # stop leaves no record behind, for the keyring stop and the could-not-be-read stop alike
        self.assertEqual(os.listdir(host.state_dir()), [], "the keyring stop made the tool's record")
        code, lines = run_setup(StandIn([]), host)  # setup after it: asks for a look, and still makes nothing
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertTrue(any(x.startswith(f"stop no look of {REPO} on this computer yet") for x in lines), lines)
        self.assertEqual(os.listdir(host.state_dir()), [], "setup after the keyring stop made the tool's record")
        unread_host = linux_host(runs={("gh", "auth", "token"): (1, "error connecting to the keyring\n")})
        code, lines = look(StandIn([]), unread_host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn("This tool made nothing, on this computer or on GitHub", next(x for x in lines if x.startswith("stop ")))
        self.assertEqual(os.listdir(unread_host.state_dir()), [], "the could-not-be-read stop made the tool's record")
        # where: a shell's token, or not read; the scopes left out when not read
        host = linux_host(runs={("gh", "auth", "token"): no_token, ("own", "gh", "auth", "status"): (
            0, "github.com\n  ✓ Logged in to github.com account example-person (GH_TOKEN)\n")})
        lines = look(StandIn([]), host)[1]
        self.assertIn("gh reads it from your shell's GH_TOKEN, which the check every 5 minutes does not have",
                      next(x for x in lines if x.startswith("stop ")))
        self.assertIn("allowed on your GitHub account, on every repository", " ".join(lines))
        lines = look(StandIn([]), linux_host(runs={("gh", "auth", "token"): no_token}))[1]
        self.assertIn("where gh keeps it could not be read here", next(x for x in lines if x.startswith("stop ")))
        # a gh whose own help names no `--insecure-storage` (no keyring there): the plain step, never a flag it lacks
        lines = look(StandIn([]), linux_host(runs={("gh", "auth", "token"): no_token,
                                                   ("gh", "auth", "login", "--hostname", "github.com", "--help"): (0, "  -w, --web  Open a browser\n")}))[1]
        stop = next(x for x in lines if x.startswith("stop "))
        self.assertIn("choose it after the lines below: `gh auth login`, then", stop)
        self.assertNotIn("insecure-storage", stop)
        # setup: the same stop, nothing made here or on GitHub, no record of a setup begun
        host, sha = setup_host()
        fresh_look(host)
        host.runs[("gh", "auth", "token")] = no_token
        host.runs[("own", "gh", "auth", "status")] = keyring
        gh = setup_gh(sha)
        data = M.load_state(host)
        data["gh"] = {"config_dir": os.path.join(TMP, "earlier-gh-place")}  # where an earlier look found it
        M.save_state(host, data)
        record = os.path.join(host.state_dir(), "minions.json")
        with open(record, "rb") as f:
            before = f.read()
        # the shell now names another place for gh's login: the read goes there, and the record is not touched
        moved = os.path.join(TMP, "moved-gh-place")
        read_at, run = [], host.run
        def run_seen(argv, timeout=20):
            if tuple(argv[:3]) == ("gh", "auth", "token"):
                read_at.append(host.gh_env()["GH_CONFIG_DIR"])
            return run(argv, timeout)
        host.run = run_seen
        with mock.patch.dict(os.environ, {"GH_CONFIG_DIR": moved}):
            code, lines = run_setup(gh, host)
        host.run = run
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertEqual(read_at, [moved])
        with open(record, "rb") as f:
            self.assertEqual(f.read(), before, "the setup stop changed the tool's record")
        self.assertTrue(any(x.startswith(f"stop {M.NO_LOGIN}") for x in lines), lines)
        self.assertEqual(admin_steps(host), [])
        self.assertEqual(gh.calls, [])
        self.assertNotIn("setting_up", M.load_state(host))
        # three answers (rule 6); gh's words never carry a token-shaped value; a gh with no `auth token` (before
        # 2.17, which keeps a login only in its file or the shell) is read from its file
        def kept(answer):
            return M.login_kept(linux_host(runs={("gh", "auth", "token"): answer}))
        self.assertEqual(kept((0, "made-up-login-value\n")), ("yes", ""))
        self.assertEqual(kept(no_token)[0], "no")
        for answer in ((1, "error connecting to the keyring\n"), (0, "two\nlines\n"), (0, ""),
                       (127, "[Errno 2] No such file or directory: 'gh'")):
            self.assertEqual(kept(answer)[0], "unread", answer)
        self.assertIn("could not be found", kept((127, "[Errno 2] No such file or directory: 'gh'"))[1])
        self.assertNotIn("ghp_made_up_value", kept((1, "ghp_made_up_value\nsomething else\n"))[1])
        old = (1, 'unknown command "token" for "gh auth"\n\nUsage:  gh auth <command> [flags]\n')
        h = linux_host(runs={("gh", "auth", "token"): old})
        path = os.path.join(h.gh_env()["GH_CONFIG_DIR"], "hosts.yml")
        h.files[path] = "github.com:\n    oauth_token: made-up-login-value\n    user: example-person\n"
        self.assertEqual(M.login_kept(h), ("yes", ""))
        h.files[path] = "github.com:\n    user: example-person\n    git_protocol: https\n"
        self.assertEqual(M.login_kept(h)[0], "no")
        del h.files[path]
        self.assertEqual(M.login_kept(h)[0], "unread")
        # could not be read: a stop that says so, nothing made, never a login step
        host = linux_host(runs={("gh", "auth", "token"): (1, "error connecting to the keyring\n")})
        lines = look(StandIn([]), host)[1]
        stop = next(x for x in lines if x.startswith("stop "))
        self.assertTrue(stop.startswith("stop gh could not be asked whether it has a login for github.com"), stop)
        self.assertIn("This tool made nothing", stop)
        self.assertNotIn("insecure-storage", stop)

    def test_G1_20_a_login_github_refuses_makes_no_record_on_a_fresh_home(self):
        # R160, owner 11-24: the need to log in comes after the login was read as the watch reads it, and before
        # gh's place is kept; on a home that never held the tool's record, it leaves none, for each of its answers
        for answer in ((4, NO_LOGIN), (1, "HTTP/2.0 401 Unauthorized\n\n{\"message\": \"Bad credentials\"}")):
            host = linux_host(runs={("gh", "api", "-i", "user"): answer})
            code, lines = look(StandIn([]), host)
            self.assertEqual(code, M.EXIT_NEED, lines)
            self.assertTrue(lines[0].startswith("need you: log in to GitHub: run `gh auth login`"), lines)
            self.assertEqual(os.listdir(host.state_dir()), [], f"the need to log in made the tool's record: {answer}")

    def test_G5_25_the_watch_and_status_say_each_reason_by_its_kind(self):
        # R160 (owner's ruling 11-24): seen 2026-11-24, a watch with no login it could read said "GitHub answered
        # HTTP 404" and "check this computer's network", and status said "GitHub does not list it" from a call sent
        # without a login. Each kind its own words and way out; an answer to a call without a login is never read.
        no_token = (1, "no oauth token found for github.com\n")

        class Gated(StatusGH):  # the real class's gate, in front of the stand-in's answers
            def api(self, method, path, body=None):
                gate = self.login_gate()
                if gate:
                    self.why = gate
                    return 0, {"message": gate}
                return super().api(method, path, body)
        host, clock = g5_host(runs={("gh", "auth", "token"): no_token})
        gh = Gated()
        gh.host = host
        code, lines = run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn(M.NO_LOGIN, lines[-1])
        self.assertIn(f"Your way out: in your own terminal, `gh auth login --insecure-storage` (run `look {REPO}` "
                      "first", lines[-1])
        self.assertNotRegex(lines[-1], r"HTTP 404|network")
        self.assertEqual(gh.calls, [])  # nothing was sent
        gh = Gated()
        gh.host = host
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertTrue(lines[0].startswith(f"stop {M.NO_LOGIN}"), lines[0])
        self.assertIn("`gh auth login --insecure-storage`", lines[0])
        self.assertFalse(any("does not list it" in x for x in lines), lines)
        self.assertEqual(gh.calls, [])
        # the real class: no process is started for a call while the login cannot be read
        with mock.patch.object(M.subprocess, "run", side_effect=AssertionError("a call was sent")):
            s, d = M.GitHub(linux_host(runs={("gh", "auth", "token"): no_token})).api("GET", f"repos/{REPO}")
        self.assertEqual(s, 0)
        self.assertIn(M.NO_LOGIN, d["message"])
        # HTTP 404 with a login: the repository is not there for this login, never the network
        class Gone(StatusGH):
            def api(self, method, path, body=None):
                if (method, path) == ("GET", f"repos/{REPO}"):
                    self.calls.append((method, path))
                    return 404, {"message": "Not Found"}
                return super().api(method, path, body)
        host, clock = g5_host()
        lines = run_cmd(M.watch, host=host, gh=Gone())[1]
        self.assertIn(f"GitHub answers that {REPO} is not there for this login (HTTP 404)", lines[-1])
        self.assertIn("Your way out: check the repository's name and this login's access to it on GitHub", lines[-1])
        self.assertNotIn("network", lines[-1])
        # no answer at all: the network
        host, clock = g5_host()
        lines = run_cmd(M.watch, host=host, gh=BlindGH(why="GitHub gave no answer: dial tcp: lookup api.github.com: "
                                                           "no such host"))[1]
        self.assertIn("Your way out: check this computer's network", lines[-1])
        # status: a runner list that was not read is never "does not list it"
        class Unread(StatusGH):
            def api(self, method, path, body=None):
                return (502, None) if "/actions/runners" in path else super().api(method, path, body)
        host, clock = g5_host()
        lines = run_cmd(M.status, REPO, host=host, gh=Unread())[1]
        self.assertFalse(any("does not list it" in x for x in lines), lines)
        self.assertTrue(any("GitHub not read (at " in x for x in lines), lines)

    def test_G1_18_the_login_read_has_three_answers(self):
        # R146 (family rule 6): logged in / no saved login / could not be read, each as Ubuntu's own gh 2.45.0 said it
        # on a stock virtual machine; the third never says to log in, log out or refresh.
        def read(answer):
            return M.login_read(linux_host(runs={("gh", "api", "-i", "user"): answer}))
        self.assertEqual(read((0, USER_OK)), ("yes", "example-person", ""))
        self.assertEqual(read((4, NO_LOGIN))[:2], ("no", None))
        self.assertEqual(read((0, "HTTP/2.0 401 Unauthorized\r\n\r\n{\"message\":\"Bad credentials\"}"))[:2], ("no", None))
        for answer in ((1, UNREACHABLE), (127, "timed out"), (0, "HTTP/2.0 502 Bad Gateway\r\n\r\n"),
                       (1, "X Failed to log in\n- To forget about this account, run: gh auth logout -h github.com\n"),
                       (4, "HTTP/2.0 403 Forbidden\r\n\r\n{}")):
            kind, name, why = read(answer)
            self.assertEqual((kind, name), ("unread", None), answer)
            self.assertNotRegex(why.lower(), r"gh auth|log ?in|log ?out|refresh", answer)
        _, _, why = read((1, UNREACHABLE))
        self.assertIn("connection refused", why)
        # look and setup: the third answer is a stop that says run the same command again, never a login
        host = linux_host(runs={("gh", "api", "-i", "user"): (1, UNREACHABLE)})
        code, lines = look(StandIn(load("look-recent-runs.jsonl")), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertEqual(len(lines), 1, lines)
        self.assertTrue(lines[0].startswith("stop GitHub could not be asked whether gh's saved login for github.com "
                                            "works: Get \"https://api.github.com/user\""), lines)
        self.assertIn("run the same command again", lines[0])
        self.assertNotRegex(lines[0], r"gh auth|log in|logout|refresh")
        host, sha = setup_host()
        fresh_look(host)
        host.runs[("gh", "api", "-i", "user")] = (1, UNREACHABLE)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue(lines[-1].startswith("stop GitHub could not be asked"), lines)
        # the login is read by `gh api -i user`, never by `gh auth status`'s exit (2.45.0: exit 0 with "Failed")
        host = linux_host(runs={("gh", "auth", "status"): (0, "  X Failed to log in to github.com account x\n"),
                                ("gh", "api", "-i", "user"): (1, UNREACHABLE)})
        self.assertEqual(look(StandIn(load("look-recent-runs.jsonl")), host)[0], M.EXIT_STOP)

    def test_G5_21_the_watch_starts_python_isolated(self):
        # R147: the watch's unit starts the tool with -I, as every start of Python by the tool does, so a person's
        # user site-packages (a usercustomize.py or a .pth file) never runs in a watch pass or stops it.
        svc, _ = M.watch_units("person", M.WATCH_EVERY_S)
        self.assertIn(f"ExecStart=/usr/bin/python3 -I {M.TOOL_COPY} watch\n", svc)
        with open(M.__file__, encoding="utf-8") as f:
            src = f.read()
        starts = re.findall(r"python3 [-/{][^\s\"'`]*", src)  # python3 then a flag, a path or a placeholder
        self.assertTrue(starts)
        self.assertEqual([x for x in starts if not x.startswith("python3 -I")], [], starts)
        hook = M.job_started_script(FOLDER, path="/usr/bin:/bin", every_s=300)
        self.assertTrue(all(x.startswith("python3 -I") for x in re.findall(r"python3 \S+", hook)))

    def test_G9_13_the_lived_in_list_holds_to_the_code(self):
        # R145: the lived-in round's list (tests/lived-in.md) names every setting the tool takes away from gh or
        # moves gh's login with, and what was not tried says so.
        with open(os.path.join(ROOT, "tests", "lived-in.md"), encoding="utf-8") as f:
            text = f.read()
        for name in (*M.GH_NOT_HANDED, *M.GH_PLACE_SETTINGS, "LANG", "LC_ALL", "LANGUAGE", "GIT_CONFIG_GLOBAL",
                     "insteadOf", "credential.helper", "core.hooksPath", "NODE_OPTIONS", "PYTHONPATH",
                     "env_keep", "timestamp_timeout", "HTTPS_PROXY"):
            self.assertIn(name, text, name)
        self.assertIn("not tried", text)
        self.assertIn("made-up", text)  # a planted token is never a real one


class Helpers(unittest.TestCase):
    def test_look_end_to_end_on_recorded_answers_reads_every_call_it_makes(self):
        gh = StandIn(load("look-recent-runs.jsonl"))
        code, lines = look(gh, linux_host())
        self.assertEqual(code, M.EXIT_NEED, lines)  # the start test's yes is still the person's (P1a)
        self.assertEqual(gh.misses, [], "every call look makes has a recorded answer")
        self.assertTrue(all(re.match(r"^(ok|warn|stop|need you:|tell the person \d+ of \d+:|-----) ", x) for x in lines), lines)

    def test_github_api_sends_bodies_on_stdin_never_argv(self):
        seen = {}

        def fake_run(argv, input=None, **kw):
            if argv[:3] == ["gh", "auth", "token"]:  # R160: the login read before the first call
                seen["token_env"] = kw.get("env") or {}
                return mock.Mock(stdout="made-up-login-value\n", stderr="", returncode=0)
            seen["argv"], seen["input"], seen["env"] = argv, input, kw.get("env") or {}
            return mock.Mock(stdout="HTTP/2.0 201 Created\nX: y\n\n{\"sha\": \"1\"}", stderr="", returncode=0)
        with mock.patch.object(M.subprocess, "run", fake_run):
            status, data = M.GitHub(M.Host()).api("POST", "repos/o/r/git/blobs", {"content": "SECRET-ish body"})
        self.assertEqual((status, data), (201, {"sha": "1"}))
        self.assertNotIn("SECRET-ish body", " ".join(seen["argv"]))
        self.assertIn("SECRET-ish body", seen["input"])
        self.assertEqual(seen["argv"][:2], ["gh", "api"])
        # R160: both read with no session bus, as the watch runs when nobody is logged in
        self.assertEqual(seen["env"].get("DBUS_SESSION_BUS_ADDRESS"), M.NO_BUS)
        self.assertEqual(seen["token_env"].get("DBUS_SESSION_BUS_ADDRESS"), M.NO_BUS)

    def test_parse_include_reads_status_and_body(self):
        self.assertEqual(M.parse_include("HTTP/2.0 204 No Content\nA: b\n\n"), (204, None))
        self.assertEqual(M.parse_include("HTTP/2.0 404 Not Found\nA: b\n\n{\"message\": \"x\"}"),
                         (404, {"message": "x"}))

    def test_classify_run_tells_the_four_cases_apart(self):
        self.assertEqual(M.classify_run({"conclusion": "startup_failure"}, [])[0], "stop")
        self.assertEqual(M.classify_run({"conclusion": "failure", "path": ".github/workflows/x.yml"}, [])[0], "broken")
        self.assertEqual(M.classify_run({"status": "queued"}, [{"status": "queued"}])[0], "ok")
        self.assertEqual(M.classify_run(None, [])[0], "warn")

    def test_G9_4_fixtures_hold_no_token_no_email_no_real_names(self):
        for name in [x for x in os.listdir(FIX) if x.endswith((".jsonl", ".html"))]:
            with open(os.path.join(FIX, name), encoding="utf-8") as f:
                t = f.read()
            self.assertIsNone(re.search(r"gh[opsu]_[A-Za-z0-9]{20,}|github_pat_", t), name)
            emails = set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", t))
            self.assertLessEqual(emails, {"person@example.invalid"}, name)
            # R25 (2026-11-23): the number GitHub gives an account names it as surely as its login does.
            self.assertIsNone(re.search(r"avatars\.githubusercontent\.com/u/[1-9]", t), name)
            if name.endswith(".jsonl"):
                def account_ids(o):
                    if isinstance(o, dict):
                        if isinstance(o.get("login"), str):
                            yield o.get("id"), o.get("node_id")
                        for v in o.values():
                            yield from account_ids(v)
                    elif isinstance(o, list):
                        for v in o:
                            yield from account_ids(v)
                for line in t.splitlines():
                    for i, node in account_ids(json.loads(line)):
                        self.assertIn(i, (None, 1), name)
                        self.assertIn(node, (None, "EXAMPLE_NODE_ID"), name)

    def test_G9_5_scrubbing_never_changes_paths(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("scrub", os.path.join(FIX, "scrub.py"))
        S = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(S)
        repo = "Some-Org/some-repo"
        rows = [{"method": "GET", "path": "repos/Some-Org/some-repo/commits/main", "status": 200, "data": {
            # GitHub's own merge committer: the name that once broke a path. It is not a person and stays.
            "committer": {"name": "GitHub", "login": "web-flow", "email": "noreply@github.com"},
            "author": {"name": "Pat Example-Real", "login": "patx", "email": "pat@real.example.com"},
            "files": [{"filename": ".github/workflows/ci.yml"}, {"filename": "GitHub/notes.md"}],
            "message": "patxyz is not patx; see .github/workflows/ci.yml",
            "html_url": "https://github.com/Some-Org/some-repo/commit/abc",
            "author_url": "https://github.com/patx",
            "full_name": "Some-Org/some-repo", "name": "some-repo"}},
            {"method": "GET", "path": "repos/Some-Org/some-repo/contents/.github/workflows", "status": 200,
             "data": [{"name": "ci.yml", "path": ".github/workflows/ci.yml"}]}]
        (a, b) = S.scrub_rows(rows, repo)
        d = a["data"]
        self.assertEqual([f["filename"] for f in d["files"]], [".github/workflows/ci.yml", "GitHub/notes.md"])
        self.assertEqual(b["data"][0]["path"], ".github/workflows/ci.yml")
        self.assertEqual(b["path"], "repos/example-org/example-repo/contents/.github/workflows")
        self.assertEqual(d["committer"]["name"], "GitHub")
        self.assertEqual(d["author"]["name"], "Example Person")
        self.assertEqual(d["author"]["login"], "example-person")
        self.assertEqual(d["author"]["email"], "person@example.invalid")
        self.assertEqual(d["author_url"], "https://github.com/example-person")
        self.assertEqual(d["message"], "patxyz is not patx; see .github/workflows/ci.yml")  # text is not a name field
        self.assertEqual(d["html_url"], "https://github.com/example-org/example-repo/commit/abc")
        self.assertEqual((d["full_name"], d["name"]), ("example-org/example-repo", "example-repo"))
        # R43: real answers. GitHub's answer for a commit whose author was named "workflows" (the same word as a path
        # segment) and which added docs/workflows/note.txt, recorded 2026-11-23 from the private proof repository
        # and scrubbed by scrub.py: the name became Example Person, the path kept its word.
        with open(os.path.join(FIX, "path-word-author.jsonl"), encoding="utf-8") as f:
            real = json.loads(f.readline())
        self.assertEqual(real["data"]["commit"]["author"]["name"], "Example Person")
        self.assertEqual(real["data"]["commit"]["committer"]["name"], "Example Person")
        self.assertEqual([f["filename"] for f in real["data"]["files"]], ["docs/workflows/note.txt"])
        self.assertTrue(real["path"].startswith("repos/example-org/example-repo/commits/"))
        # Every committed fixture: scrubbing it again changes no path.
        for name in [x for x in os.listdir(FIX) if x.endswith(".jsonl")]:
            with open(os.path.join(FIX, name), encoding="utf-8") as f:
                fixture = [json.loads(x) for x in f if x.strip()]
            again = S.scrub_rows(fixture, "example-org/example-repo")

            def paths(o, out):
                if isinstance(o, dict):
                    for k, v in o.items():
                        (out.append(v) if k in ("path", "filename") and isinstance(v, str) else paths(v, out))
                elif isinstance(o, list):
                    for v in o:
                        paths(v, out)
                return out
            self.assertEqual(paths(again, []), paths(fixture, []), name)
            self.assertFalse([p for p in paths(fixture, []) if "example-person" in p], name)

    def test_the_start_workflow_asks_for_no_permission_and_an_unserved_label(self):
        self.assertIn("permissions: {}", M.START_WORKFLOW)
        self.assertIn("timeout-minutes: 1", M.START_WORKFLOW)
        self.assertNotIn("self-hosted", M.START_WORKFLOW)


GUARD_PLANTED = """import os, shutil, subprocess, sys, tempfile, unittest
sys.path.insert(0, {tests!r})
import machine_guard
REAL = machine_guard.REAL_HOME


class Planted(unittest.TestCase):
    def test_opens_the_real_home(self):
        try:
            open(os.path.join(REAL, ".gitconfig")).close()
        except OSError:
            pass

    def test_child_with_the_real_home(self):
        subprocess.run(["true"], env=dict(os.environ, HOME=REAL))

    def test_child_with_no_home(self):
        subprocess.run([sys.executable, "-I", "-c", "pass"], env={{"PATH": os.environ["PATH"]}})

    def test_runs_this_machines_own_program(self):
        real = shutil.which("systemctl", path="/usr/bin:/bin") or shutil.which("launchctl", path="/usr/bin:/bin")
        if real:  # a harmless read, so the guard taken out can run it
            subprocess.run([real, "--version" if real.endswith("systemctl") else "help"], capture_output=True)
        else:
            subprocess.run(["sudo", "-n", "true"], capture_output=True)

    def test_bash_child_starts_sudo(self):
        subprocess.run(["bash", "-c", "sudo -n true"], capture_output=True)

    def test_swallows_the_error(self):
        try:
            subprocess.run(["crontab", "-l"], capture_output=True)
        except Exception:
            pass

    def test_child_path_without_the_stand_ins(self):
        subprocess.run(["true"], env=dict(os.environ, PATH="/usr/bin:/bin"))

    def test_child_path_with_this_machines_folder_first(self):  # rule 17: the stand-ins are there, but stepped over
        subprocess.run(["true"], env=dict(os.environ, PATH="/usr/bin" + os.pathsep + os.environ["PATH"]))

    def test_the_validator_test_as_it_was(self):  # test_G8_1's six lines at 66e3625
        if shutil.which("gh") and subprocess.run(["gh", "skill", "--help"], capture_output=True).returncode == 0 \\
                and subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0:
            r = subprocess.run(["gh", "skill", "publish", "--dry-run"], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_brings_its_own_stand_in(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "crontab"), "w") as f:
                f.write("#!/bin/sh\\necho mine\\n")
            os.chmod(os.path.join(d, "crontab"), 0o755)
            r = subprocess.run([os.path.join(d, "crontab"), "-l"], capture_output=True, text=True)
            self.assertEqual(r.stdout, "mine\\n")

    def test_stays_in_its_own_home(self):
        self.assertNotEqual(os.path.realpath(os.path.expanduser("~")), REAL)
        with open(os.path.join(os.path.expanduser("~"), "x"), "w") as f:
            f.write("x")


def tearDownModule():
    machine_guard.check()


unittest.main()
"""
GUARD_CAUGHT = {"test_opens_the_real_home": "in this account's real home",
                "test_child_with_the_real_home": "with this account's real home as HOME",
                "test_child_with_no_home": "with no HOME",
                "test_runs_this_machines_own_program": ": ran ",
                "test_bash_child_starts_sudo": "ran sudo -n true (a child)",
                "test_swallows_the_error": "ran crontab",
                "test_child_path_without_the_stand_ins": "a PATH that leaves out the stand-ins",
                "test_child_path_with_this_machines_folder_first": "a PATH that puts /usr/bin before the stand-ins",
                "test_the_validator_test_as_it_was": "ran gh"}


class MachineGuard(unittest.TestCase):
    def test_G9_12_a_test_that_reaches_this_machine_is_stopped_and_counted(self):
        # Rule 17 (R142): every test file imports the guard first. This process and every child have a temporary
        # home, and loud stand-ins first on PATH; a test that runs this machine's admin, service, package,
        # scheduling, user or login commands, or touches its real home, is stopped at that call and counted, and
        # tearDownModule fails the run on any count.
        self.assertNotEqual(os.path.realpath(os.environ["HOME"]), machine_guard.REAL_HOME)
        self.assertTrue(os.environ["HOME"].startswith(tempfile.gettempdir()))
        for k in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "GIT_CONFIG_GLOBAL"):
            self.assertTrue(os.environ[k].startswith(os.environ["HOME"] + os.sep), k)
        self.assertEqual(os.environ["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], machine_guard.BIN)
        for name in ("sudo", "systemctl", "dpkg-query", "crontab", "useradd", "gh", "launchctl", "docker", "podman",
                     "systemd-run", "systemd-inhibit", "tmux", "claude"):
            self.assertIn(name, machine_guard.FORBIDDEN)
            self.assertEqual(shutil.which(name), os.path.join(machine_guard.BIN, name))
        # every program the tool and its hooks list is on the list, or left off with its reason
        for name in (*M.PROGRAMS, *M.HOOK_PROGRAMS, *M.DARWIN_ONLY):
            self.assertTrue(name in machine_guard.FORBIDDEN or name in machine_guard.LEFT_OFF, name)
        self.assertFalse(set(machine_guard.FORBIDDEN) & set(machine_guard.LEFT_OFF))
        # the tool's own Host, with no stand-in, is stopped and counted at each call
        real = M.Host()
        for argv in (["sudo", "-n", "true"], ["systemctl", "is-active", "x"], ["dpkg-query", "-W"], ["gh", "--version"]):
            with machine_guard.expect() as seen:
                with self.assertRaises(machine_guard.MachineReached):
                    real.run(argv)
            self.assertEqual(len(seen), 1, argv)
            self.assertIn(f"test_G9_12_a_test_that_reaches_this_machine_is_stopped_and_counted: ran {argv[0]}", seen[0])
        for touch in (lambda: open(os.path.join(machine_guard.REAL_HOME, ".minions", "journal.log")),
                      lambda: os.listdir(machine_guard.REAL_HOME)):
            with machine_guard.expect() as seen:
                with self.assertRaises(machine_guard.MachineReached):
                    touch()
            self.assertIn("in this account's real home", seen[0])
        self.assertEqual(real.state_dir(), os.path.join(os.environ["HOME"], ".minions"))  # "~" is the temporary home
        self.assertEqual(machine_guard.seen(), [])  # what expect() collected is not counted against the run
        FakeHost().run(["sudo", "-n", "true"])  # the stand-in for the machine reaches nothing
        self.assertEqual(machine_guard.seen(), [])

    def test_G9_12_each_control_is_seen_to_fail(self):
        # Part 5 of rule 17's guard: planted tests, one of each kind, in a test file of their own that imports the
        # guard. Guarded, each kind fails in its own result with the line that says why (the one that swallowed
        # the error too), the two that stay in their own home pass, and the run fails. The guard taken out
        # (MINIONS_GUARD_OFF=1, honoured only in this planted run: no hook, no count; the home and stand-ins kept,
        # so nothing real runs): all pass.
        tests = os.path.dirname(os.path.abspath(__file__))
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            path = os.path.join(d, "test_planted.py")
            with open(path, "w") as f:
                f.write(GUARD_PLANTED.format(tests=tests))
            run = lambda **env: subprocess.run([sys.executable, path, "-v"], capture_output=True, text=True,
                                               timeout=120, env=dict(os.environ, **env))
            r = run()
            self.assertNotEqual(r.returncode, 0, r.stderr)
            heads = [x for x in r.stderr.splitlines() if x.startswith(("FAIL: ", "ERROR: "))]
            for name, said in GUARD_CAUGHT.items():
                self.assertTrue([h for h in heads if h.split()[1] == name], (name, r.stderr[-3000:]))
                block = r.stderr.split(f": {name} ", 1)[1]
                self.assertIn(said, block, name)
            for name in ("test_brings_its_own_stand_in", "test_stays_in_its_own_home"):
                self.assertFalse([h for h in heads if h.split()[1] == name], (name, r.stderr[-3000:]))
            self.assertIn("ERROR: tearDownModule", r.stderr)
            r = run(MINIONS_GUARD_OFF="1")
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            self.assertIn("Ran 11 tests", r.stderr)
            self.assertNotIn("rule 17", r.stderr)

    def test_G9_12_the_guard_has_no_switch(self):
        # Rule 17, THE GUARD HAS NO SWITCH (the maintainer's ruling 2026-11-24): MINIONS_GUARD_OFF=1 set in the
        # suite's own run fails it with a line that says so, and the guard stays on; the counting-only setting is
        # gone, so setting it changes nothing.
        own = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_skill.py")
        run = lambda **env: subprocess.run([sys.executable, own], capture_output=True, text=True, timeout=120,
                                           env=dict(os.environ, **env))
        r = run(MINIONS_GUARD_OFF="1")
        self.assertNotEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("MINIONS_GUARD_OFF=1 was set in the suite's own run; the guard has no switch (rule 17)", r.stderr)
        self.assertEqual(run().returncode, 0)
        r = run(MINIONS_GUARD_MEASURE="1")
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        with open(machine_guard.__file__) as f:
            self.assertNotIn("MEASURE", f.read())
        self.assertFalse(machine_guard.OFF)
        self.assertNotIn("MINIONS_GUARD_OFF", os.environ)


def tearDownModule():
    machine_guard.check()  # rule 17 (R142): a test that reached this machine fails the run, even if it swallowed it


if __name__ == "__main__":
    unittest.main()
