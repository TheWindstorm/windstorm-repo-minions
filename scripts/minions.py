#!/usr/bin/env python3
"""Windstorm Repo Minions: the tool the skill's agent runs.

Plan: DNA-STRAND-MASTER-PLAN.md. This version builds gene G1 (Look first) and gene G2 (Install, Linux with systemd).
`look` reads and reports and changes nothing on the computer except its own record in ~/.minions/. Its one change on
GitHub, the start test (G1.4), runs only with `--start-test` (the person's yes) and leaves nothing behind. `setup`
installs one runner for one private repository (G2), only after a fresh, clean `look`.

Output: one fact per line, each starting with `ok`, `warn`, `stop`, `need you` or (setup's last line, G2.10)
`waiting`. Exit 0, or 2 after a `stop`, or 3 after a `need you` that blocks, or 4 after `waiting`.

Python 3.10+, standard library only. Every GitHub call goes through GitHub.api(), which runs the person's own
GitHub CLI (`gh api`): the tool never holds or prints a token.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import getpass
import grp
import html
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.request

VERSION = "0.0.0"

EXIT_OK, EXIT_STOP, EXIT_NEED, EXIT_WAIT = 0, 2, 3, 4
RUNNER_USER = "minions-runner"
SLICE = "minions.slice"
DROPIN = "50-minions.conf"
LOOK_MAX_AGE_S = 3600
ADMIN_LIKE_GROUPS = {"sudo", "admin", "wheel", "docker", "root", "lxd", "libvirt"}
COST_URL = "https://docs.github.com/en/billing/concepts/product-billing/github-actions"
COST_LIST_HEAD = "The following usage remains free independently and does not reduce the included compute usage:"
START_BRANCH_PREFIX = "minions-start-test-"
START_FILE = ".github/workflows/minions-start-test.yml"
START_LABEL = "minions-start-test-no-runner"
START_WORKFLOW = f"""name: minions-start-test
# Made by Windstorm Repo Minions' start test. It asks for a label no runner carries, so its job waits and runs on
# no machine. The test cancels and deletes the run and deletes this branch within a minute or two.
on: [push]
permissions: {{}}
jobs:
  check:
    runs-on: [{START_LABEL}]
    timeout-minutes: 1
    steps:
      - run: echo never
"""
ZERO_JOB_WORDS = ("GitHub did not start the run; this is the account's or repository's setting or billing, "
                  "not your computer")


# ---------------------------------------------------------------------------------------------------------- output

class Report:
    """Collects the lines `look` prints; the worst kind decides the exit code."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.lines: list[str] = []
        self.stopped = False
        self.blocked = False
        self.waiting = False

    def _put(self, kind: str, text: str) -> None:
        line = f"{kind} {text}"
        self.lines.append(line)
        print(line, file=self.out, flush=True)

    def ok(self, text: str) -> None:
        self._put("ok", text)

    def warn(self, text: str) -> None:
        self._put("warn", text)

    def stop(self, text: str) -> None:
        self.stopped = True
        self._put("stop", text)

    def need(self, text: str, blocks: bool = True) -> None:
        if blocks:
            self.blocked = True
        self._put("need you:", text)

    def wait(self, text: str) -> None:
        self.waiting = True
        self._put("waiting", text)

    def code(self) -> int:
        if self.stopped:
            return EXIT_STOP
        if self.blocked:
            return EXIT_NEED
        return EXIT_WAIT if self.waiting else EXIT_OK


# ----------------------------------------------------------------------------------------------------- the computer

class Host:
    """The computer, read through these few calls so tests can stand in for it."""

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def run(self, argv: list[str], timeout: int = 20) -> tuple[int, str]:
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except (OSError, subprocess.SubprocessError) as e:
            return 127, str(e)

    def run_in(self, argv: list[str], input: str, timeout: int = 600) -> tuple[int, str]:
        """Like run(), with `input` on standard input: the only way a secret reaches another program (INV2)."""
        try:
            p = subprocess.run(argv, input=input, capture_output=True, text=True, timeout=timeout)
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except (OSError, subprocess.SubprocessError) as e:
            return 127, str(e)

    def read(self, path: str) -> str | None:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None

    def listdir(self, path: str) -> list[str]:
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def system(self) -> str:
        return platform.system()

    def machine(self) -> str:
        return platform.machine()

    def cpu_count(self) -> int | None:
        return os.cpu_count()

    def uid(self) -> int:
        return os.getuid()

    def groups(self) -> list[str]:
        names = []
        for g in os.getgroups():
            try:
                names.append(grp.getgrgid(g).gr_name)
            except KeyError:
                pass
        return names

    def home(self) -> str:
        return os.path.expanduser("~")

    def user(self) -> str:
        return getpass.getuser()

    def hostname(self) -> str:
        return platform.node().split(".")[0] or "computer"

    def state_dir(self) -> str:
        return os.path.join(self.home(), ".minions")

    def fetch_to(self, url: str, path: str, timeout: int = 300) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "windstorm-repo-minions/" + VERSION})
        with urllib.request.urlopen(req, timeout=timeout) as r, open(path, "wb") as f:
            shutil.copyfileobj(r, f)

    def disk_free(self, path: str) -> tuple[int, int] | None:
        try:
            u = shutil.disk_usage(path)
            return u.free, u.total
        except OSError:
            return None

    def dev_of(self, path: str) -> tuple[int, int] | None:
        try:
            st = os.stat(path)
            return os.major(st.st_dev), os.minor(st.st_dev)
        except OSError:
            return None

    def realpath(self, path: str) -> str:
        return os.path.realpath(path)

    def fetch(self, url: str, timeout: int = 30) -> str:
        req = urllib.request.Request(url, headers={"User-Agent": "windstorm-repo-minions/" + VERSION})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def isatty(self) -> bool:
        return sys.stdin.isatty()

    def sudo_validate(self) -> int:
        """`sudo -v` attached to the person's terminal: sudo itself asks for the password (the person's step)."""
        try:
            return subprocess.run(["sudo", "-v"]).returncode
        except (OSError, subprocess.SubprocessError):
            return 1

    def mode_of(self, path: str) -> int | None:
        try:
            return os.stat(path).st_mode & 0o777
        except OSError:
            return None


# ----------------------------------------------------------------------------------------------------------- GitHub

class GitHubError(Exception):
    def __init__(self, status: int, data):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.data = data


