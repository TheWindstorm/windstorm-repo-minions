#!/usr/bin/env python3
"""Windstorm Repo Minions: the tool the skill's agent runs.

Plan: DNA-STRAND-MASTER-PLAN.md. This version builds gene G1 (Look first) only: `look` reads and reports and changes
nothing on the computer. Its one change on GitHub, the start test (G1.4), runs only with `--start-test` (the person's
yes) and leaves nothing behind.

Output: one fact per line, each starting with `ok`, `warn`, `stop` or `need you`. Exit 0, or 2 after a `stop`,
or 3 after a `need you` that blocks.

Python 3.10+, standard library only. Every GitHub call goes through GitHub.api(), which runs the person's own
GitHub CLI (`gh api`): the tool never holds or prints a token.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
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

EXIT_OK, EXIT_STOP, EXIT_NEED = 0, 2, 3
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

    def code(self) -> int:
        return EXIT_STOP if self.stopped else EXIT_NEED if self.blocked else EXIT_OK


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
            with open(rec, "a", encoding="utf-8") as f:
                f.write(json.dumps({"method": method, "path": path, "body": body, "status": status,
                                    "data": data}) + "\n")
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
    """G1.7: what a job could disturb or be disturbed by. Names and counts only."""
    out = {"ports": set(), "containers": [], "runners": []}
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
            if rc == 0:
                out["containers"] += [f"{x} ({tool})" for x in text.split()]
            else:
                rep.warn(f"{tool} is installed but its containers could not be listed from this account")
    if host.system() == "Linux" and host.which("systemctl"):
        rc, text = host.run(["systemctl", "list-units", "--all", "--plain", "--no-legend", "--no-pager",
                             "actions.runner.*"])
        if rc == 0:
            out["runners"] = [x.split()[0] for x in text.splitlines() if x.strip()]
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
        rep.warn(f"{len(out['containers'])} container(s) running: {', '.join(out['containers'][:20])}")
    if out["runners"]:
        rep.warn(f"{len(out['runners'])} other runner service(s) here: {', '.join(out['runners'][:10])}")
    serves = bool(set(ports) & {80, 443, 5432, 3306, 6379, 27017}) or bool(out["containers"])
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


# ------------------------------------------------------------------------------------------------------------- look

def look(repo: str, start: bool, host: Host | None = None, gh: GitHub | None = None, out=None) -> int:
    host = host or Host()
    gh = gh or GitHub(host)
    rep = Report(out)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        rep.stop(f"not a repository name: {repo!r} (write owner/name)")
        return rep.code()
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
    a = ap.parse_args(argv)
    if a.cmd == "look":
        return look(a.repo, a.start_test)
    return EXIT_STOP


if __name__ == "__main__":
    sys.exit(main())
