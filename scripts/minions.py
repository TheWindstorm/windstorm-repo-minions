#!/usr/bin/env python3
"""Windstorm Repo Minions: the tool the skill's agent runs.

Plan: DNA-STRAND-MASTER-PLAN.md. This version builds gene G1 (Look first), gene G2 (Install, Linux with systemd) and
the part of gene G3 (Move) due in 0.1.0.
`look` reads and reports and changes nothing on the computer except its own record in ~/.minions/. Its one change on
GitHub, the start test (G1.4), runs only with `--start-test` (the person's yes) and leaves nothing behind. `setup`
installs one runner for one private repository (G2), only after a fresh, clean `look`. `move` (G3) opens one pull
request that changes only the `runs-on` lines of the jobs this computer can take, and never merges it; `move --verify`
says "moved" only after a real run of each moved workflow finished on our runner.

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

# G8.3: every way a command can end. The worst line decides among 0, 2, 3 and 4.
EXIT_OK, EXIT_FAILED, EXIT_STOP, EXIT_NEED, EXIT_WAIT, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 3, 4, 64, 130
LINE_KINDS = ("ok", "warn", "stop", "need you", "waiting", "tell the person")
COMMANDS = ("look", "setup", "move", "status", "watch", "resume", "drain", "doctor", "uninstall")  # built so far, in G8.3's order
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
                  "not your computer. The repository's Actions settings and the account's billing page on GitHub are "
                  "yours to read; once GitHub starts runs, run `look <owner/repo> --start-test` again")


# ---------------------------------------------------------------------------------------------------------- output

class Report:
    """Collects the lines `look` prints; the worst kind decides the exit code."""

    def __init__(self, out=None):
        self.out = out or sys.stdout
        self.lines: list[str] = []
        self.stopped = False
        self.blocked = False
        self.waiting = False
        self.asked = False
        self.out_closed = False
        self._tells: list[str] = []

    def _put(self, kind: str, text: str) -> None:
        self._flush_tells()
        self._print(f"{kind} {text}")

    def _flush_tells(self) -> None:
        """G7.10: the words for the person go out as one numbered block, "tell the person 1 of 7: ..." to "7 of 7",
        so a line dropped or merged in a relay shows to the agent and to the person (run B again: a small model
        shortened the block, dropped two lines and reworded two, under the word "verbatim")."""
        tells, self._tells = self._tells, []
        for i, text in enumerate(tells, 1):
            self._print(f"tell the person {i} of {len(tells)}: {text}")

    def _print(self, line: str) -> None:
        self.lines.append(line)
        if self.out_closed:
            return
        try:
            print(line, file=self.out, flush=True)
        except BrokenPipeError:
            # G8.13: whoever read the output stopped reading (`| head`, seen). The command still finishes its work
            # and its exit code; it stops printing, and never dies half-done with a traceback.
            self.out_closed = True
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), self.out.fileno())
            except (OSError, ValueError, AttributeError):
                pass

    def ok(self, text: str) -> None:
        self._put("ok", text)

    def warn(self, text: str) -> None:
        self._put("warn", text)

    def stop(self, text: str) -> None:
        self.stopped = True
        self._put("stop", text)

    def need(self, text: str, blocks: bool = True, answered_here: bool = False) -> None:
        """A step that is the person's. `blocks`: nothing after it may run. Every need-you line sets exit 3 (P1a: an
        agent that trusts exit codes would go on past an ask), except one answered in this same run
        (`answered_here`: the password sudo asks for at once, in this terminal)."""
        if blocks:
            self.blocked = True
        if not answered_here:
            self.asked = True
        self._put("need you:", text)

    def tell(self, text: str) -> None:
        """G7.10: words the agent passes on to the person exactly as printed, before the next step. Held until the
        block ends (the next line of another kind, or the command's end), then printed numbered."""
        self._tells.append(text)

    def wait(self, text: str) -> None:
        self.waiting = True
        self._put("waiting", text)

    def code(self) -> int:
        self._flush_tells()
        if self.stopped:
            return EXIT_STOP
        if self.blocked or self.asked:
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

    def write_text(self, path: str, text: str) -> None:
        """A small file written whole (a reader never sees half of it), readable by every user."""
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)

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

    def slept_s(self) -> float | None:
        """R83: seconds this computer has been suspended since boot (boot time minus monotonic time, the clock the
        watch's timer counts by); None where it cannot be read."""
        try:
            return max(0.0, time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC))
        except (AttributeError, OSError):
            return None

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
        self.why = ""  # G5.15: why the last call got no HTTP answer, in words
        self.limited_until: dt.datetime | None = None  # G8.14: GitHub's API limit for this login, spent until then

    def limited(self) -> bool:
        until = getattr(self, "limited_until", None)
        host = getattr(self, "host", None)
        now = host.now() if host is not None else dt.datetime.now(dt.timezone.utc)
        return until is not None and now < until

    def api(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        if self.limited():  # G8.14: no call while the limit is spent; each one would only be refused
            self.why = limit_words(self.limited_until)
            return 429, {"message": self.why}
        argv = ["gh", "api", "-i", "-X", method, path, "-H", "Accept: application/vnd.github+json"]
        stdin = None
        if body is not None:
            argv += ["--input", "-"]
            stdin = json.dumps(body)
        try:
            p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=60)
            raw = p.stdout
        except FileNotFoundError:
            self.why = ("the GitHub CLI (gh) could not be found with this PATH: "
                        + (os.environ.get("PATH") or "(none)"))
            return 0, {"message": self.why}
        except (OSError, subprocess.SubprocessError) as e:
            self.why = f"the GitHub CLI could not be run: {e}"
            return 0, {"message": self.why}
        status, data = parse_include(raw)
        self.why = "" if status else ("GitHub gave no answer: " + (tail(p.stderr or "", 2) or "no words"))
        until = rate_limited_until(raw, status, data, self.host.now())
        if until is not None:
            self.limited_until = until
            self.why = limit_words(until)
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


WATCH_CALLS_PER_HOUR = 24  # G8.14: one pass reads the repository and its runners (2 calls), every 5 minutes
WATCH_CALLS_PER_JOB = 2  # R90: and each job that ends here is read once (its record, and its notes if it failed)


def rate_limited_until(raw: str, status: int, data: object, now: dt.datetime) -> dt.datetime | None:
    """G8.14: when GitHub refused for its API limit (HTTP 403 or 429 with no calls left, or its words say so), the
    time it comes back, from GitHub's own headers; None for any other answer."""
    if status not in (403, 429):
        return None
    head = re.split(r"\r?\n\r?\n", raw or "", maxsplit=1)[0]
    left = re.search(r"(?im)^x-ratelimit-remaining:\s*(\d+)", head)
    words = str((data or {}).get("message", "")) if isinstance(data, dict) else ""
    reset = re.search(r"(?im)^x-ratelimit-reset:\s*(\d+)", head)
    retry = re.search(r"(?im)^retry-after:\s*(\d+)", head)  # GitHub's secondary limit says only this
    if not ((left and left.group(1) == "0") or "rate limit" in words.lower() or retry):
        return None
    if retry:
        return now + dt.timedelta(seconds=int(retry.group(1)))
    if reset:
        return dt.datetime.fromtimestamp(int(reset.group(1)), dt.timezone.utc)
    return now + dt.timedelta(seconds=60)


def limit_words(until: dt.datetime) -> str:
    return (f"GitHub's API limit for this login is spent until {until.strftime('%H:%MZ')}, and it comes back by itself "
            "then. Every program logged in as this account shares that limit; this tool's watch uses about "
            f"{WATCH_CALLS_PER_HOUR} calls an hour of it, and at most {WATCH_CALLS_PER_JOB} more for each job that ends here")


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
    "debian": "sudo apt update && sudo apt install gh",  # P1a: a fresh cloud image has no package lists yet
    "fedora": "sudo dnf install gh",
    "arch": "sudo pacman -Syu github-cli",  # Arch never installs on stale lists (-S alone); not seen here
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


# R77: said in the line itself, where an agent told that gh is missing looks for another road to GitHub.
GH_ONLY = ("This skill reaches GitHub only through the GitHub CLI and its own login: no SSH key and no other saved "
           "login on this computer is used or looked for. This step is yours, in your own terminal")


def check_github_cli(host: Host, rep: Report) -> bool:
    """G1.10: the first check. Without the GitHub CLI and a login, nothing else runs."""
    if not host.which("gh"):
        key = "Darwin" if host.system() == "Darwin" else os_release_id(host)
        cmd = INSTALL_GH.get(key, "see https://cli.github.com for this system")
        rep.need(f"install the GitHub CLI: {cmd}. {GH_ONLY}")
        return False
    rc, _ = host.run(["gh", "auth", "status", "--hostname", "github.com"])
    if rc != 0:
        rep.need(f"log in to GitHub: run `gh auth login` and follow its steps. {GH_ONLY}")
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
             "rights once: run it as an account that can become admin (a member of sudo), or ask this computer's admin")
    rep.stop("not touched anything: this account cannot become admin on this computer. Run it as an account "
             "that can, or ask this computer's admin")
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
        rep.ok("organisation: this version gives this one repository its own runner on this computer; runner "
               "groups for an organisation's chosen repositories come in the next version")
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
    # R79: newer gh quotes each scope ('repo', 'workflow'); older gh (2.23 on Debian 12) prints them bare.
    return set(re.findall(r"'([^']+)'", m.group(1))) or {x.strip() for x in m.group(1).split(",") if x.strip()}


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
            if deadline == wait_s:  # G8.6: the deadline is said once the command really waits
                rep.ok("start test: waiting for GitHub's run of it, until "
                       f"{(host.now() + dt.timedelta(seconds=wait_s)).strftime('%H:%M:%SZ')} at the latest")
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


def read_back_gone(gh: GitHub, host: Host, path: str) -> bool | None:
    """R82: True once GitHub answers 404. Asked up to three times, 3 s apart (a deletion can take a moment, and
    GitHub sometimes answers 5xx); then False when it still answers with the thing, None when it gave no clear
    answer: a third answer, never "left"."""
    s = 0
    for i in range(3):
        s, _ = gh.api("GET", path)
        if s == 404:
            return True
        if i < 2:
            host.sleep(3)
    return False if 200 <= s < 300 else None


def cleanup_start_test(gh: GitHub, host: Host, repo: str, branch: str | None, run_id, rep: Report) -> None:
    """Cancel and delete the run, delete the branch, and read back that both are gone. Runs even after an error."""
    left, unread = [], []

    def note(what: str, gone: bool | None) -> None:
        if gone is False:
            left.append(what)
        elif gone is None:
            unread.append(what)
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
        note(f"run {run_id}", read_back_gone(gh, host, f"repos/{repo}/actions/runs/{run_id}"))
    if branch:
        gh.api("DELETE", f"repos/{repo}/git/refs/heads/{branch}")
        note(f"branch {branch}", read_back_gone(gh, host, f"repos/{repo}/git/ref/heads/{branch}"))
        if not run_id:
            # GitHub may make the run late, after the branch is gone: look once more and take it away too
            host.sleep(10)
            s, d = gh.api("GET", f"repos/{repo}/actions/runs?branch={branch}&per_page=5")
            for late in ((d or {}).get("workflow_runs", []) if s == 200 else []):
                gh.api("POST", f"repos/{repo}/actions/runs/{late['id']}/cancel")
                host.sleep(3)
                gh.api("DELETE", f"repos/{repo}/actions/runs/{late['id']}")
                note(f"run {late['id']}", read_back_gone(gh, host, f"repos/{repo}/actions/runs/{late['id']}"))
    if branch or run_id:
        if left:
            rep.warn("start test: could not remove " + " and ".join(left) + "; remove it by hand")
        if unread:
            rep.warn("start test: deleted " + " and ".join(unread) + ", but GitHub gave no clear answer when asked "
                     "whether it is gone (three tries); look for it on the repository's Actions and branches pages")
        if not left and not unread:
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
    rep.ok(f"{s['os']}, {s['machine']}, {plural(s['cores'], 'core') if s['cores'] else 'cores not read'}, {mem}{avail}")
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
    s["sleep_delay"] = sleep_delay(host)
    if s["sleep_delay"] is None:
        rep.warn("the delay a sleep gets while a job runs: not read")
    else:
        rep.ok(f"a sleep asked while a job runs is put off by at most {s['sleep_delay']} s (logind's delay)")
    s["battery"] = has_battery(host)
    if s["battery"] is None:
        rep.warn("battery: not read")
    elif s["battery"]:
        rep.warn("this computer has a battery: " + LAPTOP_WORDS)
    s["lid"] = has_lid(host)  # G11.1: a battery OR a lid makes a laptop (a laptop on mains with its battery out)
    if s["lid"] and not s["battery"]:
        rep.warn("this computer has a lid: " + LAPTOP_WORDS)
    elif s["battery"] is False and s["lid"] is False:
        rep.ok("no battery and no lid: not a laptop")
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


VIRTUAL_DISK = "disk kind not known (virtual)"
VIRTUAL_VENDORS = re.compile(r"qemu|virtio|vmware|vbox|virtual|xen|red hat", re.I)


def virtual_disk(host: Host, block: str) -> bool:
    """G1.13: Linux reports a virtio disk as rotating, so a virtual disk's kind cannot be read from it (seen, C7)."""
    if re.match(r"(?:vd|xvd)[a-z]+$", os.path.basename(block)):
        return True
    for f in ("device/vendor", "device/model"):
        v = host.read(f"{block}/{f}")
        if v and VIRTUAL_VENDORS.search(v):
            return True
    rc, out = host.run(["systemd-detect-virt", "--vm"])
    return rc == 0 and out.strip() not in ("", "none")


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
            if virtual_disk(host, d):
                return VIRTUAL_DISK
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


LOGIND_DELAY = ["busctl", "get-property", "org.freedesktop.login1", "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager", "InhibitDelayMaxUSec"]


def sleep_delay(host: Host) -> int | None:
    """G4.9 (R48): how long this computer puts off a sleep while our delay lock is held, in whole seconds, read from
    logind (no admin rights needed); None when it could not be read."""
    if host.system() != "Linux":
        return None
    rc, out = host.run(LOGIND_DELAY)
    m = re.fullmatch(r"t (\d+)", out.strip()) if rc == 0 else None
    return int(m.group(1)) // 1_000_000 if m else None


def sleep_delay_words(seconds: int | None) -> str:
    if seconds is None:
        return "a little later (how much later could not be read on this computer)"
    if seconds == 0:
        return "at once"
    return f"about {seconds} seconds later (this computer's own delay)"


def power_source(host: Host) -> str | None:
    """G11.2: "battery" when this computer has a battery and no outside power is online, "mains" when outside power
    is online or there is no battery, None when it could not be read (not Linux)."""
    if host.system() != "Linux":
        return None
    battery, outside = False, False
    for name in host.listdir("/sys/class/power_supply"):
        kind = (host.read(f"/sys/class/power_supply/{name}/type") or "").strip()
        if kind == "Battery":
            battery = True
        elif kind in ("Mains", "USB", "USB_C", "USB_PD") and \
                (host.read(f"/sys/class/power_supply/{name}/online") or "").strip() == "1":
            outside = True
    return "battery" if battery and not outside else "mains"


def has_lid(host: Host) -> bool | None:
    """G11.1: a lid switch the system knows of (Linux: ACPI lists it)."""
    if host.system() != "Linux":
        return None
    return bool(host.listdir("/proc/acpi/button/lid"))


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
        rc, out = host.run(systemctl("is-enabled", "sleep.target", "suspend.target"))
        states = out.split()
        if states and all(x == "masked" for x in states[:2]):
            return "off (sleep and suspend masked)"
        if states:
            return "the system may sleep (sleep.target not masked)"
    return None


# ------------------------------------------------------------------------------------------------------------ G1.7

UPKEEP_PORTS = {22: "remote login", 53: "name service", 67: "address service", 68: "address service",
                123: "time service", 5353: "local name discovery", 5355: "local name resolution"}


def listening_ports(tcp_text: str) -> set[int]:
    """Ports in LISTEN state (0A) on an address other than loopback, from /proc/net/tcp or tcp6."""
    ports = set()
    for line in tcp_text.splitlines()[1:]:
        f = line.split()
        if len(f) < 4 or f[3] != "0A":
            continue
        addr, port = f[1].rsplit(":", 1)
        # Loopback is all of 127.0.0.0/8 (a stock Ubuntu's own name service listens on 127.0.0.53), ::1, and
        # 127.x mapped into IPv6; the first byte of an IPv4 address is the last pair of hex digits here.
        if (len(addr) == 8 and addr.endswith("7F")) or addr == "00000000000000000000000001000000" or (
                len(addr) == 32 and addr.startswith("0000000000000000FFFF0000") and addr.endswith("7F")):
            continue
        ports.add(int(port, 16))
    return ports


def what_lives_here(host: Host, rep: Report) -> dict:
    """G1.7: what a job could disturb or be disturbed by. On a shared computer other users' things are counted,
    never named: only what is the person's own is named (their rootless containers, runner services that run as
    them). Port numbers are named; they say nothing of whose they are."""
    out = {"ports": set(), "containers": [], "shared_containers": 0, "runners": [], "others_runners": 0, "ours": []}
    # Our own runner, after setup, is neither "other" nor the person's own: it runs as minions-runner (P1a).
    ours = {r.get("unit") for r in (load_state(host).get("runners") or {}).values()}
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
        rc, text = host.run(systemctl("list-units", "--all", "--plain", "--no-legend",
                                     "actions.runner.*"))
        if rc == 0:
            me = host.user()
            for unit in [x.split()[0] for x in text.splitlines() if x.strip()]:
                if unit in ours:
                    out["ours"].append(unit)
                    continue
                urc, owner = host.run(systemctl("show", "-p", "User", "--value", unit))
                if urc == 0 and owner.strip() == me:
                    out["runners"].append(unit)
                else:
                    out["others_runners"] += 1
    elif host.system() == "Darwin":
        rc, text = host.run(["launchctl", "list"])
        if rc == 0:
            out["runners"] = [x.split()[-1] for x in text.splitlines() if "actions.runner." in x]
    ports = sorted(out["ports"])
    # R75: the computer's own upkeep (remote login and the like) is normal on every Linux machine: an ok line that
    # says what it is, so an agent does not turn it into a question for the person; any other port stays a warn.
    upkeep = [p for p in ports if p in UPKEEP_PORTS]
    other = [p for p in ports if p not in UPKEEP_PORTS]
    if upkeep:
        rep.ok(f"{plural(len(upkeep), 'port')} open to the network for this computer's own upkeep: "
               + ", ".join(f"{p} ({UPKEEP_PORTS[p]})" for p in upkeep)
               + "; normal on most computers, not counted as something live")
    if other:
        rep.warn(f"{plural(len(other), 'port')} open to the network here: {', '.join(map(str, other[:20]))}"
                 f"{' ...' if len(other) > 20 else ''}")
    if not ports:
        rep.ok("no port open to the network")
    if out["containers"]:
        rep.warn(f"{len(out['containers'])} container(s) of yours running: {', '.join(out['containers'][:20])}")
    if out["shared_containers"]:
        rep.warn(f"{out['shared_containers']} container(s) running on this computer's shared container service "
                 "(not named: they may be another person's)")
    if out["ours"]:
        rep.ok(f"this tool's own runner here: {', '.join(out['ours'])}")
    if out["runners"]:
        rep.warn(f"{len(out['runners'])} other runner service(s) of yours here: {', '.join(out['runners'][:10])}")
    if out["others_runners"]:
        rep.warn(f"{out['others_runners']} other runner service(s) here that do not run as you (not named)")
    # R72: any port open to the network counts, but the computer's own upkeep (remote login, name service, address
    # and time service, local name discovery); a container counts too.
    live = [p for p in ports if p not in UPKEEP_PORTS]
    what = ([f"port(s) open to the network: {', '.join(map(str, live[:10]))}{' ...' if len(live) > 10 else ''}"]
            if live else [])
    if out["containers"] or out["shared_containers"]:
        what.append(f"{len(out['containers']) + out['shared_containers']} container(s) running")
    serves = bool(what)
    out["serves"], out["serves_what"] = serves, "; ".join(what)
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


PUBLISH_STEPS = (  # G3.3: a step that publishes or deploys, by what it runs or uses
    (r"\bnpm\s+publish\b", "runs npm publish"),
    (r"\b(?:twine\s+upload|poetry\s+publish|uv\s+publish|flit\s+publish)\b", "uploads a Python package"),
    (r"\bdocker\s+(?:image\s+)?push\b", "pushes a container image"),
    (r"\bgh\s+release\b", "makes a GitHub release"),
    (r"\bcargo\s+publish\b|\bgem\s+push\b", "publishes a package"),
    (r"uses:\s*[^@\s]*(?:deploy|publish|release|pages)[^@\s]*@", "uses a deploy or release action"),
)
PUBLISH_SECRET = re.compile(r"DEPLOY|PUBLISH|RELEASE|NPM|PYPI|DOCKER|REGISTRY|CARGO|GEM_HOST", re.I)


def read_workflow(text: str) -> list[dict]:
    """A small line reader for the parts of a workflow `look` and `move` need, per job: its runs-on labels and the
    lines they sit on, setup actions, sudo, container and services, its name, environment, the secrets it names and
    the steps that publish. Not a YAML parser: anything it cannot follow is reported as not read."""
    jobs: list[dict] = []
    lines = text.splitlines()
    in_jobs = False
    job_indent = None
    job = None
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = strip_comment(raw)
        at = i
        i += 1
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if indent == 0:
            in_jobs = body == "jobs:"
            job = None
            job_indent = None
            continue
        if not in_jobs:
            continue
        if job_indent is None and re.match(r"^[A-Za-z0-9_-]+:\s*$", body):
            job_indent = indent
        if indent == job_indent and re.match(r"^[A-Za-z0-9_-]+:\s*$", body):
            job = {"id": body[:-1], "runs_on": None, "setup": [], "sudo": False, "container": False,
                   "services": False, "name": None, "environment": False, "secrets": set(), "publishes": [],
                   "runs_on_at": None, "key_indent": None}
            jobs.append(job)
            continue
        if job is None:
            continue
        if job["key_indent"] is None and indent > job_indent:
            job["key_indent"] = indent
        top = indent == job["key_indent"]
        job["secrets"] |= set(re.findall(r"secrets\.([A-Za-z0-9_]+)", line))
        for pat, words in PUBLISH_STEPS:
            if re.search(pat, line) and words not in job["publishes"]:
                job["publishes"].append(words)
        if top and re.match(r"^environment:", body):
            job["environment"] = True
        m = re.match(r"^name:\s*(.+)$", body)
        if top and m:
            job["name"] = m.group(1).strip().strip("'\"")
        m = re.match(r"^runs-on:\s*(.*)$", body)
        if top and m:
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
            job["runs_on_at"] = (at, i)
            continue
        m = re.match(r"^-?\s*uses:\s*actions/(setup-[A-Za-z0-9_-]+)@", body)
        if m:
            job["setup"].append(m.group(1))
        if re.search(r"(^|[\s;&|(])sudo\s", body.lstrip("- ")):
            job["sudo"] = True
        if top and re.match(r"^container:", body):
            job["container"] = True
        if top and re.match(r"^services:", body):
            job["services"] = True
    return jobs


def read_triggers(text: str) -> dict:
    """The workflow's `on:` events, and whether a push of a tag starts it (G3.3)."""
    events: set[str] = set()
    tag_push = False
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = strip_comment(lines[i])
        i += 1
        m = re.match(r"^(?:on|\"on\"|'on'|true):\s*(.*)$", line)
        if not m:
            continue
        val = m.group(1).strip()
        if val:
            events |= set(parse_labels(val))
            break
        ev_indent = None
        current = None
        while i < len(lines):
            sub = strip_comment(lines[i])
            if sub.strip() and len(sub) - len(sub.lstrip(" ")) == 0:
                break
            i += 1
            if not sub.strip():
                continue
            ind = len(sub) - len(sub.lstrip(" "))
            body = sub.strip()
            if ev_indent is None:
                ev_indent = ind
            if ind == ev_indent:
                if body.startswith("- "):
                    events.add(body[2:].strip().strip("'\""))
                    continue
                current = body.split(":", 1)[0].strip().strip("'\"")
                events.add(current)
            elif current == "push" and re.match(r"^tags(?:-ignore)?:", body):
                tag_push = True
        break
    return {"events": events, "tag_push": tag_push}


