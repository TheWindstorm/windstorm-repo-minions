"""Tests for scripts/minions.py, one per codon of the plan (DNA-STRAND-MASTER-PLAN.md), plus helpers.

GitHub is stood in for by answers recorded from a real private repository (tests/fixtures/*.jsonl, scrubbed by
tests/fixtures/scrub.py); the computer by FakeHost. Run: python tests/test_minions.py -v
"""
import atexit
import copy
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import minions as M  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
TMP = tempfile.mkdtemp(prefix="minions-test-")
atexit.register(shutil.rmtree, TMP, True)
REPO = "example-org/example-repo"
BRANCH = re.compile(r"minions-start-test-[0-9a-f]{8}")


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
    def __init__(self, system="Linux", files=None, runs=None, which=("gh", "sudo", "systemctl"), uid=1000,
                 groups=("users",), dirs=None, links=None, page="", free=(100 * 1024 ** 3, 500 * 1024 ** 3)):
        self._system, self.files, self.runs = system, dict(files or {}), dict(runs or {})
        self._which, self._uid, self._groups = set(which), uid, list(groups)
        self.dirs, self.links, self.page, self.free = dict(dirs or {}), dict(links or {}), page, free
        self.ran, self.fed = [], []  # every argv run; (argv, standard input) of every run_in
        self._state = tempfile.mkdtemp(dir=TMP)
        self.home_mode, self.download, self.validated, self.tty = 0o750, b"a stand-in runner archive", 0, False

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

    def isatty(self):
        return self.tty

    def sudo_validate(self):
        self.validated += 1
        self.runs[("sudo", "-n", "true")] = (0, "")
        return 0

    def mode_of(self, path):
        return self.home_mode if path == self.home() else 0o700

    def which(self, name):
        return f"/usr/bin/{name}" if name in self._which else None

    def run(self, argv, timeout=20):
        self.ran.append(tuple(argv))
        return self._answer(argv)

    def read(self, path):
        return self.files.get(path)

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
MEMINFO = "MemTotal:       16384000 kB\nMemAvailable:    8192000 kB\nSwapTotal:       2097152 kB\nSwapFree:        2097152 kB\n"
ROUTE = "Iface\tDestination\tGateway\nenp3s0\t00000000\t0101A8C0\n"
TCP = ("  sl  local_address rem_address   st\n"
       "   0: 00000000:01BB 00000000:0000 0A\n"
       "   1: 0100007F:1538 00000000:0000 0A\n"
       "   2: 00000000:0016 0A0B0C0D:D431 01\n")


