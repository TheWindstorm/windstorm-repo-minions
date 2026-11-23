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
import subprocess
import sys
import time
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
    def __init__(self, system="Linux", files=None, runs=None, which=None, uid=1000,
                 groups=("users",), dirs=None, links=None, page="", free=(100 * 1024 ** 3, 500 * 1024 ** 3)):
        self._system, self.files, self.runs = system, dict(files or {}), dict(runs or {})
        if which is None:  # every program the tool and its hooks run (G8.9); no container engine
            which = ("gh", "sudo", "systemctl", *M.PROGRAMS, *M.HOOK_PROGRAMS)
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
        self.assertTrue(line.startswith("tell the person: GitHub's billing page, read 2026-11-22 18:00 UTC"), line)
        self.assertIn("Self-hosted runners.", line)
        self.assertIn(M.COST_URL, line)
        for page in (OSError("offline"), "<html><p>Something else entirely.</p></html>"):
            host = linux_host(page=page)
            _, lines = look(StandIn(self.recent), host)
            self.assertTrue(any(x.startswith("warn I could not read GitHub's rule today") for x in lines), lines)
            self.assertIn("tell the person: I could not read GitHub's page about charges today.", lines)
            self.assertFalse(any("lists \"Self-hosted runners\"" in x for x in lines))

    def test_G1_10_no_github_cli_or_login_is_the_first_need_you(self):
        gh = StandIn(self.recent)
        code, lines = look(gh, linux_host(which=("sudo",)))
        self.assertEqual(lines, ["need you: install the GitHub CLI: sudo apt update && sudo apt install gh"])
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


DROPIN_NAME = "50-minions.conf"
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
        ("systemctl", "is-active", "minions-watch.timer"): (0, "active\n"),
        ("sudo", "-n", "chown"): (0, ""),
        ("sudo", "-n", "cat", f"{FOLDER}/.env"): (0, "LANG=C.UTF-8\n"),
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


def setup_gh(sha, overrides=None):
    o = {("POST", f"repos/{REPO}/actions/runners/registration-token"): (201, {"token": TOKEN,
                                                                           "expires_at": "2026-11-22T23:00:00Z"}),
         ("GET", f"repos/{REPO}/actions/runners?per_page=100"): runners_answer()}
    o.update(overrides or {})
    return StandIn(setup_rows(sha), o)


def fresh_look(host, minutes=5, stopped=False, survey=True):
    when = (M.dt.datetime.now(M.dt.timezone.utc) - M.dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = {"system": "Linux", "machine": "x86_64", "cores": 8, "mem": 16384000 * 1024,
         "free": 100 * 1024 ** 3} if survey else {}
    M.save_state(host, {"looks": {REPO: {"time": when, "stopped": stopped, "survey": s}}})


def fresh_seen(folder, value=None):
    """G5.15: the watch's record that it read the repository private, as a hook test needs it."""
    path = os.path.join(folder, "seen-private")
    with open(path, "w") as f:
        f.write((value if value is not None else str(int(time.time()))) + "\n")
    return path


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
                                                        "labels": [{"name": "minions"}, {"name": "minions-medium"}]}]}
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
        self.assertIn(f"ok moved: .github/workflows/ci.yml job test ran on {OURS} and succeeded (run 5)", lines)
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
        host2, sha = setup_host(runs={("sudo", "-n", "test", "-e"): (0, "")})  # the runner's folder is there
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
            script = os.path.join(folder, "done.sh")
            for floor, low in ((0, False), (1 << 62, True)):
                with mock.patch.object(M, "DISK_FLOOR", floor):
                    with open(script, "w") as f:
                        f.write(M.job_completed_script(folder))
                env = dict(os.environ, GITHUB_WORKSPACE=ws)
                r = subprocess.run(["bash", script], env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(os.listdir(ws), [])
                self.assertTrue(os.path.exists(outside))
                self.assertEqual(os.path.exists(os.path.join(folder, "_minions", "disk-low")), low)
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
        env = dict(os.environ, PATH=bindir + os.pathsep + os.environ.get("PATH", ""), MINIONS_TEST_LOG=log,
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
                                ("sudo", "-n", "kill", "-TERM"): kill,
                                ("systemctl", "is-active", UNIT): (3, "inactive\n")})[0]
        rep = M.Report(io.StringIO())
        self.assertTrue(M.stop_runner(host, UNIT, FOLDER, rep, drained=True))
        self.assertIn(("sudo", "-n", "kill", "-TERM", "777"), host.ran)  # by exact PID, SIGTERM, never SIGINT
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
            "%Y-%m-%dT%H:%M:%SZ"), "stopped": False, "survey": small}}})
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
            m = re.fullmatch(rf"repos/{re.escape(rp)}/actions/jobs/(\d+)/rerun", path)
            if method == "POST" and m:
                self.reruns.append(int(m.group(1)))
                return 201, {}
        return 404, {"message": "Not Found"}