class GitHub:
    """Every GitHub call. Runs `gh api` with the person's own login; a body goes on standard input, never argv.

    With MINIONS_RECORD=<file> each call and its answer is appended to that file as one JSON line (how the test
    stand-in's fixtures are recorded; answers hold no token: `gh` never returns one to `gh api`)."""

    def __init__(self, host: Host):
        self.host = host

    def api(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        argv = ["gh", "api", "-i", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
        stdin = None
        if body is not None:
            argv += ["--input", "-"]
            stdin = json.dumps(body)
        try:
            p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=60)
            raw = p.stdout
        except (OSError, subprocess.SubprocessError) as e:
            return 0, {"message": str(e)}
        status, data = parse_include(raw)
        rec = os.environ.get("MINIONS_RECORD")
        if rec:
            kept = data
            if isinstance(data, dict) and "token" in data:  # a registration or removal token is never recorded
                kept = dict(data, token="RECORDED-TOKEN-REMOVED")
            with open(rec, "a", encoding="utf-8") as f:
                f.write(json.dumps({"method": method, "path": path, "body": body, "status": status,
                                    "data": kept}) + "\n")
        return status, data

    def get(self, path: str):
        status, data = self.api("GET", path)
        if status != 200:
            raise GitHubError(status, data)
        return data


def parse_include(raw: str) -> tuple[int, object]:
    """Split `gh api -i` output into the HTTP status and the JSON body (None when empty or not JSON)."""
    status = 0
    m = re.match(r"HTTP/[0-9.]+ (\d{3})", raw)
    if m:
        status = int(m.group(1))
    parts = re.split(r"\r?\n\r?\n", raw, maxsplit=1)
    body = parts[1] if len(parts) == 2 else ""
    body = body.strip()
    if not body:
        return status, None
    try:
        return status, json.loads(body)
    except ValueError:
        return status, {"text": body[:500]}


# --------------------------------------------------------------------------------------------------- G1.10, G1.11

INSTALL_GH = {
    "Darwin": "brew install gh",
    "debian": "sudo apt install gh",
    "fedora": "sudo dnf install gh",
    "arch": "sudo pacman -S github-cli",
}


def os_release_id(host: Host) -> str:
    text = host.read("/etc/os-release") or ""
    ids = []
    for line in text.splitlines():
        if line.startswith(("ID=", "ID_LIKE=")):
            ids += line.split("=", 1)[1].strip().strip('"').split()
    for i in ids:
        if i in ("debian", "ubuntu"):
            return "debian"
        if i in ("fedora", "rhel", "centos"):
            return "fedora"
        if i == "arch":
            return "arch"
    return ""


def check_github_cli(host: Host, rep: Report) -> bool:
    """G1.10: the first check. Without the GitHub CLI and a login, nothing else runs."""
    if not host.which("gh"):
        key = "Darwin" if host.system() == "Darwin" else os_release_id(host)
        cmd = INSTALL_GH.get(key, "see https://cli.github.com for this system")
        rep.need(f"install the GitHub CLI: {cmd}")
        return False
    rc, _ = host.run(["gh", "auth", "status", "--hostname", "github.com"])
    if rc != 0:
        rep.need("log in to GitHub: run `gh auth login` and follow its steps (this step is yours)")
        return False
    rep.ok("GitHub CLI installed and logged in")
    return True


ADMIN_GROUPS = {"Linux": ("sudo", "admin", "wheel"), "Darwin": ("admin",)}


def check_admin(host: Host, rep: Report) -> bool:
    """G1.11: the person can become admin on this computer, or `look` stops before anything is touched."""
    if host.uid() == 0:
        rep.ok("running as the computer's admin (root)")
        return True
    system = host.system()
    groups = set(host.groups())
    wanted = ADMIN_GROUPS.get(system, ())
    if groups.intersection(wanted):
        rep.ok(f"you can become admin on this computer (member of {', '.join(sorted(groups.intersection(wanted)))}); "
               "setup will ask for the admin password once")
        return True
    if system == "Linux" and host.which("sudo"):
        rc, _ = host.run(["sudo", "-n", "true"])
        if rc == 0:
            rep.ok("you can become admin on this computer (sudo)")
            return True
    rep.need("this needs an admin of this computer: making the runner's own user and its boot service needs admin "
             "rights once")
    rep.stop("not touched anything: this account cannot become admin on this computer")
    return False


# ------------------------------------------------------------------------------------------------- G1.1 to G1.3

def check_repository(gh: GitHub, host: Host, repo: str, rep: Report) -> dict | None:
    """G1.1 visibility, G1.2 account kind, G1.3 the rights the next steps need."""
    status, data = gh.api("GET", f"repos/{repo}")
    if status != 200 or not isinstance(data, dict):
        rep.stop(f"could not read {repo} on GitHub (HTTP {status}): check the name and that your login can see it")
        return None
    visibility = data.get("visibility") or ("private" if data.get("private") else "public")
    if visibility != "private":
        rep.stop(f"{repo} is {visibility}: Repo Minions never puts a runner on a repository that is not private, "
                 "because on a public one a stranger's pull request could run code on your computer")
        return None
    rep.ok(f"{repo} is private")
    kind = (data.get("owner") or {}).get("type")
    if kind == "Organization":
        rep.ok("organisation: a runner group limited to the private repositories you choose")
    elif kind == "User":
        rep.ok("personal account: one runner per repository")
    else:
        rep.warn("could not read whether the owner is a person or an organisation")
    perms = data.get("permissions") or {}
    if perms.get("admin"):
        rep.ok("your login is admin on the repository (needed to register a runner)")
    else:
        rep.need("admin on the repository is needed to register a runner: ask its owner, or log in as one")
    scopes = token_scopes(host)
    if scopes is None:
        rep.warn("could not read the token's scopes; the `workflow` scope is needed to change workflow files")
    elif "workflow" in scopes:
        rep.ok("your login has the `workflow` scope (needed to change workflow files)")
    else:
        rep.need("your login lacks the `workflow` scope: run `gh auth refresh -s workflow`", blocks=False)
    return data


def token_scopes(host: Host) -> set[str] | None:
    """The scopes `gh auth status` shows for github.com; None when it shows none (for example a fine-grained token).
    Only the scope names are read from that output; nothing else of it is kept or shown."""
    rc, out = host.run(["gh", "auth", "status", "--hostname", "github.com"])
    if rc != 0:
        return None
    m = re.search(r"Token scopes:\s*(.+)", out)
    if not m:
        return None
    return set(re.findall(r"'([^']+)'", m.group(1)))


# ------------------------------------------------------------------------------------------------------------ G1.4

def classify_run(run: dict | None, jobs: list[dict]) -> tuple[str, str]:
    """What one run says about whether GitHub starts jobs here. Returns (kind, words); kind is ok|stop|broken|warn."""
    if run is None:
        return "warn", "no run appeared within the time limit, so whether GitHub starts jobs here was not seen"
    conclusion = run.get("conclusion")
    if conclusion == "startup_failure" and not jobs:
        return "stop", ZERO_JOB_WORDS
    if conclusion == "failure" and not jobs:
        return "broken", f"the workflow file {run.get('path') or run.get('name')} is broken, so GitHub made no job"
    if jobs:
        return "ok", f"GitHub creates jobs here ({len(jobs)} job, {jobs[0].get('status')})"
    return "warn", f"a run ended {conclusion or run.get('status')} with no job; not enough to say"


def recent_runs(gh: GitHub, repo: str, rep: Report) -> bool:
    """Without the person's yes to the start test: read the last runs instead. False means setup must stop."""
    status, data = gh.api("GET", f"repos/{repo}/actions/runs?per_page=10")
    runs = (data or {}).get("workflow_runs", []) if status == 200 else None
    if runs is None:
        rep.warn(f"could not read the repository's runs (HTTP {status})")
        return True
    if not runs:
        rep.warn("no runs to read: could not test whether GitHub starts jobs here")
        return True
    for run in runs:
        if run.get("status") != "completed":
            continue
        s, d = gh.api("GET", f"repos/{repo}/actions/runs/{run['id']}/jobs")
        jobs = (d or {}).get("jobs", []) if s == 200 else []
        kind, words = classify_run(run, jobs)
        when = run.get("created_at", "?")
        if kind == "stop":
            rep.stop(f"the last finished run ({when}): {words}")
            return False
        if kind == "ok":
            rep.ok(f"jobs start here (last finished run {when}, {len(jobs)} job(s))")
            return True
        if kind == "broken":
            rep.warn(f"the last finished run ({when}): {words}")
            return True
    rep.warn("no finished run to read: could not test whether GitHub starts jobs here")
    return True


def start_test(gh: GitHub, host: Host, repo: str, rep: Report, wait_s: int = 90, poll_s: float = 5) -> bool:
    """G1.4: one job on a temporary branch whose only commit has no parent and holds only our workflow file, so
    none of the person's workflows runs and the default branch is never touched. The job waits on no machine; the
    run is cancelled and deleted and the branch deleted, and both are read back gone. False means setup must stop."""
    branch = START_BRANCH_PREFIX + secrets.token_hex(4)
    ref_made = False
    run_id = None
    result = True
    try:
        try:
            blob = post(gh, f"repos/{repo}/git/blobs", {"content": START_WORKFLOW, "encoding": "utf-8"})
            tree = post(gh, f"repos/{repo}/git/trees",
                        {"tree": [{"path": START_FILE, "mode": "100644", "type": "blob", "sha": blob["sha"]}]})
            commit = post(gh, f"repos/{repo}/git/commits",
                          {"message": "Repo Minions start test (deleted within minutes)", "tree": tree["sha"],
                           "parents": []})
            post(gh, f"repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
            ref_made = True
        except GitHubError as e:
            msg = json.dumps(e.data)[:200] if e.data else ""
            why = "the `workflow` scope is missing" if "workflow" in msg else f"GitHub said HTTP {e.status} {msg}"
            rep.warn(f"could not push the start test's branch ({why}); reading recent runs instead")
            return recent_runs(gh, repo, rep)
        run = None
        deadline = wait_s
        while deadline > 0:
            s, d = gh.api("GET", f"repos/{repo}/actions/runs?branch={branch}&per_page=5")
            runs = (d or {}).get("workflow_runs", []) if s == 200 else []
            if runs:
                run = runs[0]
                run_id = run["id"]
                if run.get("status") == "completed" or jobs_of(gh, repo, run_id):
                    break
            host.sleep(poll_s)
            deadline -= poll_s
        jobs = jobs_of(gh, repo, run_id) if run_id else []
        if run_id:
            s, d = gh.api("GET", f"repos/{repo}/actions/runs/{run_id}")
            if s == 200:
                run = d
        kind, words = classify_run(run, jobs)
        if kind == "ok":
            rep.ok(f"start test: {words}; it ran on no machine")
        elif kind == "stop":
            rep.stop(f"start test: {words}")
            result = False
        else:
            rep.warn(f"start test: {words}")
    finally:
        cleanup_start_test(gh, host, repo, branch if ref_made else None, run_id, rep)
    return result


def post(gh: GitHub, path: str, body: dict) -> dict:
    status, data = gh.api("POST", path, body)
    if status not in (200, 201):
        raise GitHubError(status, data)
    return data


def jobs_of(gh: GitHub, repo: str, run_id) -> list[dict]:
    s, d = gh.api("GET", f"repos/{repo}/actions/runs/{run_id}/jobs")
    return (d or {}).get("jobs", []) if s == 200 else []


def cleanup_start_test(gh: GitHub, host: Host, repo: str, branch: str | None, run_id, rep: Report) -> None:
    """Cancel and delete the run, delete the branch, and read back that both are gone. Runs even after an error."""
    left = []
    if run_id:
        s, d = gh.api("GET", f"repos/{repo}/actions/runs/{run_id}")
        if s == 200 and (d or {}).get("status") != "completed":
            gh.api("POST", f"repos/{repo}/actions/runs/{run_id}/cancel")
            for _ in range(12):
                s, d = gh.api("GET", f"repos/{repo}/actions/runs/{run_id}")
                if s != 200 or (d or {}).get("status") == "completed":
                    break
                host.sleep(3)
        gh.api("DELETE", f"repos/{repo}/actions/runs/{run_id}")
        s, _ = gh.api("GET", f"repos/{repo}/actions/runs/{run_id}")
        if s != 404:
            left.append(f"run {run_id}")
    if branch:
        gh.api("DELETE", f"repos/{repo}/git/refs/heads/{branch}")
        s, _ = gh.api("GET", f"repos/{repo}/git/ref/heads/{branch}")
        if s != 404:
            left.append(f"branch {branch}")
        if not run_id:
            # GitHub may make the run late, after the branch is gone: look once more and take it away too
            host.sleep(10)
            s, d = gh.api("GET", f"repos/{repo}/actions/runs?branch={branch}&per_page=5")
            for late in ((d or {}).get("workflow_runs", []) if s == 200 else []):
                gh.api("POST", f"repos/{repo}/actions/runs/{late['id']}/cancel")
                host.sleep(3)
                gh.api("DELETE", f"repos/{repo}/actions/runs/{late['id']}")
                s2, _ = gh.api("GET", f"repos/{repo}/actions/runs/{late['id']}")
                if s2 != 404:
                    left.append(f"run {late['id']}")
    if branch or run_id:
        if left:
            rep.warn("start test: could not remove " + " and ".join(left) + "; remove it by hand")
        else:
            rep.ok("start test: the run and the branch are deleted, read back gone")


# ------------------------------------------------------------------------------------------------------------ G1.5

def check_secrets(gh: GitHub, repo: str, owner_kind: str | None, rep: Report) -> None:
    """G1.5: the names (never values) of the Actions secrets a job of this repository can read."""
    found: list[str] = []
    unread: list[str] = []
    s, d = gh.api("GET", f"repos/{repo}/actions/secrets?per_page=100")
    if s == 200:
        found += [x["name"] for x in (d or {}).get("secrets", [])]
    else:
        unread.append("repository secrets")
    s, d = gh.api("GET", f"repos/{repo}/environments?per_page=100")
    if s == 200:
        for env in (d or {}).get("environments", []):
            es, ed = gh.api("GET", f"repos/{repo}/environments/{env['name']}/secrets?per_page=100")
            if es == 200:
                found += [f"{x['name']} (environment {env['name']})" for x in (ed or {}).get("secrets", [])]
            else:
                unread.append(f"environment {env['name']}")
    elif s != 404:
        unread.append("environments")
    if owner_kind == "Organization":
        s, d = gh.api("GET", f"repos/{repo}/actions/organization-secrets?per_page=100")
        if s == 200:
            found += [f"{x['name']} (organisation)" for x in (d or {}).get("secrets", [])]
        else:
            unread.append("organisation secrets")
    if found:
        rep.warn(f"{len(found)} secret(s) a job of this repository can read: {', '.join(found)} "
                 "(names only; a job on your computer can read their values)")
    else:
        rep.ok("no Actions secrets a job of this repository can read")
    if unread:
        rep.warn("could not read: " + ", ".join(unread))


# ------------------------------------------------------------------------------------------------------------ G1.6

def parse_meminfo(text: str) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        m = re.match(r"(\w+):\s+(\d+) kB", line)
        if m:
            out[m.group(1)] = int(m.group(2)) * 1024
    return out


def gb(n: int | float) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


def survey(host: Host, rep: Report) -> dict:
    """G1.6: what kind of computer this is. Each part that cannot be read says "not read", never a guess."""
    s = {"system": host.system(), "machine": host.machine(), "cores": host.cpu_count()}
    if s["system"] == "Linux":
        osr = host.read("/etc/os-release") or ""
        m = re.search(r'^PRETTY_NAME="?([^"\n]+)', osr, re.M)
        s["os"] = m.group(1) if m else "Linux"
        mem = parse_meminfo(host.read("/proc/meminfo") or "")
        s["mem"] = mem.get("MemTotal")
        s["mem_avail"] = mem.get("MemAvailable")
        s["swap_used"] = (mem["SwapTotal"] - mem["SwapFree"]) if "SwapTotal" in mem and "SwapFree" in mem else None
    elif s["system"] == "Darwin":
        rc, out = host.run(["sw_vers", "-productVersion"])
        s["os"] = f"macOS {out.strip()}" if rc == 0 else "macOS"
        rc, out = host.run(["sysctl", "-n", "hw.memsize"])
        s["mem"] = int(out.strip()) if rc == 0 and out.strip().isdigit() else None
        s["mem_avail"] = None
        rc, out = host.run(["sysctl", "-n", "vm.swapusage"])
        m = re.search(r"used = ([\d.]+)M", out) if rc == 0 else None
        s["swap_used"] = int(float(m.group(1)) * 1024 ** 2) if m else None
    else:
        s["os"], s["mem"], s["mem_avail"], s["swap_used"] = s["system"] or "unknown", None, None, None
    mem = gb(s["mem"]) if s["mem"] else "memory not read"
    avail = f", {gb(s['mem_avail'])} available" if s.get("mem_avail") else ""
    rep.ok(f"{s['os']}, {s['machine']}, {s['cores'] or 'cores not read'} cores, {mem}{avail}")
    if s["swap_used"] is None:
        rep.warn("swap in use: not read")
    elif s["swap_used"] > 512 * 1024 ** 2:
        rep.warn(f"{gb(s['swap_used'])} of swap in use now: jobs here will be slower while that lasts")
    else:
        rep.ok(f"swap in use: {gb(s['swap_used'])}")
    home = host.home()
    s["disk"] = disk_kind(host, home)
    free = host.disk_free(home)
    if free:
        s["free"] = free[0]
        rep.ok(f"disk for the work folders: {s['disk']}, {gb(free[0])} free of {gb(free[1])}")
    else:
        rep.warn(f"disk for the work folders: {s['disk']}, free space not read")
    s["battery"] = has_battery(host)
    if s["battery"] is None:
        rep.warn("battery: not read")
    elif s["battery"]:
        rep.warn("this computer has a battery: a laptop takes jobs only while it is awake, on the network and "
                 "(by default) on mains power")
    else:
        rep.ok("no battery: not a laptop")
    s["wifi_only"] = wifi_only(host)
    if s["wifi_only"] is None:
        rep.warn("network: not read")
    elif s["wifi_only"]:
        rep.warn("on Wi-Fi only: a job can be lost when the link drops")
    else:
        rep.ok("network: wired or not Wi-Fi")
    sleep = sleep_setting(host)
    (rep.ok if sleep and "off" in sleep else rep.warn)(f"sleep: {sleep or 'not read'}")
    return s


def disk_kind(host: Host, path: str) -> str:
    if host.system() == "Darwin":
        rc, out = host.run(["diskutil", "info", "/"])
        m = re.search(r"Solid State:\s*(\w+)", out) if rc == 0 else None
        return ("SSD" if m.group(1) == "Yes" else "spinning disk") if m else "disk kind not read"
    dev = host.dev_of(path)
    if not dev:
        return "disk kind not read"
    seen = set()
    todo = [host.realpath(f"/sys/dev/block/{dev[0]}:{dev[1]}")]
    while todo:
        d = todo.pop()
        if d in seen or not d.startswith("/sys"):
            continue
        seen.add(d)
        rot = host.read(f"{d}/queue/rotational")
        if rot is not None and rot.strip() in ("0", "1"):
            return "spinning disk" if rot.strip() == "1" else "SSD"
        slaves = host.listdir(f"{d}/slaves")
        todo += [host.realpath(f"/sys/class/block/{x}") for x in slaves]
        parent = os.path.dirname(d)
        if parent != d:
            todo.append(parent)
    return "disk kind not read"


def has_battery(host: Host) -> bool | None:
    if host.system() == "Darwin":
        rc, out = host.run(["pmset", "-g", "batt"])
        return ("InternalBattery" in out) if rc == 0 else None
    if host.system() != "Linux":
        return None
    for name in host.listdir("/sys/class/power_supply"):
        if (host.read(f"/sys/class/power_supply/{name}/type") or "").strip() == "Battery":
            return True
    return False


def default_route_iface(route_text: str) -> str | None:
    for line in route_text.splitlines()[1:]:
        f = line.split()
        if len(f) > 2 and f[1] == "00000000":
            return f[0]
    return None


def wifi_only(host: Host) -> bool | None:
    if host.system() != "Linux":
        return None
    iface = default_route_iface(host.read("/proc/net/route") or "")
    if not iface:
        return None
    return bool(host.listdir(f"/sys/class/net/{iface}/wireless")) or \
        host.read(f"/sys/class/net/{iface}/phy80211/name") is not None


def sleep_setting(host: Host) -> str | None:
    if host.system() == "Darwin":
        rc, out = host.run(["pmset", "-g"])
        m = re.search(r"^\s*sleep\s+(\d+)", out, re.M) if rc == 0 else None
        if not m:
            return None
        return "off (never sleeps by itself)" if m.group(1) == "0" else f"after {m.group(1)} min idle"
    if host.system() == "Linux" and host.which("systemctl"):
        rc, out = host.run(["systemctl", "is-enabled", "sleep.target", "suspend.target"])
        states = out.split()
        if states and all(x == "masked" for x in states[:2]):
            return "off (sleep and suspend masked)"
        if states:
            return "the system may sleep (sleep.target not masked)"
    return None


# ------------------------------------------------------------------------------------------------------------ G1.7

def listening_ports(tcp_text: str) -> set[int]:
    """Ports in LISTEN state (0A) on an address other than loopback, from /proc/net/tcp or tcp6."""
    ports = set()
    for line in tcp_text.splitlines()[1:]:
        f = line.split()
        if len(f) < 4 or f[3] != "0A":
            continue
        addr, port = f[1].rsplit(":", 1)
        if addr in ("0100007F", "00000000000000000000000001000000"):
            continue
        ports.add(int(port, 16))
    return ports


def what_lives_here(host: Host, rep: Report) -> dict:
    """G1.7: what a job could disturb or be disturbed by. On a shared computer other users' things are counted,
    never named: only what is the person's own is named (their rootless containers, runner services that run as
    them). Port numbers are named; they say nothing of whose they are."""
    out = {"ports": set(), "containers": [], "shared_containers": 0, "runners": [], "others_runners": 0}
    if host.system() == "Linux":
        for f in ("/proc/net/tcp", "/proc/net/tcp6"):
            out["ports"] |= listening_ports(host.read(f) or "")
    elif host.system() == "Darwin":
        rc, text = host.run(["netstat", "-an", "-p", "tcp"])
        if rc == 0:
            for line in text.splitlines():
                m = re.match(r"tcp\S*\s+\d+\s+\d+\s+(\S+)\.(\d+)\s+\S+\s+LISTEN", line)
                if m and not m.group(1).startswith(("127.", "::1")):
                    out["ports"].add(int(m.group(2)))
    for tool in ("docker", "podman"):
        if host.which(tool):
            rc, text = host.run([tool, "ps", "--format", "{{.Names}}"])
            if rc != 0:
                rep.warn(f"{tool} is installed but its containers could not be listed from this account")
            elif tool == "podman":
                out["containers"] += [f"{x} (podman, yours)" for x in text.split()]
            else:
                # docker's service is shared by every account allowed to use it: counted, not named
                out["shared_containers"] += len(text.split())
    if host.system() == "Linux" and host.which("systemctl"):
        rc, text = host.run(["systemctl", "list-units", "--all", "--plain", "--no-legend", "--no-pager",
                             "actions.runner.*"])
        if rc == 0:
            me = host.user()
            for unit in [x.split()[0] for x in text.splitlines() if x.strip()]:
                urc, owner = host.run(["systemctl", "show", "-p", "User", "--value", unit])
                if urc == 0 and owner.strip() == me:
                    out["runners"].append(unit)
                else:
                    out["others_runners"] += 1
    elif host.system() == "Darwin":
        rc, text = host.run(["launchctl", "list"])
        if rc == 0:
            out["runners"] = [x.split()[-1] for x in text.splitlines() if "actions.runner." in x]
    ports = sorted(out["ports"])
    if ports:
        rep.warn(f"{len(ports)} port(s) open to the network here: {', '.join(map(str, ports[:20]))}"
                 f"{' ...' if len(ports) > 20 else ''}")
    else:
        rep.ok("no port open to the network")
    if out["containers"]:
        rep.warn(f"{len(out['containers'])} container(s) of yours running: {', '.join(out['containers'][:20])}")
    if out["shared_containers"]:
        rep.warn(f"{out['shared_containers']} container(s) running on this computer's shared container service "
                 "(not named: they may be another person's)")
    if out["runners"]:
        rep.warn(f"{len(out['runners'])} other runner service(s) of yours here: {', '.join(out['runners'][:10])}")
    if out["others_runners"]:
        rep.warn(f"{out['others_runners']} other runner service(s) here that do not run as you (not named)")
    serves = (bool(set(ports) & {80, 443, 5432, 3306, 6379, 27017}) or bool(out["containers"])
              or bool(out["shared_containers"]))
    out["serves"] = serves
    if serves:
        rep.warn("this computer looks like it serves something live: setup will ask you to choose another "
                 "computer or to say yes in words")
    return out


# ------------------------------------------------------------------------------------------------------------ G1.8

def strip_comment(s: str) -> str:
    out, q = [], None
    for ch in s:
        if q:
            out.append(ch)
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def parse_labels(value: str) -> list[str]:
    v = value.strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
        return [x.strip().strip("'\"") for x in v.split(",") if x.strip()]
    return [v.strip("'\"")] if v else []


def read_workflow(text: str) -> list[dict]:
    """A small line reader for the parts of a workflow `look` needs, per job: its runs-on labels, setup actions,
    sudo, container and services. Not a YAML parser: anything it cannot follow is reported as not read."""
    jobs: list[dict] = []
    lines = text.splitlines()
    in_jobs = False
    jobs_indent = job_indent = None
    job = None
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = strip_comment(raw)
        i += 1
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if indent == 0:
            in_jobs = body == "jobs:"
            job = None
            jobs_indent = 0 if in_jobs else None
            job_indent = None
            continue
        if not in_jobs:
            continue
        if job_indent is None and re.match(r"^[A-Za-z0-9_-]+:\s*$", body):
            job_indent = indent
        if indent == job_indent and re.match(r"^[A-Za-z0-9_-]+:\s*$", body):
            job = {"id": body[:-1], "runs_on": None, "setup": [], "sudo": False, "container": False,
                   "services": False}
            jobs.append(job)
            continue
        if job is None:
            continue
        m = re.match(r"^runs-on:\s*(.*)$", body)
        if m:
            val = m.group(1).strip()
            if val == "":
                labels = []
                while i < len(lines):
                    nxt = strip_comment(lines[i]).strip()
                    if nxt.startswith("- "):
                        labels.append(nxt[2:].strip().strip("'\""))
                        i += 1
                    else:
                        break
                job["runs_on"] = labels or None
            elif val.startswith(("group:", "labels:")) or "${{" in val:
                job["runs_on"] = "expression" if "${{" in val else "not read"
            else:
                job["runs_on"] = parse_labels(val)
            continue
        m = re.match(r"^-?\s*uses:\s*actions/(setup-[A-Za-z0-9_-]+)@", body)
        if m:
            job["setup"].append(m.group(1))
        if re.search(r"(^|[\s;&|(])sudo\s", body.lstrip("- ")):
            job["sudo"] = True
        if re.match(r"^container:", body):
            job["container"] = True
        if re.match(r"^services:", body):
            job["services"] = True
    return jobs


HOSTED = {"ubuntu": "Linux", "macos": "Darwin", "windows": "Windows"}


def judge_job(job: dict, system: str) -> tuple[str, str]:
    """Can this job move to this computer? (kind, words); kind is ok|warn|stay."""
    ro = job["runs_on"]
    if ro in (None, "not read"):
        return "warn", "runs-on not read"
    if ro == "expression":
        return "warn", "runs-on is chosen when the job runs; not read"
    labels = [x.lower() for x in ro]
    needs = []
    if job["setup"]:
        needs.append("downloads its tools (" + ", ".join(job["setup"]) + ")")
    if job["sudo"]:
        needs.append("uses sudo, and the runner's user has no admin rights")
    if job["container"] or job["services"]:
        needs.append("needs containers, which the runner's user cannot start by default")
    hosted_os = [HOSTED[k] for k in HOSTED for x in labels if x.startswith(k + "-") or x == k + "-latest"]
    if hosted_os:
        if hosted_os[0] != system:
            return "stay", f"asks for GitHub's {ro[0]}; this computer is {system}, so it stays on GitHub"
        kind = "warn" if job["sudo"] or job["container"] or job["services"] else "ok"
        return kind, f"on GitHub's {ro[0]} now; can move here" + ("; " + "; ".join(needs) if needs else "")
    if "self-hosted" in labels:
        return "warn", f"already asks for a self-hosted runner ({', '.join(ro)})"
    return "warn", f"asks for labels {', '.join(ro)}"


def check_workflows(gh: GitHub, repo: str, system: str, rep: Report) -> list[dict]:
    s, d = gh.api("GET", f"repos/{repo}/contents/.github/workflows")
    if s == 404:
        rep.ok("no workflows yet: nothing to move")
        return []
    if s != 200 or not isinstance(d, list):
        rep.warn(f"could not list the workflows (HTTP {s})")
        return []
    found = []
    for f in d:
        if not f.get("name", "").endswith((".yml", ".yaml")):
            continue
        fs, fd = gh.api("GET", f"repos/{repo}/contents/{f['path']}")
        try:
            text = base64.b64decode((fd or {}).get("content", "")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            text = None
        if fs != 200 or text is None:
            rep.warn(f"{f['path']}: could not read")
            continue
        jobs = read_workflow(text)
        if not jobs:
            rep.warn(f"{f['path']}: no job read")
        for job in jobs:
            kind, words = judge_job(job, system)
            (rep.ok if kind == "ok" else rep.warn)(f"{f['path']} job {job['id']}: {words}")
            found.append({"file": f["path"], **job, "kind": kind})
    return found


# ------------------------------------------------------------------------------------------------------------ G1.9

def read_cost_rule(page: str) -> list[str] | None:
    """The items of GitHub's list of uses that do not reduce the included usage, or None when it is not found."""
    text = page.split("__NEXT_DATA__")[0]
    text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    lines = [re.sub(r"\s+", " ", x).strip() for x in text.splitlines()]
    lines = [x for x in lines if x]
    try:
        i = lines.index(COST_LIST_HEAD)
    except ValueError:
        return None
    items, cur = [], ""
    for x in lines[i + 1:i + 12]:
        cur = (cur + " " + x).strip() if cur else x
        if cur.endswith("."):
            items.append(cur.replace(" .", "."))
            cur = ""
        if len(items) >= 2 and items[-1].startswith("Self-hosted"):
            break
    return items or None


def check_cost_rule(host: Host, rep: Report) -> None:
    """G1.9: GitHub's own sentence, with the date read; never a guess."""
    when = host.now().strftime("%Y-%m-%d %H:%M UTC")
    try:
        items = read_cost_rule(host.fetch(COST_URL))
    except Exception:  # noqa: BLE001 - any failure to read is said as such
        items = None
    if not items or not any(x.startswith("Self-hosted runners") for x in items):
        rep.warn(f"I could not read GitHub's rule today ({when}): see {COST_URL}")
        return
    rep.ok(f"GitHub's billing page, read {when}, lists \"Self-hosted runners\" among the uses that do not reduce "
           "the included usage (its words: \"" + COST_LIST_HEAD + " " + " ".join(items) + "\"). In December 2025 "
           "GitHub announced a charge for self-hosted runner use and postponed it. Artifact and cache storage "
           f"count the same whichever computer ran the job. Source: {COST_URL}")


# ------------------------------------------------------------------------------------------- state and journal

def state_path(host: Host) -> str:
    return os.path.join(host.state_dir(), "minions.json")


def ensure_state_dir(host: Host) -> str:
    d = host.state_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def load_state(host: Host) -> dict:
    try:
        with open(state_path(host), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(host: Host, data: dict) -> None:
    """minions.json: which repositories, which runners, the last looks. Nothing secret. 0600, written whole."""
    ensure_state_dir(host)
    path = state_path(host)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def journal(host: Host, step: str, detail: str = "") -> None:
    """INV8, first half: each step is written down BEFORE it runs. Never a secret."""
    ensure_state_dir(host)
    path = os.path.join(host.state_dir(), "journal.log")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(f"{host.now().strftime('%Y-%m-%dT%H:%M:%SZ')} {step} {detail}".rstrip() + "\n")


def record_look(host: Host, repo: str, rep: Report, s: dict | None) -> None:
    data = load_state(host)
    keep = {k: v for k, v in (s or {}).items() if k in ("system", "machine", "cores", "mem", "disk", "free",
                                                          "battery", "os")}
    data.setdefault("looks", {})[repo] = {"time": host.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                                          "stopped": rep.stopped, "survey": keep}
    save_state(host, data)


# ------------------------------------------------------------------------------------------------------- G2 install

SIZE_RULES = (  # (label, at least this many cores, at least this much memory in GB); the first that fits wins
    ("minions-large", 16, 32),
    ("minions-medium", 4, 8),
    ("minions-small", 0, 0),
)
ARCH = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm"}
CONFIG_SCRIPT = ('read -r T; cd "$1" || exit 9; ACTIONS_RUNNER_INPUT_TOKEN="$T" ./config.sh --unattended '
                 '--url "$2" --name "$3" --labels "$4" --no-default-labels --work _work; rc=$?; unset T; exit $rc')
REMOVE_SCRIPT = 'read -r T; cd "$1" || exit 9; ACTIONS_RUNNER_INPUT_TOKEN="$T" ./config.sh remove; rc=$?; unset T; exit $rc'


def size_label(survey_: dict) -> str:
    """G2.7: the size label from the survey; the thresholds live in SIZE_RULES only."""
    cores = survey_.get("cores") or 0
    mem_gb = (survey_.get("mem") or 0) / 1024 ** 3
    for label, c, m in SIZE_RULES:
        if cores >= c and mem_gb >= m - 0.5:
            return label
    return "minions-small"


def runner_labels(survey_: dict) -> list[str]:
    """G2.7, G2.11: only ours. Never `self-hosted`, an OS or an architecture label."""
    return ["minions", size_label(survey_)]


def slice_limits(survey_: dict) -> dict:
    """G2.9 / G4.2, simple for now: all our runners together get half the cores (at least one) and at most 60 % of
    the memory (soft limit at 50 %). G4.2's measured values replace these."""
    cores = max(1, (survey_.get("cores") or 2) // 2)
    mem = survey_.get("mem") or 4 * 1024 ** 3
    return {"CPUQuota": f"{cores * 100}%", "MemoryHigh": str(int(mem * 0.5)), "MemoryMax": str(int(mem * 0.6))}


def dropin_text() -> str:
    return ("# Windstorm Repo Minions (G2.9): the runner's own unit has Restart=no and does not ask for the network.\n"
            "[Unit]\nWants=network-online.target\n\n"
            f"[Service]\nSlice={SLICE}\nNice=10\nCPUWeight=50\nRestart=on-failure\nRestartSec=30\n")


def slice_text(limits: dict) -> str:
    return ("# Windstorm Repo Minions: every runner of ours on this computer, together (G2.9, G4.2, G12.1).\n"
            "[Unit]\nDescription=Windstorm Repo Minions runners\nBefore=slices.target\n\n"
            f"[Slice]\nCPUQuota={limits['CPUQuota']}\nMemoryHigh={limits['MemoryHigh']}\nMemoryMax={limits['MemoryMax']}\n")


def sudo(host: Host, argv: list[str], input: str | None = None, timeout: int = 600) -> tuple[int, str]:
    """An admin step: always `sudo -n` (the password was asked once, before the first step)."""
    full = ["sudo", "-n"] + argv
    return host.run_in(full, input, timeout) if input is not None else host.run(full, timeout)


def as_runner(host: Host, argv: list[str], input: str | None = None, timeout: int = 600) -> tuple[int, str]:
    return sudo(host, ["-u", RUNNER_USER, "--"] + argv, input, timeout)


def mask(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


def tail(text: str, n: int = 3) -> str:
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    return " / ".join(lines[-n:])


def check_fresh_look(host: Host, repo: str, rep: Report) -> dict | None:
    """G2.1: a `look` of the same repository on this computer, under an hour old, with no `stop` line."""
    entry = (load_state(host).get("looks") or {}).get(repo)
    if not entry:
        rep.stop(f"no look of {repo} on this computer yet: run `look {repo}` first")
        return None
    try:
        when = dt.datetime.strptime(entry["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except (KeyError, ValueError):
        rep.stop(f"the last look of {repo} cannot be read: run `look {repo}` again")
        return None
    age = (host.now() - when).total_seconds()
    if age > LOOK_MAX_AGE_S or age < -60:
        rep.stop(f"the last look of {repo} is {int(age // 60)} minutes old: run `look {repo}` again (setup needs one "
                 "under an hour old)")
        return None
    if entry.get("stopped"):
        rep.stop(f"the last look of {repo} ended with a `stop` line: settle that first, then look again")
        return None
    if not entry.get("survey"):
        rep.stop(f"the last look of {repo} did not reach its end: settle what it asked, then look again")
        return None
    rep.ok(f"a look of {repo} {int(age // 60)} minute(s) ago, with no stop")
    return entry


def ask_admin_once(host: Host, rep: Report, interactive: bool) -> bool:
    """G2.2: the admin password is the person's step, asked once (sudo -v); every later step is `sudo -n`."""
    rc, _ = host.run(["sudo", "-n", "true"])
    if rc == 0:
        rep.ok("admin rights ready (sudo did not need to ask now)")
        return True
    if interactive:
        rep.need("type this computer's admin password once, where sudo asks for it below (this step is yours)",
                 blocks=False)
        if host.sudo_validate() == 0 and host.run(["sudo", "-n", "true"])[0] == 0:
            rep.ok("admin rights ready (asked once)")
            return True
        rep.stop("sudo did not accept the password: nothing was touched")
        return False
    rep.need("this needs this computer's admin password once: run `sudo -v` in your terminal, then run setup again "
             "from that same terminal")
    return False


def ensure_runner_user(host: Host, rep: Report) -> bool:
    """G2.2: the runner's own user, in no group but its own, its home closed to others."""
    rc, _ = host.run(["id", "-u", RUNNER_USER])
    if rc != 0:
        journal(host, "useradd", RUNNER_USER)
        rc, out = sudo(host, ["useradd", "--create-home", "--user-group", "--shell", "/bin/bash", RUNNER_USER])
        if rc != 0:
            rep.stop(f"could not make the user {RUNNER_USER}: {tail(out)}")
            return False
        rep.ok(f"made the user {RUNNER_USER}")
    else:
        rep.ok(f"the user {RUNNER_USER} is there already")
    rc, out = host.run(["id", "-nG", RUNNER_USER])
    groups = set(out.split()) if rc == 0 else set()
    extra = groups - {RUNNER_USER}
    if rc != 0 or extra & ADMIN_LIKE_GROUPS or extra:
        rep.stop(f"{RUNNER_USER} is in other groups ({', '.join(sorted(extra)) or 'not read'}): a runner user must be "
                 "in its own group only, never one that gives admin rights")
        return False
    home = f"/home/{RUNNER_USER}"
    journal(host, "chmod", f"0750 {home}")
    sudo(host, ["chmod", "0750", home])
    rc, out = sudo(host, ["stat", "-c", "%a", home])
    if rc != 0 or out.strip() != "750":
        rep.stop(f"the runner's home {home} is not closed to others (mode {out.strip() or 'not read'})")
        return False
    rep.ok(f"{RUNNER_USER}: in its own group only, no admin rights, its home closed to others (750)")
    return True


def check_persons_home(host: Host, rep: Report) -> bool:
    """G2.2: the person's own files are not reachable from the runner user."""
    mode = host.mode_of(host.home())
    if mode is None:
        rep.warn("could not read who may enter your home folder")
        return True
    if mode & 0o005:
        rep.need(f"your home folder can be entered by every user of this computer (mode {mode:o}), so a job could "
                 f"read your files: run `chmod 750 {host.home()}`, then setup again")
        return False
    rep.ok(f"your home folder is closed to other users (mode {mode:o})")
    return True


def runner_release(gh: GitHub, repo: str, host: Host, rep: Report) -> dict | None:
    """G2.3: the runner for this OS and architecture, and its checksum from two places that must agree."""
    arch = ARCH.get(host.machine().lower())
    system = {"Linux": "linux", "Darwin": "osx"}.get(host.system())
    s, d = gh.api("GET", f"repos/{repo}/actions/runners/downloads")
    pick = [x for x in (d if s == 200 and isinstance(d, list) else []) if x.get("os") == system
            and x.get("architecture") == arch]
    if not pick:
        rep.stop(f"GitHub offers no runner for {system} {arch} (HTTP {s})")
        return None
    rel = pick[0]
    m = re.search(r"-(\d+\.\d+\.\d+)\.(tar\.gz|zip)$", rel.get("filename", ""))
    if not m or not re.fullmatch(r"[0-9a-f]{64}", rel.get("sha256_checksum") or ""):
        rep.stop("GitHub's runner download list is not in the shape this tool knows: nothing was downloaded")
        return None
    version = m.group(1)
    s, notes = gh.api("GET", f"repos/actions/runner/releases/tags/v{version}")
    key = f"{system}-{arch}"
    mm = re.search(rf"<!-- BEGIN SHA {re.escape(key)} -->([0-9a-f]{{64}})<!-- END SHA {re.escape(key)} -->",
                   (notes or {}).get("body", "") if s == 200 else "")
    if not mm:
        rep.stop(f"the release notes of runner {version} carry no checksum for {key}: nothing was downloaded")
        return None
    if mm.group(1) != rel["sha256_checksum"]:
        rep.stop(f"the two checksums GitHub publishes for runner {version} ({key}) disagree: nothing was downloaded")
        return None
    rep.ok(f"runner {version} for {key}; its checksum is the same in GitHub's download list and its release notes")
    return {"version": version, "filename": rel["filename"], "url": rel["download_url"], "sha256": mm.group(1)}


def sha256_file(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def unpack_runner(host: Host, rel: dict, folder: str, rep: Report) -> bool:
    """G2.3, G2.4: downloaded, checked, then unpacked fresh into the runner's own folder, never copied."""
    rc, out = sudo(host, ["test", "-e", folder])
    if rc == 0:
        rep.stop(f"{folder} exists already: a runner for this repository is set up here, or a removal was not "
                 "finished; nothing was changed")
        return False
    ensure_state_dir(host)
    tmp = os.path.join(host.state_dir(), rel["filename"])
    journal(host, "download", rel["url"])
    try:
        host.fetch_to(rel["url"], tmp)
        got = sha256_file(tmp)
    except Exception as e:  # noqa: BLE001
        rep.stop(f"could not download the runner: {e}")
        return False
    if got != rel["sha256"]:
        os.remove(tmp)
        rep.stop(f"the downloaded runner's checksum is not the one GitHub publishes: deleted, nothing installed")
        return False
    rep.ok(f"downloaded {rel['filename']}, checksum checked")
    journal(host, "unpack", folder)
    steps = [
        (["install", "-d", "-o", RUNNER_USER, "-g", RUNNER_USER, "-m", "0700", f"/home/{RUNNER_USER}/runners"], "runners folder"),
        (["install", "-d", "-o", RUNNER_USER, "-g", RUNNER_USER, "-m", "0700", folder], "the runner's folder"),
        (["install", "-o", RUNNER_USER, "-g", RUNNER_USER, "-m", "0600", tmp, f"{folder}/runner.tgz"], "the download"),
    ]
    for argv, what in steps:
        rc, out = sudo(host, argv)
        if rc != 0:
            rep.stop(f"could not make {what}: {tail(out)}")
            return False
    os.remove(tmp)
    rc, out = as_runner(host, ["sha256sum", f"{folder}/runner.tgz"])
    if rc != 0 or not out.startswith(rel["sha256"]):
        rep.stop("the copy in the runner's folder does not have the checked checksum: nothing unpacked")
        return False
    rc, out = as_runner(host, ["tar", "xzf", f"{folder}/runner.tgz", "-C", folder])
    if rc != 0:
        rep.stop(f"could not unpack the runner: {tail(out)}")
        return False
    as_runner(host, ["rm", "-f", f"{folder}/runner.tgz"])
    rep.ok(f"unpacked fresh into {folder}, with its own work folder")
    journal(host, "dependencies", f"{folder}/bin/installdependencies.sh")
    rc, out = sudo(host, [f"{folder}/bin/installdependencies.sh"], timeout=900)
    if rc != 0:
        rep.warn(f"the runner's own dependency script did not finish: {tail(out)}")
    else:
        rep.ok("the runner's own dependency script ran")
    return True


def register(gh: GitHub, host: Host, repo: str, folder: str, name: str, labels: list[str], rep: Report) -> bool:
    """G2.5, G2.6, G2.7, G2.11: registered to this one repository with only our labels; the token by stdin."""
    s, d = gh.api("POST", f"repos/{repo}/actions/runners/registration-token")
    token = (d or {}).get("token") if s in (200, 201) else None
    if not token:
        rep.stop(f"GitHub gave no registration token (HTTP {s}): admin on the repository is needed")
        return False
    journal(host, "register", f"{repo} as {name} with labels {','.join(labels)}")
    url = f"https://github.com/{repo}"
    rc, out = as_runner(host, ["bash", "-c", CONFIG_SCRIPT, "minions-config", folder, url, name, ",".join(labels)],
                        input=token + "\n")
    out = mask(out, token)
    token = None  # dropped (INV2)
    if rc != 0:
        rep.stop(f"the runner's configuration failed: {tail(out)}")
        return False
    rep.ok(f"registered to {repo} only, as {name}, labels {', '.join(labels)} (no default labels)")
    return True


def lock_credentials(host: Host, folder: str, rep: Report) -> bool:
    """G2.8: the runner writes .credentials and .runner as 664; they become 600, read back."""
    files = [f"{folder}/.credentials", f"{folder}/.runner", f"{folder}/.credentials_rsaparams"]
    journal(host, "chmod", "0600 credential files")
    sudo(host, ["chmod", "0600"] + files)
    rc, out = sudo(host, ["stat", "-c", "%a %U %n"] + files)
    rows = [x.split(None, 2) for x in out.splitlines() if x.strip()]
    if rc != 0 or len(rows) != 3 or any(r[0] != "600" or r[1] != RUNNER_USER for r in rows):
        rep.stop(f"the runner's credential files are not its own only: {tail(out, 3)}")
        return False
    rep.ok(f"the credential files are 600, owned by {RUNNER_USER}, read back")
    return True


def install_service(host: Host, folder: str, survey_: dict, rep: Report) -> str | None:
    """G2.9: the runner's own unit, then our drop-in and slice, read back."""
    journal(host, "svc.sh install", folder)
    rc, out = sudo(host, ["bash", "-c", 'cd "$1" && ./svc.sh install "$2"', "minions-svc", folder, RUNNER_USER])
    if rc != 0:
        rep.stop(f"the runner's service install failed: {tail(out)}")
        return None
    rc, unit = sudo(host, ["cat", f"{folder}/.service"])
    unit = unit.strip()
    if rc != 0 or not re.fullmatch(r"actions\.runner\.[A-Za-z0-9_.@-]+\.service", unit):
        rep.stop("could not read the name of the runner's unit")
        return None
    limits = slice_limits(survey_)
    journal(host, "write", f"/etc/systemd/system/{SLICE} and {unit}.d/{DROPIN}")
    for path, text in ((f"/etc/systemd/system/{SLICE}", slice_text(limits)),
                       (f"/etc/systemd/system/{unit}.d/{DROPIN}", dropin_text())):
        sudo(host, ["install", "-d", "-m", "0755", os.path.dirname(path)])
        rc, out = sudo(host, ["tee", path], input=text)
        if rc != 0:
            rep.stop(f"could not write {path}: {tail(out)}")
            return None
    for argv in (["systemctl", "daemon-reload"], ["systemctl", "enable", unit], ["systemctl", "restart", unit]):
        journal(host, " ".join(argv))
        rc, out = sudo(host, argv)
        if rc != 0:
            rep.stop(f"`{' '.join(argv)}` failed: {tail(out)}")
            return None
    rc, out = host.run(["systemctl", "show", unit, "-p", "Slice", "-p", "Nice", "-p", "CPUWeight", "-p", "Restart",
                        "-p", "Wants", "-p", "UnitFileState", "-p", "User"])
    got = dict(x.split("=", 1) for x in out.splitlines() if "=" in x)
    want = {"Slice": SLICE, "Nice": "10", "CPUWeight": "50", "Restart": "on-failure", "UnitFileState": "enabled",
            "User": RUNNER_USER}
    bad = [f"{k}={got.get(k)!r}" for k, v in want.items() if got.get(k) != v]
    if "network-online.target" not in got.get("Wants", ""):
        bad.append("Wants has no network-online.target")
    if bad:
        rep.stop(f"the runner's service is not as written: {', '.join(bad)}")
        return None
    rc, out = host.run(["systemctl", "show", SLICE, "-p", "CPUQuotaPerSecUSec", "-p", "MemoryMax"])
    rep.ok(f"boot service {unit}: enabled, in {SLICE} ({limits['CPUQuota']} CPU, memory at most "
           f"{gb(int(limits['MemoryMax']))}), nice 10, restarts on failure, after the network; read back")
    return unit


def both_sides(gh: GitHub, host: Host, repo: str, name: str, unit: str, labels: list[str], rep: Report,
               wait_s: int = 90, poll_s: float = 5) -> bool:
    """G2.10: GitHub lists the runner online and idle, with only our labels (G2.11), and the service is active."""
    seen = "not listed"
    left = wait_s
    while True:
        s, d = gh.api("GET", f"repos/{repo}/actions/runners?per_page=100")
        mine = [r for r in ((d or {}).get("runners", []) if s == 200 else []) if r.get("name") == name]
        rc, active = host.run(["systemctl", "is-active", unit])
        active = active.strip()
        if mine:
            r = mine[0]
            got = sorted(x.get("name") for x in r.get("labels", []))
            if got != sorted(labels):
                rep.stop(f"GitHub lists {name} with labels {', '.join(got)}: only ours ({', '.join(labels)}) are "
                         "allowed, so no job asking for `self-hosted` can land on it")
                return False
            seen = f"{r.get('status')}, {'busy' if r.get('busy') else 'idle'}"
            if r.get("status") == "online" and not r.get("busy") and active == "active":
                rep.ok(f"both sides: GitHub lists {name} online and idle with only our labels; the service is active")
                return True
        if left <= 0:
            break
        host.sleep(poll_s)
        left -= poll_s
    rep.wait(f"GitHub says {name} is {seen}; the service is {active or 'not read'}. Run `look {repo}` later, or "
             "check the service's log")
    return False


def setup(repo: str, host: Host | None = None, gh: GitHub | None = None, out=None,
          interactive: bool | None = None) -> int:
    host = host or Host()
    gh = gh or GitHub(host)
    rep = Report(out)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        rep.stop(f"not a repository name: {repo!r} (write owner/name)")
        return rep.code()
    entry = check_fresh_look(host, repo, rep)
    if entry is None:
        return rep.code()
    if host.system() != "Linux" or not host.which("systemctl"):
        rep.stop("this version sets up Linux with systemd only; macOS comes in the next version")
        return rep.code()
    if not check_persons_home(host, rep):
        return rep.code()
    if interactive is None:
        interactive = host.isatty()
    if not ask_admin_once(host, rep, interactive):
        return rep.code()
    if not ensure_runner_user(host, rep):
        return rep.code()
    owner, name_ = repo.split("/", 1)
    folder = f"/home/{RUNNER_USER}/runners/{owner}-{name_}"
    rel = runner_release(gh, repo, host, rep)
    if rel is None or not unpack_runner(host, rel, folder, rep):
        return rep.code()
    survey_ = entry.get("survey") or {}
    labels = runner_labels(survey_)
    runner_name = f"minions-{re.sub(r'[^A-Za-z0-9-]', '-', host.hostname())}"[:60]
    if not register(gh, host, repo, folder, runner_name, labels, rep):
        return rep.code()
    if not lock_credentials(host, folder, rep):
        return rep.code()
    unit = install_service(host, folder, survey_, rep)
    if unit is None:
        return rep.code()
    data = load_state(host)
    data.setdefault("runners", {})[repo] = {"name": runner_name, "folder": folder, "unit": unit, "labels": labels,
                                            "version": rel["version"],
                                            "time": host.now().strftime("%Y-%m-%dT%H:%M:%SZ")}
    save_state(host, data)
    both_sides(gh, host, repo, runner_name, unit, labels, rep)
    return rep.code()


# ------------------------------------------------------------------------------------------------------------- look

def look(repo: str, start: bool, host: Host | None = None, gh: GitHub | None = None, out=None) -> int:
    host = host or Host()
    gh = gh or GitHub(host)
    rep = Report(out)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        rep.stop(f"not a repository name: {repo!r} (write owner/name)")
        return rep.code()
    found: dict = {}
    try:
        return _look(repo, start, host, gh, rep, found)
    finally:
        try:
            record_look(host, repo, rep, found.get("survey"))
        except OSError as e:
            rep.warn(f"could not write this look down in {host.state_dir()}: {e}; setup will ask for a new look")


def _look(repo: str, start: bool, host: Host, gh: GitHub, rep: Report, found: dict) -> int:
    if not check_github_cli(host, rep):
        return rep.code()
    if not check_admin(host, rep):
        return rep.code()
    data = check_repository(gh, host, repo, rep)
    if data is None:
        return rep.code()
    if start:
        start_test(gh, host, repo, rep)
    else:
        rep.need("say yes to one start test: it pushes a temporary branch with one workflow file, its job runs on "
                 "no machine, and the branch and the run are deleted within minutes; then run "
                 f"`look {repo} --start-test`", blocks=False)
        recent_runs(gh, repo, rep)
    if rep.stopped:
        return rep.code()
    check_secrets(gh, repo, (data.get("owner") or {}).get("type"), rep)
    s = survey(host, rep)
    found["survey"] = s
    what_lives_here(host, rep)
    check_workflows(gh, repo, s["system"], rep)
    check_cost_rule(host, rep)
    return rep.code()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="minions.py", description="Windstorm Repo Minions " + VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lk = sub.add_parser("look", help="read the repository and this computer; change nothing")
    lk.add_argument("repo", help="owner/name of a private GitHub repository")
    lk.add_argument("--start-test", action="store_true",
                    help="with your yes: one job on a temporary branch, run on no machine, then deleted")
    st = sub.add_parser("setup", help="install one runner for one private repository on this computer (Linux)")
    st.add_argument("repo", help="owner/name; needs a look of it on this computer under an hour old")
    a = ap.parse_args(argv)
    if a.cmd == "look":
        return look(a.repo, a.start_test)
    if a.cmd == "setup":
        return setup(a.repo)
    return EXIT_STOP


if __name__ == "__main__":
    sys.exit(main())
