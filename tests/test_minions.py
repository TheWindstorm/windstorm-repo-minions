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
        reload_ = order.index("sudo -n systemctl daemon-reload --no-pager")
        enable = order.index(f"sudo -n systemctl enable {UNIT} --no-pager")
        start = order.index(f"sudo -n systemctl restart {UNIT} --no-pager")
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


class G8(unittest.TestCase):
    def test_G8_3_commands_and_line_kinds_are_exactly_these(self):
        # The commands built so far (look, setup, move) are the plan's first ones, in its order; more come with genes.
        plan = ("look", "setup", "move", "lint", "status", "watch", "hold", "resume", "drain", "doctor", "remove",
                "self-update")
        sub = next(a for a in M.parser()._actions if isinstance(a, M.argparse._SubParsersAction))
        self.assertEqual(tuple(sub.choices), M.COMMANDS)
        self.assertEqual(M.COMMANDS, plan[:len(M.COMMANDS)])
        # Five line kinds; the worst decides the code: 0 ok/warn, 2 stop, 3 need you, 4 waiting.
        self.assertEqual(M.LINE_KINDS, ("ok", "warn", "stop", "need you", "waiting"))
        cases = [((), 0), (("warn",), 0), (("wait",), 4), (("wait", "need"), 3), (("wait", "need", "stop"), 2)]
        for kinds, code in cases:
            rep = M.Report(io.StringIO())
            rep.ok("x")
            for k in kinds:
                getattr(rep, k)("x")
            self.assertEqual(rep.code(), code, kinds)
            for line in rep.lines:
                self.assertTrue(any(line.startswith(k) for k in M.LINE_KINDS), line)
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