HOSTED = {"ubuntu": "Linux", "macos": "Darwin", "windows": "Windows"}


NOT_OURS = "asks for a runner that is not ours"


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
    return "warn", f"{NOT_OURS} ({', '.join(ro)})"


def look_job_line(path: str, triggers: dict, job: dict, survey_: dict) -> tuple[str, str]:
    """G1.15: look judges a job with move's own function (plan_job), so both say the same thing in the same words."""
    system = survey_.get("system") or "Linux"
    machine = survey_.get("machine") or "x86_64"
    kind, words, _ = plan_job(path, triggers, job, system, machine, runner_labels(survey_), set())
    if kind == "move":
        _, jw = judge_job(job, system)
        extra = jw.split("can move here", 1)[1] if "can move here" in jw else ""
        return "ok", f"can move here: {words}{extra}"
    if kind == "here":
        return "ok", words
    if kind == "publish":
        return "warn", (f"publishes or deploys ({words}): it is left as it is, and moves only with your word for "
                        f"this job (`move --include-job {os.path.basename(path)}:{job['id']}`)")
    return "warn", words


def check_workflows(gh: GitHub, repo: str, survey_: dict, rep: Report) -> list[dict]:
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
        triggers = read_triggers(text)
        for job in jobs:
            kind, words = look_job_line(f["path"], triggers, job, survey_)
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
    if items and any(x.startswith("Self-hosted runners") for x in items):
        keep_cost_read(host, items)  # G5.10: the watch compares against it once a week
    if not items or not any(x.startswith("Self-hosted runners") for x in items):
        rep.warn(f"I could not read GitHub's rule today ({when}): see {COST_URL}")
        rep.tell("I could not read GitHub's page about charges today.")
        return
    rep.tell(f"GitHub's billing page, read {when}, lists \"Self-hosted runners\" among the uses that do not reduce "
           "the included usage (its words: \"" + COST_LIST_HEAD + " " + " ".join(items) + "\"). In December 2025 "
           "GitHub announced a charge for self-hosted runner use and postponed it. Artifact and cache storage "
           f"count the same whichever computer ran the job. Source: {COST_URL}")


COST_EVERY_S = 7 * 24 * 3600  # G5.10: the watch reads GitHub's cost rule again once a week


def keep_cost_read(host: Host, items: list[str]) -> None:
    data = load_state(host)
    data["cost"] = {"items": items, "time": stamp(host.now())}
    save_state(host, data)


def cost_weekly(host: Host, data: dict) -> None:
    """G5.10 (R16): once a week the watch reads GitHub's cost rule again (GitHub's documentation site, not its API:
    no call of the person's API limit). A changed sentence is kept with both versions and dates for the next
    `status`; a page that cannot be read is journaled, never taken for a change."""
    last = data.get("cost") or {}
    try:
        then = parse_stamp(last.get("checked") or last.get("time") or "")
    except ValueError:
        then = None
    if then is not None and (host.now() - then).total_seconds() < COST_EVERY_S:
        return
    try:
        items = read_cost_rule(host.fetch(COST_URL))
    except Exception:  # noqa: BLE001 - any failure to read is said as such
        items = None
    now = stamp(host.now())
    if not items:  # a list found but worded otherwise, or without the self-hosted line, IS a change
        journal(host, "cost rule", f"GitHub's page could not be read or its list was not found ({COST_URL})")
        data["cost"] = dict(last, checked=now)
        return
    if last.get("items") and items != last["items"]:
        data["cost_change"] = {"old": last["items"], "old_time": last.get("time"), "new": items, "new_time": now}
        journal(host, "cost rule changed", f"GitHub's list changed since {last.get('time')}")
    data["cost"] = {"items": items, "time": now, "checked": now}


def cost_change_words(c: dict) -> str:
    return (f"GitHub's billing page changed what it says about self-hosted runners: read {c['old_time']}, it said \""
            + COST_LIST_HEAD + " " + " ".join(c["old"]) + f"\"; read {c['new_time']}, it says \"" + COST_LIST_HEAD
            + " " + " ".join(c["new"]) + f"\". Read it yourself before relying on it: {COST_URL}")


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


def systemctl(*args: str) -> list[str]:
    """G8.5: every systemctl call of the tool, with --no-pager (last, so the command reads as it is typed)."""
    return ["systemctl", *args, "--no-pager"]


def journalctl(*args: str) -> list[str]:
    """G8.5: every journalctl call of the tool, with --no-pager."""
    return ["journalctl", *args, "--no-pager"]


def last_journal_step(host: Host) -> str:
    """The journal's last step, for a stop line when the tool fails or is interrupted (G8.3)."""
    try:
        with open(os.path.join(host.state_dir(), "journal.log"), encoding="utf-8") as f:
            lines = [x.strip() for x in f if x.strip()]
    except OSError:
        return "none written yet"
    if not lines:
        return "none written yet"
    parts = lines[-1].split(" ", 2)
    return " ".join(parts[1:]) if len(parts) > 1 else lines[-1]


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
                                                          "battery", "lid", "os", "sleep_delay", "serves",
                                                          "serves_what")}
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


DEFAULT_MEMORY_PCT = 60  # G4.2: interim fixed defaults until measured values exist (memory high 10 points lower)
MEMORY_FLOOR = 2 * 1024 ** 3  # G4.8: a memory max under this is too little for a job's build, tests and tools
MEMORY_PCT_MOST = 90  # --limits never gives jobs more than this share: something stays the person's