def linux_host(**kw):
    files = {"/etc/os-release": 'PRETTY_NAME="Example Linux 1"\nID=ubuntu\nID_LIKE=debian\n',
             "/proc/meminfo": MEMINFO, "/proc/net/route": ROUTE, "/proc/net/tcp": TCP,
             "/sys/devices/nvme0n1/queue/rotational": "0\n"}
    files.update(kw.pop("files", {}))
    runs = {("gh", "auth", "status"): (0, SCOPES), ("sudo", "-n", "true"): (0, ""),
            ("systemctl", "is-enabled"): (0, "enabled\nenabled\n")}
    runs.update(kw.pop("runs", {}))
    links = {"/sys/dev/block/259:2": "/sys/devices/nvme0n1/nvme0n1p2"}
    with open(os.path.join(FIX, "billing-page-excerpt.html"), encoding="utf-8") as f:
        page = f.read()
    return FakeHost(files=files, runs=runs, links=links, page=kw.pop("page", page), **kw)


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
        self.assertIn("ok organisation: a runner group limited to the private repositories you choose", lines)
        rows = with_repo(self.recent, lambda d: d["owner"].update(type="User"))
        _, lines = look(StandIn(rows), linux_host())
        self.assertIn("ok personal account: one runner per repository", lines)

    def test_G1_3_look_names_missing_token_rights(self):
        rows = with_repo(self.recent, lambda d: d["permissions"].update(admin=False))
        host = linux_host(runs={("gh", "auth", "status"): (0, SCOPES.replace(", 'workflow'", ""))})
        _, lines = look(StandIn(rows), host)
        self.assertTrue(any(x.startswith("need you: admin on the repository") for x in lines), lines)
        self.assertIn("need you: your login lacks the `workflow` scope: run `gh auth refresh -s workflow`", lines)
        host = linux_host(runs={("gh", "auth", "status"): (0, "  ✓ Logged in to github.com account x\n")})
        _, lines = look(StandIn(self.recent), host)
        self.assertTrue(any("could not read the token's scopes" in x for x in lines), lines)

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
        self.assertIn("warn 1 port(s) open to the network here: 443", lines)
        self.assertIn("warn 1 container(s) of yours running: my-db (podman, yours)", lines)
        self.assertTrue(any("serves something live" in x for x in lines), lines)
        self.assertEqual(M.listening_ports(TCP), {443}, "loopback and non-listening sockets are not counted")
        host = linux_host(files={"/proc/net/tcp": "  sl local st\n"})
        _, lines = look(StandIn(self.recent), host)
        self.assertIn("ok no port open to the network", lines)
        self.assertFalse(any("serves something live" in x for x in lines))

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
        self.assertIn("warn 1 other runner service(s) of yours here: actions.runner.o-r.box-r.service", lines)
        self.assertIn("warn 1 other runner service(s) here that do not run as you (not named)", lines)
        self.assertTrue(any("serves something live" in x for x in lines), lines)

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
        self.assertIn("already asks for a self-hosted runner", k[2][1])
        self.assertIn("chosen when the job runs; not read", k[3][1])
        # Through look, on the recorded repository's four workflows.
        _, lines = look(StandIn(self.recent), linux_host())
        self.assertIn("warn .github/workflows/c3-a-self-hosted-alone.yml job j: already asks for a self-hosted "
                      "runner (self-hosted)", lines)
        self.assertIn("warn .github/workflows/c3-c-ours-only.yml job j: asks for labels minions-c3", lines)

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
            self.assertFalse(any("lists \"Self-hosted runners\"" in x for x in lines))

    def test_G1_10_no_github_cli_or_login_is_the_first_need_you(self):
        gh = StandIn(self.recent)
        code, lines = look(gh, linux_host(which=("sudo",)))
        self.assertEqual(lines, ["need you: install the GitHub CLI: sudo apt install gh"])
        self.assertEqual(code, M.EXIT_NEED)
        self.assertEqual(gh.calls, [])
        host = linux_host(runs={("gh", "auth", "status"): (1, "You are not logged into any GitHub hosts.")})
        code, lines = look(StandIn(self.recent), host)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("need you: log in to GitHub: run `gh auth login`"), lines)
        mac = FakeHost(system="Darwin", which=())
        _, lines = look(StandIn(self.recent), mac)
        self.assertEqual(lines, ["need you: install the GitHub CLI: brew install gh"])

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


SHOW = ("Slice=minions.slice\nNice=10\nCPUWeight=50\nRestart=on-failure\n"
        "Wants=network-online.target\nUnitFileState=enabled\nUser=minions-runner\n")


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
        ("sudo", "-n", "stat", "-c", "%a %U %n"): (0, creds),
        ("sudo", "-n", "test", "-e"): (1, ""),
        ("sudo", "-n", "install"): (0, ""),
        ("sudo", "-n", "-u", "minions-runner", "--", "sha256sum"): (0, f"{sha}  {FOLDER}/runner.tgz\n"),
        ("sudo", "-n", "-u", "minions-runner", "--", "tar"): (0, ""),
        ("sudo", "-n", "-u", "minions-runner", "--", "rm"): (0, ""),
        ("sudo", "-n", f"{FOLDER}/bin/installdependencies.sh"): (0, "-----\nlibicu found\n"),
        ("sudo", "-n", "-u", "minions-runner", "--", "bash", "-c", M.CONFIG_SCRIPT):
            (0, "√ Connected to GitHub\n√ Runner successfully added\n√ Settings Saved.\n"),
        ("sudo", "-n", "bash", "-c"): (0, "Creating launch runner in /etc/systemd/system/" + UNIT + "\n"),
        ("sudo", "-n", "cat", f"{FOLDER}/.service"): (0, UNIT + "\n"),
        ("sudo", "-n", "tee"): (0, ""),
        ("sudo", "-n", "systemctl"): (0, ""),
        ("systemctl", "show", UNIT): (0, SHOW),
        ("systemctl", "show", "minions.slice"): (0, "CPUQuotaPerSecUSec=4s\nMemoryMax=10066329600\n"),
        ("systemctl", "is-active", UNIT): (0, "active\n"),
    })
    host.runs.update(extra)
    return host, sha


def setup_gh(sha, overrides=None):
    o = {("POST", f"repos/{REPO}/actions/runners/registration-token"): (201, {"token": TOKEN,
                                                                           "expires_at": "2026-11-22T23:00:00Z"}),
         ("GET", f"repos/{REPO}/actions/runners?per_page=100"): runners_answer()}
    o.update(overrides or {})
    return StandIn(setup_rows(sha), o)