def g5_host(runs=None, repos=(REPO,), worker=False):
    """A computer with our runner set up for each repository, the service active, and a survey."""
    r = {("systemctl", "is-active"): (0, "active\n"),
         ("pgrep", "-u", "minions-runner", "-f"): (0, "4321\n") if worker else (1, ""),
         ("sudo", "-n", "systemctl"): (0, ""), ("sudo", "-n", "kill"): (0, "")}
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


class G5(unittest.TestCase):
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
            line = next(x for x in lines if x.startswith("ok minions-box-1 for"))
            self.assertIn(f"for {REPO}: {word}.", line, (gh_busy, worker))
            self.assertIn("GitHub says online, " + ("busy" if gh_busy else "not busy"), line)
            self.assertIn("a job process runs (4321)" if worker else "no job process", line)
        # GitHub offline: never "idle", even with no job process here.
        host, _ = g5_host()
        line = next(x for x in run_cmd(M.status, REPO, host=host, gh=StatusGH(status="offline"))[1]
                    if x.startswith("ok minions-box-1 for"))
        self.assertIn(": not ready.", line)

    def test_G5_3_the_watch_runs_and_its_silence_is_noticed(self):
        # The units: a system timer every 5 minutes; its service runs as the person, the tool's root-owned copy.
        svc, tmr = M.watch_units("person", M.WATCH_EVERY_S)
        self.assertIn("User=person\n", svc)
        self.assertIn(f"ExecStart=/usr/bin/python3 {M.TOOL_COPY} watch\n", svc)
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

    def test_G5_4_a_repository_no_longer_private_is_stopped_after_a_short_drain(self):
        # A job still runs: labels off at once, a drain of at most 2 minutes, then stopped and kept stopped.
        host, clock = g5_host(worker=True)
        gh = StatusGH(private=False, busy=True)
        code, lines = run_cmd(M.watch, host=host, gh=gh)
        self.assertEqual(gh.puts, [(REPO, ["minions-held"])])
        self.assertTrue(any("until 23:02Z at the latest" in x for x in lines), lines)
        stop = ("sudo", "-n", "systemctl", "stop", f"actions.runner.example-org-example-repo.minions-box-1.service",
                "--no-pager")
        disable = ("sudo", "-n", "systemctl", "disable", "actions.runner.example-org-example-repo.minions-box-1.service",
                   "--no-pager")
        self.assertIn(stop, host.ran)
        self.assertIn(disable, host.ran)
        self.assertLess(host.ran.index(stop), host.ran.index(disable))
        self.assertLessEqual(clock.t, M.parse_stamp("2026-11-22T23:03:00Z"))  # within the short drain
        self.assertEqual(code, M.EXIT_STOP)
        # status says why, first.
        host2 = host
        lines = run_cmd(M.status, REPO, host=host2, gh=StatusGH(private=False))[1]
        self.assertTrue(lines[0].startswith(f"stop {REPO} was not private at"), lines)
        self.assertIn("anyone's fork can ask a public repository's runner for a job", lines[0])
        # resume: refused without the person's word, refused while GitHub says public, works once private again.
        code, lines = run_cmd(M.resume, REPO, host=host, gh=StatusGH(private=False))
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("--private-again", lines[-1])
        code, lines = run_cmd(M.resume, REPO, host=host, gh=StatusGH(private=False), private_again=True)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertIn("the runner stays stopped", lines[-1])
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

    def test_G5_12_a_runner_github_removed_is_named_and_registered_again(self):
        host, clock = g5_host()
        gh = StatusGH(listed=False)
        code, lines = run_cmd(M.watch, host=host, gh=gh)
        unit = "actions.runner.example-org-example-repo.minions-box-1.service"
        self.assertIn(("sudo", "-n", "systemctl", "stop", unit, "--no-pager"), host.ran)  # no restart loop
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
        self.assertEqual(named, ["warn job 'check' (run 555, started 22:40:00Z) was cut by a stop of this computer's "
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

    def test_G5_14_a_job_killed_by_our_limit_is_named_as_such(self):
        t0 = int(M.parse_stamp("2026-11-22T22:40:00Z").timestamp())
        failed = {"conclusion": "failure", "steps": [{"name": "Build", "conclusion": "failure"}]}
        # The slice's kill count rose during the job: named as our limit, with the one way to change it.
        host, gh, _ = self._named(f"start {t0} 555 1 0 check\nend {t0 + 70} 555 1 1 check\n", failed,
                                  notes=["Process completed with exit code 137."])
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        named = [x for x in lines if x.startswith("warn job 'check'")]
        self.assertEqual(named, ["warn job 'check' (run 555, started 22:40:00Z) was stopped by this computer's memory "
                                 "limit (4.0 GB), not by your code failing; GitHub shows it as failed. The one way to "
                                 "change the limit: `setup --limits <processors>,<memory %>`. To run it again, once: "
                                 f"`status {REPO} --rerun 9001`."])
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
            env = dict(os.environ, GITHUB_RUN_ID="555", GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="check",
                       GITHUB_WORKSPACE=os.path.join(d, "nowhere"), PATH="/usr/bin:/bin")
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

    def gone(self, path):
        self.paths = {p for p in self.paths if p != path and not p.startswith(path.rstrip("/") + "/")}

    def answer(self, argv, input=None):
        a = list(argv)
        if self.interrupt_on and self.interrupt_on(a):
            self.interrupt_on = None
            raise KeyboardInterrupt
        if a[:2] == ["sudo", "-n"]:
            a = a[2:]
            if a[:3] == ["-u", "minions-runner", "--"]:
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
        if a[:2] == ["bash", "-c"] and "svc.sh uninstall" in a[2]:
            self.order.append("svc.sh uninstall")
            for p in (f"{FOLDER}/.service", f"/etc/systemd/system/{UNIT}"):
                self.gone(p)
            self.active.pop(UNIT, None)
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
            return (0, "uid=1001(minions-runner)") if self.user else (1, "no such user")
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
    Clock(host)
    data = {"runners": {REPO: {"name": OURS, "folder": FOLDER, "unit": UNIT, "labels": ["minions", "minions-medium"]}}}
    if moved:
        data["moves"] = {REPO: {"pr": 12, "branch": "minions-move-1", "base": "main", "files": {
            ".github/workflows/ci.yml": [{"id": "test", "name": "Unit tests",
                                          "runs_on": "    runs-on: ubuntu-latest   # the person's own comment\n"}]}}}
    M.save_state(host, data)
    return gh, world, host


def run_uninstall(gh, host, repo=REPO, **kw):
    out = io.StringIO()
    code = M.uninstall(repo, host=host, gh=gh, out=out, interactive=False, **kw)
    return code, out.getvalue().splitlines()


class G6(unittest.TestCase):
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
        for step in ("remove ", "hold ", "stop ", "svc.sh uninstall", "config.sh remove", "delete "):
            self.assertIn(step, j)
        # A job still running at the deadline: `waiting`, exit 4, the next command named, nothing taken away.
        gh, world, host = remove_setup(busy=(True,))
        code, lines = run_uninstall(gh, host, wait_s=60, poll_s=10)
        self.assertEqual(code, M.EXIT_WAIT)
        self.assertTrue(lines[-1].startswith("waiting") and "`uninstall example-org/example-repo` again" in lines[-1])
        self.assertIsNotNone(gh.runner)
        self.assertIn(FOLDER, world.paths)

    def test_G6_6_uninstall_runs_as_the_runner_and_github_goes_first(self):
        gh, world, host = remove_setup()
        self.assertEqual(run_uninstall(gh, host, everything=True)[0], M.EXIT_NEED)  # the put-back PR is the person's
        # The runner's own removal ran as the runner user, after svc.sh uninstall (it refuses while the service is
        # installed) and before anything was deleted; the runner user went last.
        o = world.order
        self.assertLess(o.index("svc.sh uninstall"), o.index("config.sh remove"))
        first_delete = min(i for i, x in enumerate(o) if x.startswith("rm "))
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
        self.assertFalse(any(x.startswith("rm ") for x in world.order))

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

    def test_G6_3_uninstall_ends_with_a_census(self):
        gh, world, host = remove_setup()
        code, lines = run_uninstall(gh, host)
        census = [x for x in lines if "census:" in x]
        self.assertIn(f"ok census: runners on GitHub for {REPO}: none", census)
        self.assertIn("ok census: runner services: none", census)
        self.assertTrue(any("the runner user: minions-runner (kept; `uninstall --all` takes it)" in x for x in census))
        self.assertTrue(any(x.startswith("ok census: the slice and the watch") and "kept" in x for x in census))
        gh, world, host = remove_setup()
        code, lines = run_uninstall(gh, host, everything=True)
        census = [x for x in lines if "census:" in x]
        self.assertEqual(len(census), 5, census)
        self.assertTrue(all(x.startswith("ok census:") and x.endswith(": none") for x in census), census)
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
        gh, world, host = remove_setup(interrupt_on=lambda a: a[:3] == ["sudo", "-n", "rm"])
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


class G8(unittest.TestCase):
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
            for line in rep.lines:
                self.assertTrue(any(line.startswith(k) for k in M.LINE_KINDS), line)
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
    env = dict(os.environ, PATH="/usr/bin:/bin", GITHUB_RUN_ID="77", GITHUB_RUN_ATTEMPT="1", GITHUB_JOB="j")
    env.pop("GITHUB_EVENT_PATH", None)
    if event is not None:
        env["GITHUB_EVENT_PATH"] = os.path.join(folder, "event.json")
        with open(env["GITHUB_EVENT_PATH"], "w") as f:
            json.dump(event, f)
    r = subprocess.run(["bash", path], env=env, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


class P1a(unittest.TestCase):
    """What the two cold starts (P1a, 2026-11-22) found: each fix and its test."""

    def test_G7_10_look_prints_what_to_tell_the_person_before_setup(self):
        code, lines = look(StandIn(load("look-recent-runs.jsonl")), linux_host())
        tells = [x[len("tell the person: "):] for x in lines if x.startswith("tell the person: ")]
        said = " ".join(tells)
        for words in ("Anyone who can push to example-org/example-repo can run code on this computer",
                      "This is for a private repository only",
                      "While this computer is off, asleep or held, your checks wait. GitHub cancels a check that has "
                      "waited 24 hours.",
                      "A restart of this computer cancels the job running on it",
                      "This version serves one repository per computer",
                      "GitHub's billing page, read "):
            self.assertIn(words, said)
        limits = next(x for x in lines if x.startswith("ok jobs here get at most"))
        self.assertIn(limits[len("ok "):], tells)  # the limits, word for word as look printed them
        self.assertFalse(any(x.startswith("This computer has a battery") for x in tells))  # no laptop sentence
        # A laptop: its sentence is said too.
        host = linux_host(files={"/sys/class/power_supply/BAT0/type": "Battery\n"},
                          dirs={"/sys/class/power_supply": ["BAT0"]})
        lines = look(StandIn(load("look-recent-runs.jsonl")), host)[1]
        self.assertIn("tell the person: This computer has a battery: " + M.LAPTOP_WORDS + ".", lines)
        # They come after the reading, all together, so an agent says them before setup.
        first = min(i for i, x in enumerate(lines) if x.startswith("tell the person: "))
        self.assertTrue(all(x.startswith("tell the person: ") for x in lines[first:]), lines[first:])

    def test_G6_7_uninstall_says_the_put_back_checks_before_its_pull_request(self):
        gh, world, host = remove_setup()
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        tell = next(i for i, x in enumerate(lines) if x.startswith("tell the person: The pull request that puts 1 "
                                                                    "job(s) back starts its own checks"))
        ask = next(i for i, x in enumerate(lines) if x.startswith("need you: read and merge pull request #12"))
        self.assertLess(tell, ask)
        self.assertIn("runs there once for it, before you merge", lines[tell])

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
        self.assertIn("this computer takes no job of it now", lines[-1])
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
        self.assertIn("Your way out: log in again with `gh auth login`", lines[-1])
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
            p = subprocess.Popen(["bash", os.path.join(folder, "s.sh")], env=dict(os.environ, PATH="/usr/bin:/bin"),
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
            self.assertEqual(named, [f"warn job 'check' (run 555, started 22:40:00Z) was refused before its first step "
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
        self.assertTrue(lines[-1].startswith("stop the watch's first pass could not read whether the repository is "
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
        self.assertIn("awk (/home/person/bin/awk)", lines[-1])


class Helpers(unittest.TestCase):
    def test_look_end_to_end_on_recorded_answers_reads_every_call_it_makes(self):
        gh = StandIn(load("look-recent-runs.jsonl"))
        code, lines = look(gh, linux_host())
        self.assertEqual(code, M.EXIT_NEED, lines)  # the start test's yes is still the person's (P1a)
        self.assertEqual(gh.misses, [], "every call look makes has a recorded answer")
        self.assertTrue(all(re.match(r"^(ok|warn|stop|need you:|tell the person:) ", x) for x in lines), lines)

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


if __name__ == "__main__":
    unittest.main()