def slice_limits(survey_: dict, chosen: tuple[int, int] | None = None) -> dict:
    """G2.9 / G4.2 / G4.8: what all our runners together may use. Interim fixed defaults (G4.2 is not built): half
    the processors, never less than one (G4.8), memory high at 50 % and max at 60 %. `setup --limits` (G7.8)
    chooses other shares."""
    total = survey_.get("cores") or 2
    mem = survey_.get("mem") or 4 * 1024 ** 3
    cores, pct = chosen if chosen else (total // 2, DEFAULT_MEMORY_PCT)
    cores = max(1, min(cores, total))
    pct = max(10, min(pct, MEMORY_PCT_MOST))
    return {"CPUQuota": f"{cores * 100}%", "MemoryHigh": str(int(mem * (pct - 10) / 100)),
            "MemoryMax": str(int(mem * pct / 100)), "cores": cores, "total_cores": total, "pct": pct, "mem": mem}


def limits_in_words(lim: dict) -> str:
    """G7.8: the limits as the person hears them, with the real numbers and the one way to change them."""
    total = lim["total_cores"]
    words = (f"jobs here get at most {lim['cores']} of this computer's {plural(total, 'processor')} and "
             f"{lim['pct']} % of its memory ({gb(int(lim['MemoryMax']))}); the one way to change that is "
             "`setup <owner/repo> --limits <processors>,<memory %>`")
    if total == 1:  # the owner's ruling, 2026-11-23: warned plainly, never refused, never hidden by a rounding
        words += (". This computer has one processor: while a job runs it uses all of it, at low priority, and "
                  "everything else here will be slow")
    return words


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def memory_floor_line(repo: str, lim: dict) -> str | None:
    """G4.8: None when the memory max reaches the floor; else the stop/warn text with the way out and its cost."""
    if int(lim["MemoryMax"]) >= MEMORY_FLOOR:
        return None
    need = -(-MEMORY_FLOOR * 100 // lim["mem"])  # the smallest whole share that reaches the floor
    head = (f"at most {lim['pct']} % of this computer's memory is {gb(int(lim['MemoryMax']))}, under the "
            f"{gb(MEMORY_FLOOR)} a job needs at the least, so a job would likely be killed by the limit")
    if need > MEMORY_PCT_MOST:
        return head + (f". This computer has {gb(lim['mem'])} in all: too little to give a job "
                       f"{gb(MEMORY_FLOOR)} and keep some for you; it cannot be a minion, and there is no step to take "
                       "on this computer")
    return head + (f". The way out: `setup {repo} --limits {lim['cores']},{need}` gives jobs up to {need} % "
                   f"({gb(lim['mem'] * need / 100)}); while a job runs, that much less memory is left for your own "
                   "work, so the computer may slow down or the system may close other programs")


def parse_limits(raw: str) -> tuple[int, int]:
    """`--limits <processors>,<memory %>`, e.g. `2,70`."""
    m = re.fullmatch(r"\s*(\d+)\s*,\s*(\d+)\s*%?\s*", raw or "")
    if not m or int(m.group(1)) < 1 or not 10 <= int(m.group(2)) <= MEMORY_PCT_MOST:
        raise argparse.ArgumentTypeError(f"write --limits <processors>,<memory %>, e.g. 2,70 (processors at least "
                                         f"1, memory between 10 and {MEMORY_PCT_MOST} %)")
    return int(m.group(1)), int(m.group(2))


def dropin_text(folder: str = "") -> str:
    """G2.9; R88: when the kernel kills a job's process at our memory max, systemd's default (OOMPolicy=stop) stops
    the whole runner service (seen: the runner offline 30 s, the job marked cancelled). The job's process is the one
    that ends; the runner goes on. R89: a stop of the service mid-job skips the job-completed hook, so the stop
    itself ends the idle-sleep hold the job-started hook took (seen: left running, it would hold for 6 hours)."""
    stop_post = ""
    if folder:
        pidf = f"{folder}/_minions/inhibit.pid"
        stop_post = ("ExecStopPost=-/bin/sh -c 'p=\"$(cat " + pidf + " 2>/dev/null)\"; case \"$p\" in \"\"|*[!0-9]*) ;; "
                     "*) kill -TERM -- \"-$p\" 2>/dev/null || kill -TERM \"$p\" 2>/dev/null ;; esac; rm -f " + pidf + "'\n")
    return ("# Windstorm Repo Minions (G2.9): the runner's own unit has Restart=no and does not ask for the network.\n"
            "[Unit]\nWants=network-online.target\n\n"
            f"[Service]\nSlice={SLICE}\nNice=10\nCPUWeight=50\nRestart=on-failure\nRestartSec=30\nOOMPolicy=continue\n"
            + stop_post)


def slice_text(limits: dict) -> str:
    return ("# Windstorm Repo Minions: every runner of ours on this computer, together (G2.9, G4.2, G12.1).\n"
            "[Unit]\nDescription=Windstorm Repo Minions runners\nBefore=slices.target\n\n"
            f"[Slice]\nCPUQuota={limits['CPUQuota']}\nMemoryHigh={limits['MemoryHigh']}\nMemoryMax={limits['MemoryMax']}\n")


def sudo(host: Host, argv: list[str], input: str | None = None, timeout: int = 600) -> tuple[int, str]:
    """An admin step: always `sudo -n` (the password was asked once, before the first step)."""
    full = ["sudo", "-n"] + argv
    rc, out = host.run_in(full, input, timeout) if input is not None else host.run(full, timeout)
    if rc != 0 and "a password is required" in out:  # G2.12: the permission ran out, or this is another terminal
        out = out.rstrip("\n") + ("\nsudo's permission ran out or belongs to another terminal: run the same command "
                                  "again from the terminal where you typed the password (what was done is read back, "
                                  "never done twice)")
    return rc, out


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


def check_beside_live(survey_: dict, repo: str, yes: bool, rep: Report) -> bool:
    """G1.7, R72: a computer that serves something live is named at setup too, and setup goes on only on the person's
    yes in words; otherwise nothing is touched."""
    if "serves" not in survey_:
        rep.stop(f"the last look of {repo} did not say whether this computer serves something live (an older version "
                 f"of this tool made it): run `look {repo}` again")
        return False
    if not survey_["serves"]:
        return True
    what = survey_.get("serves_what") or "something live"
    if yes:
        rep.ok(f"this computer serves something live ({what}); you said yes to jobs beside it, and they get only the "
               "limits below")
        return True
    rep.need(f"this computer serves something live ({what}), and a job here would share its processors, memory and "
             f"disk. Nothing was changed. Choose another computer, or, if you want jobs here anyway, say yes in words: "
             f"`setup {repo} --beside-live-too`")
    return False


def ask_admin_once(host: Host, rep: Report, interactive: bool, cmd: str = "setup") -> bool:
    """G2.2: the admin password is the person's step, asked once (sudo -v); every later step is `sudo -n`."""
    rc, _ = host.run(["sudo", "-n", "true"])
    if rc == 0:
        rep.ok("admin rights ready (sudo did not need to ask now)")
        return True
    if interactive:
        rep.need("type this computer's admin password once, where sudo asks for it below (this step is yours)",
                 blocks=False, answered_here=True)
        if host.sudo_validate() == 0 and host.run(["sudo", "-n", "true"])[0] == 0:
            rep.ok("admin rights ready (asked once)")
            return True
        rep.stop("sudo did not accept the password: nothing was touched. Run the same command again and type the "
                 "password where sudo asks")
        return False
    rep.need(f"this needs this computer's admin password once: run `sudo -v` in your terminal, then run {cmd} again "
             "from that same terminal")
    return False


def ensure_runner_user(host: Host, rep: Report) -> bool:
    """G2.2: the runner's own user, in no group but its own, its home closed to others."""
    rc, _ = host.run(["id", "-u", RUNNER_USER])
    if rc != 0:
        journal(host, "useradd", RUNNER_USER)
        rc, out = sudo(host, ["useradd", "--create-home", "--user-group", "--shell", "/bin/bash", RUNNER_USER])
        if rc != 0:
            rep.stop(f"could not make the user {RUNNER_USER}: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
            return False
        rep.ok(f"made the user {RUNNER_USER}")
    else:
        rep.ok(f"the user {RUNNER_USER} is there already")
    rc, out = host.run(["id", "-nG", RUNNER_USER])
    groups = set(out.split()) if rc == 0 else set()
    extra = groups - {RUNNER_USER}
    if rc != 0 or extra & ADMIN_LIKE_GROUPS or extra:
        rep.stop(f"{RUNNER_USER} is in other groups ({', '.join(sorted(extra)) or 'not read'}): a runner user must be "
                 "in its own group only, never one that gives admin rights. Settle what it says, then run the same command again: what was done is read back, never done twice")
        return False
    home = f"/home/{RUNNER_USER}"
    journal(host, "chmod", f"0750 {home}")
    sudo(host, ["chmod", "0750", home])
    rc, out = sudo(host, ["stat", "-c", "%a", home])
    if rc != 0 or out.strip() != "750":
        rep.stop(f"the runner's home {home} is not closed to others (mode {out.strip() or 'not read'}). Settle what it says, then run the same command again: what was done is read back, never done twice")
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
        rep.stop(f"GitHub offers no runner for {system} {arch} (HTTP {s}). Run the same command again; if it stops here again, GitHub's answer named here is the reason")
        return None
    rel = pick[0]
    m = re.search(r"-(\d+\.\d+\.\d+)\.(tar\.gz|zip)$", rel.get("filename", ""))
    if not m or not re.fullmatch(r"[0-9a-f]{64}", rel.get("sha256_checksum") or ""):
        rep.stop("GitHub's runner download list is not in the shape this tool knows: nothing was downloaded. Run the same command again later; if it stops here again, do not go on: open an issue on this tool's repository with this line")
        return None
    version = m.group(1)
    s, notes = gh.api("GET", f"repos/actions/runner/releases/tags/v{version}")
    key = f"{system}-{arch}"
    mm = re.search(rf"<!-- BEGIN SHA {re.escape(key)} -->([0-9a-f]{{64}})<!-- END SHA {re.escape(key)} -->",
                   (notes or {}).get("body", "") if s == 200 else "")
    if not mm:
        rep.stop(f"the release notes of runner {version} carry no checksum for {key}: nothing was downloaded. Run the same command again later; if it stops here again, do not go on: open an issue on this tool's repository with this line")
        return None
    if mm.group(1) != rel["sha256_checksum"]:
        rep.stop(f"the two checksums GitHub publishes for runner {version} ({key}) disagree: nothing was downloaded. Run the same command again later; if it stops here again, do not go on: open an issue on this tool's repository with this line")
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
                 "finished; nothing was changed. `uninstall <owner/repo>` finishes a removal that was not finished; then "
                 "setup again")
        return False
    ensure_state_dir(host)
    tmp = os.path.join(host.state_dir(), rel["filename"])
    journal(host, "download", rel["url"])
    try:
        host.fetch_to(rel["url"], tmp)
        got = sha256_file(tmp)
    except Exception as e:  # noqa: BLE001
        rep.stop(f"could not download the runner: {e}. Run the same command again; if it stops here again, the answer named here is the reason")
        return False
    if got != rel["sha256"]:
        os.remove(tmp)
        rep.stop(f"the downloaded runner's checksum is not the one GitHub publishes: deleted, nothing installed. Run the same command again later; if it stops here again, do not go on: open an issue on this tool's repository with this line")
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
            rep.stop(f"could not make {what}: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
            return False
    os.remove(tmp)
    rc, out = as_runner(host, ["sha256sum", f"{folder}/runner.tgz"])
    if rc != 0 or not out.startswith(rel["sha256"]):
        rep.stop("the copy in the runner's folder does not have the checked checksum: nothing unpacked. Run the same command again later; if it stops here again, do not go on: open an issue on this tool's repository with this line")
        return False
    rc, out = as_runner(host, ["tar", "xzf", f"{folder}/runner.tgz", "-C", folder])
    if rc != 0:
        rep.stop(f"could not unpack the runner: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
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
        rep.stop(f"GitHub gave no registration token (HTTP {s}): admin on the repository is needed: log in "
                 "as one (`gh auth login`) or ask its owner, then run the same command again")
        return False
    journal(host, "register", f"{repo} as {name} with labels {','.join(labels)}")
    url = f"https://github.com/{repo}"
    rc, out = as_runner(host, ["bash", "-c", CONFIG_SCRIPT, "minions-config", folder, url, name, ",".join(labels)],
                        input=token + "\n")
    out = mask(out, token)
    token = None  # dropped (INV2)
    if rc != 0:
        rep.stop(f"the runner's configuration failed: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
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
        rep.stop(f"the runner's credential files are not its own only: {tail(out, 3)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
        return False
    rep.ok(f"the credential files are 600, owned by {RUNNER_USER}, read back")
    return True


def install_service(host: Host, folder: str, survey_: dict, rep: Report, limits: dict | None = None) -> str | None:
    """G2.9: the runner's own unit, then our drop-in and slice, read back."""
    journal(host, "svc.sh install", folder)
    rc, out = sudo(host, ["bash", "-c", 'cd "$1" && ./svc.sh install "$2"', "minions-svc", folder, RUNNER_USER])
    if rc != 0:
        rep.stop(f"the runner's service install failed: {tail(out)}")
        return None
    rc, unit = sudo(host, ["cat", f"{folder}/.service"])
    unit = unit.strip()
    if rc != 0 or not re.fullmatch(r"actions\.runner\.[A-Za-z0-9_.@-]+\.service", unit):
        rep.stop("could not read the name of the runner's unit. Settle what it says, then run the same command again: what was done is read back, never done twice")
        return None
    limits = limits or slice_limits(survey_)
    journal(host, "write", f"/etc/systemd/system/{SLICE} and {unit}.d/{DROPIN}")
    for path, text in ((f"/etc/systemd/system/{SLICE}", slice_text(limits)),
                       (f"/etc/systemd/system/{unit}.d/{DROPIN}", dropin_text(folder))):
        sudo(host, ["install", "-d", "-m", "0755", os.path.dirname(path)])
        rc, out = sudo(host, ["tee", path], input=text)
        if rc != 0:
            rep.stop(f"could not write {path}: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
            return None
    for argv in (systemctl("daemon-reload"), systemctl("enable", unit), systemctl("start", unit)):
        journal(host, " ".join(argv))
        rc, out = sudo(host, argv)
        if rc != 0:
            rep.stop(f"`{' '.join(argv)}` failed: {tail(out)}")
            return None
    rc, out = host.run(systemctl("show", unit, "-p", "Slice", "-p", "Nice", "-p", "CPUWeight", "-p", "Restart",
                                  "-p", "Wants", "-p", "UnitFileState", "-p", "User", "-p", "OOMPolicy"))
    got = dict(x.split("=", 1) for x in out.splitlines() if "=" in x)
    want = {"Slice": SLICE, "Nice": "10", "CPUWeight": "50", "Restart": "on-failure", "UnitFileState": "enabled",
            "User": RUNNER_USER, "OOMPolicy": "continue"}
    bad = [f"{k}={got.get(k)!r}" for k, v in want.items() if got.get(k) != v]
    if "network-online.target" not in got.get("Wants", ""):
        bad.append("Wants has no network-online.target")
    if bad:
        rep.stop(f"the runner's service is not as written: {', '.join(bad)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
        return None
    rc, out = host.run(systemctl("show", SLICE, "-p", "CPUQuotaPerSecUSec", "-p", "MemoryMax"))
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
        rc, active = host.run(systemctl("is-active", unit))
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
                data = load_state(host)  # R85: an offline record from before it came online is over
                rec = (data.get("runners") or {}).get(repo) or {}
                if rec.pop("offline_since", None) is not None:
                    rec.pop("offline_slept_s", None)
                    save_state(host, data)
                return True
        if left <= 0:
            break
        if left == wait_s:  # G8.6: the deadline is said once the command really waits
            rep.ok(f"waiting for GitHub to list {name} online and idle, until "
                   f"{(host.now() + dt.timedelta(seconds=wait_s)).strftime('%H:%M:%SZ')} at the latest")
        host.sleep(poll_s)
        left -= poll_s
    rep.wait(f"GitHub says {name} is {seen}; the service is {active or 'not read'}. Run `look {repo}` later, or "
             "check the service's log")
    return False


def one_repository_here(host: Host, repo: str, rep: Report) -> bool:
    """G7.6: version 0.1.0 serves one repository per computer, says so at setup, and holds it: a second repository's
    setup changes nothing while this tool's record names another one here."""
    data = load_state(host)
    others = sorted(r for r in {**(data.get("setting_up") or {}), **(data.get("runners") or {})} if r != repo)
    if others:
        rep.stop(f"this computer already serves {others[0]}, and this version serves one repository per computer, so "
                 f"nothing was changed. To serve {repo} here instead, run `uninstall {others[0]}` first, then "
                 f"`setup {repo}` again; more repositories per computer come in the next version")
        return False
    rep.ok(f"this version serves one repository per computer: here, {repo}; more come in the next version")
    return True


def setup(repo: str, host: Host | None = None, gh: GitHub | None = None, out=None,
          interactive: bool | None = None, limits: tuple[int, int] | None = None, beside_live: bool = False) -> int:
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
        rep.stop("this version sets up Linux with systemd only; macOS comes in the next version. Nothing was changed, "
                 "and there is no step to take on this computer now")
        return rep.code()
    if not one_repository_here(host, repo, rep):
        return rep.code()
    survey_ = entry.get("survey") or {}
    if not check_beside_live(survey_, repo, beside_live, rep):
        return rep.code()
    lim = slice_limits(survey_, limits)
    floor = memory_floor_line(repo, lim)
    if floor:
        rep.stop(floor)
        return rep.code()
    rep.ok(limits_in_words(lim))
    now_free = host.disk_free(host.home())  # read now, not the look's number
    if not check_disk_floor(now_free[0] if now_free else survey_.get("free"), rep, "no runner is set up"):
        return rep.code()
    if not check_persons_home(host, rep):
        return rep.code()
    if interactive is None:
        interactive = host.isatty()
    if not ask_admin_once(host, rep, interactive):
        return rep.code()
    known = load_state(host)
    if ((known.get("runners") or {}).get(repo) or {}).get("gone_on_github"):
        reregister(gh, host, repo, known, rep)  # G5.12
        return rep.code()
    adopt_unfinished(host, known, {repo})
    left = (known.get("runners") or {}).get(repo) or {}
    if left.get("unfinished") or left.get("removing"):
        # R71: run again after a cut, setup finishes it: what the cut setup made is taken away, read back, then fresh.
        rep.ok(f"a setup or removal of {repo} here was cut part-way: what it made is taken away first, read back, "
               "then setup starts fresh")
        if not remove_one(gh, host, repo, known, rep, DRAIN_WAIT_S, 10, f"setup {repo}"):
            return rep.code()
    if not ensure_runner_user(host, rep):
        return rep.code()
    owner, name_ = repo.split("/", 1)
    folder = f"/home/{RUNNER_USER}/runners/{owner}-{name_}"
    labels = runner_labels(survey_)
    runner_name = f"minions-{re.sub(r'[^A-Za-z0-9-]', '-', host.hostname())}"[:60]
    rel = runner_release(gh, repo, host, rep)
    if rel is None:
        return rep.code()
    known = load_state(host)
    if repo not in (known.get("runners") or {}):  # R71: written before anything is made, so a cut is found again
        known.setdefault("setting_up", {})[repo] = {"name": runner_name, "folder": folder, "labels": labels,
                                                    "time": host.now().strftime("%Y-%m-%dT%H:%M:%SZ")}
        save_state(host, known)
    if not unpack_runner(host, rel, folder, rep):
        return rep.code()
    if not register(gh, host, repo, folder, runner_name, labels, rep):
        return rep.code()
    if not lock_credentials(host, folder, rep):
        return rep.code()
    if not install_hooks(host, repo, folder, rep):
        return rep.code()
    version = runner_version(host, folder, rep) or rel["version"]
    unit = install_service(host, folder, survey_, rep, lim)
    if unit is None:
        return rep.code()
    data = load_state(host)
    data.setdefault("runners", {})[repo] = {"name": runner_name, "folder": folder, "unit": unit, "labels": labels,
                                            "version": version, "hooks": hook_dir(repo),
                                            "limits": {k: lim[k] for k in ("cores", "pct", "CPUQuota", "MemoryHigh",
                                                                           "MemoryMax")},
                                            "time": host.now().strftime("%Y-%m-%dT%H:%M:%SZ")}
    (data.get("setting_up") or {}).pop(repo, None)
    if "setting_up" in data and not data["setting_up"]:
        data.pop("setting_up")
    save_state(host, data)
    if not install_watch(host, rep, repo=repo):
        return rep.code()
    both_sides(gh, host, repo, runner_name, unit, labels, rep)
    return rep.code()


# ---------------------------------------------------------------------------------------------------- G4 keep ready

HOOKS_ROOT = "/usr/local/lib/windstorm-repo-minions"
DISK_FLOOR = 5 * 1024 ** 3  # G4.3: under this much free space for the work folders, the runner is to be held
JOB_HOURS_MOST = 6  # GitHub's default job time limit; a lock left by a runner that died ends by itself after this
HELD_LABEL = "minions-held"
RECORDS_ROOT = "/var/lib/windstorm-repo-minions"  # G5.7, G5.14: each job's start and end, as this computer saw them
# G5.15 (P1a): the watch writes, as you, the time it last READ the repository private; the job-start hook, as the
# runner user, refuses a job when that is older than SEEN_FRESH_S (after waiting HOLD_WAIT_S for a fresh one) or says
# "public". So a blind watch (no GitHub CLI, no login, no network, or not running) holds the runner on this computer,
# with no GitHub call and no admin rights.
SEEN_ROOT = f"{RECORDS_ROOT}/seen"
SEEN_PASSES = 3  # as for a silent watch (G5.3): one or two missed reads are a blip; three are 15 minutes blind
HOLD_WAIT_S = 360  # longer than one watch interval: after a boot or a wake the next pass comes within it
# G8.9 (P1a run B): every program the tool runs by name, and why. setup resolves each to a full path with your PATH,
# and its services run with exactly those folders (a system service has the system's PATH, not yours: a `gh` in
# your own folder was not found, in silence). A test fails when the tool runs a program by name not listed here.
PROGRAMS = {
    "gh": "every GitHub call", "systemctl": "services and timers", "sudo": "admin steps, after one password",
    "systemd-detect-virt": "the disk kind", "pgrep": "runner processes", "kill": "a stop by exact PID",
    "useradd": "the runner's user", "userdel": "removing it", "install": "folders and the tool's copy",
    "tee": "writing root's files from standard input", "cat": "reading root's files", "chown": "ownership",
    "chmod": "modes", "stat": "reading modes back", "id": "your group", "rm": "removing files", "test": "a path exists",
    "bash": "the runner's own scripts", "systemd-inhibit": "the two locks during a job",
    "journalctl": "the runner's own log lines", "sha256sum": "the runner's checksum", "tar": "unpacking the runner",
}
DARWIN_ONLY = {"launchctl", "pmset", "diskutil", "sw_vers", "sysctl", "netstat"}  # macOS; resolved there, later
# The programs the two job hooks run (as the runner user, which cannot enter your home folder).
HOOK_PROGRAMS = ("awk", "date", "cat", "dirname", "mkdir", "chmod", "env", "setsid", "systemd-inhibit", "sleep",
                 "pgrep", "seq", "kill", "rm", "realpath", "find", "df", "tail", "tr", "python3")
MEMORY_EVENTS = f"/sys/fs/cgroup/{SLICE}/memory.events"  # its oom_kill count: a kill by OUR memory limit
MEMORY_PRESSURE = f"/sys/fs/cgroup/{SLICE}/memory.pressure"  # R87: how much of the last minute jobs stalled on memory
MEMORY_CURRENT = f"/sys/fs/cgroup/{SLICE}/memory.current"
MEMORY_HIGH = f"/sys/fs/cgroup/{SLICE}/memory.high"
HELD_FULL_PCT = 10.0  # R87: every job task stalled on memory for at least this share of the last minute
HELD_PASSES = 2  # R87: named once this many watch passes in a row found the job held
DRAIN_WAIT_S = 1800
# G4.4 and G4.9: exactly two locks while a job runs, both nested in one process group the hooks start and end.
# `idle` in block mode stops only the computer's own idle sleep; `sleep` in DELAY mode only delays a sleep that was
# asked for (logind's InhibitDelayMaxSec), never refuses it. Never the lid, the keys, shutdown, or sleep in block mode.
INHIBIT_IDLE = ["systemd-inhibit", "--what=idle", "--mode=block", "--who=Windstorm Repo Minions",
                "--why=a job is running; this holds only the computer's own idle sleep"]
INHIBIT_SLEEP_DELAY = ["systemd-inhibit", "--what=sleep", "--mode=delay", "--who=Windstorm Repo Minions",
                       "--why=a job is running; a sleep you ask for still happens, a few seconds later"]
FORBIDDEN_INHIBIT = ("handle-lid-switch", "handle-suspend-key", "handle-hibernate-key", "handle-power-key",
                     "handle-reboot-key", "shutdown")


def inhibitor_argv() -> list[str]:
    """The one command the job-started hook runs to hold the two locks, ending by itself after JOB_HOURS_MOST."""
    return INHIBIT_IDLE + INHIBIT_SLEEP_DELAY + ["sleep", str(JOB_HOURS_MOST * 3600)]


def sh_quote(argv: list[str]) -> str:
    return " ".join("'" + a.replace("'", "'\\''") + "'" for a in argv)


def hook_dir(repo: str) -> str:
    owner, name = repo.split("/", 1)
    return f"{HOOKS_ROOT}/{owner}-{name}"


def records_dir_of(folder: str) -> str:
    return f"{RECORDS_ROOT}/{os.path.basename(folder.rstrip('/'))}"


def record_line(kind: str, records: str) -> str:
    """G5.7, G5.14: one line per job start or end: the time, the run, its attempt, the slice's kill count, the job."""
    return (f'REC={sh_quote([records + "/jobs.log"])}\n'
            f'oom="$(awk \'/^oom_kill /{{print $2}}\' {sh_quote([MEMORY_EVENTS])} 2>/dev/null)"\n'
            f'[ -w "$(dirname "$REC")" ] && printf \'{kind} %s %s %s %s %s\\n\' "$(date -u +%s)" '
            '"${GITHUB_RUN_ID:-?}" "${GITHUB_RUN_ATTEMPT:-?}" "${oom:-?}" "${GITHUB_JOB:-?}" >> "$REC"\n')


# G5.15: read in the job-start hook. "public", "private", or "none" when the event's record names no repository.
EVENT_PY = ('import json,os\n'
            'try: r = json.load(open(os.environ["GITHUB_EVENT_PATH"])).get("repository") or dict()\n'
            'except Exception: r = dict()\n'
            'print("public" if r.get("private") is False or r.get("visibility") == "public" else '
            '"private" if r.get("private") is True else "none")')


def seen_path(folder: str) -> str:
    return f"{SEEN_ROOT}/{os.path.basename(folder.rstrip('/'))}"


def path_line(path: str | None) -> str:
    return f"export PATH={sh_quote([path])}\n" if path else ""


def job_started_script(folder: str, records: str | None = None, path: str | None = None, seen: str | None = None,
                       every_s: int = 300, hold_wait_s: int = HOLD_WAIT_S) -> str:
    """G4.4, G5.15: written by setup, owned by root, read-only to the runner user; runs as the runner user at each
    job's start, before any step of the job."""
    fresh_s = SEEN_PASSES * every_s
    recs = records or records_dir_of(folder)
    return f"""#!/bin/bash
# Windstorm Repo Minions, job started (G4.4, G4.9, G5.7, G5.15). Written by setup; owned by root.
# It refuses the job unless the watch read this repository private in the last {fresh_s // 60} minutes. Then it holds
# the computer's own idle sleep off and delays (never refuses) a sleep you ask for, while this job runs.
{path_line(path)}FOLDER={sh_quote([folder])}
STATE="$FOLDER/_minions"
SEEN={sh_quote([seen or seen_path(folder)])}
mkdir -p "$STATE" && chmod 700 "$STATE"
{record_line("start", recs)}seen_ok() {{
  v="$(cat "$SEEN" 2>/dev/null)"
  case "$v" in ''|*[!0-9]*) return 1 ;; esac
  now="$(date +%s)"
  [ "$v" -le $(( now + 60 )) ] && [ $(( now - v )) -le {fresh_s} ]  # a time in the future is not fresh
}}
# GitHub's own record of the event that started this job names the repository and whether it is private (push,
# pull_request and workflow_dispatch seen; a schedule's record has no repository). When the record cannot be read or
# names no repository, the watch's record alone decides: that is neither a refusal nor a pass by itself.
event="$(python3 -I -c '{EVENT_PY}' 2>/dev/null)"
if [ "$event" = public ]; then
{record_line("refused-public", recs)}  echo "::error::Windstorm Repo Minions refused this job before any of its steps: GitHub's own record of this event says the repository is not private, and this computer takes no job of a repository that is not private. On that computer, \\`status\\` says what to do."
  exit 1
fi
if [ "$(cat "$SEEN" 2>/dev/null)" != public ] && ! seen_ok; then
  echo "minions: the watch has not read this repository private in the last {fresh_s // 60} minutes; waiting up to {hold_wait_s // 60} minutes for it to"
  for _ in $(seq {hold_wait_s // 10}); do sleep 10; seen_ok && break; [ "$(cat "$SEEN" 2>/dev/null)" = public ] && break; done
fi
if [ "$(cat "$SEEN" 2>/dev/null)" = public ]; then
{record_line("refused-public", recs)}  echo "::error::Windstorm Repo Minions refused this job before any of its steps: the repository is no longer private, and this computer takes no job of a repository that is not private. On that computer, \\`status\\` says what to do."
  exit 1
fi
if ! seen_ok; then
{record_line("refused-stale", recs)}  echo "::error::Windstorm Repo Minions refused this job before any of its steps: the watch on that computer has not read the repository private in the last {fresh_s // 60} minutes, so it may not be private now. On that computer, \\`status\\` says why and what to do."
  exit 1
fi
# R89: a hold left by an earlier job whose end hook never ran (its service stopped mid-job) ends here first.
if [ -f "$STATE/inhibit.pid" ]; then
  old="$(cat "$STATE/inhibit.pid")"
  case "$old" in ''|*[!0-9]*) ;; *) kill -TERM -- "-$old" 2>/dev/null || kill -TERM "$old" 2>/dev/null ;; esac
  rm -f "$STATE/inhibit.pid"
fi
if command -v systemd-inhibit >/dev/null 2>&1; then
  env -u RUNNER_TRACKING_ID setsid {sh_quote(inhibitor_argv())} </dev/null >/dev/null 2>&1 &
  pid="$!"
  echo "$pid" > "$STATE/inhibit.pid"
  # Each systemd-inhibit takes its lock before it starts what it runs: once the last one's `sleep` runs, both
  # locks are held. Wait for that (5 s at most), so the job's first step already runs under them.
  for _ in $(seq 50); do pgrep -g "$pid" -x sleep >/dev/null 2>&1 && break; sleep 0.1; done
  if pgrep -g "$pid" -x sleep >/dev/null 2>&1; then
    echo "minions: this computer will not fall asleep by itself during this job (a sleep you ask for still happens)"
  else
    echo "minions: the locks against sleep were not seen taken; this computer may fall asleep by itself during this job"
  fi
else
  echo "minions: systemd-inhibit is missing, so this computer may fall asleep by itself during this job"
fi
exit 0
"""


def job_completed_script(folder: str, records: str | None = None, path: str | None = None) -> str:
    """G4.3 and G4.4: ends the locks, empties this job's work folder, and marks a disk under the floor."""
    return f"""#!/bin/bash
# Windstorm Repo Minions, job completed (G4.3, G4.4, G5.7). Written by setup; owned by root.
{path_line(path)}FOLDER={sh_quote([folder])}
STATE="$FOLDER/_minions"
mkdir -p "$STATE" && chmod 700 "$STATE"
{record_line("end", records or records_dir_of(folder))}if [ -f "$STATE/inhibit.pid" ]; then
  pid="$(cat "$STATE/inhibit.pid")"
  case "$pid" in ''|*[!0-9]*) ;; *) kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null ;; esac
  rm -f "$STATE/inhibit.pid"
fi
ws="$(realpath -e -- "${{GITHUB_WORKSPACE:-/nonexistent}}" 2>/dev/null)"
case "$ws" in
  "$FOLDER/_work/"?*) find "$ws" -mindepth 1 -delete 2>/dev/null; echo "minions: this job's work folder was emptied" ;;
esac
[ -d "$FOLDER/_work/_temp" ] && find "$FOLDER/_work/_temp" -mindepth 1 -delete 2>/dev/null
# The actions a job fetched: downloaded before any hook runs, even for a job the start hook refuses (seen).
acts="$FOLDER/_work/_actions"
if [ -d "$acts" ] && [ -n "$(find "$acts" -mindepth 1 -print -quit 2>/dev/null)" ]; then
  find "$acts" -mindepth 1 -delete 2>/dev/null; echo "minions: the actions this job fetched were deleted"
fi
avail="$(df --output=avail -B1 "$FOLDER" 2>/dev/null | tail -n 1 | tr -d ' ')"
case "$avail" in
  ''|*[!0-9]*) ;;
  *) if [ "$avail" -lt {DISK_FLOOR} ]; then date -u +%Y-%m-%dT%H:%M:%SZ > "$STATE/disk-low"
       echo "minions: less than {DISK_FLOOR // 1024 ** 3} GB free for the work folders: run \\`drain\\` before the disk fills"
     else rm -f "$STATE/disk-low"; fi ;;
esac
exit 0
"""


def resolve_programs(host: Host, names) -> tuple[dict, list]:
    """G8.9: each program's full path, found with your PATH; and the ones not found."""
    found, missing = {}, []
    for n in names:
        p = host.which(n)
        if p:
            found[n] = os.path.abspath(p)
        else:
            missing.append(n)
    return found, missing


def path_for(found: dict) -> str:
    """G8.9: the folders of the resolved programs, in your PATH's order, so each name finds the same program again."""
    order = [os.path.abspath(x) for x in os.environ.get("PATH", "").split(os.pathsep) if x]
    dirs = sorted({os.path.dirname(p) for p in found.values()},
                  key=lambda d: (order.index(d) if d in order else len(order), d))
    return ":".join(dirs)


def install_hooks(host: Host, repo: str, folder: str, rep: Report) -> bool:
    """G4.3, G4.4: the two job hooks, root-owned and read-only to the runner; named in the runner's .env.
    G8.9: every program they run is resolved here, outside your home folder (the runner user cannot enter it)."""
    d = hook_dir(repo)
    found, missing = resolve_programs(host, HOOK_PROGRAMS)
    home = host.home().rstrip("/") + "/"
    inside = sorted(f"{n} ({p})" for n, p in found.items() if p.startswith(home))
    if inside or [m for m in missing if m != "systemd-inhibit"]:
        rep.stop("the job hooks need these programs where the runner's user can run them, outside your home folder: "
                 + ", ".join(inside + [f"{m} (not found)" for m in missing if m != "systemd-inhibit"])
                 + ". Install them for the whole computer, then run setup again")
        return False
    hook_path = path_for(found)
    journal(host, "write", f"{d}/job-started.sh, {d}/job-completed.sh and their lines in {folder}/.env")
    sudo(host, ["install", "-d", "-o", "root", "-g", "root", "-m", "0755", HOOKS_ROOT, d])
    # G5.7, G5.14: the jobs' records, written by the runner, read by you (nothing secret in them).
    rc, grp_ = host.run(["id", "-gn"])
    group = grp_.strip() if rc == 0 and grp_.strip() else host.user()
    sudo(host, ["install", "-d", "-o", "root", "-g", "root", "-m", "0755", RECORDS_ROOT])
    sudo(host, ["install", "-d", "-o", RUNNER_USER, "-g", group, "-m", "2750", records_dir_of(folder)])
    # G5.15: where the watch, as you, writes when it last read the repository private; the runner reads it.
    sudo(host, ["install", "-d", "-o", host.user(), "-g", group, "-m", "0755", SEEN_ROOT])
    every_s = int((load_state(host).get("watch") or {}).get("every_s") or WATCH_EVERY_S)
    for name, text in (("job-started.sh", job_started_script(folder, path=hook_path, every_s=every_s)),
                       ("job-completed.sh", job_completed_script(folder, path=hook_path))):
        rc, out = sudo(host, ["tee", f"{d}/{name}"], input=text)
        if rc != 0:
            rep.stop(f"could not write {d}/{name}: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
            return False
        sudo(host, ["chown", "root:root", f"{d}/{name}"])
        sudo(host, ["chmod", "0755", f"{d}/{name}"])
    rc, env = sudo(host, ["cat", f"{folder}/.env"])
    keep = [x for x in (env.splitlines() if rc == 0 else []) if not x.startswith("ACTIONS_RUNNER_HOOK_JOB_")]
    keep += [f"ACTIONS_RUNNER_HOOK_JOB_STARTED={d}/job-started.sh", f"ACTIONS_RUNNER_HOOK_JOB_COMPLETED={d}/job-completed.sh"]
    rc, out = as_runner(host, ["tee", f"{folder}/.env"], input="\n".join(keep) + "\n")
    if rc != 0:
        rep.stop(f"could not name the job hooks in {folder}/.env: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
        return False
    rc, out = sudo(host, ["stat", "-c", "%a %U", f"{d}/job-started.sh", f"{d}/job-completed.sh"])
    if rc != 0 or [x.strip() for x in out.splitlines() if x.strip()] != ["755 root", "755 root"]:
        rep.stop(f"the job hooks are not root's and read-only to the runner: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
        return False
    rep.ok("after each job its work folder is emptied, and during a job this computer does not fall asleep by "
           "itself; a sleep you ask for, or the lid, still puts it to sleep (job hooks, owned by root, read back)")
    return True


def check_disk_floor(free: int | None, rep: Report, what: str) -> bool:
    """G4.3: under DISK_FLOOR free, no runner is started or released to take jobs."""
    if free is None:
        rep.warn("free space for the work folders: not read")
        return True
    if free < DISK_FLOOR:
        rep.stop(f"only {gb(free)} free for the work folders, under the {gb(DISK_FLOOR)} floor: {what}. Free some "
                 "space first")
        return False
    return True


def runner_version(host: Host, folder: str, rep: Report) -> str | None:
    """G4.5: the runner's own self-update stays on (setup never passes --disableupdate); its version is read."""
    rc, out = as_runner(host, [f"{folder}/bin/Runner.Listener", "--version"])
    v = out.strip().splitlines()[-1].strip() if rc == 0 and out.strip() else None
    if v and re.fullmatch(r"\d+\.\d+\.\d+", v):
        rep.ok(f"runner version {v}; GitHub's own self-update stays on")
        return v
    rep.warn("could not read the runner's version; GitHub's own self-update stays on")
    return None


def read_runner(gh: GitHub, repo: str, name: str) -> tuple[int, dict | None]:
    """GitHub's answer and our runner in it. An error answer is not "not listed": GitHub answered errors while it
    still listed our runner (HTTP 502, seen), and an uninstall that took that for gone left a runner on GitHub."""
    s, d = gh.api("GET", f"repos/{repo}/actions/runners?per_page=100")
    mine = [r for r in ((d or {}).get("runners", []) if s == 200 else []) if r.get("name") == name]
    return s, (mine[0] if mine else None)


def listed_runner(gh: GitHub, repo: str, name: str) -> dict | None:
    """Our runner as GitHub lists it; None when not listed OR not read. Where gone must mean gone: read_runner."""
    return read_runner(gh, repo, name)[1]


def runner_pids(host: Host, folder: str, part: str = "Runner.") -> list[int]:
    """Processes of THIS runner (its own folder's programs), by exact PID. Read only; never a broad kill."""
    rc, out = host.run(["pgrep", "-u", RUNNER_USER, "-f", f"{folder}/bin/{part}"])
    return [int(x) for x in out.split() if x.isdigit()] if rc == 0 else []


def set_labels(gh: GitHub, repo: str, runner_id, labels: list[str]) -> list[str] | None:
    s, d = gh.api("PUT", f"repos/{repo}/actions/runners/{runner_id}/labels", {"labels": labels})
    if s != 200:
        return None
    return sorted(x.get("name") for x in (d or {}).get("labels", []))


def stop_runner(host: Host, unit: str, folder: str, rep: Report, drained: bool) -> bool:
    """G4.6, G4.7: the one way the tool stops a runner. Only after a drain; SIGTERM, then read back that no process
    of this runner is left, before anything may start it again."""
    if not drained:
        rep.stop("the runner is stopped only after a drain (a stop cancels a running job within seconds): run "
                 "`drain <owner/repo>` first")
        return False
    journal(host, "stop", unit)
    sudo(host, systemctl("stop", unit))  # systemd sends SIGTERM to the runner's processes
    left = runner_pids(host, folder)
    for _ in range(10):
        if not left:
            break
        host.sleep(1)
        left = runner_pids(host, folder)
    for pid in left:  # still there: SIGTERM by exact PID, then read back again
        journal(host, "SIGTERM", str(pid))
        sudo(host, ["kill", "-TERM", str(pid)])
    if left:
        host.sleep(2)
        left = runner_pids(host, folder)
    rc, active = host.run(systemctl("is-active", unit))
    if left:
        rep.stop(f"the runner did not stop: process(es) {', '.join(map(str, left))} are left; nothing will start "
                 "it again until they are gone")
        return False
    rep.ok(f"the runner is stopped: the service is {active.strip() or 'not read'}, and no process of it is left")
    return True


def start_runner(host: Host, unit: str, folder: str, rep: Report) -> bool:
    """G4.7: started only when no process of this runner is left, so two copies never run."""
    rc, active = host.run(systemctl("is-active", unit))
    if active.strip() == "active":
        return True
    left = runner_pids(host, folder)
    if left:
        rep.stop(f"process(es) {', '.join(map(str, left))} of this runner are still there while its service is "
                 "not: it is not started again, so two copies never run")
        return False
    journal(host, "start", unit)
    rc, out = sudo(host, systemctl("start", unit))
    if rc != 0:
        rep.stop(f"the runner's service did not start: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
        return False
    rep.ok("the runner's service is started again")
    return True


def hold_runner(gh: GitHub, host: Host, repo: str, data: dict, rep: Report, why: str = "drain") -> dict | None:
    """The hold (Part 7 C3): our runner carries only `minions-held`, so no new job lands on it. Labels kept to lift it."""
    mine = data["runners"][repo]
    s, r = read_runner(gh, repo, mine["name"])
    if s != 200:
        rep.stop(f"GitHub could not be read for {repo}'s runners (HTTP {s}): nothing was held, nothing else was changed. Run the same command again; if it stops here again, GitHub's answer named here is the reason")
        return None
    if r is None:
        rep.stop(f"GitHub does not list our runner {mine['name']} for {repo}: nothing to hold. `status {repo}` "
                 "says what GitHub and this computer read now")
        return None
    labels = sorted(x.get("name") for x in r.get("labels", []))
    if labels != [HELD_LABEL]:
        journal(host, "hold", f"{repo} {mine['name']} for {why} (labels were {','.join(labels)})")
        mine["labels_before_hold"] = labels
        mine["held_for"] = why
        mine["held_at"] = host.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(host, data)
        if set_labels(gh, repo, r["id"], [HELD_LABEL]) != [HELD_LABEL]:
            # GitHub can answer an error and still take the change (HTTP 502, seen): what it lists decides.
            back = listed_runner(gh, repo, mine["name"])
            if back is None or sorted(x.get("name") for x in back.get("labels", [])) != [HELD_LABEL]:
                rep.stop("GitHub did not take the hold label: nothing else was changed. Run the same command again; if it stops here again, GitHub's answer named here is the reason")
                return None
            journal(host, "hold read back", f"{repo} {mine['name']}: GitHub answered an error, and lists it held")
    rep.ok(f"held: {mine['name']} now carries only `{HELD_LABEL}`, so no new job lands on it; jobs wait on GitHub")
    return r


def wait_idle(gh: GitHub, host: Host, repo: str, mine: dict, wait_s: int, poll_s: float, rep: Report,
              next_cmd: str) -> str:
    """G5.2, G5.6: wait until both sides say idle, with a deadline said in the line (G8.6). Returns "idle",
    "offline" (no job process here and GitHub says offline) or "running" (the deadline passed: a `waiting` line)."""
    deadline = host.now() + dt.timedelta(seconds=wait_s)
    said = False
    while True:
        r = listed_runner(gh, repo, mine["name"]) or {}
        if gh.limited():  # G8.14: never poll into a spent limit
            rep.wait(f"for GitHub: {gh.why}. Held, nothing stopped. Next: `{next_cmd}` again after that time")
            return "running"
        busy = bool(r.get("busy"))
        worker = runner_pids(host, mine["folder"], "Runner.Worker")
        if not busy and not worker:
            return "idle" if r.get("status") == "online" else "offline"
        if host.now() >= deadline:
            side = ", ".join(x for x in (("GitHub says busy" if busy else ""),
                                         (f"a job process runs here ({worker[0]})" if worker else "")) if x)
            rep.wait(f"for the running job to end ({side}); held, nothing stopped. Next: `{next_cmd}` again")
            return "running"
        if not said:
            rep.ok(f"a job is running ({'GitHub says busy' if busy else 'a job process runs here'}): waiting for it "
                   f"to end, until {deadline.strftime('%H:%MZ')} at the latest")
            said = True
        host.sleep(poll_s)


def drain(repo: str | None, stop: bool = False, wait_s: int = DRAIN_WAIT_S, poll_s: float = 10,
          host: Host | None = None, gh: GitHub | None = None, out=None, restart: bool = False,
          interactive: bool | None = None) -> int:
    """G4.6, G5.6: hold by label, then wait until both sides say idle, with a deadline (G8.6); `--stop` stops the
    service only after that. G5.11: `--restart` does it for every runner here and says when a restart is safe."""
    host = host or Host()
    gh = gh or GitHub(host)
    rep = Report(out)
    data = load_state(host)
    runners = data.get("runners") or {}
    if restart:
        repos = sorted(runners)
        if repo:
            repos = [repo] if repo in runners else []
    else:
        repos = [repo] if repo in runners else []
    if not repos:
        rep.stop(f"no runner of ours serves {repo} on this computer: `setup {repo}` sets one up" if repo else
                 "no runner of ours on this computer: `look <owner/repo>`, then `setup <owner/repo>`, sets one up")
        return rep.code()
    states = {}
    for rp in repos:
        if hold_runner(gh, host, rp, data, rep, "a restart" if restart else "drain") is None:
            return rep.code()
    for rp in repos:
        mine = runners[rp]
        nxt = "drain --restart" if restart else f"drain {rp}"
        states[rp] = wait_idle(gh, host, rp, mine, wait_s, poll_s, rep, nxt)
        if states[rp] == "running":
            return rep.code()
        if states[rp] == "idle":
            rep.ok(f"drained: online and idle: {mine['name']} is held, GitHub says online and not busy, and no job "
                   "process runs here")
        else:
            rep.warn(f"drained: no job process runs here, but GitHub says {mine['name']} is offline, so nothing can "
                     "be running on it")
    if restart:
        data["restart_hold"] = {"time": host.now().strftime("%Y-%m-%dT%H:%M:%SZ"), "boot_id": boot_id(host),
                                "repos": repos}
        save_state(host, data)
        journal(host, "restart hold", ",".join(repos))
        rep.ok("online and idle: safe to restart now. The hold lifts by itself once this computer has restarted and "
               "GitHub reads the runner online again (the watch does it)")
        return rep.code()
    if stop:
        # R73: stopping the service is an admin step: the password is asked here, once, never assumed.
        if not ask_admin_once(host, rep, host.isatty() if interactive is None else interactive,
                              f"`drain {repo} --stop`"):
            return rep.code()
        if not stop_runner(host, runners[repo]["unit"], runners[repo]["folder"], rep, drained=True):
            return rep.code()
    rep.ok(f"`resume {repo}` lets jobs land here again")
    return rep.code()


def resume(repo: str, host: Host | None = None, gh: GitHub | None = None, out=None,
           private_again: bool = False, on_battery: str | None = None, interactive: bool | None = None) -> int:
    """G4.6's other half: lift the hold (and start the service if a drain stopped it), never under the disk floor.
    G5.4: a runner stopped because its repository was no longer private resumes only once the repository is
    private again AND the person says so (`--private-again`)."""
    host = host or Host()
    gh = gh or GitHub(host)
    rep = Report(out)
    asked: list[bool] = []

    def admin() -> bool:
        """R73: starting or enabling the service is an admin step: the password is asked here, once, never assumed."""
        if not asked:
            if not ask_admin_once(host, rep, host.isatty() if interactive is None else interactive, f"`resume {repo}`"):
                return False
            asked.append(True)
        return True
    data = load_state(host)
    mine = (data.get("runners") or {}).get(repo)
    if not mine:
        rep.stop(f"no runner of ours serves {repo} on this computer: `setup {repo}` sets one up")
        return rep.code()
    if mine.get("public_stop"):
        if not private_again:
            rep.stop(f"{repo} was not private at {mine['public_stop']['time']}, so this computer refuses its jobs. "
                     f"Once you have made it private again: `resume {repo} --private-again`")
            return rep.code()
        s, d = gh.api("GET", f"repos/{repo}")
        if s != 200 or not (d or {}).get("private"):
            rep.stop(f"GitHub does not say {repo} is private (HTTP {s}): this computer keeps refusing its jobs. "
                     "Make it private on GitHub, then run the same command again")
            return rep.code()
        rep.ok(f"GitHub says {repo} is private again, and you said so")
        write_seen(host, mine, str(int(host.now().timestamp())))  # G5.15: just read private
        if mine["public_stop"].get("removed"):  # G5.4: coming back is registering again, the person's step
            mine.pop("public_stop", None)
            mine["gone_on_github"] = stamp(host.now())
            save_state(host, data)
            rep.need(f"run `setup {repo}`: it registers the runner again in place, with its folders and labels "
                     "(it asks for this computer's admin password once; this step is yours)")
            return rep.code()
        if not admin():
            return rep.code()
        journal(host, "enable", mine["unit"])
        sudo(host, systemctl("enable", mine["unit"]))
    if mine.get("gone_on_github"):
        rep.stop(f"GitHub no longer lists our runner {mine['name']}: run `setup {repo}` again to register it")
        return rep.code()
    free = host.disk_free(mine["folder"]) or host.disk_free(host.home())
    if not check_disk_floor(free[0] if free else None, rep, "the runner stays held"):
        return rep.code()
    if on_battery is not None:  # G11.2: the person's word, by name, kept until they change it
        mine["jobs_on_battery"] = on_battery == "too"
        save_state(host, data)
        rep.ok("jobs land here on battery too, by your word; `resume " + repo + " --on-battery-held` undoes it"
               if on_battery == "too" else "on battery the runner is held again, the default")
    if power_source(host) == "battery" and not mine.get("jobs_on_battery"):
        rep.stop(f"this computer runs on battery, so the runner stays held (the default for a laptop): plug it in "
                 f"and the hold lifts by itself, or `resume {repo} --on-battery-too` to take jobs on battery")
        return rep.code()
    if host.run(systemctl("is-active", mine["unit"]))[1].strip() != "active" and not admin():
        return rep.code()
    if not start_runner(host, mine["unit"], mine["folder"], rep):
        return rep.code()
    if not lift_hold(gh, host, repo, data, rep):
        return rep.code()
    mine.pop("public_stop", None)
    save_state(host, data)
    return rep.code()


def lift_hold(gh: GitHub, host: Host, repo: str, data: dict, rep: Report, tries: int = 9) -> bool:
    """Our labels back, once GitHub lists the runner online."""
    mine = data["runners"][repo]
    want = sorted(mine.get("labels_before_hold") or mine.get("labels") or [])
    r = None
    for i in range(tries):
        r = listed_runner(gh, repo, mine["name"])
        if r and r.get("status") == "online":
            break
        if i == 0 and tries > 1:  # G8.6: the deadline is said once the command really waits
            rep.ok(f"waiting for GitHub to read {mine['name']} online, until "
                   f"{(host.now() + dt.timedelta(seconds=5 * (tries - 1))).strftime('%H:%M:%SZ')} at the latest")
        if i < tries - 1:
            host.sleep(5)
    if not r:
        rep.stop(f"GitHub does not list our runner {mine['name']} for {repo}: `status {repo}` says why and what to do")
        return False
    if r.get("status") != "online":
        rep.wait(f"for GitHub to read {mine['name']} online before the hold is lifted. Next: `resume {repo}` again")
        return False
    journal(host, "resume", f"{repo} {mine['name']} labels {','.join(want)}")
    if set_labels(gh, repo, r["id"], want) != want:
        rep.stop("GitHub did not take our labels back: the runner is still held. Run the same command again; if it stops here again, GitHub's answer named here is the reason")
        return False
    for k in ("labels_before_hold", "held_for", "held_at"):
        mine.pop(k, None)
    save_state(host, data)
    rep.ok(f"resumed: {mine['name']} carries {', '.join(want)} again, so jobs land here")
    return True


# ------------------------------------------------------------------------------------------------ G5 watch and hold

WATCH_EVERY_S = 300  # G5.3: the watch's default interval
WATCH_UNIT = "minions-watch"
TOOL_COPY = f"{HOOKS_ROOT}/minions.py"  # the copy the watch runs: root's, so neither the runner nor a job can change it
WAIT_WARN_S = 1800  # G5.13: a job waiting longer than this behind our hold is a `warn`, first
GITHUB_GIVES_UP_H = 24  # GitHub cancels a job that waited this long for a runner


def stamp(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def hms(t: dt.datetime) -> str:
    return t.strftime("%H:%M:%SZ")


def parse_stamp(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.strptime(s.replace(".000", ""), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def minutes(seconds: float) -> str:
    m = int(seconds // 60)
    return f"{m // 60} h {m % 60} min" if m >= 60 else f"{m} min"


def boot_id(host: Host) -> str:
    return (host.read("/proc/sys/kernel/random/boot_id") or "").strip()


def survey_words(data: dict, repo: str) -> str:
    """G5.1: the minion's OS and architecture from its own survey, never from GitHub's list (`unknown` there)."""
    s = ((data.get("looks") or {}).get(repo) or {}).get("survey") or {}
    os_ = s.get("os") or s.get("system") or "OS not surveyed"
    return f"{os_} {s.get('machine') or ''}".strip() + " (from this computer's own survey)"


def computer_side(host: Host, mine: dict) -> tuple[str, list[int], dt.datetime]:
    rc, active = host.run(systemctl("is-active", mine["unit"]))
    return active.strip() or "not read", runner_pids(host, mine["folder"], "Runner.Worker"), host.now()


def both_sides_words(r: dict | None, gh_at: dt.datetime, active: str, worker: list[int], at: dt.datetime) -> tuple[str, str]:
    """G5.1, G5.2: one line, both sides with the time each was read; "idle" only when both agree."""
    if r is None:
        gh_side = f"GitHub does not list it (read {hms(gh_at)})"
    else:
        gh_side = f"GitHub says {r.get('status', 'not read')}, {'busy' if r.get('busy') else 'not busy'} (read {hms(gh_at)})"
    here = f"this computer: service {active}, {'a job process runs (' + str(worker[0]) + ')' if worker else 'no job process'} (read {hms(at)})"
    if r is not None and r.get("status") == "online" and not r.get("busy") and not worker and active == "active":
        word = "idle"
    elif r is not None and (r.get("busy") or worker) and active == "active":
        # R84: GitHub showing the runner offline while a job runs here is a disagreement, never plain "busy"
        word = ("busy" if (r.get("busy") and worker and r.get("status") == "online")
                else "the two sides disagree")
    else:
        word = "not ready"
    return word, f"{gh_side}; {here}"


def queued_jobs(gh: GitHub, repo: str) -> list[dict]:
    """The repository's jobs waiting for a runner, with the labels they ask for and when they were made."""
    s, d = gh.api("GET", f"repos/{repo}/actions/runs?status=queued&per_page=50")
    out = []
    for run in ((d or {}).get("workflow_runs", []) if s == 200 else []):
        s2, d2 = gh.api("GET", f"repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100")
        for j in ((d2 or {}).get("jobs", []) if s2 == 200 else []):
            if j.get("status") == "queued":
                out.append({"id": j.get("id"), "name": j.get("name"), "workflow": run.get("name") or run.get("path"),
                            "labels": j.get("labels") or [], "created": parse_stamp(j.get("created_at"))})
    return out


def watch_heartbeat(data: dict, now: dt.datetime, slept_now: float | None = None) -> tuple[bool, str]:
    """G5.3: a heartbeat older than three intervals of waking time is "the watch is not running". R83: the watch's
    timer counts only waking time, so time this computer slept since the last pass is not counted against it."""
    w = data.get("watch") or {}
    every = int(w.get("every_s") or WATCH_EVERY_S)
    last = parse_stamp(w.get("time"))
    if last is None:
        return False, "the watch is not running: it has never written a heartbeat"
    age = (now - last).total_seconds()
    slept_then = w.get("slept_s")
    slept = (max(0.0, slept_now - slept_then)
             if isinstance(slept_now, (int, float)) and isinstance(slept_then, (int, float)) else 0.0)
    if age > 3 * every and age - slept <= 3 * every:
        return True, (f"the watch ran {minutes(age)} ago, at {hms(last)}; this computer slept {minutes(slept)} since, "
                      f"and the watch's timer counts only waking time: its next pass comes within {minutes(every)} "
                      "of waking time")
    if age > 3 * every:
        return False, (f"the watch is not running: its last heartbeat was {minutes(age)} ago, at {hms(last)} "
                       f"(it runs every {minutes(every)})")
    return True, f"the watch ran {minutes(age)} ago, at {hms(last)} (every {minutes(every)})"


def status(repo: str | None = None, host: Host | None = None, gh: GitHub | None = None, out=None,
           wait_warn_s: int = WAIT_WARN_S, rerun: int | None = None) -> int:
    """G5.1 to G5.4, G5.12, G5.13 (and G5.7, G5.14 through name_cut_jobs): read only, unless `--rerun JOB`."""
    host = host or Host()
    gh = gh or GitHub(host)
    rep = Report(out)
    data = load_state(host)
    runners = data.get("runners") or {}
    repos = [repo] if repo else sorted(runners)
    if not repos or any(r not in runners for r in repos):
        rep.stop(f"no runner of ours serves {repo} on this computer: `setup {repo}` sets one up" if repo else
                 "no runner of ours on this computer: `look <owner/repo>`, then `setup <owner/repo>`, sets one up")
        return rep.code()
    first, rest = [], []  # G5.4's why, then G5.13's warns, come first
    now = host.now()
    for rp in repos:
        mine = runners[rp]
        ps = mine.get("public_stop")
        if ps:
            first.append(("stop", public_words(rp, public_now(gh, host, rp, mine)) + ". Anyone's fork can ask a "
                                  "public repository's runner for a job"))
        s_r, r = read_runner(gh, rp, mine["name"])
        gh_at = host.now()
        active, worker, at = computer_side(host, mine)
        if s_r != 200:
            first.append(("warn", f"GitHub could not be read for {rp}'s runners now (HTTP {s_r}): what this says "
                                  "of GitHub's side is not read; `status` again later"))
        elif r is None:
            if mine.get("gone_on_github") or active in ("active", "activating"):
                first.append(("warn", gone_words(rp, mine)))
        word, sides = both_sides_words(r, gh_at, active, worker, at)
        held = r is not None and sorted(x.get("name") for x in r.get("labels", [])) == [HELD_LABEL]
        hold_words = ""
        if held:
            why = mine.get("held_for") or "a hold"
            hold_words = f"; held for {why} since {mine.get('held_at', 'a time not written')}"
        # The first word is the worst true thing (P2, 2026-11-23: `ok ...: not ready` while a job waited).
        rest.append(("ok" if word in ("idle", "busy") and not held else "warn",
                     f"{mine['name']} for {rp}: {word}. {sides}; {survey_words(data, rp)}{hold_words}"))
        # G11.4 (R74): GitHub has shown it offline for more than two watch intervals while its service runs here.
        # R86: the watch reads GitHub once a pass, so the line says what was read (each pass, and now), not that
        # GitHub showed it offline the whole span: between passes it may have been online.
        since = parse_stamp(mine.get("offline_since"))
        every = int((data.get("watch") or {}).get("every_s") or WATCH_EVERY_S)
        # R85: counted in waking time, as the watch's own timer is: a computer that slept was not offline meanwhile
        slept_then, slept_now = mine.get("offline_slept_s"), host.slept_s()
        asleep = (max(0.0, slept_now - slept_then)
                  if isinstance(slept_now, (int, float)) and isinstance(slept_then, (int, float)) else 0.0)
        awake = (now - since).total_seconds() - asleep if since else 0.0
        if (since and r is not None and r.get("status") != "online" and active == "active" and not worker
                and awake > 2 * every):
            span = minutes((now - since).total_seconds()) + (f", {minutes(awake)} of it awake" if asleep else "")
            first.append(("warn", f"{mine['name']} for {rp}: GitHub has shown it offline at every watch pass "
                                  f"since {hms(since)} ({span}), and now, while its service runs here, so no job "
                                  "lands on it. A runner usually comes back by itself within a few minutes of a wake "
                                  f"or of the network returning; this one has not. `drain {rp} --stop`, then "
                                  f"`resume {rp}`, starts it fresh (each asks for the admin password once)"))
        # R87: a job held at our memory limit, found so by the watch HELD_PASSES passes in a row, while it still runs.
        if (mine.get("memory_held") or {}).get("passes", 0) >= HELD_PASSES and worker:
            first.append(("warn", memory_held_words(rp, mine, read_records(host, mine["folder"]))))
        # The queue (G5.1) and the jobs waiting behind our hold (G5.13).
        q = [j for j in queued_jobs(gh, rp) if set(j["labels"]) & set(mine.get("labels") or ["minions"])]
        blocked_by = ("our hold (" + (mine.get("held_for") or "a hold") + ")" if held else
                      "our runner being offline or asleep" if r is None or r.get("status") != "online" else None)
        summary = []
        if q:
            oldest = min((j["created"] for j in q if j["created"]), default=None)
            line = (f"queue for {rp}: {len(q)} job(s) waiting for our runner, the oldest for "
                    f"{minutes((now - oldest).total_seconds()) if oldest else 'a time not read'}")
            if blocked_by:  # never `ok` first while a job waits on a runner that cannot take it
                summary.append(("warn", f"{line}, behind {blocked_by}. "
                                      + (f"`resume {rp}` lets them land here" if held and mine.get("held_for") in
                                         (None, "drain") else
                                         "The hold lifts by itself (see the line below)" if held else
                                         f"Start this computer's runner: `resume {rp}` (it asks for the admin "
                                         f"password once; `setup {rp}` again if it does not come back)")))
            else:
                rest.append(("ok", line))
        else:
            rest.append(("ok", f"queue for {rp}: no job waiting for our runner"))
        if blocked_by:
            for j in q:  # G5.13's per-job warnings first, then the queue's summary
                age = (now - j["created"]).total_seconds() if j["created"] else 0
                if age > wait_warn_s:
                    first.append(("warn", f"job '{j['name']}' ({j['workflow']}) of {rp} has waited {minutes(age)} behind "
                                          f"{blocked_by}; GitHub cancels a job that waits {GITHUB_GIVES_UP_H} hours. "
                                          + (f"`resume {rp}` lets it land here" if held else
                                             f"Start this computer's runner (`resume {rp}`), or `drain {rp}` and "
                                             "move the job back")))
        first.extend(summary)
        rest.extend(name_cut_jobs(gh, host, rp, mine, data, rerun))
    ok_, words = watch_heartbeat(data, now, host.slept_s())
    rest.append(("ok" if ok_ else "warn", words))
    w = data.get("watch") or {}
    every = int(w.get("every_s") or WATCH_EVERY_S)
    for rp in repos:  # G5.15: a blind watch is a stop line, first, never "ok"
        b = (w.get("blind") or {}).get(rp)
        if b:
            first.insert(0, ("stop", blind_words(rp, b, every)))
    if not ok_:
        first.insert(0, ("stop", "the watch is not running, so this computer takes no job now: each job is refused before its first step until "
                                 "the watch reads the repository private again. Your way out: `sudo systemctl start "
                                 f"{WATCH_UNIT}.timer`, or `setup` again"))
    cc = data.get("cost_change")
    if cc and not cc.get("told"):  # G5.10: told once, at the next status, first
        first.insert(0, ("warn", cost_change_words(cc)))
        fresh = load_state(host)  # never write back an older copy of what other steps saved meanwhile
        if fresh.get("cost_change"):
            fresh["cost_change"]["told"] = stamp(now)
            save_state(host, fresh)
    rh = data.get("restart_hold")
    if rh:
        rest.append(("ok", f"held for a restart since {rh['time']}: the hold lifts by itself once this computer has "
                           "restarted and GitHub reads the runner online"))
    for kind, text in first + rest:
        {"ok": rep.ok, "warn": rep.warn, "stop": rep.stop, "need": rep.need}[kind](text)
    return rep.code()


def gone_words(repo: str, mine: dict) -> str:
    return (f"GitHub no longer lists our runner {mine['name']} for {repo}. GitHub removes a runner it has not seen "
            "connect for more than 14 days (a computer away or switched off that long), and the watch takes it off a "
            "repository that is no longer private. GitHub hands it no job now. Run "
            f"`setup {repo}` again: it stops what is left of the runner here, then registers it again with your login, keeping its "
            "folders and labels")


CANCELED = "The operation was canceled."
RECORDS_KEPT = 30  # the last jobs read back by `status`
MATCH_S = 300  # a record and GitHub's job are the same job when they started this close together


def read_records(host: Host, folder: str) -> list[dict]:
    """The job hooks' lines (G5.7, G5.14): each start, with its end if the computer saw one."""
    jobs: list[dict] = []
    for line in (host.read(records_dir_of(folder) + "/jobs.log") or "").splitlines()[-4 * RECORDS_KEPT:]:
        parts = line.split(" ", 5)
        if len(parts) < 6 or parts[0] not in ("start", "end", "refused-public", "refused-stale") or \
                not parts[1].isdigit():
            continue
        kind, t, run, attempt, oom, name = parts
        rec = {"t": int(t), "run": run, "attempt": attempt, "oom": int(oom) if oom.isdigit() else None, "job": name}
        if kind == "start":
            jobs.append({"start": rec, "end": None})
        else:
            if kind.startswith("refused-"):
                rec["refused"] = kind[len("refused-"):]  # G5.15: the hook refused it (the end hook still runs after)
            for j in reversed(jobs):
                if j["end"] is None and (j["start"]["run"], j["start"]["attempt"]) == (run, attempt):
                    j["end"] = rec
                    break
    return jobs[-RECORDS_KEPT:]


def unix_of(value: str) -> int | None:
    m = re.search(r"@(\d+)", value or "")
    return int(m.group(1)) if m else None


# systemd-sleep's line when the computer goes to sleep: "Entering sleep state 'suspend'..." in older systemd,
# "Performing sleep operation 'suspend'..." in systemd 255 (seen on a stock Ubuntu 24.04, 2026-11-23).
SLEEP_ENTERED = re.compile(r"Entering sleep state|Performing sleep operation")


def computer_record(host: Host, unit: str) -> dict:
    """What this computer itself knows: when the runner's service last started and stopped, when it booted, when it
    slept, and the slice's kill count now."""
    rc, out = host.run(systemctl("show", unit, "-p", "ActiveEnterTimestamp", "-p", "InactiveEnterTimestamp",
                                 "--timestamp=unix"))
    got = dict(x.split("=", 1) for x in out.splitlines() if "=" in x) if rc == 0 else {}
    m = re.search(r"^btime (\d+)$", host.read("/proc/stat") or "", re.M)
    rc, out = host.run(journalctl("-u", "systemd-suspend.service", "-o", "short-unix", "--since", "-3d"))
    sleeps = [int(float(x.split()[0])) for x in (out.splitlines() if rc == 0 else [])
              if re.search(SLEEP_ENTERED, x) and re.match(r"^\d+(\.\d+)?\s", x)]
    oom = re.search(r"^oom_kill (\d+)$", host.read(MEMORY_EVENTS) or "", re.M)
    # systemd keeps only the LAST start and stop times (seen: a reboot replaced the record of an earlier stop), so
    # every stop of the runner's unit is also read from its journal.
    rc, out = host.run(journalctl("-u", unit, "-o", "short-unix", "--since", "-3d"))
    stops = [int(float(x.split()[0])) for x in (out.splitlines() if rc == 0 else [])
             if re.match(r"^\d+(\.\d+)?\s", x) and re.search(r"Stopping |Stopped |Failed with result|Deactivated", x)]
    for k in ("ActiveEnterTimestamp", "InactiveEnterTimestamp"):
        if unix_of(got.get(k, "")):
            stops.append(unix_of(got[k]))
    return {"started": unix_of(got.get("ActiveEnterTimestamp", "")), "stopped": unix_of(got.get("InactiveEnterTimestamp", "")),
            "stops": sorted(set(stops)), "boot": int(m.group(1)) if m else None, "sleeps": sleeps,
            "oom": int(oom.group(1)) if oom else None}


def github_job(gh: GitHub, repo: str, rec: dict, runner_name: str) -> dict | None:
    if not rec["run"].isdigit():
        return None
    s, d = gh.api("GET", f"repos/{repo}/actions/runs/{rec['run']}/jobs?filter=all&per_page=100")
    best = None
    for j in ((d or {}).get("jobs", []) if s == 200 else []):
        st = parse_stamp(j.get("started_at"))
        if j.get("runner_name") != runner_name or st is None:
            continue
        if rec["attempt"].isdigit() and j.get("run_attempt") not in (None, int(rec["attempt"])):
            continue
        gap = abs(st.timestamp() - rec["t"])
        if gap <= MATCH_S and (best is None or gap < best[0]):
            best = (gap, j)
    return best[1] if best else None


def judge_cut(job: dict, notes: list[str], start: dict, end: dict | None, until: int, here: dict,
              oom_after: int | None) -> tuple[str, str] | None:
    """G5.7, G5.14: a job is named only when BOTH sides agree. GitHub: the job failed. This computer: its memory
    limit killed something during the job (the slice's kill count rose), or the job had no end here and the service
    stopped or restarted, or the computer booted or slept, during it. A job that came through a sleep is not cut."""
    if job.get("conclusion") != "failure":
        return None
    if end is not None and end.get("refused"):  # G5.15: both sides: this computer's record, and GitHub's failure
        return "refused", ("GitHub's own record of the event, or the watch, saying the repository is not private"
                           if end["refused"] == "public" else
                           f"the watch not having read the repository private in the last {SEEN_PASSES * WATCH_EVERY_S // 60} minutes")
    t0 = start["t"]
    if start["oom"] is not None and oom_after is not None and oom_after > start["oom"]:
        return "killed", "this computer's memory limit"
    gh_cut = any(s.get("conclusion") == "cancelled" for s in job.get("steps") or []) or any(CANCELED in n for n in notes)
    if not gh_cut:
        return None
    if end is None:
        if here.get("boot") and t0 < here["boot"] <= until:
            return "cut", "a restart of this computer"
        if any(t0 < s <= until for s in here.get("stops") or []):
            return "cut", "a stop of this computer's runner service"
    window_end = end["t"] if end else until
    if any(t0 < s <= window_end for s in here.get("sleeps") or []):
        return "cut", "this computer going to sleep"
    return None


def name_cut_jobs(gh: GitHub, host: Host, repo: str, mine: dict, data: dict, rerun: int | None) -> list:
    """G5.7, G5.14: name the jobs a cut or our memory limit stopped, never as the person's test failing; offer each
    a run again, once, only on the person's word (`status --rerun JOB_ID`)."""
    lines = []
    recs = read_records(host, mine["folder"])
    here = computer_record(host, mine["unit"])
    now = int(host.now().timestamp())
    named = {}
    keys = {f"{j['start']['run']}/{j['start']['attempt']}/{j['start']['t']}" for j in recs}
    settled = {k: v for k, v in ((mine.get("settled_jobs") or {}).items()) if k in keys}
    fresh_settled = len(settled) != len(mine.get("settled_jobs") or {})
    for i, j in enumerate(recs):
        nxt = recs[i + 1]["start"] if i + 1 < len(recs) else None
        until = nxt["t"] if nxt else now
        if j["end"] is not None:
            oom_after = j["end"]["oom"]
        else:
            oom_after = nxt["oom"] if nxt else here.get("oom")
        if j["end"] is None and nxt is None and runner_pids(host, mine["folder"], "Runner.Worker"):
            continue  # still running
        key = f"{j['start']['run']}/{j['start']['attempt']}/{j['start']['t']}"
        done_ = settled.get(key)
        if done_ is not None:  # R90: a finished job is read from GitHub once, then judged from what was kept
            if not done_.get("verdict"):
                continue
            g, verdict, kept = {"id": done_["id"], "name": done_.get("name")}, tuple(done_["verdict"]), True
        else:
            g = github_job(gh, repo, j["start"], mine["name"])
            if g is None or g.get("status") not in (None, "completed") or g.get("conclusion") is None:
                continue  # not on GitHub yet, or not finished there: read again next time
            verdict = None
            if g.get("conclusion") == "failure":
                s, notes = gh.api("GET", f"repos/{repo}/check-runs/{g['id']}/annotations")
                msgs = [n.get("message") or "" for n in (notes if s == 200 and isinstance(notes, list) else [])]
                verdict = judge_cut(g, msgs, j["start"], j["end"], until, here, oom_after)
            kept = (data.get("named") or {}).get(str(g["id"]))
            if verdict is None and kept:
                verdict = (kept["kind"], kept["why"])  # named once, while this computer still had its record
            settled[key] = {"id": g["id"], "name": g.get("name"), "verdict": list(verdict) if verdict else None}
            fresh_settled = True
        if verdict is None and kept:
            verdict = (kept["kind"], kept["why"])  # named once, while this computer still had its record
        if verdict is None:
            continue
        if not kept:
            data.setdefault("named", {})[str(g["id"])] = {"kind": verdict[0], "why": verdict[1], "run": j["start"]["run"],
                                                         "time": stamp(host.now())}
            save_state(host, data)
            journal(host, "named", f"{repo} job {g['id']}: {verdict[0]} by {verdict[1]}")
        named[int(g["id"])] = g
        when = hms(dt.datetime.fromtimestamp(j["start"]["t"], dt.timezone.utc))
        done = int(g["id"]) in (data.get("reruns") or [])
        again = (" It was run again once already." if done else
                 f" To run it again, once: `status {repo} --rerun {g['id']}`.")
        if verdict[0] == "refused":
            lines.append(("warn", f"job '{g.get('name')}' (run {j['start']['run']}, started {when}) was refused before "
                                  f"its first step by this computer's guard: {verdict[1]}. No step of it ran here; "
                                  f"GitHub shows it as failed, not a failing test of yours.{again}"))
        elif verdict[0] == "killed":
            lim = (mine.get("limits") or {}).get("MemoryMax")
            lines.append(("warn", f"job '{g.get('name')}' (run {j['start']['run']}, started {when}) was stopped by "
                                  f"this computer's memory limit{' (' + gb(int(lim)) + ')' if lim else ''}, not by your "
                                  "code failing; GitHub shows it as failed. The one way to change the limit: "
                                  f"`setup --limits <processors>,<memory %>`.{again}"))
        else:
            lines.append(("warn", f"job '{g.get('name')}' (run {j['start']['run']}, started {when}) was cut by "
                                  f"{verdict[1]}, not a failing test of yours; GitHub shows it as failed.{again}"))
    if fresh_settled:
        fresh = load_state(host)  # never write back an older copy of what other steps saved meanwhile
        if repo in (fresh.get("runners") or {}):
            fresh["runners"][repo]["settled_jobs"] = settled
            save_state(host, fresh)
        mine["settled_jobs"] = settled
    if rerun is not None:
        lines.append(rerun_once(gh, host, repo, rerun, named, data))
    return lines


def rerun_once(gh: GitHub, host: Host, repo: str, job_id: int, named: dict, data: dict) -> tuple[str, str]:
    if job_id not in named:
        return ("stop", f"job {job_id} is not one this computer cut or its limit stopped: nothing was run again")
    if job_id in (data.get("reruns") or []):
        return ("stop", f"job {job_id} was run again once already: it is not run a second time")
    journal(host, "rerun", f"{repo} job {job_id}, on your word")
    s, d = gh.api("POST", f"repos/{repo}/actions/jobs/{job_id}/rerun")
    if s not in (200, 201, 204):
        return ("stop", f"GitHub did not run job {job_id} again (HTTP {s})")
    data.setdefault("reruns", []).append(job_id)
    save_state(host, data)
    return ("ok", f"asked GitHub to run job '{named[job_id].get('name')}' again, once; it lands here when this computer "
                  "is free")



def watch(host: Host | None = None, gh: GitHub | None = None, out=None) -> int:
    """G5.3: one pass, run by the timer every WATCH_EVERY_S; one journal line per pass, and the heartbeat."""
    host = host or Host()
    gh = gh or GitHub(host)
    rep = Report(out)
    data = load_state(host)
    runners = data.get("runners") or {}
    said, blind = [], {}
    for rp in sorted(runners):
        gh.why = ""
        words, why_blind = watch_one(gh, host, rp, data, rep)
        said.append(words)
        if why_blind:
            blind[rp] = why_blind
    lift_after_restart(gh, host, data, rep)
    data = load_state(host)
    old = (data.get("watch") or {}).get("blind") or {}
    w = {"time": stamp(host.now()), "every_s": int((data.get("watch") or {}).get("every_s") or WATCH_EVERY_S),
         "slept_s": host.slept_s()}
    if blind:  # G5.15: counted, kept with its reason, and said: a blind pass ends `failed`, never in silence
        w["blind"] = {rp: {"why": why, "passes": (old.get(rp) or {}).get("passes", 0) + 1,
                           "since": (old.get(rp) or {}).get("since") or stamp(host.now())} for rp, why in blind.items()}
    cost_weekly(host, data)
    data["watch"] = w
    save_state(host, data)
    journal(host, "watch", "; ".join(said) or "no runner of ours here")
    for rp, b in (w.get("blind") or {}).items():
        rep.stop(blind_words(rp, b, w["every_s"]))
    return rep.code()


def write_seen(host: Host, mine: dict, value: str) -> str:
    """G5.15: as you, into the folder setup made yours; the runner's hook reads it. Returns why it failed, or ""."""
    path = seen_path(mine["folder"])
    try:
        host.write_text(path, value + "\n")
    except OSError as e:
        return f"could not write {path}: {e}"
    return ""


def blind_words(repo: str, b: dict, every_s: int) -> str:
    held = b["passes"] >= SEEN_PASSES
    way = ("nothing to do now: it comes back by itself at the time named; if it is spent again, something else logged "
           "in as this account uses it up" if "API limit" in b["why"] else
           "log in again with `gh auth login`" if "auth login" in b["why"] or "401" in b["why"] else
           "make `gh` reachable, then run `setup` again from a terminal where `gh` works (it finds gh's folder again)"
           if "could not be found" in b["why"] or "could not be run" in b["why"] else
           "check this computer's network; the next pass reads again by itself")
    return (f"the watch could not read whether {repo} is still private, {b['passes']} pass(es) in a row since "
            f"{b['since']}: {b['why']}. "
            + (f"So this computer takes no job of it now: each job is refused before its first step, until the watch "
               "reads the repository private again. " if held else
               f"After {SEEN_PASSES} such passes ({SEEN_PASSES * every_s // 60} minutes) this computer refuses its "
               "jobs until the watch reads it private again. ")
            + f"Your way out: {way}")


def watch_one(gh: GitHub, host: Host, repo: str, data: dict, rep: Report) -> tuple[str, str]:
    """One repository's pass: what to journal, and why the watch could not read it private ("" when it did)."""
    mine = data["runners"][repo]
    if mine.get("public_stop"):
        write_seen(host, mine, "public")  # G5.15: kept so until the person says it is private again
        ps = mine["public_stop"]
        if not ps.get("removed"):
            if not ps.get("labels_off", True):
                ps["labels_off"] = hold_runner(gh, host, repo, data, rep,
                                               "the repository being no longer private") is not None
            remove_public_runner(gh, host, repo, mine)
            save_state(host, data)
        return (f"{repo}: refusing its jobs, not private at {ps['time']}"
                + ("; our runner is off it on GitHub" if ps.get("removed") else "")), ""
    s, d = gh.api("GET", f"repos/{repo}")
    if s == 200 and d is not None and (d.get("private") is False or d.get("visibility") == "public"):
        write_seen(host, mine, "public")  # G5.15: the hook refuses at once, whatever the steps below manage
        public_guard(gh, host, repo, data, rep)
        return f"{repo}: no longer private: every job of it refused here", ""
    why = ""
    if s == 200 and d is not None and d.get("private") is True:
        why = write_seen(host, mine, str(int(host.now().timestamp())))
    else:
        why = gh.why or f"GitHub answered HTTP {s} for the repository"
    r = listed_runner(gh, repo, mine["name"])
    if r is not None and not r.get("busy"):
        name_cut_jobs(gh, host, repo, mine, data, None)  # named and kept now, while this computer has its record
    if r is None and s == 200:
        if not mine.get("gone_on_github"):
            gone_on_github(host, repo, data, rep)
        return f"{repo}: GitHub no longer lists {mine['name']}", why
    if r is None:
        return f"{repo}: GitHub not read (HTTP {s}){': ' + why if why else ''}", why
    offline_while_running(host, mine, r, data)
    memory_held_pass(host, mine, data)
    return (f"{repo}: {r.get('status')}, {'busy' if r.get('busy') else 'not busy'}"
            + disk_pass(gh, host, repo, data, rep) + battery_pass(gh, host, repo, data, rep)), why


def memory_held_pass(host: Host, mine: dict, data: dict) -> None:
    """R87: a job held at our memory limit is not killed: the kernel slows it, and it can crawl until GitHub's own
    time limit while this computer is under memory pressure. The watch reads the slice's own files (no admin rights
    needed): the count of times it was held at its high mark rose since the last pass, and the job's tasks all
    stalled on memory for a real share of the last minute (or, without that file, its memory sits at the high mark).
    Written down pass by pass; `status` names it first once HELD_PASSES passes in a row found it."""
    worker = runner_pids(host, mine.get("folder") or "", "Runner.Worker")
    ev = re.search(r"^high (\d+)$", host.read(MEMORY_EVENTS) or "", re.M)
    high = int(ev.group(1)) if ev else None
    before, mine["mem_high_count"] = mine.get("mem_high_count"), high
    full = re.search(r"^full avg10=[\d.]+ avg60=([\d.]+)", host.read(MEMORY_PRESSURE) or "", re.M)
    held = False
    if worker and high is not None and high > (before if isinstance(before, int) else 0):
        if full:
            held = float(full.group(1)) >= HELD_FULL_PCT
        else:
            cur, top = (host.read(MEMORY_CURRENT) or "").strip(), (host.read(MEMORY_HIGH) or "").strip()
            held = cur.isdigit() and top.isdigit() and int(cur) >= 0.95 * int(top)
    if held:
        h = mine.get("memory_held") or {"since": stamp(host.now()), "passes": 0}
        h["passes"] += 1
        h["full_pct"] = float(full.group(1)) if full else None
        mine["memory_held"] = h
    else:
        mine.pop("memory_held", None)
    save_state(host, data)


def memory_held_words(repo: str, mine: dict, recs: list[dict]) -> str:
    """R87: the job held at our memory limit, by name and run where this computer's record has it, and the two ways
    out that are the person's."""
    h = mine["memory_held"]
    run = next((j["start"] for j in reversed(recs) if j["end"] is None), None)
    what = f"job '{run['job']}' (run {run['run']})" if run else "the job running here"
    high = ((mine.get("limits") or {}).get("MemoryHigh") or "")
    limit = f" ({gb(int(high))})" if str(high).isdigit() else ""
    stall = f"; its tasks all stood still {h['full_pct']:.0f} % of the last minute" if h.get("full_pct") is not None else ""
    cancel = f"`gh run cancel {run['run']} -R {repo}`" if run else "on GitHub, from the run's page"
    return (f"{what} has been held at this computer's memory limit for jobs{limit} since {h['since'][11:19]}Z, "
            f"{h['passes']} watch passes in a row: it needs more memory than jobs here may use, so it barely moves"
            f"{stall}, and it may go on like this until GitHub's own time limit. Your two ways out: cancel it "
            f"({cancel}), or give jobs more memory: `setup {repo} --limits <processors>,<memory %>`")


def offline_while_running(host: Host, mine: dict, r: dict, data: dict) -> None:
    """G11.4 (R74): since when GitHub has shown our runner offline while its service runs here with no job process.
    The watch only writes it down (it has no admin rights and restarts nothing); `status` names it first."""
    rc, active = host.run(systemctl("is-active", mine.get("unit") or "none"))
    stuck = (r.get("status") != "online" and active.strip() == "active"
             and not runner_pids(host, mine.get("folder") or "", "Runner.Worker"))
    if stuck and not mine.get("offline_since"):
        mine["offline_since"] = stamp(host.now())
        mine["offline_slept_s"] = host.slept_s()  # R85: time asleep is not counted against it
        save_state(host, data)
    elif not stuck and mine.get("offline_since"):
        mine.pop("offline_since")
        mine.pop("offline_slept_s", None)
        save_state(host, data)


BATTERY_HOLD = "on battery"


def battery_pass(gh: GitHub, host: Host, repo: str, data: dict, rep: Report) -> str:
    """G11.2 (R19): on battery our runner is held, by default; the hold lifts on mains power. The person switches it
    off by name (`resume <repo> --on-battery-too`). A runner held for another reason is left as it is."""
    mine = data["runners"][repo]
    src, held = power_source(host), mine.get("held_for")
    if held == BATTERY_HOLD and (src == "mains" or mine.get("jobs_on_battery")):
        if lift_hold(gh, host, repo, data, rep, tries=1):
            return "; the hold on battery lifted: " + ("on mains power" if src == "mains" else "jobs on battery, "
                                                                                              "by your word")
        return "; the hold on battery could not be lifted yet (the next pass tries again)"
    if src == "battery" and not held and not mine.get("jobs_on_battery"):
        if hold_runner(gh, host, repo, data, rep, BATTERY_HOLD) is None:
            return "; on battery: the hold could not be taken yet (the next pass tries again)"
        return "; held: on battery"
    return "; held on battery" if held == BATTERY_HOLD else ""


DISK_HOLD = "low disk"
DISK_LIFT_MARGIN = 1024 ** 3  # G4.3: lifted only at the floor plus this, so a disk near the floor does not flap


def disk_pass(gh: GitHub, host: Host, repo: str, data: dict, rep: Report) -> str:
    """G4.3 (P4): below the free-space floor our runner is held before the disk fills, and the hold lifts by itself
    once the space is back. Read as the person: the runner user's home is not opened, only its file system measured.
    A runner already held for another reason is left as it is."""
    mine = data["runners"][repo]
    u = host.disk_free(f"/home/{RUNNER_USER}")
    if u is None:
        return "; free space not read"
    free, held = u[0], mine.get("held_for")
    if free < DISK_FLOOR and not held:
        journal(host, "low disk", f"{repo}: {gb(free)} free for the work folders, under the {gb(DISK_FLOOR)} floor")
        if hold_runner(gh, host, repo, data, rep, DISK_HOLD) is None:
            return f"; {gb(free)} free, under the floor: the hold could not be taken yet (the next pass tries again)"
        return f"; held: {gb(free)} free, under the {gb(DISK_FLOOR)} floor"
    if held == DISK_HOLD and free >= DISK_FLOOR + DISK_LIFT_MARGIN:
        if lift_hold(gh, host, repo, data, rep, tries=1):
            return f"; the hold for low disk lifted: {gb(free)} free"
        return f"; {gb(free)} free again: the hold could not be lifted yet (the next pass tries again)"
    if held == DISK_HOLD:
        return f"; held for low disk: {gb(free)} free, lifted at {gb(DISK_FLOOR + DISK_LIFT_MARGIN)}"
    return ""


def public_guard(gh: GitHub, host: Host, repo: str, data: dict, rep: Report) -> None:
    """G5.4 (INV1): a repository no longer private. The hook already refuses every job of it (the record says
    `public`, G5.15); here our labels come off when GitHub can be reached. The watch stops no service and kills no
    job: it runs as the person, with no admin rights (plan 2.0; a system timer has no sudo ticket)."""
    mine = data["runners"][repo]
    journal(host, "public", f"{repo} is no longer private")
    labels_off = hold_runner(gh, host, repo, data, rep, "the repository being no longer private") is not None
    mine["public_stop"] = {"time": stamp(host.now()), "labels_off": labels_off, "removed": False}
    remove_public_runner(gh, host, repo, mine)
    save_state(host, data)
    rep.stop(public_words(repo, mine["public_stop"]))


def remove_public_runner(gh: GitHub, host: Host, repo: str, mine: dict) -> None:
    """G5.4 (INV1): our runner taken off a repository no longer private, on GitHub's side, with the person's own
    rights, once no job runs on it. GitHub refuses while one does (HTTP 422, seen); once it is removed the runner stops
    its own service ("terminated error, stop the service, no retry needed", seen). Tried again each pass until done."""
    ps = mine["public_stop"]
    s, d = gh.api("GET", f"repos/{repo}/actions/runners?per_page=100")
    if s != 200:
        return  # GitHub not reached: the hook holds meanwhile; the next pass tries again
    r = next((x for x in (d or {}).get("runners", []) if x.get("name") == mine["name"]), None)
    if r is not None and r.get("busy"):
        return  # a job that began before the turn finishes (INV4); the next pass tries again
    if r is not None:
        journal(host, "remove on GitHub", f"{repo} {mine['name']}: the repository is no longer private")
        s, _ = gh.api("DELETE", f"repos/{repo}/actions/runners/{r['id']}")
        if s not in (204, 404):
            return
    ps["removed"] = True
    ps["removed_at"] = stamp(host.now())


def public_now(gh: GitHub, host: Host, repo: str, mine: dict) -> dict:
    """What `status` says of a repository no longer private is read now, never taken from the watch's last pass:
    GitHub's list of runners (an error answer can hide a change GitHub made, seen) and this computer's service."""
    ps = dict(mine["public_stop"])
    s, d = gh.api("GET", f"repos/{repo}/actions/runners?per_page=100")
    if s == 200:
        r = next((x for x in (d or {}).get("runners", []) if x.get("name") == mine["name"]), None)
        ps["removed"] = r is None
        ps["labels_off"] = r is None or sorted(x.get("name") for x in r.get("labels", [])) == [HELD_LABEL]
        ps["owed_idle"] = r is not None and not r.get("busy")
    else:
        ps["unread"] = f"GitHub not read now (HTTP {s}): from the watch's pass"
    rc, active = host.run(systemctl("is-active", mine["unit"]))
    ps["service"] = active.strip() or "not read"
    return ps


def public_words(repo: str, ps: dict) -> str:
    return (f"{repo} is no longer private (read {ps['time']}): this computer refuses every job of it before its "
            "first step"
            + ("; our runner is off the repository on GitHub's side, so GitHub hands this computer no job of it"
               if ps.get("removed") else
               "; our runner is still on the repository on GitHub's side, with no job on it: the watch could not "
               "take it off yet (each pass tries again)" if ps.get("owed_idle") else
               "; our runner is taken off the repository on GitHub's side as soon as no job runs on it (each pass "
               "tries again)")
            + ("" if ps.get("labels_off", True) or ps.get("removed") else "; our labels could not be taken off yet")
            + (f" ({ps['unread']})" if ps.get("unread") else "")
            + (f"; its service here is {ps['service']}" + (" (a removed runner's listener can wait idle for a long "
               "while; GitHub hands it nothing, and `setup` stops it before registering again)"
               if ps["service"] == "active" and ps.get("removed") else "") if ps.get("service") else "")
            + f"; a job already running finishes. Once it is private again: `resume {repo} --private-again`, then "
            f"`setup {repo}` registers the runner again")


def gone_on_github(host: Host, repo: str, data: dict, rep: Report) -> None:
    """G5.12: GitHub removed the runner and hands it no job. Its listener stops itself, or waits idle for a long while
    (both seen, no restart loop); the watch, which has no admin rights (G9.7), only reads the service and names it.
    `setup` again stops what still runs before it registers again."""
    mine = data["runners"][repo]
    if runner_pids(host, mine["folder"], "Runner.Worker"):
        return  # a job process still runs here: named once it ends
    rc, active = host.run(systemctl("is-active", mine["unit"]))
    journal(host, "gone on GitHub", f"{repo} {mine['name']}: its service is {active.strip() or 'not read'}")
    mine["gone_on_github"] = stamp(host.now())
    save_state(host, data)
    rep.warn(gone_words(repo, mine))


def lift_after_restart(gh: GitHub, host: Host, data: dict, rep: Report) -> None:
    """G5.11: the restart hold lifts by itself once the computer has restarted and GitHub reads the runner online."""
    rh = data.get("restart_hold")
    if not rh or boot_id(host) == rh.get("boot_id"):
        return
    left = []
    for rp in rh.get("repos", []):
        if rp in (data.get("runners") or {}):
            r = listed_runner(gh, rp, data["runners"][rp]["name"])
            if r and r.get("status") == "online":
                if not lift_hold(gh, host, rp, data, rep, tries=1):
                    left.append(rp)
            else:
                left.append(rp)
    if left:
        rh["repos"] = left
    else:
        data.pop("restart_hold", None)
        journal(host, "restart hold lifted", "every runner online again after the restart")
    save_state(host, data)


def watch_units(user: str, every_s: int, path: str = "") -> tuple[str, str]:
    """G5.3: a system timer whose service runs as the person (User=), not a user timer: a user timer runs only while
    the person is logged in, unless lingering is turned on for their whole account, which changes more of their
    computer than this skill needs. Runs the tool's root-owned copy."""
    svc = ("# Windstorm Repo Minions: the watch (G5.3), run by minions-watch.timer as you.\n"
           "[Unit]\nDescription=Windstorm Repo Minions watch\nWants=network-online.target\n"
           "After=network-online.target\n\n"
           f"[Service]\nType=oneshot\nUser={user}\n"
           + (f'Environment="PATH={path}"\n' if path else "")
           + f"ExecStart=/usr/bin/python3 {TOOL_COPY} watch\nNice=10\n")
    tmr = ("# Windstorm Repo Minions: the watch's schedule (G5.3).\n"
           f"[Unit]\nDescription=Windstorm Repo Minions watch, every {every_s // 60} minutes\n\n"
           f"[Timer]\nOnBootSec=2min\nOnUnitActiveSec={every_s}s\nAccuracySec=30s\n\n"
           "[Install]\nWantedBy=timers.target\n")
    return svc, tmr


def install_watch(host: Host, rep: Report, every_s: int = WATCH_EVERY_S, src: str | None = None,
                  repo: str | None = None) -> bool:
    """G5.3: the watch's units and the tool's copy it runs, read back. G8.9: the programs it runs, resolved now with
    your PATH and given to its unit; then one real pass, read back (G5.15), before setup says ok."""
    src = src or os.path.abspath(__file__)
    names = sorted(PROGRAMS) if host.system() == "Linux" else sorted(set(PROGRAMS) | DARWIN_ONLY)
    found, missing = resolve_programs(host, names)
    if [m for m in ("gh", "systemctl", "sudo") if m in missing]:
        rep.stop("the watch needs " + ", ".join(m for m in ("gh", "systemctl", "sudo") if m in missing)
                 + ", and this terminal's PATH does not find it: nothing of the watch was written. Install it so this terminal finds it, then run the same command again")
        return False
    journal(host, "write", f"{TOOL_COPY}, /etc/systemd/system/{WATCH_UNIT}.service and .timer")
    sudo(host, ["install", "-d", "-o", "root", "-g", "root", "-m", "0755", HOOKS_ROOT])
    rc, out = sudo(host, ["install", "-o", "root", "-g", "root", "-m", "0755", src, TOOL_COPY])
    if rc != 0:
        rep.stop(f"could not copy the tool for the watch: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
        return False
    svc, tmr = watch_units(host.user(), every_s, path_for(found))
    for path, text in ((f"/etc/systemd/system/{WATCH_UNIT}.service", svc),
                       (f"/etc/systemd/system/{WATCH_UNIT}.timer", tmr)):
        rc, out = sudo(host, ["tee", path], input=text)
        if rc != 0:
            rep.stop(f"could not write {path}: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
            return False
    for argv in (systemctl("daemon-reload"), systemctl("enable", "--now", f"{WATCH_UNIT}.timer")):
        journal(host, " ".join(argv))
        sudo(host, argv)
    rc, active = host.run(systemctl("is-active", f"{WATCH_UNIT}.timer"))
    if active.strip() != "active":
        rep.stop(f"the watch's timer is not active ({active.strip() or 'not read'}). Settle what it says, then run the same command again: what was done is read back, never done twice")
        return False
    data = load_state(host)
    data["watch"] = dict(data.get("watch") or {}, every_s=every_s)
    data["programs"] = found
    save_state(host, data)
    rep.ok(f"the watch runs every {every_s // 60} minutes as you (a system timer, read back); `status` says when it "
           f"last ran. The programs it runs by name are found where this terminal finds them (gh: {found['gh']})")
    if repo is None:
        return True
    # G5.15: one real pass now, as the timer runs it, and what it read, read back.
    before = (load_state(host).get("watch") or {}).get("time")
    journal(host, "the watch's first pass")
    sudo(host, systemctl("start", f"{WATCH_UNIT}.service"))
    w = load_state(host).get("watch") or {}
    b = (w.get("blind") or {}).get(repo)
    seen = (host.read(seen_path(load_state(host)["runners"][repo]["folder"])) or "").strip()
    if w.get("time") in (None, before):
        rep.stop(f"the watch's first pass wrote nothing: read `journalctl -u {WATCH_UNIT}.service`")
        return False
    if b or not seen.isdigit():
        rep.stop("the watch's first pass could not read whether the repository is private: "
                 + (b["why"] if b else "its record was not written") + ". This computer takes no job of it until "
                 "the watch can")
        return False
    rep.ok(f"the watch's first pass ran now, as the timer runs it, and read {repo} private (read back); a job here "
           f"runs only while the watch has read that in the last {SEEN_PASSES * every_s // 60} minutes")
    return True


def reregister(gh: GitHub, host: Host, repo: str, data: dict, rep: Report) -> bool:
    """G5.12: `setup` again for a runner GitHub removed: registered again in place, its folders and labels kept."""
    mine = data["runners"][repo]
    folder = mine["folder"]
    if runner_pids(host, folder, "Runner.Worker"):
        rep.wait(f"a job process of {mine['name']} still runs here; nothing is changed. Next: `setup {repo}` again "
                 "once it has ended")
        return False
    # A removed runner's listener can keep running, idle, long after GitHub dropped it (13 minutes and more, seen):
    # it is stopped and read back before the new registration, or the old process keeps the service and the new
    # registration never connects (seen). GitHub hands a removed runner no job, so nothing is cut.
    if not stop_runner(host, mine["unit"], folder, rep, drained=True):
        return False
    journal(host, "re-register", f"{repo} {mine['name']} in {folder}")
    as_runner(host, ["rm", "-f", f"{folder}/.runner", f"{folder}/.credentials", f"{folder}/.credentials_rsaparams"])
    if not register(gh, host, repo, folder, mine["name"], mine["labels"], rep):
        return False
    if not lock_credentials(host, folder, rep):
        return False
    for argv in (systemctl("enable", mine["unit"]), systemctl("start", mine["unit"])):
        journal(host, " ".join(argv))
        sudo(host, argv)
    for k in ("gone_on_github", "labels_before_hold", "held_for", "held_at"):
        mine.pop(k, None)
    save_state(host, data)
    rep.ok(f"registered again in place: {mine['name']}, its folders and labels kept")
    return both_sides(gh, host, repo, mine["name"], mine["unit"], mine["labels"], rep)


# --------------------------------------------------------------------------------------------------------- G3 move

MOVE_BRANCH_PREFIX = "minions-move-"
VERIFY_WAIT_S = 600


def size_rank(label: str) -> int:
    names = [x[0] for x in reversed(SIZE_RULES)]  # small, medium, large
    return names.index(label) if label in names else -1


def cores_asked(labels: list[str]) -> int | None:
    """GitHub's larger runners carry the core count in the label (ubuntu-latest-8-cores, ubuntu-22.04-16core)."""
    for x in labels:
        m = re.search(r"(\d+)[-_]?cores?\b", x)
        if m:
            return int(m.group(1))
    return None


def plan_job(path: str, triggers: dict, job: dict, system: str, machine: str, our_labels: list[str],
             include: set[str]) -> tuple[str, str, list[str] | None]:
    """One job: ("move", words, new labels) | ("left", reason, None) | ("publish", reason, None) | ("here", ...)."""
    ro = job["runs_on"]
    if isinstance(ro, list) and ro and set(ro) <= set(our_labels):
        return "here", f"already asks for {', '.join(ro)}", None
    kind, words = judge_job(job, system)
    if kind != "ok":
        if kind == "stay":
            return "left", words, None
        return "left", words.replace("; can move here", ""), None
    labels = [x.lower() for x in ro]
    if any("gpu" in x for x in labels):
        return "left", f"asks for a GPU ({ro[0]})", None
    if any(re.search(r"-arm(64)?\b", x) for x in labels) and ARCH.get(machine, machine) != "arm64":
        return "left", f"asks for an ARM machine ({ro[0]}); this computer is {machine}", None
    publish = []
    if triggers.get("tag_push"):
        publish.append("its workflow starts on a pushed tag")
    if "release" in triggers.get("events", ()):
        publish.append("its workflow starts on a release")
    if job["environment"]:
        publish.append("it deploys to an environment")
    publish += job["publishes"]
    sec = sorted(s for s in job["secrets"] if PUBLISH_SECRET.search(s))
    if sec:
        publish.append("it holds a publishing secret (" + ", ".join(sec) + ")")
    key = f"{os.path.basename(path)}:{job['id']}"
    if publish and key not in include:
        return "publish", "; ".join(publish), None
    new = ["minions"]
    cores = cores_asked(labels)
    if cores and cores > 2:
        need = size_label({"cores": cores, "mem": 10 ** 15})
        have = next((x for x in our_labels if x != "minions"), "minions-small")
        if size_rank(have) < size_rank(need):
            return "left", f"asks for {cores} cores ({ro[0]}); this computer's runner is {have}", None
        if need != "minions-small":
            new.append(need)
    assert "self-hosted" not in new
    why = "with your word for this job (--include-job), " if publish else ""
    return "move", f"{why}{', '.join(ro)} -> [{', '.join(new)}]", new


def edit_runs_on(text: str, edits: list[tuple[tuple[int, int], list[str]]]) -> str:
    """Change only the runs-on lines, by line: indent, trailing comment and every other line stay as they were."""
    lines = text.splitlines(keepends=True)
    for (start, end), labels in sorted(edits, reverse=True):
        raw = lines[start]
        nl = raw[len(raw.rstrip("\r\n")):]
        body = raw.rstrip("\r\n")
        code = strip_comment(body)
        comment = body[len(code):] if "#" in body[len(code):] else ""
        indent = code[:len(code) - len(code.lstrip(" "))]
        value = "[" + ", ".join(labels) + "]"
        assert "self-hosted" not in value
        lines[start:end] = [f"{indent}runs-on: {value}{comment}{nl}"]
    return "".join(lines)


def read_workflow_files(gh: GitHub, repo: str, ref: str | None, rep: Report) -> list[tuple[str, str]] | None:
    q = f"?ref={ref}" if ref else ""
    s, d = gh.api("GET", f"repos/{repo}/contents/.github/workflows{q}")
    if s == 404:
        return []
    if s != 200 or not isinstance(d, list):
        rep.stop(f"could not list the workflows: GitHub answered HTTP {s}; nothing is changed. Run the same command again; if it stops here again, GitHub's answer named here is the reason")
        return None
    files = []
    for f in d:
        if not f.get("name", "").endswith((".yml", ".yaml")):
            continue
        fs, fd = gh.api("GET", f"repos/{repo}/contents/{f['path']}{q}")
        try:
            text = base64.b64decode((fd or {}).get("content", "")).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            text = None
        if fs != 200 or text is None:
            why = f"GitHub answered HTTP {fs}" if fs != 200 else "its content could not be read as text"
            rep.stop(f"{f['path']}: could not read it ({why}); nothing is changed. Run the same command again; if it stops here again, GitHub's answer named here is the reason")
            return None
        files.append((f["path"], text))
    return files


def our_runner_online(gh: GitHub, host: Host, repo: str, rep: Report) -> dict | None:
    mine = (load_state(host).get("runners") or {}).get(repo)
    if not mine:
        rep.stop(f"no runner of ours serves {repo} on this computer: run `setup {repo}` first")
        return None
    s, d = gh.api("GET", f"repos/{repo}/actions/runners?per_page=100")
    listed = [r for r in ((d or {}).get("runners", []) if s == 200 else []) if r.get("name") == mine["name"]]
    if not listed or listed[0].get("status") != "online":
        seen = listed[0].get("status") if listed else "not listed"
        rep.stop(f"GitHub says our runner {mine['name']} is {seen}: no job is moved to a runner that is not there. "
                 f"Run `setup {repo}` (or `doctor`) first")
        return None
    rep.ok(f"our runner {mine['name']} is online for {repo}")
    return mine


MOVE_PR = {"prefix": MOVE_BRANCH_PREFIX, "title": "Run these jobs on your own computer",
           "message": "Run these jobs on your own computer (Windstorm Repo Minions)\n\nOnly `runs-on` lines change."}


def open_move_pr(gh: GitHub, host: Host, repo: str, base: str, changed: dict[str, str], body: str,
                 rep: Report, kind: dict | None = None) -> dict | None:
    """G3.1 (and G6.2's put-back): one branch made through the API, one commit, one pull request. Nothing of the
    person's is checked out."""
    kind = kind or MOVE_PR
    s, ref = gh.api("GET", f"repos/{repo}/git/ref/heads/{base}")
    head_sha = ((ref or {}).get("object") or {}).get("sha") if s == 200 else None
    s2, com = gh.api("GET", f"repos/{repo}/git/commits/{head_sha}") if head_sha else (0, None)
    base_tree = ((com or {}).get("tree") or {}).get("sha") if s2 == 200 else None
    if not base_tree:
        rep.stop(f"could not read the tip of {base} (HTTP {s}/{s2}). Run the same command again; if it stops here again, GitHub's answer named here is the reason")
        return None
    entries = []
    for path, text in sorted(changed.items()):
        bs, blob = gh.api("POST", f"repos/{repo}/git/blobs", {"content": text, "encoding": "utf-8"})
        if bs != 201:
            rep.stop(f"GitHub refused the changed {path} (HTTP {bs}). Run the same command again; if it stops here again, GitHub's answer named here is the reason")
            return None
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    ts, tree = gh.api("POST", f"repos/{repo}/git/trees", {"base_tree": base_tree, "tree": entries})
    cs, commit = gh.api("POST", f"repos/{repo}/git/commits", {
        "message": kind["message"], "tree": (tree or {}).get("sha"), "parents": [head_sha]}) if ts == 201 else (ts, None)
    if cs != 201:
        rep.stop(f"GitHub refused the commit (HTTP {ts}/{cs}). Run the same command again; if it stops here again, GitHub's answer named here is the reason")
        return None
    branch = kind["prefix"] + secrets.token_hex(4)
    journal(host, "move-branch", f"{repo} {branch}")
    rs, _ = gh.api("POST", f"repos/{repo}/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    if rs != 201:
        why = " (a workflow file needs the `workflow` right on your GitHub login: `gh auth refresh -s workflow`)" \
            if rs in (403, 404, 422) else ""
        rep.stop(f"GitHub refused the branch {branch} (HTTP {rs}){why}. Run the same command again; if it stops here again, GitHub's answer named here is the reason")
        return None
    journal(host, "move-pr", f"{repo} {branch}")
    ps, pr = gh.api("POST", f"repos/{repo}/pulls", {
        "title": kind["title"], "head": branch, "base": base, "body": body})
    if ps != 201:
        gh.api("DELETE", f"repos/{repo}/git/refs/heads/{branch}")
        rep.stop(f"GitHub refused the pull request (HTTP {ps}); the branch was deleted again")
        return None
    return {"number": pr["number"], "url": pr.get("html_url"), "branch": branch}


def pr_body(moved: list, left: list, publish: list, here: list) -> str:
    out = ["This pull request changes only `runs-on` lines, so these jobs run on your own computer's runner "
           "(labels `minions`). Nothing else in your workflows changes. The tool that opened it never merges it: "
           "you do.", "", "## Moved"]
    out += [f"- `{p}` job `{j}`: {w}" for p, j, w in moved]
    cannot = [x for x in left if not x[2].startswith(NOT_OURS)]
    theirs = [x for x in left if x[2].startswith(NOT_OURS)]
    if cannot:
        out += ["", "## Left as they are (they cannot run on this computer)"]
        out += [f"- `{p}` job `{j}`: {w}" for p, j, w in cannot]
    if theirs:
        out += ["", "## Left as they are (they ask for a runner that is not ours)"]
        out += [f"- `{p}` job `{j}`: {w}" for p, j, w in theirs]
    if publish:
        out += ["", "## Left as they are (they publish or deploy)",
                "Each needs your word for that one job before it moves: `move <owner/repo> --include-job "
                "<file>:<job>`."]
        out += [f"- `{p}` job `{j}`: {w}" for p, j, w in publish]
    if here:
        out += ["", "## Already on your computer"]
        out += [f"- `{p}` job `{j}`: {w}" for p, j, w in here]
    out += ["", "After you merge it, `move --verify` sees one real run of each moved workflow finish on your "
            "computer. Until then nothing is called moved."]
    return "\n".join(out) + "\n"


def move(repo: str, verify: bool = False, include: list[str] | None = None, host: Host | None = None,
         gh: GitHub | None = None, out=None, wait_s: int = VERIFY_WAIT_S, poll_s: float = 10) -> int:
    host = host or Host()
    gh = gh or GitHub(host)
    rep = Report(out)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        rep.stop(f"not a repository name: {repo!r} (write owner/name)")
        return rep.code()
    if verify:
        move_verify(gh, host, repo, rep, wait_s, poll_s)
        return rep.code()
    entry = check_fresh_look(host, repo, rep)
    if entry is None:
        return rep.code()
    mine = our_runner_online(gh, host, repo, rep)
    if mine is None:
        return rep.code()
    last = (load_state(host).get("moves") or {}).get(repo)
    if last and not last.get("merged"):
        s, pr = gh.api("GET", f"repos/{repo}/pulls/{last['pr']}")
        if s == 200 and pr.get("state") == "open":
            rep.need(f"pull request #{last['pr']} is still open ({pr.get('html_url')}): merge it yourself, then "
                     f"run `move {repo} --verify`; no second one is opened")
            return rep.code()
    s, d = gh.api("GET", f"repos/{repo}")
    base = (d or {}).get("default_branch") if s == 200 else None
    if not base:
        rep.stop(f"could not read {repo} (HTTP {s}). Run the same command again; if it stops here again, GitHub's answer named here is the reason")
        return rep.code()
    files = read_workflow_files(gh, repo, None, rep)
    if files is None:
        return rep.code()
    survey_ = entry.get("survey") or {}
    system, machine = survey_.get("system") or host.system(), survey_.get("machine") or host.machine()
    inc = set(include or [])
    moved, left, publish, here, changed, plan = [], [], [], [], {}, {}
    for path, text in files:
        triggers = read_triggers(text)
        edits = []
        for job in read_workflow(text):
            kind, words, labels = plan_job(path, triggers, job, system, machine, mine["labels"], inc)
            row = (path, job["id"], words)
            {"move": moved, "left": left, "publish": publish, "here": here}[kind].append(row)
            if kind == "move":
                edits.append((job["runs_on_at"], labels))
                start, end = job["runs_on_at"]
                plan.setdefault(path, []).append({"id": job["id"], "name": job["name"] or job["id"],
                                                  "runs_on": "".join(text.splitlines(keepends=True)[start:end])})
            (rep.ok if kind in ("move", "here") else rep.warn)(
                f"{path} job {job['id']}: " + {"move": "moves, ", "left": "stays: ", "publish": "stays, it "
                                                "publishes or deploys: ", "here": ""}[kind] + words)
        if edits:
            changed[path] = edit_runs_on(text, edits)
    if not moved:
        rep.ok("no job can move to this computer now; nothing was changed")
        return rep.code()
    pr = open_move_pr(gh, host, repo, base, changed, pr_body(moved, left, publish, here), rep)
    if pr is None:
        return rep.code()
    data = load_state(host)
    data.setdefault("moves", {})[repo] = {"pr": pr["number"], "branch": pr["branch"], "files": plan, "base": base,
                                          "time": host.now().strftime("%Y-%m-%dT%H:%M:%SZ")}
    save_state(host, data)
    rep.ok(f"pull request #{pr['number']} opened: {len(moved)} job(s) to move, {len(left) + len(publish)} left "
           "as they are and named in it")
    rep.need(f"read and merge pull request #{pr['number']} yourself ({pr['url']}); this tool never merges. Then "
             f"run `move {repo} --verify`")
    return rep.code()


def run_jobs_matching(jobs: list[dict], wanted: dict) -> list[dict]:
    names = {wanted["id"], wanted["name"]}
    return [j for j in jobs if j.get("name") in names or any(j.get("name", "").startswith(n + " (") for n in names)]


def move_verify(gh: GitHub, host: Host, repo: str, rep: Report, wait_s: int, poll_s: float) -> None:
    """G3.6: "moved" only after one real run of each moved workflow finished on a runner of ours."""
    data = load_state(host)
    mv = (data.get("moves") or {}).get(repo)
    mine = (data.get("runners") or {}).get(repo)
    if not mv or not mine:
        rep.stop(f"no move of {repo} is written down on this computer: run `move {repo}` first")
        return
    s, pr = gh.api("GET", f"repos/{repo}/pulls/{mv['pr']}")
    if s != 200:
        rep.stop(f"could not read pull request #{mv['pr']} (HTTP {s}). Run the same command again; if it stops here again, GitHub's answer named here is the reason")
        return
    if not pr.get("merged_at"):
        if pr.get("state") == "closed":
            rep.stop(f"pull request #{mv['pr']} was closed without a merge: nothing moved. Run `move {repo}` again "
                     "if you want a new one")
        else:
            rep.need(f"pull request #{mv['pr']} is not merged yet ({pr.get('html_url')}): merge it yourself first")
        return
    merged_at = pr["merged_at"]
    rep.ok(f"pull request #{mv['pr']} was merged at {merged_at}")
    files = dict(read_workflow_files(gh, repo, mv.get("base"), rep) or [])
    all_ok = True
    for path, wanted_jobs in sorted(mv["files"].items()):
        text = files.get(path, "")
        jobs_now = read_workflow(text)
        every_job_moved = {j["id"] for j in jobs_now} <= {w["id"] for w in wanted_jobs}
        run = find_or_start_run(gh, host, repo, path, mv.get("base"), merged_at, read_triggers(text),
                                every_job_moved, rep, wait_s, poll_s)
        if run is None:
            all_ok = False
            continue
        jobs = jobs_of(gh, repo, run["id"])
        for w in wanted_jobs:
            got = run_jobs_matching(jobs, w)
            if not got:
                rep.warn(f"{path} job {w['id']}: not in run {run['id']} (it may be skipped by a condition)")
                all_ok = False
                continue
            for j in got:
                labels = [x.lower() for x in j.get("labels") or []]
                where = j.get("runner_name") or "no runner"
                if where != mine["name"] or "minions" not in labels:
                    rep.stop(f"{path} job {w['id']} ran on {where} (labels {', '.join(labels) or 'none'}), not on our "
                             f"runner {mine['name']}: it did not move. Check that job's `runs-on`, then run the same command again")
                    all_ok = False
                elif j.get("conclusion") == "success":
                    rep.ok(f"moved: {path} job {w['id']} ran on {where} and succeeded (run {run['id']})")
                else:
                    rep.warn(f"moved: {path} job {w['id']} ran on {where} and ended {j.get('conclusion')}: that is "
                             f"the job's own result; its log is at {j.get('html_url')}")
    if rep.waiting and not rep.stopped:
        # R76: a run that waits on our runner while GitHub shows it offline is said as that, with the one command
        # that starts it; "after the next push" would send the person to make a push the run does not need.
        s, d = gh.api("GET", f"repos/{repo}/actions/runners?per_page=100")
        listed = [r for r in ((d or {}).get("runners", []) if s == 200 else []) if r.get("name") == mine["name"]]
        seen = (listed[0].get("status") if listed else "not listed") if s == 200 else None
        if seen and seen != "online":
            rep.wait(f"on our runner {mine['name']}: GitHub shows it {seen}, so the moved jobs wait for it; nothing is "
                     f"called moved before one has run here. Next: `resume {repo}`, then `move {repo} --verify` again")
        else:
            rep.wait(f"for one finished run of each moved workflow of {repo}; nothing is called moved before that. "
                     f"Next: `move {repo} --verify` again after the next push or pull request")
        return
    if all_ok and not rep.stopped:
        data = load_state(host)
        data["moves"][repo].update(merged=merged_at, verified=host.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
        save_state(host, data)
        rep.ok(f"moved: every moved job of {repo} ran on this computer")


def find_or_start_run(gh: GitHub, host: Host, repo: str, path: str, base: str, merged_at: str, triggers: dict,
                      every_job_moved: bool, rep: Report, wait_s: int, poll_s: float) -> dict | None:
    wf = os.path.basename(path)
    runs_path = f"repos/{repo}/actions/workflows/{wf}/runs?branch={base}&per_page=20"

    def after_merge():
        s, d = gh.api("GET", runs_path)
        runs = (d or {}).get("workflow_runs", []) if s == 200 else []
        return sorted((r for r in runs if r.get("created_at", "") >= merged_at), key=lambda r: r["created_at"])

    runs = after_merge()
    deadline = (host.now() + dt.timedelta(seconds=wait_s)).strftime("%H:%MZ")
    if not runs:
        if "workflow_dispatch" in triggers["events"] and every_job_moved:
            journal(host, "verify-dispatch", f"{repo} {path}")
            s, d = gh.api("POST", f"repos/{repo}/actions/workflows/{wf}/dispatches",
                          {"ref": base, "return_run_details": True})
            if s not in (200, 204):
                rep.warn(f"{path}: GitHub refused to start it (HTTP {s})")
        else:
            why = ("some of its jobs stay on GitHub's machines, so the tool does not start it"
                   if "workflow_dispatch" in triggers["events"] else "it cannot be started by hand")
            rep.wait(f"{path}: no run since the merge yet, and {why}; its next push or pull request run will show "
                     "it")
            return None
    rep.ok(f"{path}: watching for its run to finish, until {deadline} at the latest")
    left = wait_s
    while True:
        runs = after_merge()
        done = [r for r in runs if r.get("status") == "completed"]
        if done:
            return done[0]
        if gh.limited():  # G8.14: never poll into a spent limit
            rep.wait(f"{path}: {gh.why}. Run `move {repo} --verify` again after that time")
            return None
        if left <= 0:
            break
        host.sleep(poll_s)
        left -= poll_s
    state = runs[0].get("status") if runs else "not seen"
    rep.wait(f"{path}: its run since the merge was still {state} at the deadline ({deadline})")
    return None


# ------------------------------------------------------------------------------------------------------------- look

# ---------------------------------------------------------------------------------------------------------- G6 uninstall

PUT_BACK_PREFIX = "minions-put-back-"
PUT_BACK_PR = {"prefix": PUT_BACK_PREFIX, "title": "Run these jobs where they ran before",
               "message": "Put runs-on back as it was before Windstorm Repo Minions moved it\n\n"
                          "Only `runs-on` lines change."}
RUNNERS_HOME = f"/home/{RUNNER_USER}/runners/"


def root_test(host: Host, path: str) -> bool | None:
    """Read as root: the runner user's home is closed to the person (G2.2), so the person's own checks cannot see in.
    None when it could not be read (no admin rights open in this terminal): a third answer, never a no (R71)."""
    rc, out = sudo(host, ["test", "-e", path])
    if rc == 0:
        return True
    return None if "a password is required" in out else False


def exists_as_root(host: Host, path: str) -> bool:
    """A path that could not be read counts as there, so a removal never reads "gone" from a refused read (R71)."""
    return root_test(host, path) is not False


def unit_path(unit: str) -> str:
    return f"/etc/systemd/system/{unit}"


def unit_gone(host: Host, unit: str) -> bool:
    """A unit is gone when systemd no longer has it loaded and nothing of it is active (G6.5's lesson). A slice is never
    "not-found": systemd makes any slice from its name alone (seen on a stock Ubuntu 24.04, systemd 255: a slice never
    made reads LoadState=loaded). So a slice is gone when it is inactive with no file and no drop-in loaded."""
    rc, out = host.run(systemctl("show", unit, "-p", "LoadState", "-p", "ActiveState", "-p", "FragmentPath",
                                 "-p", "DropInPaths"))
    got = dict(x.split("=", 1) for x in out.splitlines() if "=" in x)
    if got.get("ActiveState") not in ("inactive", None):
        return False
    if got.get("LoadState") in ("not-found", None):
        return True
    return unit.endswith(".slice") and not got.get("FragmentPath") and not got.get("DropInPaths")


def remove_github_side(gh: GitHub, host: Host, repo: str, mine: dict, rep: Report) -> bool:
    """G6.1, G6.6: the GitHub side goes first. The runner's own removal runs as the runner user, the removal token on
    standard input (INV2); never by entering the runner's home as the person. If it cannot run: GitHub's API deletes
    the runner. Either way, read back that GitHub no longer lists it."""
    s, r = read_runner(gh, repo, mine["name"])
    if s != 200:
        rep.stop(f"GitHub could not be read for {repo}'s runners (HTTP {s}): nothing on this computer is deleted yet. "
                 f"Run `uninstall {repo}` again")
        return False
    if r is None:
        rep.ok(f"GitHub no longer lists {mine['name']} for {repo}")
        return True
    folder = mine.get("folder") or ""
    own = False
    if folder and exists_as_root(host, f"{folder}/.runner"):
        s, d = gh.api("POST", f"repos/{repo}/actions/runners/remove-token")
        token = (d or {}).get("token") if s in (200, 201) else None
        if token:
            journal(host, "config.sh remove", f"{repo} {mine['name']} (as {RUNNER_USER}, token by standard input)")
            rc, out = as_runner(host, ["bash", "-c", REMOVE_SCRIPT, "minions-remove", folder], input=token + "\n")
            out = mask(out, token)
            token = None  # dropped (INV2)
            own = rc == 0
            if not own:
                rep.warn(f"the runner's own removal did not finish ({tail(out)}); GitHub's API removes it instead")
    if not own:
        journal(host, "DELETE runner", f"{repo} {mine['name']} id {r.get('id')}")
        s, _ = gh.api("DELETE", f"repos/{repo}/actions/runners/{r['id']}")
        if s not in (204, 404):
            rep.stop(f"GitHub did not remove {mine['name']} (HTTP {s}): nothing on this computer is deleted yet. Run the same command again; if it stops here again, GitHub's answer named here is the reason")
            return False
    s, back = read_runner(gh, repo, mine["name"])
    if s != 200 or back is not None:
        rep.stop(f"GitHub still lists {mine['name']} for {repo}: nothing on this computer is deleted yet"
                 if s == 200 else f"GitHub could not be read back for {repo}'s runners (HTTP {s}): nothing on this "
                 f"computer is deleted yet. Run `uninstall {repo}` again")
        return False
    rep.ok(f"GitHub no longer lists {mine['name']} for {repo} (" +
           ("the runner's own removal, as the runner user" if own else "removed through GitHub's API") + "), read back")
    return True


def remove_service(host: Host, mine: dict, rep: Report) -> bool:
    """The runner's own unit, through its own svc.sh run as root; our drop-in; read back gone. Safe to run again."""
    unit, folder = mine.get("unit") or "", mine.get("folder") or ""
    if folder and exists_as_root(host, f"{folder}/.service"):
        journal(host, "svc.sh uninstall", folder)
        rc, out = sudo(host, ["bash", "-c", 'cd "$1" && ./svc.sh uninstall', "minions-svc", folder])
        if rc != 0:
            rep.warn(f"the runner's own service uninstall did not finish ({tail(out)}); its unit is removed directly")
    if unit:
        if exists_as_root(host, unit_path(unit)):
            journal(host, "remove unit", unit)
            sudo(host, systemctl("disable", unit))
            sudo(host, ["rm", "-f", unit_path(unit)])
        if exists_as_root(host, unit_path(unit) + ".d"):
            journal(host, "remove drop-in", f"{unit_path(unit)}.d")
            sudo(host, ["rm", "-rf", unit_path(unit) + ".d"])
        sudo(host, systemctl("daemon-reload"))
        if not unit_gone(host, unit):
            rep.stop(f"the runner's service {unit} is still there after its removal. Run the same command again: it finishes a removal that was not finished")
            return False
    rep.ok("the runner's service and our drop-in are gone, read back")
    return True


def remove_folders(host: Host, repo: str, mine: dict, rep: Report) -> bool:
    """The runner's own folders (only under the runner user's runners folder), its hooks and its jobs' records."""
    folder = mine.get("folder") or ""
    paths = [x for x in (folder if folder.startswith(RUNNERS_HOME) and "/.." not in folder else "",
                         hook_dir(repo), records_dir_of(folder) if folder else "") if x]
    for path in paths:
        if exists_as_root(host, path):
            journal(host, "delete", path)
            sudo(host, ["rm", "-rf", "--one-file-system", path])
    left = [x for x in paths if exists_as_root(host, x)]
    if left:
        rep.stop(f"could not delete {', '.join(left)}. Run the same command again: it finishes a removal that was not finished")
        return False
    rep.ok("the runner's folders, its hooks and its jobs' records are deleted, read back")
    return True


def adopt_unfinished(host: Host, data: dict, repos: set[str] | None) -> list[str]:
    """R71: a setup cut part-way left a half-made runner (a folder, maybe a registration or a service). Its record
    joins the runners to remove, so `uninstall` and `setup` again find it instead of saying there is nothing."""
    half = data.get("setting_up") or {}
    took = sorted(r for r in half if repos is None or r in repos)
    if not took:
        return []
    runners = data.setdefault("runners", {})
    for r in took:
        entry = half.pop(r)
        runners.setdefault(r, {**entry, "unit": entry.get("unit") or "", "unfinished": entry.get("time")})
    if not half:
        data.pop("setting_up", None)
    save_state(host, data)
    return took


def remove_one(gh: GitHub, host: Host, repo: str, data: dict, rep: Report, wait_s: int, poll_s: float,
               next_cmd: str) -> bool:
    """G6.1: drain, the GitHub side first (G6.6), then the service, then the folders. Each step is journaled before it
    runs and is safe to run again (G6.4): a removal cut part-way is finished by running uninstall again."""
    mine = data["runners"][repo]
    if not mine.get("removing"):
        journal(host, "uninstall", f"{repo} {mine.get('name')}")
        mine["removing"] = host.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        save_state(host, data)
    folder = mine.get("folder") or ""
    s, listed = read_runner(gh, repo, mine["name"])
    if s != 200:
        rep.stop(f"GitHub could not be read for {repo}'s runners (HTTP {s}): nothing is changed. Run `uninstall "
                 f"{repo}` again")
        return False
    if listed is not None:
        if hold_runner(gh, host, repo, data, rep, "removal") is None:
            return False
        state = wait_idle(gh, host, repo, mine, wait_s, poll_s, rep, next_cmd)
        if state == "running":
            return False
        rep.ok(f"drained: no job runs on {mine['name']}")
    elif folder and runner_pids(host, folder, "Runner.Worker"):
        rep.wait(f"a job process of this runner still runs here, while GitHub no longer lists it; nothing is cut. "
                 f"Next: `{next_cmd}` again")
        return False
    if runner_pids(host, folder) or host.run(systemctl("is-active", mine.get("unit") or "none"))[1].strip() == "active":
        if not stop_runner(host, mine.get("unit") or "", folder, rep, drained=True):
            return False
    # The runner's own `config.sh remove` refuses while its service is installed, so svc.sh uninstall comes first;
    # the GitHub side still goes before anything is deleted (G6.6).
    if folder and exists_as_root(host, f"{folder}/.service"):
        journal(host, "svc.sh uninstall", folder)
        rc, out = sudo(host, ["bash", "-c", 'cd "$1" && ./svc.sh uninstall', "minions-svc", folder])
        if rc != 0:
            rep.warn(f"the runner's own service uninstall did not finish ({tail(out)}); its unit is removed directly")
    if not remove_github_side(gh, host, repo, mine, rep):
        return False
    if not remove_service(host, mine, rep) or not remove_folders(host, repo, mine, rep):
        return False
    journal(host, "removed", f"{repo} {mine.get('name')}")
    data["runners"].pop(repo, None)
    save_state(host, data)
    rep.ok(f"uninstalled: {repo} has no runner on this computer now")
    return True


# ------------------------------------------------------------------------------------------------------- G8.4 doctor
def doctor(repo: str | None = None, host: Host | None = None, gh: GitHub | None = None, out=None,
           interactive: bool | None = None) -> int:
    """G8.4, INV8: every promise of G2 and G4 read back on this computer as it is now, and a half-done step finished
    or undone. It changes nothing else: a promise not kept is named with its way on, and the last line says how many.
    A read it cannot make (no admin rights open here) is said as not read, never as kept."""
    host = host or Host()
    gh = gh or GitHub(host)
    rep = Report(out)
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        rep.stop(f"not a repository name: {repo!r} (write owner/name)")
        return rep.code()
    data = load_state(host)
    wanted = None if repo is None else {repo}
    half = sorted(r for r in (data.get("setting_up") or {}) if wanted is None or r in wanted)
    cut = sorted(r for r, m in (data.get("runners") or {}).items()
                 if (wanted is None or r in wanted) and (m.get("removing") or m.get("unfinished")))
    repos = sorted(r for r in (data.get("runners") or {}) if wanted is None or r in wanted)
    if not (half or repos):
        rep.ok(f"no runner of ours serves {repo} on this computer: nothing to check" if repo else
               "no runner of ours on this computer: nothing to check")
        return rep.code()
    interactive = host.isatty() if interactive is None else interactive
    if interactive or half or cut or host.run(["sudo", "-n", "true"])[0] == 0:
        # INV8: finishing a half-done step is an admin step; with no terminal to ask, the need line says how.
        admin = ask_admin_once(host, rep, interactive, "`doctor`" if repo is None else f"`doctor {repo}`")
        if not admin:
            return rep.code()
    else:
        admin = False
        rep.warn("admin rights are not open here and no terminal can ask: what only an admin can read is said as "
                 "not read below; run `doctor` in your own terminal to read it all")
    broken: list[str] = []
    # INV8: a setup or a removal cut part-way is finished (a removal) or undone (a setup), read back.
    for rp in sorted(set(half) | set(cut)):
        adopt_unfinished(host, data, {rp})
        rep.ok(f"a {'setup' if rp in half or data['runners'][rp].get('unfinished') else 'removal'} of {rp} here was "
               "cut part-way: what it made is taken away, read back")
        if not remove_one(gh, host, rp, data, rep, DRAIN_WAIT_S, 10, f"doctor {rp}"):
            return rep.code()
        rep.ok(f"`setup {rp}` sets it up again, fresh, when you want it")
    for rp in [r for r in repos if r not in cut]:
        broken += doctor_one(gh, host, rp, data["runners"][rp], rep)
    rc, active = host.run(systemctl("is-active", f"{WATCH_UNIT}.timer"))
    if active.strip() == "active":
        rep.ok("the watch's timer is active")
    else:
        rep.warn(f"the watch's timer is {active.strip() or 'not read'}: `setup` again puts it back")
        broken.append("the watch")
    if broken:
        rep.stop(f"{len(broken)} promise(s) not kept here: {', '.join(broken)}. Nothing was changed: run the command "
                 "each line above names, then `doctor` again")
    else:
        rep.ok("every promise of the install read back as kept" + ("" if admin else ", of those that could be read"))
    return rep.code()


def doctor_one(gh: GitHub, host: Host, repo: str, mine: dict, rep: Report) -> list[str]:
    """One runner's promises, each read now. Returns the names of those not kept."""
    broken: list[str] = []
    folder, unit, name = mine.get("folder") or "", mine.get("unit") or "", mine.get("name") or ""
    again = f"`setup {repo}` again after `uninstall {repo}`"

    def bad(what: str, words: str) -> None:
        rep.warn(f"{repo}: {words}")
        broken.append(f"{repo} {what}")

    # G2.2: the runner's user has no admin rights and its home is closed.
    rc, groups = host.run(["id", "-nG", RUNNER_USER])
    extra = sorted(set(groups.split()) - {RUNNER_USER}) if rc == 0 else None
    if extra is None:
        bad("runner user", f"the user {RUNNER_USER} is not there: {again}")
    elif extra:
        bad("runner user", f"{RUNNER_USER} is in other groups ({', '.join(extra)}): take it out of them "
                           f"(`sudo gpasswd -d {RUNNER_USER} <group>`)")
    else:
        rep.ok(f"{repo}: {RUNNER_USER} is in its own group only, no admin rights")
    rc, mode = sudo(host, ["stat", "-c", "%a", f"/home/{RUNNER_USER}"])
    if rc != 0:
        rep.warn(f"{repo}: the mode of /home/{RUNNER_USER}: not read")
    elif mode.strip() not in ("750", "700"):
        bad("runner home", f"/home/{RUNNER_USER} is open to others (mode {mode.strip()}): "
                           f"`sudo chmod 750 /home/{RUNNER_USER}`")
    # G2.4: its own folder and work folder.
    for path in (folder, f"{folder}/_work"):
        t = root_test(host, path)
        if t is None:
            rep.warn(f"{repo}: {path}: not read")
        elif not t and path == folder:
            bad("folder", f"the runner's folder {folder} is gone: {again}")
    # G2.8: the credential files are the runner's own, 600.
    files = [f"{folder}/.credentials", f"{folder}/.runner", f"{folder}/.credentials_rsaparams"]
    rc, out = sudo(host, ["stat", "-c", "%a %U %n"] + files)
    rows = [x.split(None, 2) for x in out.splitlines() if x.strip()]
    if "a password is required" in out:
        rep.warn(f"{repo}: the credential files: not read")
    elif rc != 0 or len(rows) != 3 or any(r[0] != "600" or r[1] != RUNNER_USER for r in rows):
        bad("credential files", f"the runner's credential files are not 600 and its own ({tail(out, 3)}): "
                                f"`sudo chmod 600` them, or {again}")
    else:
        rep.ok(f"{repo}: the credential files are 600, owned by {RUNNER_USER}")
    # G2.9, G4.2: the boot service as written, in our slice, and the slice's limits.
    rc, out = host.run(systemctl("show", unit, "-p", "Slice", "-p", "Nice", "-p", "CPUWeight", "-p", "Restart",
                                  "-p", "Wants", "-p", "UnitFileState", "-p", "User", "-p", "OOMPolicy"))
    got = dict(x.split("=", 1) for x in out.splitlines() if "=" in x)
    want = {"Slice": SLICE, "Nice": "10", "CPUWeight": "50", "Restart": "on-failure", "UnitFileState": "enabled",
            "User": RUNNER_USER, "OOMPolicy": "continue"}
    off = [f"{k}={got.get(k)!r}" for k, v in want.items() if got.get(k) != v]
    if "network-online.target" not in got.get("Wants", ""):
        off.append("Wants has no network-online.target")
    if off:
        bad("boot service", f"the boot service {unit or '(none recorded)'} is not as written: {', '.join(off)}: "
                            f"{again}")
    else:
        rep.ok(f"{repo}: the boot service is enabled, in {SLICE}, nice 10, restarts on failure, after the network")
    lim = mine.get("limits") or {}
    rc, out = host.run(systemctl("show", SLICE, "-p", "CPUQuotaPerSecUSec", "-p", "MemoryMax", "-p", "MemoryHigh"))
    sl = dict(x.split("=", 1) for x in out.splitlines() if "=" in x)
    want_l = {"CPUQuotaPerSecUSec": f"{lim.get('cores')}s", "MemoryHigh": str(lim.get("MemoryHigh")),
              "MemoryMax": str(lim.get("MemoryMax"))}
    off = [f"{k}={sl.get(k)!r}" for k, v in want_l.items() if sl.get(k) != v]
    if not lim:
        rep.warn(f"{repo}: the limits set up here: not recorded, so not compared")
    elif off:
        bad("limits", f"{SLICE}'s limits read {', '.join(off)}, not those set up ({want_l['CPUQuotaPerSecUSec']}, "
                      f"{want_l['MemoryHigh']}, {want_l['MemoryMax']}): `uninstall {repo}`, then `setup {repo} "
                      f"--limits {lim.get('cores')},{lim.get('pct')}`")
    else:
        rep.ok(f"{repo}: the limits read back: at most {plural(int(lim['cores']), 'processor')} and {lim['pct']} % of "
               f"the memory ({gb(int(lim['MemoryMax']))}), in {SLICE}")
    # G4.3, G4.4: the two job hooks, root's and read-only to the runner.
    d = hook_dir(repo)
    rc, out = sudo(host, ["stat", "-c", "%a %U", f"{d}/job-started.sh", f"{d}/job-completed.sh"])
    if "a password is required" in out:
        rep.warn(f"{repo}: the job hooks: not read")
    elif rc != 0 or [x.strip() for x in out.splitlines() if x.strip()] != ["755 root", "755 root"]:
        bad("job hooks", f"the job hooks are not root's and read-only to the runner ({tail(out)}): {again}")
    else:
        rep.ok(f"{repo}: the job hooks are root's and read-only to the runner")
    # G4.3: the disk floor.
    free = host.disk_free(folder) if folder else None
    free = free or host.disk_free(host.home())
    if free is None:
        rep.warn(f"{repo}: free space for the work folders: not read")
    elif free[0] < DISK_FLOOR:
        bad("disk", f"only {gb(free[0])} free for the work folders, under the {gb(DISK_FLOOR)} floor: the watch holds "
                    "the runner until there is more; free some space")
    else:
        rep.ok(f"{repo}: {gb(free[0])} free for the work folders, over the {gb(DISK_FLOOR)} floor")
    # G4.5: the version, GitHub's self-update on.
    runner_version(host, folder, rep)
    # G4.7: never two copies.
    listeners = runner_pids(host, folder, "Runner.Listener")
    if len(listeners) > 1:
        bad("one copy", f"{len(listeners)} copies of the runner run ({', '.join(map(str, listeners))}): "
                        f"`drain {repo} --stop`, then `resume {repo}`")
    # G2.6, G2.7, G2.10: GitHub's side and the service, with only our labels (or our hold's).
    s, r = read_runner(gh, repo, name)
    rc, active = host.run(systemctl("is-active", unit))
    active = active.strip() or "not read"
    if s != 200:
        rep.warn(f"{repo}: GitHub's list of runners: not read (HTTP {s})")
    elif r is None:
        bad("on GitHub", f"GitHub does not list {name}: `setup {repo}` registers it again in place")
    else:
        labels = sorted(x.get("name") for x in r.get("labels", []))
        held = bool(mine.get("held_for"))
        ok_labels = [HELD_LABEL] if held else sorted(mine.get("labels") or [])
        if labels != ok_labels:
            bad("labels", f"GitHub lists {name} with labels {', '.join(labels)}, not "
                          f"{', '.join(ok_labels)}" + (" (held)" if held else "") + f": `drain {repo}`, then "
                          f"`resume {repo}` puts ours back")
        if r.get("status") == "online" and active == "active":
            rep.ok(f"{repo}: both sides: GitHub lists {name} online, "
                   f"{'busy' if r.get('busy') else 'idle'}" + (f", held for {mine['held_for']}" if held else "")
                   + "; the service is active")
        elif mine.get("held_for") and active != "active":
            rep.ok(f"{repo}: held for {mine['held_for']} and stopped, as a drain leaves it: `resume {repo}` "
                   "starts it")
        else:
            bad("both sides", f"GitHub says {name} is {r.get('status')}; the service is {active}: "
                              f"`resume {repo}` starts it")
    return broken


def restore_runs_on(text: str, job_id: str, original: str) -> str | None:
    """G6.2: put one job's runs-on back as it was before `move`, by line; None when the job no longer asks for ours."""
    for job in read_workflow(text):
        if job["id"] != job_id or not job.get("runs_on_at"):
            continue
        labels = job.get("runs_on") if isinstance(job.get("runs_on"), list) else []
        if not any(x == "minions" or x.startswith("minions-") for x in labels):
            return None
        start, end = job["runs_on_at"]
        lines = text.splitlines(keepends=True)
        lines[start:end] = [original if original.endswith("\n") else original + "\n"]
        return "".join(lines)
    return None


def put_back(gh: GitHub, host: Host, repo: str, data: dict, rep: Report) -> None:
    """G6.2: one pull request that puts each moved runs-on back as it was before `move`. The tool never merges it."""
    mv = (data.get("moves") or {}).get(repo)
    if not mv:
        rep.ok(f"no job of {repo} was moved by this tool: no pull request is needed")
        return
    s, d = gh.api("GET", f"repos/{repo}")
    base = (d or {}).get("default_branch") if s == 200 else mv.get("base")
    files = dict(read_workflow_files(gh, repo, None, rep) or [])
    changed, back, unknown = {}, [], []
    for path, jobs in sorted((mv.get("files") or {}).items()):
        text = changed.get(path, files.get(path))
        if text is None:
            continue
        for j in jobs:
            if not j.get("runs_on"):
                unknown.append(f"`{path}` job `{j['id']}`")
                continue
            new = restore_runs_on(text, j["id"], j["runs_on"])
            if new is not None:
                text = new
                changed[path] = new
                back.append(f"- `{path}` job `{j['id']}`: `{j['runs_on'].strip()}`")
    if unknown:
        rep.warn(f"the line before `move` was not recorded for {', '.join(unknown)}: put it back by hand")
    if not changed:
        rep.ok(f"no workflow of {repo} still asks for our runner: no pull request is needed")
        return
    body = ("This pull request puts each `runs-on` line back as it was before Windstorm Repo Minions moved it, "
            "because its runner on your computer was removed. Nothing else changes. The tool that opened it never "
            "merges it: you do. Until you merge it, those jobs wait for a runner.\n\n## Put back\n" +
            "\n".join(back) + "\n")
    # G6.7 (P1a): said before the person meets it: opening a pull request starts its own checks where they now ask.
    rep.tell(f"The pull request that puts {len(back)} job(s) back starts its own checks as soon as it is opened, as "
             "any pull request does: a job put back on GitHub's machines whose workflow starts on pull requests runs "
             "there once for it, before you merge.")
    journal(host, "put-back pull request", repo)
    pr = open_move_pr(gh, host, repo, base, changed, body, rep, PUT_BACK_PR)
    if pr is None:
        return
    mv["put_back_pr"] = pr["number"]
    save_state(host, data)
    rep.need(f"read and merge pull request #{pr['number']} yourself ({pr['url']}): it puts {len(back)} job(s) back "
             "where they ran before; until then they wait for a runner")


def remove_shared(host: Host, rep: Report) -> bool:
    """G6.2: the watch, the slice (stopped first, G6.5), the tool's own folders, then the runner user."""
    ok = True
    for unit in (f"{WATCH_UNIT}.timer", f"{WATCH_UNIT}.service"):
        if exists_as_root(host, unit_path(unit)):
            journal(host, "remove", unit)
            sudo(host, systemctl("disable", "--now", unit))
            sudo(host, ["rm", "-f", unit_path(unit)])
    sudo(host, systemctl("daemon-reload"))
    for unit in (f"{WATCH_UNIT}.timer", f"{WATCH_UNIT}.service"):
        if not unit_gone(host, unit):
            rep.stop(f"{unit} is still there after its removal. Run the same command again: it finishes a removal that was not finished")
            ok = False
    if ok:
        rep.ok("the watch's timer and service are gone, read back")
    if not remove_slice(host, rep):
        return False  # the user stays, so `uninstall --all` run again finishes from here (G6.4)
    for path in (HOOKS_ROOT, RECORDS_ROOT):
        if exists_as_root(host, path):
            journal(host, "delete", path)
            sudo(host, ["rm", "-rf", "--one-file-system", path])
    if any(exists_as_root(host, x) for x in (HOOKS_ROOT, RECORDS_ROOT)):
        rep.stop(f"could not delete {HOOKS_ROOT} or {RECORDS_ROOT}. Run the same command again: it finishes a removal that was not finished")
        ok = False
    else:
        rep.ok(f"the tool's copy, the job hooks and the jobs' records are deleted ({HOOKS_ROOT}, {RECORDS_ROOT})")
    if host.run(["id", RUNNER_USER])[0] == 0:
        left = host.run(["pgrep", "-u", RUNNER_USER])[1].split()
        if left:
            rep.stop(f"processes of {RUNNER_USER} still run ({', '.join(left)}): the user is not deleted")
            return False
        journal(host, "userdel", RUNNER_USER)
        sudo(host, ["userdel", "-r", RUNNER_USER])
    if host.run(["id", RUNNER_USER])[0] == 0 or exists_as_root(host, f"/home/{RUNNER_USER}"):
        rep.stop(f"the user {RUNNER_USER} or its home is still there. Run the same command again: it finishes a removal that was not finished")
        return False
    rep.ok(f"the user {RUNNER_USER} and its home are deleted, read back")
    return ok


def remove_slice(host: Host, rep: Report) -> bool:
    """G6.5: a slice whose file is deleted stays loaded and active after a reload until it is stopped; so stop it
    first, then delete the file, reload, and read back that it is gone."""
    path = unit_path(SLICE)
    journal(host, "stop", SLICE)
    sudo(host, systemctl("stop", SLICE))
    if exists_as_root(host, path):
        journal(host, "delete", path)
        sudo(host, ["rm", "-f", path])
    sudo(host, systemctl("daemon-reload"))
    if not unit_gone(host, SLICE):
        rep.stop(f"{SLICE} is still loaded or active after its removal. Run the same command again: it finishes a removal that was not finished")
        return False
    rep.ok(f"{SLICE} was stopped before its file was deleted, and is gone, read back")
    return True


def census(gh: GitHub, host: Host, repos: list[str], everything: bool, rep: Report) -> None:
    """G6.3, INV6: what is left, each "none" or named. With --all every line should read none."""
    unread: list[str] = []

    def line(what: str, found: list[str], kept_why: str = "", start: int | None = None) -> None:
        missed = unread[start:] if start is not None else []
        if missed:  # R71: not read is a third answer, never "none"
            rep.warn(f"census: {what}: " + (", ".join(found) + "; " if found else "") + "not read, because admin "
                     f"rights are not open in this terminal: {', '.join(missed)} (the same command run in your own "
                     "terminal asks for the password and reads them)")
        elif not found:
            rep.ok(f"census: {what}: none")
        elif kept_why:
            rep.ok(f"census: {what}: {', '.join(found)} ({kept_why})")
        else:
            rep.warn(f"census: {what}: {', '.join(found)}")
    for repo in repos:
        s, d = gh.api("GET", f"repos/{repo}/actions/runners?per_page=100")
        if s != 200:
            rep.warn(f"census: runners on GitHub for {repo}: not read (HTTP {s})")
        else:
            names = [r.get("name") for r in d.get("runners", [])]
            line(f"runners on GitHub for {repo}", [f"{n} (not ours)" if not n.startswith("minions-") else n
                                                   for n in names])
    def there(path: str) -> bool:
        t = root_test(host, path)
        if t is None:
            unread.append(path)
        return bool(t)
    rc, out = host.run(systemctl("list-units", "--all", "--plain", "--no-legend", "actions.runner.*", "minions*"))
    units = sorted({x.split()[0] for x in out.splitlines() if x.strip() and not x.startswith("0 ")})
    mark = len(unread)
    files = [u for u in ("minions.slice", f"{WATCH_UNIT}.timer", f"{WATCH_UNIT}.service")
             if there(unit_path(u))]
    shared = {SLICE, f"{WATCH_UNIT}.timer", f"{WATCH_UNIT}.service"}
    # R78 (G1.12): a runner service that runs as neither our runner user nor the person is counted, never named,
    # as look does; and the slice is said to be kept for other runners only when runners of ours are left.
    named, others = [], 0
    for u in (u for u in units if u.startswith("actions.runner.")):
        urc, who = host.run(systemctl("show", "-p", "User", "--value", u))
        if urc == 0 and who.strip() in (RUNNER_USER, host.user()):
            named.append(u)
        else:
            others += 1
    if named or not others:
        line("runner services", named)
    if others:
        rep.warn(f"census: {plural(others, 'runner service')} here not running as you or as {RUNNER_USER} "
                 "(not named: they may be another person's; this tool did not make them)")
    ours_left = bool(load_state(host).get("runners"))
    line("the slice and the watch (scheduled checks)", sorted(set(u for u in units if u in shared) | set(files)),
         "" if everything else ("kept for this tool's other runners here; `uninstall --all` takes them" if ours_left
                                else "kept until `uninstall --all`, which takes them"), mark)
    rc, _ = host.run(["id", RUNNER_USER])
    line("the runner user", [RUNNER_USER] if rc == 0 else [],
         "" if everything else "kept; `uninstall --all` takes it")
    mark = len(unread)
    folders = [x for x in (HOOKS_ROOT, RECORDS_ROOT, f"/home/{RUNNER_USER}") if there(x)]
    line("folders", folders, "" if everything else "kept; `uninstall --all` takes them", mark)


def uninstall(repo: str | None, everything: bool = False, wait_s: int = DRAIN_WAIT_S, poll_s: float = 10,
           host: Host | None = None, gh: GitHub | None = None, out=None, interactive: bool | None = None) -> int:
    """G6: `uninstall <owner/repo>` takes one runner away on both sides; `uninstall --all` also takes the runner user, the
    watch, the slice, the tool's folders and `~/.minions/`, and opens a pull request that puts moved jobs back."""
    host = host or Host()
    gh = gh or GitHub(host)
    rep = Report(out)
    if repo and not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        rep.stop(f"not a repository name: {repo!r} (write owner/name)")
        return rep.code()
    data = load_state(host)
    adopt_unfinished(host, data, None if everything or not repo else {repo})
    runners = data.get("runners") or {}
    if repo and repo not in runners and not everything:
        rep.ok(f"no runner of ours serves {repo} on this computer: nothing to uninstall")
        census(gh, host, [repo], False, rep)
        return rep.code()
    if interactive is None:
        interactive = host.isatty()
    if not ask_admin_once(host, rep, interactive, "uninstall"):
        return rep.code()
    repos = [repo] if repo and not everything else sorted(runners)
    known = sorted(set(repos) | set(data.get("moves") or {}) | ({repo} if repo else set()))
    next_cmd = "uninstall --all" if everything else f"uninstall {repo}"
    for rp in repos:
        if not remove_one(gh, host, rp, data, rep, wait_s, poll_s, next_cmd):
            return rep.code()
    if everything:
        if not remove_shared(host, rep):
            return rep.code()
        for rp in sorted(data.get("moves") or {}):
            put_back(gh, host, rp, data, rep)
        census(gh, host, known, True, rep)
        journal(host, "delete", f"{host.state_dir()} (the last step)")
        shutil.rmtree(host.state_dir(), ignore_errors=True)
        if os.path.isdir(host.state_dir()):
            rep.stop(f"could not delete {host.state_dir()}. Run the same command again: it finishes a removal that was not finished")
        else:
            rep.ok(f"this tool's state ({host.state_dir()}) is deleted: nothing of Windstorm Repo Minions is left "
                   "on this computer")
        return rep.code()
    census(gh, host, known, False, rep)
    return rep.code()


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
    lim = slice_limits(s)
    floor = memory_floor_line(repo, lim)
    (rep.warn if floor else rep.ok)(floor or limits_in_words(lim))
    lives = what_lives_here(host, rep)
    s["serves"], s["serves_what"] = lives["serves"], lives["serves_what"]  # R72: setup reads it
    check_workflows(gh, repo, s, rep)
    before_setup_words(repo, s, lim, floor, rep)
    check_cost_rule(host, rep)
    return rep.code()


LONG_SLEEP_MIN = 10  # G7.9: GitHub failed a job about 10 minutes after the computer fell asleep (seen 2026-11-22)
LAPTOP_WORDS = ("a laptop takes jobs only while it is awake, on the network and (by default) on mains power; "
                "`resume <owner/repo> --on-battery-too` lets it take jobs on battery as well")


def before_setup_words(repo: str, s: dict, lim: dict, floor: str | None, rep: Report) -> None:
    """G7.10 (P1a run B): what the person must hear before setup, printed by the tool for the agent to pass on word
    for word. A list in the skill file was skipped by a small model; a line the tool prints is not."""
    rep.tell(f"Anyone who can push to {repo} can run code on this computer, as its own user {RUNNER_USER}, which "
             "has no admin rights.")
    rep.tell("This is for a private repository only: on a public one, a stranger's pull request could run code on "
             f"this computer. The tool refuses a public repository, and if {repo} is made public later, its runner "
             "stops taking jobs.")
    rep.tell("While this computer is off, asleep or held, your checks wait. GitHub cancels a check that has waited "
             f"{GITHUB_GIVES_UP_H} hours.")
    rep.tell("A restart of this computer cancels the job running on it. A short sleep only delays it; a sleep of more "
             f"than about {LONG_SLEEP_MIN} minutes fails it on GitHub (seen). While a job "
             "runs, this computer does not fall asleep by itself; a sleep you ask for, the lid, or a low battery still "
             f"puts it to sleep, {sleep_delay_words(s.get('sleep_delay'))}. `status` names a job cut that way as cut, "
             "not as your test failing.")
    rep.tell(floor or limits_in_words(lim))
    if s.get("battery"):
        rep.tell("This computer has a battery: " + LAPTOP_WORDS + ".")
    elif s.get("lid"):
        rep.tell("This computer has a lid: " + LAPTOP_WORDS + ".")
    rep.tell("This version serves one repository per computer; more come in the next version.")


class Parser(argparse.ArgumentParser):
    """G8.3: a command typed wrong exits 64, never 2 (2 is `stop`)."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        print(f"{self.prog}: the command was typed wrong: {message}", file=sys.stderr)
        raise SystemExit(EXIT_USAGE)


def parser() -> Parser:
    ap = Parser(prog="minions.py", description="Windstorm Repo Minions " + VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True, parser_class=Parser)
    lk = sub.add_parser("look", help="read the repository and this computer; change nothing")
    lk.add_argument("repo", help="owner/name of a private GitHub repository")
    lk.add_argument("--start-test", action="store_true",
                    help="with your yes: one job on a temporary branch, run on no machine, then deleted")
    st = sub.add_parser("setup", help="install one runner for one private repository on this computer (Linux)")
    st.add_argument("repo", help="owner/name; needs a look of it on this computer under an hour old")
    st.add_argument("--limits", type=parse_limits, metavar="PROCESSORS,MEMORY%",
                    help="what jobs here may use (default: half the processors and 60 %% of the memory)")
    st.add_argument("--beside-live-too", dest="beside_live", action="store_true",
                    help="your word to run jobs on a computer that serves something live")
    mv = sub.add_parser("move", help="open a pull request that moves the jobs this computer can take; never merges")
    mv.add_argument("repo", help="owner/name; needs a fresh look and a runner of ours online")
    mv.add_argument("--verify", action="store_true", help="after your merge: see one real run finish here")
    mv.add_argument("--include-job", action="append", default=[], metavar="FILE:JOB",
                    help="your word to move one job that publishes or deploys")
    sa = sub.add_parser("status", help="both sides of each runner, the queue, the watch, and jobs a cut or limit stopped")
    sa.add_argument("repo", nargs="?", help="owner/name (default: every runner of ours here)")
    sa.add_argument("--wait-warn-minutes", type=int, default=WAIT_WARN_S // 60,
                    help="warn first about a job waiting longer than this behind our hold")
    sa.add_argument("--rerun", type=int, metavar="JOB_ID", help="your word: run one job a cut or our limit stopped again, once")
    sub.add_parser("watch", help="one pass of the watch (its timer runs it every 5 minutes)")
    rs = sub.add_parser("resume", help="lift a hold so jobs land on this computer again")
    rs.add_argument("repo", help="owner/name")
    rs.add_argument("--private-again", action="store_true",
                    help="your word that a repository that was not private is private again")
    rs.add_argument("--on-battery-too", dest="on_battery", action="store_const", const="too",
                    help="a laptop takes jobs on battery too (by default it is held on battery)")
    rs.add_argument("--on-battery-held", dest="on_battery", action="store_const", const="held",
                    help="back to the default: held on battery")
    dr = sub.add_parser("drain", help="hold the runner and wait until no job runs here; nothing is cut")
    dr.add_argument("repo", nargs="?", help="owner/name (with --restart: default every runner here)")
    dr.add_argument("--restart", action="store_true", help="before a planned restart: hold every runner here, wait")
    dr.add_argument("--stop", action="store_true", help="then stop the runner's service (only after the drain)")
    dr.add_argument("--wait-minutes", type=int, default=DRAIN_WAIT_S // 60, help="how long to wait for a job to end")
    dc = sub.add_parser("doctor", help="read back every promise of the install here; finish or undo a half-done step")
    dc.add_argument("repo", nargs="?", help="owner/name (default: every runner here)")
    rm = sub.add_parser("uninstall", help="take a runner away on both sides; --all leaves nothing of this tool here")
    rm.add_argument("repo", nargs="?", help="owner/name (with --all: default every runner here)")
    rm.add_argument("--all", dest="everything", action="store_true",
                    help="also the runner user, the watch, the slice, the tool's folders and ~/.minions, and a pull "
                         "request that puts moved jobs back")
    rm.add_argument("--wait-minutes", type=int, default=DRAIN_WAIT_S // 60, help="how long to wait for a job to end")
    return ap


def main(argv: list[str] | None = None, host: Host | None = None, out=None, gh: GitHub | None = None) -> int:
    """G8.3's table: the command's own code (0, 2, 3, 4); 64 typed wrong; 1 the tool failed; 130 interrupted."""
    a = parser().parse_args(argv)
    host = host or Host()
    out = out or sys.stdout
    try:
        if a.cmd == "look":
            return look(a.repo, a.start_test, host=host)
        if a.cmd == "setup":
            return setup(a.repo, host=host, limits=a.limits, beside_live=a.beside_live)
        if a.cmd == "drain":
            if not a.repo and not a.restart:
                parser().error("drain needs owner/name, or --restart")
            return drain(a.repo, stop=a.stop, wait_s=max(0, a.wait_minutes) * 60, host=host, restart=a.restart)
        if a.cmd == "resume":
            return resume(a.repo, host=host, private_again=a.private_again, on_battery=a.on_battery)
        if a.cmd == "status":
            return status(a.repo, host=host, wait_warn_s=max(0, a.wait_warn_minutes) * 60, rerun=a.rerun)
        if a.cmd == "watch":
            return watch(host=host)
        if a.cmd == "move":
            return move(a.repo, verify=a.verify, include=a.include_job, host=host)
        if a.cmd == "doctor":
            return doctor(a.repo, host=host)
        if a.cmd == "uninstall":
            if not a.repo and not a.everything:
                parser().error("uninstall needs owner/name, or --all")
            return uninstall(a.repo, everything=a.everything, wait_s=max(0, a.wait_minutes) * 60, host=host, gh=gh,
                          out=out)
        return EXIT_USAGE
    except KeyboardInterrupt:
        step = last_journal_step(host)
        try:
            journal(host, "interrupted", f"by you, during: {step}")
        except OSError:
            pass
        print(f"stop interrupted by you during: {step}. Run the same command again; `doctor` finishes or undoes a "
              "half-done step", file=out, flush=True)
        return EXIT_INTERRUPTED
    except Exception as e:  # noqa: BLE001 -- G8.3: an error the tool did not expect still ends in one clear line
        print(f"stop the tool itself failed: {type(e).__name__}: {e}. The journal's last step: "
              f"{last_journal_step(host)}", file=out, flush=True)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