def fresh_look(host, minutes=5, stopped=False, survey=True):
    when = (M.dt.datetime.now(M.dt.timezone.utc) - M.dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = {"system": "Linux", "machine": "x86_64", "cores": 8, "mem": 16384000 * 1024} if survey else {}
    M.save_state(host, {"looks": {REPO: {"time": when, "stopped": stopped, "survey": s}}})


def run_setup(gh, host, interactive=False):
    out = io.StringIO()
    code = M.setup(REPO, host=host, gh=gh, out=out, interactive=interactive)
    return code, out.getvalue().splitlines()


def admin_steps(host):
    return [a for a in host.ran if a[:1] == ("sudo",) and a[:3] != ("sudo", "-n", "true")]


class G2(unittest.TestCase):
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
        self.assertEqual(set(entry["survey"]), {"system", "machine", "cores", "mem", "disk", "free", "battery", "os"})
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
        need = [x for x in lines if x.startswith("need you:")]
        self.assertTrue(need[0].startswith("need you: type this computer's admin password once"), lines)
        self.assertLess(lines.index(need[0]), lines.index("ok admin rights ready (asked once)"))
        self.assertEqual(sum(x.startswith("need you:") for x in lines), 1)
        # not on a terminal: a blocking need-you, nothing touched
        host, sha = setup_host(runs={("sudo", "-n", "true"): (1, "sudo: a password is required")})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual((code, host.validated, admin_steps(host)), (M.EXIT_NEED, 0, []))
        self.assertIn("run `sudo -v` in your terminal", lines[-1])
        # a runner user that is in an admin group is refused
        for groups in ("minions-runner sudo", "minions-runner docker", "minions-runner wheel", "minions-runner users"):
            host, sha = setup_host(runs={("id", "-nG", "minions-runner"): (0, groups + "\n"),
                                         ("id", "-u", "minions-runner"): (0, "998\n")})
            fresh_look(host)
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual(code, M.EXIT_STOP)
            self.assertIn("own group only", lines[-1])
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
        self.assertIn("checksum is not the one GitHub publishes", lines[-1])
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
        self.assertIn(("sudo", "-n", "install", "-d", "-o", "minions-runner", "-g", "minions-runner", "-m", "0700",
                       FOLDER), host.ran)
        self.assertIn(("sudo", "-n", "-u", "minions-runner", "--", "tar", "xzf", f"{FOLDER}/runner.tgz", "-C",
                       FOLDER), host.ran)
        self.assertIn('--work _work', M.CONFIG_SCRIPT)
        config = [a for a, _ in host.fed if "bash" in a and M.CONFIG_SCRIPT in a][0]
        self.assertEqual(config[-4], FOLDER)
        self.assertFalse(any(a[3:4] == ("cp",) or "cp" in a[:5] for a in host.ran), "unpacked fresh, never copied")
        # a folder that is there already is never reused
        host, sha = setup_host(runs={("sudo", "-n", "test", "-e"): (0, "")})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("exists already", lines[-1])
        self.assertFalse(any("tar" in a for a in host.ran))

    def test_G2_5_the_registration_token_goes_by_stdin_only(self):
        host, sha = setup_host()
        echo = host.runs[("sudo", "-n", "-u", "minions-runner", "--", "bash", "-c", M.CONFIG_SCRIPT)]
        host.runs[("sudo", "-n", "-u", "minions-runner", "--", "bash", "-c", M.CONFIG_SCRIPT)] = \
            lambda argv, input: (echo[0], echo[1] + f"token was {input.strip()}\n")  # a runner that echoes it
        fresh_look(host)
        out = io.StringIO()
        code = M.setup(REPO, host=host, gh=setup_gh(sha), out=out, interactive=False)
        self.assertEqual(code, M.EXIT_OK, out.getvalue())
        self.assertFalse([a for a in host.ran if any(TOKEN in x for x in a)], "the token is on a command line")
        fed = [(a, i) for a, i in host.fed if i and TOKEN in i]
        self.assertEqual(len(fed), 1)
        self.assertEqual(fed[0][1], TOKEN + "\n")
        self.assertIn("read -r T", fed[0][0][7])
        self.assertNotIn("--token", " ".join(fed[0][0]))
        self.assertFalse(re.search(r"\benv\b", M.CONFIG_SCRIPT))
        self.assertNotIn(TOKEN, out.getvalue())
        for name in os.listdir(host.state_dir()):
            with open(os.path.join(host.state_dir(), name), errors="replace") as f:
                self.assertNotIn(TOKEN, f.read(), name)

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
        self.assertIn(("sudo", "-n", "chmod", "0600", f"{FOLDER}/.credentials", f"{FOLDER}/.runner",
                       f"{FOLDER}/.credentials_rsaparams"), host.ran)
        chmod = host.ran.index(("sudo", "-n", "chmod", "0600", f"{FOLDER}/.credentials", f"{FOLDER}/.runner",
                                f"{FOLDER}/.credentials_rsaparams"))
        readback = [i for i, a in enumerate(host.ran) if a[:5] == ("sudo", "-n", "stat", "-c", "%a %U %n")]
        self.assertTrue(readback and readback[0] > chmod)
        # the runner's own 664, as seen (C3), left as it was: stop
        host, sha = setup_host(runs={("sudo", "-n", "stat", "-c", "%a %U %n"): (
            0, f"664 minions-runner {FOLDER}/.credentials\n664 minions-runner {FOLDER}/.runner\n"
               f"600 minions-runner {FOLDER}/.credentials_rsaparams\n")})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("credential files are not its own only", lines[-1])
        self.assertFalse(any("svc.sh" in " ".join(a) for a in host.ran), "no service before the files are closed")

    def test_G2_9_the_runner_is_a_boot_service_started_after_its_limits(self):
        host, sha = setup_host()
        fresh_look(host)
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        tees = {a[-1]: i for a, i in host.fed if a[:3] == ("sudo", "-n", "tee")}
        dropin = tees[f"/etc/systemd/system/{UNIT}.d/50-minions.conf"]
        for line in ("Wants=network-online.target", "Slice=minions.slice", "Nice=10", "CPUWeight=50",
                     "Restart=on-failure", "RestartSec=30"):
            self.assertIn(line + "\n", dropin)
        sl = tees["/etc/systemd/system/minions.slice"]
        self.assertIn("CPUQuota=400%\n", sl)  # half of 8 cores
        self.assertIn(f"MemoryMax={int(16384000 * 1024 * 0.6)}\n", sl)
        order = [" ".join(a) for a in host.ran]
        svc = next(i for i, a in enumerate(order) if "svc.sh install" in a)
        tee = max(i for i, a in enumerate(order) if a.startswith("sudo -n tee"))
        reload_ = order.index("sudo -n systemctl daemon-reload")
        enable = order.index(f"sudo -n systemctl enable {UNIT}")
        start = order.index(f"sudo -n systemctl restart {UNIT}")
        show = next(i for i, a in enumerate(order) if a.startswith(f"systemctl show {UNIT}"))
        self.assertTrue(svc < tee < reload_ < enable < start < show, order)
        # read back not as written: stop
        host, sha = setup_host(runs={("systemctl", "show", UNIT): (0, SHOW.replace("Slice=minions.slice",
                                                                                   "Slice=system.slice"))})
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("Slice='system.slice'", lines[-1])
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
        for s in ("useradd", "download", "unpack", "register", "chmod", "svc.sh"):
            self.assertIn(s, steps)

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


class Helpers(unittest.TestCase):
    def test_look_end_to_end_on_recorded_answers_reads_every_call_it_makes(self):
        gh = StandIn(load("look-recent-runs.jsonl"))
        code, lines = look(gh, linux_host())
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertEqual(gh.misses, [], "every call look makes has a recorded answer")
        self.assertTrue(all(re.match(r"^(ok|warn|stop|need you:) ", x) for x in lines), lines)

    def test_github_api_sends_bodies_on_stdin_never_argv(self):
        seen = {}

        def fake_run(argv, input=None, **kw):
            seen["argv"], seen["input"] = argv, input
            return mock.Mock(stdout="HTTP/2.0 201 Created\nX: y\n\n{\"sha\": \"1\"}", stderr="", returncode=0)
        with mock.patch.object(M.subprocess, "run", fake_run):
            status, data = M.GitHub(M.Host()).api("POST", "repos/o/r/git/blobs", {"content": "SECRET-ish body"})
        self.assertEqual((status, data), (201, {"sha": "1"}))
        self.assertNotIn("SECRET-ish body", " ".join(seen["argv"]))
        self.assertIn("SECRET-ish body", seen["input"])
        self.assertEqual(seen["argv"][:2], ["gh", "api"])

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

    def test_the_start_workflow_asks_for_no_permission_and_an_unserved_label(self):
        self.assertIn("permissions: {}", M.START_WORKFLOW)
        self.assertIn("timeout-minutes: 1", M.START_WORKFLOW)
        self.assertNotIn("self-hosted", M.START_WORKFLOW)


if __name__ == "__main__":
    unittest.main()
