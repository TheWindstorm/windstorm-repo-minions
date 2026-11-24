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

VERSION = "0.1.0"

# G8.3: every way a command can end. The worst line decides among 0, 2, 3 and 4.
EXIT_OK, EXIT_FAILED, EXIT_STOP, EXIT_NEED, EXIT_WAIT, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 3, 4, 64, 130
LINE_KINDS = ("ok", "warn", "stop", "need you", "waiting", "tell the person")
COMMANDS = ("look", "setup", "move", "status", "watch", "resume", "drain", "doctor", "uninstall")  # built so far, in G8.3's order
RUNNER_USER = "minions-runner"
# R143 (rule 2, part 1): the runner's own config.sh, at registration and at removal, runs with an environment this tool
# builds, by name: nothing of the person's shell crosses, whatever sudo on this computer is set to keep. LANG is
# C.UTF-8, and the tool writes it into the runner's .env itself, so a person's locale never reaches every job.
RUNNER_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
RUNNER_ENV = ("env", "-i", f"HOME=/home/{RUNNER_USER}", f"USER={RUNNER_USER}", f"LOGNAME={RUNNER_USER}",
              "SHELL=/bin/bash", f"PATH={RUNNER_PATH}", "LANG=C.UTF-8")
# R144 (rule 2, part 2): the tool's own gh calls go to github.com as the login gh saved, always, so setup (run from
# the person's shell) and the watch (run without it) act as the same account by the same road. These, from a shell,
# would move that road or that account; the tool's gh never gets them, and setup says which it found.
GH_NOT_HANDED = ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN")
# Where gh keeps its saved login moves with these two; the watch has neither, so the place setup read is kept in the
# tool's state and handed to every gh call of the tool as GH_CONFIG_DIR, setup's and the watch's alike.
GH_PLACE_SETTINGS = ("GH_CONFIG_DIR", "XDG_CONFIG_HOME")
# R160: every gh call of the tool runs as the watch runs when nobody is logged in: with no session bus, so a login gh
# keeps only in a keyring is not read by setup either (seen 2026-11-24: the watch, a system timer, never reaches it).
# An address that cannot connect, never "no bus in this shell": a library may look for a session's bus by itself.
NO_BUS = "unix:path=/nonexistent/windstorm-repo-minions-no-session-bus"
SLICE = "minions.slice"
DROPIN = "50-minions.conf"
LOOK_MAX_AGE_S = 3600
ADMIN_LIKE_GROUPS = {"sudo", "admin", "wheel", "docker", "root", "lxd", "libvirt"}
COST_URL = "https://docs.github.com/en/billing/concepts/product-billing/github-actions"
# G7.13 (R109): the block's cost line is a pointer only; the sentence read, with its date, is look's own line.
COST_POINTER = f"What GitHub charges for jobs that run on your own computers is GitHub's to say: read {COST_URL}"
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
        self.here = False  # G7.15: the block is read by the person on their own screen, not relayed by an agent
        self.stayed: dict = {}  # R136 (rule 8): what a leave left because this tool did not make it, for the census

    def _put(self, kind: str, text: str) -> None:
        self._flush_tells()
        self._print(f"{kind} {text}")

    def _flush_tells(self) -> None:
        """G7.10: the words for the person go out as one numbered block, "tell the person 1 of 7: ..." to "7 of 7",
        so a line dropped or merged in a relay shows to the agent and to the person (run B again: a small model
        shortened the block, dropped two lines and reworded two, under the word "verbatim")."""
        tells, self._tells = self._tells, []
        if not tells:
            return
        n = len(tells)
        span = f"1 of {n} to {n} of {n}" if n > 1 else "1 of 1"
        if self.here:  # G7.15: the person's own terminal, before anything changes
            self._print(f"----- read these {n} line{'s' if n > 1 else ''} before anything changes -----")
        else:  # G7.16 (R113): the instruction travels in the tool's own output, at the head and the foot
            self._print(f"----- the {n} numbered line{'s' if n > 1 else ''} below go to the person whole and word "
                        "for word, numbers included: paste them as they are -----")
        for i, text in enumerate(tells, 1):
            self._print(f"tell the person {i} of {n}: {text}")
        if not self.here:
            self._print(f"----- end of the lines for the person: paste {span} whole and unchanged; your own words "
                        "come only after this line -----")

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

def gh_config_dir(env, home: str) -> str:
    """R144: where gh keeps its saved login, by gh's own order: GH_CONFIG_DIR, then XDG_CONFIG_HOME/gh, then
    ~/.config/gh."""
    if env.get("GH_CONFIG_DIR"):
        return env["GH_CONFIG_DIR"]
    if env.get("XDG_CONFIG_HOME"):
        return os.path.join(env["XDG_CONFIG_HOME"], "gh")
    return os.path.join(home, ".config", "gh")


def c_language(env) -> dict:
    """R172 (seen, a stock Ubuntu 24.04 in German: id, stat, pgrep and sudo answered in German, and setup stopped at
    its first step): every program the tool reads runs in the C language, whatever the person's shell is set to, so
    the words it matches are the same in every language. LC_ALL wins over LANG and every LC_*; LANGUAGE goes (seen:
    LC_ALL=C.UTF-8 alone gave English, through sudo too). Nothing else of the environment changes."""
    out = {k: v for k, v in env.items() if k != "LANGUAGE"}
    out["LC_ALL"] = "C.UTF-8"
    return out


def gh_environ(env, config_dir: str) -> dict:
    """R144: the environment the tool's own gh runs with: the person's, without GH_NOT_HANDED, and with the one place
    its saved login is read from. Proxy and certificate settings stay as the person has them."""
    out = {k: v for k, v in c_language(env).items() if k not in GH_NOT_HANDED}
    out["GH_CONFIG_DIR"] = config_dir
    out["DBUS_SESSION_BUS_ADDRESS"] = NO_BUS  # R160
    return out


class Out(str):
    """R168 (family rule 30's last paragraph, "a warning is not part of the answer"): what a program wrote on standard
    output, the answer every parse reads, with what it wrote on standard error kept beside it (`err`). A warning on
    standard error (seen: "sudo: unable to resolve host NAME: ...", the command run and its exit passed on) never
    reaches a parse. Both streams are read only through `said()`: the words shown at a refusal, and the few answers a
    program writes on standard error, each named where it is read."""
    err: str

    def __new__(cls, out: str = "", err: str = ""):
        o = super().__new__(cls, out)
        o.err = err
        return o


def said(out: str) -> str:
    """R168: both streams of a run (standard output, then standard error): for words shown to the person, and for an
    answer a program gives on standard error (named at each use). A plain string is itself."""
    err = getattr(out, "err", "")
    if not err:
        return str(out)
    return str(out) + ("" if not out or out.endswith("\n") else "\n") + err


class Host:
    """The computer, read through these few calls so tests can stand in for it."""

    def which(self, name: str) -> str | None:
        return shutil.which(name)

    def environ(self):
        """The environment this run of the tool was started with (the person's shell, or the watch's unit)."""
        return os.environ

    def gh_env(self) -> dict:
        """R144: every gh call of the tool reads the saved login from the same place: the one setup or look kept in
        the tool's state, else where this environment says gh keeps it."""
        kept = getattr(self, "gh_place", None) or (load_state(self).get("gh") or {}).get("config_dir")
        env = self.environ()
        return gh_environ(env, kept or gh_config_dir(env, self.home()))

    def run(self, argv: list[str], timeout: int = 20) -> tuple[int, str]:
        """R168: the answer is what the program wrote on standard output; standard error is kept beside it (Out).
        R172: in the C language, so the words read are the same whatever the person's language."""
        env = self.gh_env() if argv[:1] == ["gh"] else c_language(os.environ)  # R144
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL,
                               env=env)
            return p.returncode, Out(p.stdout or "", p.stderr or "")
        except (OSError, subprocess.SubprocessError) as e:
            return 127, Out("", str(e))

    def run_own(self, argv: list[str], timeout: int = 20) -> tuple[int, str]:
        """R160: one read only, with this run's own environment unchanged (the person's shell, its session bus too):
        where gh keeps a login the tool's own gh cannot read. Never a call whose answer the tool acts on."""
        try:
            p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            return p.returncode, Out(p.stdout or "", p.stderr or "")
        except (OSError, subprocess.SubprocessError) as e:
            return 127, Out("", str(e))

    def run_in(self, argv: list[str], input: str, timeout: int = 600) -> tuple[int, str]:
        """Like run(), with `input` on standard input: the only way a secret reaches another program (INV2)."""
        env = self.gh_env() if argv[:1] == ["gh"] else c_language(os.environ)  # R144, R172
        try:
            p = subprocess.run(argv, input=input, capture_output=True, text=True, timeout=timeout, env=env)
            return p.returncode, Out(p.stdout or "", p.stderr or "")
        except (OSError, subprocess.SubprocessError) as e:
            return 127, Out("", str(e))

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

    def there(self, path: str) -> bool | None:
        """As this user, with no admin rights: True, False, or None when it could not be told (rule 6)."""
        try:
            os.stat(path)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            return None

    def listdir_or_none(self, path: str) -> list[str] | None:
        """[] when the folder is not there; None when it could not be read (rule 6: never read as empty)."""
        try:
            return sorted(os.listdir(path))
        except FileNotFoundError:
            return []
        except OSError:
            return None

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

    def record_dir(self) -> str:
        """G6.10, INV6: the person's record of what this tool did, kept by `uninstall` in a folder they own."""
        return os.path.join(self.home(), RECORD_NAME)

    def keep_in_record(self, name: str, text: str, append: bool = True) -> None:
        """Adds `text` to one plain-text file of the record. Raises OSError when it cannot."""
        os.makedirs(self.record_dir(), mode=0o700, exist_ok=True)
        with open(os.path.join(self.record_dir(), name), "a" if append else "w", encoding="utf-8") as f:
            f.write(text)

    def record_count(self) -> tuple[int, int] | None:
        """(files, lines) in the record; (0, 0) when there is none; None when it could not be read (rule 6)."""
        d = self.record_dir()
        if not os.path.lexists(d):
            return 0, 0
        try:
            files = [os.path.join(d, x) for x in sorted(os.listdir(d))]
            lines = 0
            for x in files:
                with open(x, encoding="utf-8", errors="replace") as f:
                    lines += sum(1 for _ in f)
            return len(files), lines
        except OSError:
            return None

    def delete_record(self) -> list[str]:
        """R129 (rule 23): only what this tool wrote, by its names; the folder only when empty. Returns the names
        left there that are not this tool's."""
        return delete_ours(self.record_dir(), RECORD_NAMES)

    def ask(self, prompt: str) -> str:
        """A word typed by the person at this terminal; "" when nothing could be read."""
        try:
            return input(prompt)
        except (EOFError, OSError):
            return ""

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

    def read_enter(self) -> bool:
        """G7.15: the person presses Enter in their own terminal; False when nothing can be read (no key, closed)."""
        try:
            return sys.stdin.readline() != ""
        except (OSError, ValueError):
            return False

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

    def login_gate(self) -> str:
        """R160 (owner's ruling 11-24): an answer GitHub gives to a call sent without a login is never read as
        anything (a private repository answers 404 to it). Read once per command, before the first call: "" when
        the tool's gh has a login it can read, else why not, and no call is sent."""
        host = getattr(self, "host", None)
        if host is None:
            return ""
        if getattr(self, "_gate", None) is None:
            kept, why = login_kept(host)
            self._gate = "" if kept == "yes" else why
        return self._gate

    def api(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        gate = self.login_gate()
        if gate:
            self.why = gate
            return 0, {"message": gate}
        if self.limited():  # G8.14: no call while the limit is spent; each one would only be refused
            self.why = limit_words(self.limited_until)
            return 429, {"message": self.why}
        # R144: github.com, as the login gh saved, whatever the shell exports (GH_HOST, GH_TOKEN, ...)
        argv = ["gh", "api", "-i", "-X", method, path, "-H", "Accept: application/vnd.github+json",
                "--hostname", "github.com"]
        stdin = None
        if body is not None:
            argv += ["--input", "-"]
            stdin = json.dumps(body)
        try:
            p = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=60,
                               env=self.host.gh_env() if getattr(self, "host", None) is not None
                               else gh_environ(os.environ, gh_config_dir(os.environ, os.path.expanduser("~"))))
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

# Rule 7 (11-23), G8.16: a command printed for the person has been run exactly as printed on that kind of system
# (PRINTED_COMMANDS says where). Elsewhere the line says what is missing, what that means, and where to read.
INSTALL_GH = {
    "debian": "sudo apt update && sudo apt install gh",  # P1a: a fresh cloud image has no package lists yet
}
GH_DOCS = "https://github.com/cli/cli#installation"
# G8.16, the owner's ruling (11-23): `gh auth refresh -s workflow` acts only on a login made in a browser, so it was
# never run as printed here; the line says what is missing and names GitHub's own manual page for adding a permission.
GH_SCOPE_DOCS = "https://cli.github.com/manual/gh_auth_refresh"
# G8.16 (R108): every system command this tool prints for the person, with where it was run exactly as printed (its
# prompts answered by hand). Empty: not yet run as printed; its version is refused (test_strand) until it is, or the
# line is replaced by what is missing and where to read. Our own subcommands are run by the tests themselves.
PRINTED_COMMANDS = {
    "sudo apt update && sudo apt install gh":
        "2026-11-23, stock Ubuntu 22.04.5 (gh 2.4.0), Debian 12 (gh 2.23.0) and Ubuntu 24.04.5 (gh 2.45.0) virtual "
        "machines, typed as printed by the person, sudo's password typed at its own prompt",
    "gh auth login":
        "2026-11-23, the same three stock virtual machines: run as printed up to GitHub's one-time code, gh's own "
        "questions answered with their first choice; the login itself is the person's own act and was not done",
    "gh auth login --insecure-storage":  # R160
        "2026-11-24, a stock Ubuntu 24.04.5 virtual machine (gh 2.45.0, whose own help names the flag): run as "
        "printed up to GitHub's one-time code, gh's own questions answered with their first choice; the login itself "
        "is the person's own act and was not done",
    "sudo -v": "2026-11-23, a stock Ubuntu 24.04.5 virtual machine, as printed, the password typed at sudo's prompt",
    "journalctl -u {}.service": "2026-11-23, a stock Ubuntu 24.04.5 virtual machine, as printed, as the person",
    "sudo systemctl start {}.timer":
        "2026-11-23, a stock Ubuntu 24.04.5 virtual machine, steered (the watch's timer stopped by hand): as printed "
        "by status once the watch was silent 15 minutes; the timer active again",
    "chmod 750 {}":
        "2026-11-23, a stock Debian 12 virtual machine: copied from setup's own line (chmod 750 /home/debian) and run "
        "as printed; setup again then went through",
    "sudo chmod 750 /home/{}":
        "2026-11-23, a stock Ubuntu 24.04.5 virtual machine, steered (the runner's home opened to 755 by hand): copied "
        "from doctor's line and run as printed; doctor then read it kept",
    "sudo -u {} chmod 600 {}":
        "2026-11-24, a stock Ubuntu 24.04.5 virtual machine, steered (one credential file made 644 by hand, as the "
        "runner user): doctor's line with the three files, run as printed; doctor then read every promise kept",
    "sudo gpasswd -d {} {}":
        "2026-11-23, a stock Ubuntu 24.04.5 virtual machine, steered (the runner user put in a group by hand): "
        "doctor's line run as printed; doctor then read it kept",
    "sudo kill {}":
        "2026-11-23, a stock Ubuntu 24.04.5 virtual machine, steered (the runner's unit blanked in the state and its "
        "service file moved aside by hand): uninstall's line with the one PID, run as printed; no process of it left",
    # G7.15 (R112): the command the person runs in their own terminal, which shows their lines and waits for Enter.
    "python3 -I {} setup {}{}":
        "2026-11-23, a stock Ubuntu 24.04.5 virtual machine: printed by setup in the agent's shell, passed on by the agent "
        "and run as printed by the person in their own terminal; its 11 lines shown, Enter pressed, the password typed at "
        "sudo's prompt, and setup went to the end",
    "python3 -I {} uninstall {}{}":
        "2026-11-23, the same machine: printed by uninstall in the agent's shell (as uninstall --all), run as printed in "
        "the person's own terminal; its 3 lines shown, Enter pressed, the password typed at sudo's prompt, and uninstall "
        "went to the end",
    "python3 -I {} uninstall --all":
        "2026-11-23, a stock Ubuntu 24.04.5 virtual machine: status, run from the tool's copy with the skill's folder "
        "deleted, printed it; run as printed, uninstall went to the end",
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


def check_github_cli(host: Host, rep: Report, repo: str | None = None) -> bool:
    """G1.10: the first check. Without the GitHub CLI and a login, nothing else runs."""
    if not host.which("gh"):
        key = "Darwin" if host.system() == "Darwin" else os_release_id(host)
        cmd = INSTALL_GH.get(key)
        rep.need((f"install the GitHub CLI: `{cmd}`. " if cmd else
                  "install the GitHub CLI: it is not installed here, and without it nothing of this skill runs. This "
                  f"tool has seen no install command run on this kind of system, so it names none: read {GH_DOCS}. ")
                 + GH_ONLY)
        return False
    return check_gh_road(host, rep, repo)


def login_read(host: Host) -> tuple[str, str | None, str]:
    """G1.18 (R146, family rule 6: AN EXIT CODE CAN SAY "NO" FOR "COULD NOT ASK"): whether gh's saved login for
    github.com works, in three answers, from one `gh api -i user`. `gh auth status` cannot tell them apart: seen on
    Ubuntu's own gh 2.45.0, a saved login with GitHub unreachable gave exit 0 and "Failed to log in"; seen on gh
    2.105.0, it gave exit 1, the same exit as no saved login at all.
    ("yes", the account's name or None, "") when GitHub answered 200;
    ("no", None, why) when GitHub refused the login (401) or gh said it has none (exit 4 and no HTTP status line);
    ("unread", None, gh's last lines) for anything else: no answer from GitHub, a timeout, another status."""
    rc, out = host.run(["gh", "api", "-i", "user", "--hostname", "github.com"], timeout=60)
    status, data = parse_include(out)
    if status == 200:
        name = data.get("login") if isinstance(data, dict) else None
        return "yes", name if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9-]+", name) else None, ""
    if status == 401:
        return "no", None, "GitHub refuses the login gh saved for github.com (HTTP 401)"
    if not status and rc == 4:
        return "no", None, "gh has no saved login for github.com"
    # gh's own lines about logging in, out or refreshing are not passed on: this answer is "could not ask", not "no"
    words = [x.strip() for x in said(out).splitlines()  # R168: words shown, gh's standard error too
             if x.strip() and not re.search(r"gh auth|log ?in|log ?out|refresh", x, re.I)]
    return "unread", None, (f"HTTP {status}: " if status else "") + (" / ".join(words[-2:]) or f"gh exit {rc}, no words")


NO_LOGIN = "could not read your GitHub login"
TOKEN_SHAPE = re.compile(r"gh[opsur]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+")


def login_kept(host: Host) -> tuple[str, str]:
    """R160 (rule 11, rule 6): whether the tool's own gh, run as the watch runs when nobody is logged in (no session
    bus: NO_BUS), has a login for github.com. Three answers, from `gh auth token`: ("yes", "") a token came back;
    ("no", why) gh says it has none; ("unread", why) anything else. The token is never kept, printed or copied: only
    whether one line came back is read. gh older than 2.17 has no `auth token` and keeps a login only in its file or
    the shell (a keyring came in 2.26), so there its file is read for whether a token line is there."""
    rc, out = host.run(["gh", "auth", "token", "--hostname", "github.com"])
    lines = [x.strip() for x in out.splitlines() if x.strip()]
    if rc == 0 and len(lines) == 1 and re.fullmatch(r"\S+", lines[0]):
        return "yes", ""
    if re.search(r"no oauth token found", said(out), re.I):  # R168: gh's refusal words are on its standard error
        return "no", (f"{NO_LOGIN}: gh has none for github.com that it can read when nobody is logged in (a login "
                      "kept only in a keyring, or only in a shell's GH_TOKEN, cannot be read then)")
    if re.search(r'unknown command "token"', said(out)):
        return hosts_file_login(host)
    words = [x for x in lines if not TOKEN_SHAPE.search(x)]
    if rc == 127:  # Host.run: gh not found with this PATH, or it could not be run at all
        return "unread", ("the GitHub CLI (gh) could not be found or run with this PATH, so it could not be asked "
                          "for its login: " + (" / ".join(words[-1:]) or "no words"))
    return "unread", ("gh could not be asked whether it has a login for github.com: "
                      + (" / ".join(words[-2:]) or f"gh exit {rc}, no words"))


def hosts_file_login(host: Host) -> tuple[str, str]:
    """R160: for a gh with no `auth token`: is a token line under github.com in gh's own file? Only whether one is
    there is read; its value is never kept."""
    path = os.path.join(host.gh_env().get("GH_CONFIG_DIR") or gh_config_dir(host.environ(), host.home()), "hosts.yml")
    text = host.read(path)
    if text is None:
        return "unread", f"gh could not be asked whether it has a login for github.com, and {path} could not be read"
    here = False
    for line in text.splitlines():
        if line and not line[0].isspace():
            here = line.split(":", 1)[0].strip().strip("'\"") == "github.com"
        elif here and re.match(r"\s+oauth_token:\s*\S", line):
            return "yes", ""
    return "no", f"{NO_LOGIN}: {path} holds no login for github.com"


def login_where(host: Host) -> tuple[str, str]:
    """R160: where gh keeps the person's login, from gh's own word in `gh auth status` run in the person's own
    environment (run_own), and the scopes it names. Words only; nothing of it is acted on."""
    rc, out = host.run_own(["gh", "auth", "status", "--hostname", "github.com"])
    out = said(out)  # R168, on purpose: gh 2.4.0 and 2.23.0 print `auth status` on standard error, newer gh on output
    m = re.search(r"Token scopes:\s*(.+)", out)
    scopes = ", ".join(re.findall(r"'([^']+)'", m.group(1)) or [x.strip() for x in m.group(1).split(",") if x.strip()]) \
        if m else ""
    env = re.search(r"\((GH_TOKEN|GITHUB_TOKEN|GH_ENTERPRISE_TOKEN|GITHUB_ENTERPRISE_TOKEN)\)", out)
    if "(keyring)" in out:
        where = "gh keeps it in this computer's keyring, which only a logged-in session can open"
    elif env:
        where = f"gh reads it from your shell's {env.group(1)}, which the check every 5 minutes does not have"
    else:
        where = "where gh keeps it could not be read here"
    return where, scopes


def login_step(host: Host) -> str:
    """R160: the one login step that keeps the login in gh's own file, as printed (with its backticks):
    `--insecure-storage` where this gh has that flag, read from gh's own help now; a gh without it has no keyring
    and keeps every login in that file already."""
    rc, out = host.run(["gh", "auth", "login", "--hostname", "github.com", "--help"])
    out = said(out)  # R168, on purpose: the flag's name is looked for in gh's help, whichever stream it is on
    return "`gh auth login --insecure-storage`" if "--insecure-storage" in out else "`gh auth login`"


def keyring_step_words(host: Host, repo: str | None, rep: Report) -> None:
    """R160 (owner's ruling 11-24, option A): the stop, the one step, and the six things the person hears before
    choosing it (the git question's line added by the owner's ruling of 11-24, as seen on a desktop login). This tool
    made nothing when this is said."""
    where, scopes = login_where(host)
    hosts = os.path.join(host.gh_env().get("GH_CONFIG_DIR") or gh_config_dir(host.environ(), host.home()), "hosts.yml")
    again = f"`look {repo}`" if repo else "the same command"
    rep.stop(f"{NO_LOGIN} the way this computer's check every 5 minutes will read it, when nobody is logged in: "
             f"{where}. This tool made nothing, on this computer or on GitHub. Your next step, in your own terminal, if you "
             f"choose it after the lines below: {login_step(host)}, then {again} again")
    rep.tell(f"What that step does: gh then keeps your GitHub login in {hosts}, as plain text, which your account and "
             "this computer's admins can read.")
    rep.tell("What that login can do: everything gh was allowed on your GitHub account"
             + (f" (its scopes: {scopes})" if scopes else "") + ", on every repository of yours, not only this one.")
    rep.tell("Where else it then is: in every copy or backup of your home folder, and on this computer's disk for "
             "whoever gets the disk, if the disk is not encrypted.")
    rep.tell("Why it is needed: the check every 5 minutes runs when nobody is logged in, and then only that file "
             "can be read.")
    rep.tell('If gh asks "Authenticate Git with your GitHub credentials?": this tool does not need it. Answered Yes '
             "(gh's first choice), gh also makes itself git's helper for github.com and gist.github.com, two settings "
             "in ~/.gitconfig, and git on this computer then uses this login too; answered No, gh leaves git as it is.")
    rep.tell("If you would rather not, stop here: this tool made nothing, and this computer will not run jobs.")


def check_gh_road(host: Host, rep: Report, repo: str | None = None) -> bool:
    """G1.17 (R144, rule 2): the tool's gh goes to github.com as the login gh saved, the same for setup (run from the
    person's shell) and the watch (run without it). Names the account it acts as; what of the shell it found and does
    not use, and why; where the saved login is read, kept for the watch. No saved login for github.com: need you."""
    env = host.environ()
    found = [n for n in GH_NOT_HANDED if env.get(n)]
    place = gh_config_dir(env, host.home())
    moved = [n for n in GH_PLACE_SETTINGS if env.get(n)]
    host.gh_place = place  # R160: this run's gh reads it here; the record keeps it only after the reads below pass
    unused = (f" Found in your shell and not used by this tool: {', '.join(found)}. The watch runs without your "
              "shell, so this tool's every GitHub call, setup's and the watch's alike, goes to github.com as the login "
              "gh saved; your own use of gh is unchanged.") if found else ""
    kept, why = login_kept(host)  # R160: first, read as the watch reads it; nothing is asked of GitHub before it
    if kept == "no":
        keyring_step_words(host, repo, rep)
        return False
    if kept == "unread":
        rep.stop(f"{why}. This tool made nothing, on this computer or on GitHub. Settle what it says, then run the same "
                 "command again")
        return False
    answer, name, why = login_read(host)
    if answer == "no":
        rep.need(f"log in to GitHub: run `gh auth login` and follow its steps ({why}). {GH_ONLY}{unused}")
        return False
    if answer == "unread":
        rep.stop(f"GitHub could not be asked whether gh's saved login for github.com works: {why}. Nothing was "
                 f"changed; run the same command again.{unused}")
        return False
    # R160: kept only once every login read has passed, so a stop above has made nothing on this computer
    data = load_state(host)
    if (data.get("gh") or {}).get("config_dir") != place:
        data["gh"] = dict(data.get("gh") or {}, config_dir=place)
        try:
            save_state(host, data)
        except OSError as e:
            rep.stop(f"could not keep where gh saves its login ({place}) in {host.state_dir()}: {e}. The watch runs "
                     "without your shell and would read another place; nothing was changed. Settle what it says, then "
                     "run the same command again")
            return False
    who = f"as {name}, the login gh saved for github.com" if name else \
        "as the login gh saved for github.com (its name could not be read from gh's answer)"
    where = (f", read from {place} (your shell's {' and '.join(moved)}; the watch reads it there too)"
             if moved else "")
    rep.ok(f"GitHub CLI installed and logged in: this tool acts {who}{where}")
    if found:
        rep.warn(unused.strip())
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
        rep.need("your login may not change workflow files (it lacks GitHub's `workflow` permission), so a workflow "
                 "cannot be moved to your runner until it has it. Adding a permission to your login is your step, in "
                 f"your own terminal: read {GH_SCOPE_DOCS}", blocks=False)
    return data


def token_scopes(host: Host) -> set[str] | None:
    """The scopes `gh auth status` shows for github.com; None when it shows none (for example a fine-grained token).
    Only the scope names are read from that output; nothing else of it is kept or shown."""
    rc, out = host.run(["gh", "auth", "status", "--hostname", "github.com"])
    if rc != 0:
        return None
    out = said(out)  # R168, on purpose: gh 2.4.0 and 2.23.0 print `auth status` on standard error, newer gh on output
    m = re.search(r"Token scopes:\s*(.+)", out)
    if not m:
        # R97: an older gh (2.4.0 on Ubuntu 22.04) prints no scopes; GitHub itself sends them with every answer to a
        # classic token, in the X-OAuth-Scopes header. Only that header's names are read; a token without it
        # (fine-grained) stays "could not read".
        rc, out = host.run(["gh", "api", "-i", "user", "--hostname", "github.com"])
        head = out.split("\r\n\r\n", 1)[0].split("\n\n", 1)[0] if rc == 0 else ""
        h = re.search(r"^X-Oauth-Scopes:[ \t]*(.*?)\r?$", head, re.M | re.I)
        if not h:
            return None
        return {x.strip() for x in h.group(1).split(",") if x.strip()}
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
        if host.which("systemctl") and systemd_runs_here(host) is False:  # R155: look says it before setup is asked
            rep.warn(no_systemd_words("`setup` stops before it changes anything."))
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
        # R167 (rule 30, ONE CALL, ONE UNIT): each target read by a call of its own
        states = [(host.run(systemctl("is-enabled", t))[1].split() or [""])[0] for t in ("sleep.target", "suspend.target")]
        if all(x == "masked" for x in states):
            return "off (sleep and suspend masked)"
        if any(states):
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


def what_lives_here(host: Host, rep: Report, show_names: bool = False) -> dict:
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
        # G1.16, rule 18: their names may be the person's projects; counted, named only on `--show-names`.
        rep.warn(f"{len(out['containers'])} container(s) of yours running: " + (
            ', '.join(out['containers'][:20]) if show_names else "not named here (`look <owner/repo> --show-names` names them)"))
    if out["shared_containers"]:
        rep.warn(f"{out['shared_containers']} container(s) running on this computer's shared container service "
                 "(not named: they may be another person's)")
    if out["ours"]:
        rep.ok(f"this tool's own runner here: {', '.join(out['ours'])}")
    if out["runners"]:
        rep.warn(f"{len(out['runners'])} other runner service(s) of yours here: " + (
            ', '.join(out['runners'][:10]) if show_names else "not named here (`look <owner/repo> --show-names` names them)"))
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


def check_cost_rule(host: Host, rep: Report) -> str:
    """G1.9: GitHub's own sentence, with the date read, as look's own line; never a guess. Returns the block's line
    (G7.13: a pointer only), said by the caller as the block's last line."""
    when = host.now().strftime("%Y-%m-%d %H:%M UTC")
    try:
        items = read_cost_rule(host.fetch(COST_URL))
    except Exception:  # noqa: BLE001 - any failure to read is said as such
        items = None
    if items and any(x.startswith("Self-hosted runners") for x in items):
        keep_cost_read(host, items)  # G5.10: the watch compares against it once a week
    if not items or not any(x.startswith("Self-hosted runners") for x in items):
        rep.warn(f"I could not read GitHub's rule today ({when}): see {COST_URL}")
        return "I could not read GitHub's page about charges today."
    rep.ok(f"GitHub's billing page, read {when}, lists \"Self-hosted runners\" among the uses that do not reduce "
           "the included usage (its words: \"" + COST_LIST_HEAD + " " + " ".join(items) + "\"). In December 2025 "
           "GitHub announced a charge for self-hosted runner use and postponed it. Artifact and cache storage "
           f"count the same whichever computer ran the job. Source: {COST_URL}")
    return COST_POINTER


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
    except (OSError, ValueError):
        return {}
    if not isinstance(d, dict):
        return {}
    # R167 (owner 3:51 PM ET 11-24, answer 5): a runner's folder is its repository's own (setup's rule, and every
    # record the tool writes carries it); a record that lost it (a hand edit) gets it back here, so no road reads
    # processes or paths with an empty folder
    for key in ("runners", "setting_up"):
        for repo, mine in (d.get(key) or {}).items() if isinstance(d.get(key), dict) else ():
            if isinstance(mine, dict) and not mine.get("folder") and re.fullmatch(r"[^/]+/[^/]+", str(repo)):
                mine["folder"] = runner_folder(repo)
    return d


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
    if (rep.stopped or rep.blocked) and not os.path.exists(state_path(host)):
        return  # R160: a look cut short (a stop, or a step of the person's that nothing may pass) on a computer that
        # never held the record makes none; setup then asks for a look, as it does when none was run
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
    the processors, never less than one (G4.8), and memory max at 60 % with no high mark and no swap for jobs
    (run26: a job held at a high mark with swap to fall into crawls; at the max alone with no swap it fails at once,
    by name). `setup --limits` (G7.8) chooses other shares."""
    total = survey_.get("cores") or 2
    mem = survey_.get("mem") or 4 * 1024 ** 3
    cores, pct = chosen if chosen else (total // 2, DEFAULT_MEMORY_PCT)
    cores = max(1, min(cores, total))
    pct = max(10, min(pct, MEMORY_PCT_MOST))
    return {"CPUQuota": f"{cores * 100}%", "MemoryHigh": "infinity", "MemorySwapMax": "0",
            "MemoryMax": str(int(mem * pct / 100)), "cores": cores, "total_cores": total, "pct": pct, "mem": mem}


def limits_in_words(lim: dict) -> str:
    """G7.8: the limits as the person hears them, with the real numbers and the one way to change them."""
    total = lim["total_cores"]
    words = (f"jobs here get at most {lim['cores']} of this computer's {plural(total, 'processor')} and "
             f"{lim['pct']} % of its memory ({gb(int(lim['MemoryMax']))}); the one way to change that is "
             "`setup <owner/repo> --limits <processors>,<memory %>`")
    if total == 1:  # the owner's ruling, 2026-11-23: warned plainly, never refused, never hidden by a rounding
        words += ("; this computer has one processor: while a job runs it uses all of it, at low priority, and "
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


RECORD_NAMES = r"README\.txt|journal\.log|jobs-[A-Za-z0-9_.-]+\.log"
STATE_NAMES = r"minions\.json(\.tmp)?|journal\.log|actions-runner-linux-[a-z0-9]+-[0-9.]+\.tar\.gz"


def delete_ours(folder: str, names: str) -> list[str]:
    """R129 (family rule 23): in a folder of our own name, only what this tool wrote, file by file by its exact name
    format; the folder itself only when then empty. A thing there that is not ours is left, and named back."""
    try:
        entries = os.listdir(folder)
    except FileNotFoundError:
        return []
    except OSError:
        return [folder]
    left = []
    for n in sorted(entries):
        p = os.path.join(folder, n)
        if re.fullmatch(names, n) and os.path.isfile(p) and not os.path.islink(p):
            try:
                os.remove(p)
            except OSError:
                left.append(n)
        else:
            left.append(n)
    if not left:
        try:
            os.rmdir(folder)
        except OSError:
            return [folder]
    return left


UNIT_RE = r"actions\.runner\.[A-Za-z0-9_.@-]+\.service"


def unit_name(folder: str, runner_name: str) -> str:
    """Rule 28 (R127): the tool names and writes the runner's unit itself, by the runner's own convention, from parts
    it chose (the folder's name and the runner's name); root never runs the runner's svc.sh."""
    base = os.path.basename(folder.rstrip("/"))
    return re.sub(r"[^A-Za-z0-9_.@-]", "-", f"actions.runner.{base}.{runner_name}.service")


def unit_text(folder: str, unit: str) -> str:
    """GitHub's own unit template (actions.runner.service.template), filled in here: the runner runs as the runner
    user from its own folder; root owns this file and no job can change it. One change from GitHub's template
    (R131): KillMode=mixed, not process: at a stop the runner alone gets SIGTERM, and whatever a job left running in
    the service (nohup, setsid, a double fork) is ended once the runner has gone, never left behind."""
    what = unit[len("actions.runner."):-len(".service")]
    return ("# Windstorm Repo Minions (R127): GitHub's runner unit, written by this tool, owned by root.\n"
            f"[Unit]\nDescription=GitHub Actions Runner ({what})\nAfter=network-online.target\n\n"
            f"[Service]\nExecStart={folder}/runsvc.sh\nUser={RUNNER_USER}\nWorkingDirectory={folder}\n"
            "KillMode=mixed\nKillSignal=SIGTERM\nTimeoutStopSec=5min\n\n[Install]\nWantedBy=multi-user.target\n")


def slice_text(limits: dict) -> str:
    return ("# Windstorm Repo Minions: every runner of ours on this computer, together (G2.9, G4.2, G12.1).\n"
            "[Unit]\nDescription=Windstorm Repo Minions runners\nBefore=slices.target\n\n"
            f"[Slice]\nCPUQuota={limits['CPUQuota']}\nMemoryHigh={limits['MemoryHigh']}\nMemoryMax={limits['MemoryMax']}\n"
            f"MemorySwapMax={limits.get('MemorySwapMax', 'infinity')}\n")


def root_file(host: Host, path: str, text: str, mode: str = "0644") -> tuple[int, str]:
    """R134: a file root writes for this tool, then given its owner and mode by name (root:root, 0644 unless said),
    never left to whatever umask the write ran under. The first answer that is not 0 is returned."""
    for argv, inp in ((["tee", path], text), (["chown", "root:root", path], None), (["chmod", mode, path], None)):
        rc, out = sudo(host, argv, input=inp)
        if rc != 0:
            return rc, out
    return 0, ""


def sudo(host: Host, argv: list[str], input: str | None = None, timeout: int = 600) -> tuple[int, str]:
    """An admin step: always `sudo -n` (the password was asked once, before the first step)."""
    full = ["sudo", "-n"] + argv
    rc, out = host.run_in(full, input, timeout) if input is not None else host.run(full, timeout)
    if rc != 0 and "a password is required" in said(out):  # G2.12: sudo's refusal, on its standard error (R168)
        out = Out(str(out), getattr(out, "err", "").rstrip("\n") + (
            "\nsudo's permission ran out or belongs to another terminal: run the same command again from the terminal "
            "where you typed the password (what was done is read back, never done twice)"))
    return rc, out


def as_runner(host: Host, argv: list[str], input: str | None = None, timeout: int = 600) -> tuple[int, str]:
    return sudo(host, ["-u", RUNNER_USER, "--"] + argv, input, timeout)


def mask(text: str, secret: str | None) -> str:
    if not secret:
        return text
    if isinstance(text, Out):  # R168: both streams masked, each kept on its own
        return Out(str(text).replace(secret, "***"), text.err.replace(secret, "***"))
    return text.replace(secret, "***")


def tail(text: str, n: int = 3) -> str:
    """The last lines a run wrote, for words shown to the person: both streams (R168, said)."""
    lines = [x.strip() for x in said(text).splitlines() if x.strip()]
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
        took = host.sudo_validate() == 0
        if took and host.run(["sudo", "-n", "true"])[0] == 0:
            rep.ok("admin rights ready (asked once)")
            return True
        if took:  # R155: the password was right; this computer keeps no admin ticket between steps (timestamp_timeout=0)
            rep.stop("sudo took the password, but this computer does not keep admin rights open between one step and "
                     "the next (sudo's `timestamp_timeout` is 0 here, or its ticket is not kept for this terminal), and "
                     "this tool's admin steps each need them: nothing was touched. Your next step: an admin of this "
                     "computer lets sudo keep them a few minutes (`timestamp_timeout` above 0, with `visudo`), then "
                     "run the same command again")
            return False
        rep.stop("sudo did not take the password (or it was not typed): nothing was touched. Run the same command "
                 "again and type the password where sudo asks")
        return False
    rep.need(f"this needs this computer's admin password once: run `sudo -v` in your terminal, then run {cmd} again "
             "from that same terminal")
    return False


def runner_user_state(host: Host) -> tuple[str, str]:
    """R129 (rule 28, three answers): ("absent", ""), ("there", uid) or ("unread", what was said)."""
    rc, out = host.run(["id", "-u", RUNNER_USER])
    if rc == 0 and out.strip().isdigit():
        return "there", out.strip()
    if rc == 1 and "no such user" in said(out).lower():  # R168: id says so on its standard error
        return "absent", ""
    return "unread", tail(out) or f"exit {rc}"


NOT_OURS_USER = (f"a user named {RUNNER_USER} is already on this computer, and this tool did not make it (its record "
                 "here says so): nothing is touched. This tool never takes over, or removes, a user it did not make. If "
                 f"you know it is left from an earlier copy of this tool, remove it yourself (sudo userdel -r "
                 f"{RUNNER_USER}), then run the same command again; if not, open an issue")


def runner_user_is_ours_or_absent(host: Host, rep: Report) -> bool:
    """R129 (rule 28): setup uses only a runner user it made, as its record here says; one it did not make, or a
    read that gave no answer, stops setup before any admin step."""
    kind, said = runner_user_state(host)
    if kind == "unread":
        rep.stop(f"could not read whether the user {RUNNER_USER} is on this computer ({said}): nothing is touched. "
                 "Settle what it says, then run the same command again")
        return False
    if kind == "there" and not load_state(host).get("runner_user_made"):
        rep.stop(NOT_OURS_USER)
        return False
    return True


def ensure_runner_user(host: Host, rep: Report) -> bool:
    """G2.2: the runner's own user, in no group but its own, its home closed to others."""
    rc, _ = host.run(["id", "-u", RUNNER_USER])
    if rc != 0:
        journal(host, "useradd", RUNNER_USER)
        rc, out = sudo(host, ["useradd", "--create-home", "--user-group", "--shell", "/bin/bash", RUNNER_USER])
        if rc != 0:
            rep.stop(f"could not make the user {RUNNER_USER}: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
            return False
        known = load_state(host)  # R129 (rule 28): a user this tool made is recorded; only a recorded one is removed
        known["runner_user_made"] = True
        save_state(host, known)
        rep.ok(f"made the user {RUNNER_USER}")
    elif not load_state(host).get("runner_user_made"):
        rep.stop(NOT_OURS_USER)
        return False
    else:
        rep.ok(f"the user {RUNNER_USER} is there already (made by this tool, as its record here says)")
    rc, out = host.run(["id", "-nG", RUNNER_USER])
    groups = set(out.split()) if rc == 0 else set()
    extra = groups - {RUNNER_USER}
    if rc != 0 or extra & ADMIN_LIKE_GROUPS or extra:
        rep.stop(f"{RUNNER_USER} is in other groups ({', '.join(sorted(extra)) or answer_or_words(out)}): a runner user must be "
                 "in its own group only, never one that gives admin rights. Settle what it says, then run the same command again: what was done is read back, never done twice")
        return False
    home = f"/home/{RUNNER_USER}"
    journal(host, "chmod", f"0750 {home}")
    sudo(host, ["chmod", "0750", home])
    rc, out = sudo(host, ["stat", "-c", "%a", home])
    if rc != 0 or out.strip() != "750":
        rep.stop(f"the runner's home {home} is not closed to others (mode {answer_or_words(out)}). Settle what it says, then run the same command again: what was done is read back, never done twice")
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
    """G2.3, G2.4: downloaded, checked, then unpacked fresh into the runner's own folder, never copied.
    R127/R128 (rule 28): the copy that is checked is the copy that is used. Root copies the download into a folder of
    root's own, checks its checksum there, takes the dependency script out there and runs it from there; the runner
    user unpacks from that same read-only copy. Every step in the runner user's home is done as the runner user."""
    there = root_test(host, folder)  # R167: the marked read; a read with no answer stops here too
    if there is None:
        rep.stop(f"whether {folder} exists could not be read (as {RUNNER_USER}): nothing was changed. Run the same "
                 "command again")
        return False
    if there:
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
    try:
        return unpack_from_roots_copy(host, rel, tmp, folder, rep)
    finally:
        os.remove(tmp)


ROOTS_COPY_RE = r"/tmp/minions-download\.[A-Za-z0-9]{8}"


def unpack_from_roots_copy(host: Host, rel: dict, tmp: str, folder: str, rep: Report) -> bool:
    rc, made = sudo(host, ["mktemp", "-d", "/tmp/minions-download.XXXXXXXX"])
    d = made.strip()
    if rc != 0 or not re.fullmatch(ROOTS_COPY_RE, d):
        rep.stop(f"could not make a folder of root's own for the download ({tail(made)}): nothing unpacked. Settle what "
                 "it says, then run the same command again")
        return False
    tgz = f"{d}/runner.tgz"
    journal(host, "unpack", folder)
    try:
        rc, out = sudo(host, ["install", "-o", "root", "-g", "root", "-m", "0644", tmp, tgz])
        if rc == 0:
            rc, out = sudo(host, ["sha256sum", tgz])
        if rc != 0 or not out.startswith(rel["sha256"]):
            rep.stop("root's copy of the download does not have the checked checksum: nothing unpacked. Run the same "
                     "command again later; if it stops here again, do not go on: open an issue on this tool's repository with this line")
            return False
        # The folder stays root's; 0711 lets the runner user read the one file it is told the name of, never write.
        steps = [(sudo, ["chmod", "0711", d], "root's folder for the download"),
                 (as_runner, ["install", "-d", "-m", "0700", RUNNERS_HOME.rstrip("/")], "runners folder"),
                 (as_runner, ["install", "-d", "-m", "0700", folder], "the runner's folder")]
        for how, argv, what in steps:
            rc, out = how(host, argv)
            if rc != 0:
                rep.stop(f"could not make {what}: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
                return False
        rc, out = as_runner(host, ["tar", "xzf", tgz, "-C", folder])
        if rc != 0:
            rep.stop(f"could not unpack the runner: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
            return False
        rep.ok(f"unpacked fresh into {folder}, with its own work folder")
        run_dependencies(host, tgz, f"{d}/root", rep)
        return True
    finally:
        sudo(host, ["rm", "-rf", "--one-file-system", d])


def package_list(host: Host) -> set[str] | None:
    """R139 (rule 8): the system's installed packages, read only, as you (no admin rights). None: not read."""
    for argv in (["dpkg-query", "-W", "-f", "${db:Status-Status} ${Package}\n"],
                 ["rpm", "-qa", "--qf", "installed %{NAME}\n"]):
        if host.which(argv[0]):
            rc, out = host.run(argv, timeout=120)
            if rc != 0:
                return None
            return {x.split()[1] for x in out.splitlines() if len(x.split()) == 2 and x.split()[0] == "installed"}
    return None


def record_packages(host: Host, before: set[str] | None, after: set[str] | None) -> list[str] | None:
    """R139: what was added to the system while the runner's dependency script ran (a list before and a list after
    tell what was added in that time, not who added it), kept in this tool's notes (and so, through the journal, in
    your record) for the census when the tool leaves; never removed by this tool. None: not read. A list not read at
    one setup stays said even when a later setup reads one: an error is never an absence."""
    known = load_state(host)
    if before is None or after is None:
        known["dependency_packages_unread"] = True
        save_state(host, known)
        journal(host, "packages", "the system's package list could not be read before and after the dependency script")
        return None
    added = sorted(after - before)
    known["dependency_packages"] = sorted(set(known.get("dependency_packages") or []) | set(added))
    save_state(host, known)
    journal(host, "packages", "added to the system while the runner's dependency script ran: "
            + (", ".join(added) or "none"))
    return added


def run_dependencies(host: Host, tgz: str, d: str, rep: Report) -> None:
    """R127 (rule 28): the runner's dependency script runs as root from root's own checked copy of the download,
    taken out into a folder only root can enter; never the copy in the runner's folder, which a job could rewrite.
    R139 (rule 8): the system's package list is read before and after it, and what it added is recorded and said."""
    script = f"{d}/bin/installdependencies.sh"
    journal(host, "dependencies", f"{script} (from root's checked copy)")
    rc, out = sudo(host, ["install", "-d", "-o", "root", "-g", "root", "-m", "0700", d])
    if rc == 0:
        rc, out = sudo(host, ["tar", "xzf", tgz, "-C", d, "--wildcards", "*bin/installdependencies.sh"])
    if rc != 0:  # the script never started: it added nothing, and there is nothing to read or record
        rep.warn(f"the runner's own dependency script did not start: {tail(out)}; it added nothing to the system")
        return
    before = package_list(host)
    rc, out = sudo(host, ["bash", script], timeout=900)
    added = record_packages(host, before, package_list(host)) if before is not None else record_packages(host, None, None)
    if added is None:
        what = ("it may add system packages, which stay on this computer when this tool leaves; which were added "
                "while it ran could not be read here")
    elif added:
        what = (f"these system packages were added while it ran: {', '.join(added)}; they stay on this computer when "
                "this tool leaves, because another program may come to use them")
    else:
        what = "no system packages were added while it ran"
    if rc != 0:
        rep.warn(f"the runner's own dependency script did not finish: {tail(out)}; {what}")
    else:
        rep.ok(f"the runner's own dependency script ran (from root's checked copy of the download); {what}")


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
    # R143: an environment built by name (RUNNER_ENV); nothing of the person's shell crosses into config.sh
    rc, out = as_runner(host, [*RUNNER_ENV, "bash", "-c", CONFIG_SCRIPT, "minions-config", folder, url, name,
                               ",".join(labels)], input=token + "\n")
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
    as_runner(host, ["chmod", "0600"] + files)  # R128: the runner's own files, as the runner user (rule 28)
    rc, out = as_runner(host, ["stat", "-c", "%a %U %n"] + files)
    rows = [x.split(None, 2) for x in out.splitlines() if x.strip()]
    if rc != 0 or len(rows) != 3 or any(r[0] != "600" or r[1] != RUNNER_USER for r in rows):
        rep.stop(f"the runner's credential files are not its own only: {tail(out, 3)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
        return False
    rep.ok(f"the credential files are 600, owned by {RUNNER_USER}, read back")
    return True


SWAP_MAX_FILE = f"/sys/fs/cgroup/{SLICE}/memory.swap.max"


def swap_cap_read(host: Host) -> tuple[str, str]:
    """R94: whether this computer holds jobs out of swap, read from the running slice itself, never from what was
    written: ("kept", line), ("not kept", line) or ("unread", line). Only "kept" is ever said or recorded as kept."""
    v = host.read(SWAP_MAX_FILE)
    after = ("A job that outgrows its cap here may run slowly through swap, with your disk, instead of failing by "
             "name. Next step: none for jobs to run; `doctor` says this each time it reads it")
    if v is None:
        return "unread", (f"could not read whether jobs are kept out of swap: {SWAP_MAX_FILE} is not there (this "
                          "computer does not count swap for each group of processes, or the slice is not running). "
                          + after)
    if v.strip() == "0":
        return "kept", "jobs are kept out of this computer's swap, read back from the running slice"
    return "not kept", (f"this computer did not keep jobs out of swap: the running slice reads {v.strip()} for its swap "
                        "cap, not 0. " + after)


def say_swap_cap(host: Host, rep: Report) -> bool:
    kind, line = swap_cap_read(host)
    (rep.ok if kind == "kept" else rep.warn)(line)
    return kind == "kept"


def change_limits(host: Host, repo: str, known: dict, lim: dict, rep: Report) -> int:
    """R92 (G7.8): `setup <repo> --limits` on a runner already set up here rewrites the slice only, reads it back from
    systemd and records it. Nothing else is touched: the runner, its registration and a job running now go on, and
    a running job is held to the new limits from now on."""
    path = f"/etc/systemd/system/{SLICE}"
    journal(host, "write", f"{path} (new limits)")
    rc, out = root_file(host, path, slice_text(lim))
    if rc != 0:
        rep.stop(f"could not write {path}: {tail(out)}. The limits are as they were. Settle what it says, then run "
                 "the same command again")
        return rep.code()
    sudo(host, systemctl("daemon-reload"))
    rc, out = host.run(systemctl("show", SLICE, "-p", "CPUQuotaPerSecUSec", "-p", "MemoryMax", "-p", "MemoryHigh",
                                 "-p", "MemorySwapMax"))
    got = dict(x.split("=", 1) for x in out.splitlines() if "=" in x)
    want = {"CPUQuotaPerSecUSec": f"{lim['cores']}s", "MemoryMax": str(lim["MemoryMax"]),
            "MemoryHigh": str(lim["MemoryHigh"])}
    if "MemorySwapMax" in lim:
        want["MemorySwapMax"] = str(lim["MemorySwapMax"])
    off = [f"{k}={got.get(k)!r}" for k, v in want.items() if got.get(k) != v]
    if off:
        rep.stop(f"{SLICE} was written but systemd reads {', '.join(off)}: the new limits are not in force. Run the "
                 "same command again; `doctor` says what differs")
        return rep.code()
    keep = ("cores", "pct", "CPUQuota", "MemoryHigh", "MemoryMax", "MemorySwapMax")
    known["runners"][repo]["limits"] = {k: lim[k] for k in keep if k in lim}
    if say_swap_cap(host, rep):
        known["runners"][repo]["limits"]["swap_kept"] = True
    save_state(host, known)
    rep.ok(f"the limits of {repo}'s runner are changed, read back from systemd: {limits_in_words(lim).split(';')[0]}. "
           "Nothing else was touched; a job running now goes on, held to these limits from now on")
    return rep.code()


def install_service(host: Host, folder: str, unit: str, survey_: dict, rep: Report,
                    limits: dict | None = None) -> str | None:
    """G2.9, R127 (rule 28: root runs only what root owns): the runner's unit is written by this tool as root's own
    file, never by the runner's svc.sh run as root; what goes in the runner's folder is done as the runner user;
    then our drop-in and slice, read back."""
    if not re.fullmatch(UNIT_RE, unit):
        rep.stop(f"the runner's unit name {unit!r} is not one this tool writes: nothing is touched. Check the "
                 "runner's name and folder name (letters, digits, dot, dash, underscore), then run the same command again")
        return None
    if exists_as_root(host, unit_path(unit)):
        rep.stop(f"a unit named {unit} is already on this computer, and this setup did not write it: nothing is "
                 "touched. Settle what it is, then run the same command again")
        return None
    journal(host, "write unit", unit)
    for argv, what in ((["cp", f"{folder}/bin/runsvc.sh", f"{folder}/runsvc.sh"], "the runner's service script"),
                       (["chmod", "0755", f"{folder}/runsvc.sh"], "the runner's service script"),
                       (["tee", f"{folder}/.service"], "the runner's own note of its unit")):
        rc, out = as_runner(host, argv, input=unit + "\n" if argv[0] == "tee" else None)
        if rc != 0:
            rep.stop(f"could not write {what} as the runner user: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
            return None
    rc, out = root_file(host, unit_path(unit), unit_text(folder, unit))
    if rc != 0:
        rep.stop(f"could not write {unit_path(unit)}: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
        return None
    limits = limits or slice_limits(survey_)
    journal(host, "write", f"/etc/systemd/system/{SLICE} and {unit}.d/{DROPIN}")
    for path, text in ((f"/etc/systemd/system/{SLICE}", slice_text(limits)),
                       (f"/etc/systemd/system/{unit}.d/{DROPIN}", dropin_text(folder))):
        sudo(host, ["install", "-d", "-o", "root", "-g", "root", "-m", "0755", os.path.dirname(path)])
        rc, out = root_file(host, path, text)
        if rc != 0:
            rep.stop(f"could not write {path}: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
            return None
    for argv in (systemctl("daemon-reload"), systemctl("enable", unit), systemctl("start", unit)):
        journal(host, " ".join(argv))
        rc, out = sudo(host, argv)
        if rc != 0:
            rep.stop(f"`{' '.join(argv)}` failed: {tail(out)}. Settle what it says, then run the same command again: "
                     "what was done is read back, never done twice")
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
        active = answer_or_words(active) if rc != 0 and not active.strip() else active.strip()
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
                 f"`setup {repo}` again")
        return False
    rep.ok(f"this version serves one repository per computer: here, {repo}")
    return True


SYSTEMD_RUN_DIR = "/run/systemd/system"  # systemd's own test that it runs this computer (sd_booted)


def systemd_runs_here(host: Host) -> bool | None:
    """Row R155 (rule 11, THE FLOOR IS CHECKED): `systemctl` on the PATH does not mean systemd runs this computer (a
    container, Linux inside Windows without it). systemd's own test: its run folder is there. True, False, or None
    when it could not be told; only a seen False is a "no" (rule 11: any other answer is not a "no")."""
    return host.there(SYSTEMD_RUN_DIR)


def no_systemd_words(where: str) -> str:
    return (f"systemd does not run this computer: `systemctl` is here, but {SYSTEMD_RUN_DIR} is not (a container, or "
            "Linux inside Windows with systemd not turned on). This version sets up a runner only on a Linux computer "
            f"that systemd runs. {where} Your next step: run this tool on such a computer; in Linux inside Windows, "
            "systemd is turned on by `systemd=true` under `[boot]` in /etc/wsl.conf, then `wsl --shutdown` in Windows")


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
    if not check_gh_road(host, rep, repo):  # R144: setup's own line names the account it acts as
        return rep.code()
    if host.system() != "Linux" or not host.which("systemctl"):
        rep.stop("this version sets up Linux with systemd only; macOS comes in the next version. Nothing was changed, "
                 "and there is no step to take on this computer now")
        return rep.code()
    if systemd_runs_here(host) is False:  # R155: before the first write, on this computer and on GitHub
        rep.stop(no_systemd_words("Nothing was changed, on this computer or on GitHub."))
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
    if not runner_user_is_ours_or_absent(host, rep):  # R129: before the person's lines and before any admin step
        return rep.code()
    if interactive is None:
        interactive = host.isatty()
    flags = (f" --limits {limits[0]},{limits[1]}" if limits else "") + (" --beside-live-too" if beside_live else "")
    if not persons_own_eyes(host, rep, setup_lines(repo, survey_, lim), interactive,
                            f"`python3 -I {tool_path()} setup {repo}{flags}`"):
        return rep.code()
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
    if left.get("unit") and limits is not None:  # R92: an installed runner's limits change in place
        return change_limits(host, repo, known, lim, rep)
    if left.get("unit"):  # G4.10 (R101): an installed runner: this version's copy for the watch, nothing else touched
        if install_watch(host, rep, repo=repo):
            kind, words = copy_version_line(host)
            (rep.ok if kind == "ok" else rep.warn)(words + "; the runner, its service, its limits and a running job "
                                                   "were not touched")
        return rep.code()
    folder = runner_folder(repo)
    labels = runner_labels(survey_)
    runner_name = f"minions-{re.sub(r'[^A-Za-z0-9-]', '-', host.hostname())}"[:60]
    known = load_state(host)
    if repo not in (known.get("runners") or {}):  # R71, R155: written before the first write, so a cut is found again
        known.setdefault("setting_up", {})[repo] = {"name": runner_name, "folder": folder, "labels": labels,
                                                    "time": host.now().strftime("%Y-%m-%dT%H:%M:%SZ")}
        save_state(host, known)
    if not setup_writes(gh, host, repo, folder, labels, runner_name, survey_, lim, rep):
        # R155 (the stop-line rule): a stop after the first write names what it left and the one next step
        if rep.stopped or rep.blocked:
            say_what_setup_left(gh, host, repo, rep)
        return rep.code()
    both_sides(gh, host, repo, runner_name, unit_of(host, repo), labels, rep)
    return rep.code()


def runner_folder(repo: str) -> str:
    """The one folder a repository's runner has (setup's own rule; every record carries it)."""
    owner, name_ = repo.split("/", 1)
    return f"/home/{RUNNER_USER}/runners/{owner}-{name_}"


def unit_of(host: Host, repo: str) -> str:
    return ((load_state(host).get("runners") or {}).get(repo) or {}).get("unit") or ""


def setup_writes(gh: GitHub, host: Host, repo: str, folder: str, labels: list[str], runner_name: str, survey_: dict,
                 lim: dict, rep: Report) -> bool:
    """setup's road from its first write (the runner user) to the watch; False at the first step that stopped."""
    if not ensure_runner_user(host, rep):
        return False
    rel = runner_release(gh, repo, host, rep)
    if rel is None:
        return False
    if not unpack_runner(host, rel, folder, rep):
        return False
    if not register(gh, host, repo, folder, runner_name, labels, rep):
        return False
    if not lock_credentials(host, folder, rep):
        return False
    if not install_hooks(host, repo, folder, rep):
        return False
    version = runner_version(host, folder, rep) or rel["version"]
    unit = install_service(host, folder, unit_name(folder, runner_name), survey_, rep, lim)
    if unit is None:
        return False
    swap_kept = say_swap_cap(host, rep)  # R94: read from the running slice; only "kept" is recorded as kept
    data = load_state(host)
    if swap_kept:
        lim = {**lim, "swap_kept": True}
    data.setdefault("runners", {})[repo] = {"name": runner_name, "folder": folder, "unit": unit, "labels": labels,
                                            "version": version, "hooks": hook_dir(repo),
                                            "limits": {k: lim[k] for k in ("cores", "pct", "CPUQuota", "MemoryHigh",
                                                                           "MemoryMax", "MemorySwapMax",
                                                                           "swap_kept") if k in lim},
                                            "time": host.now().strftime("%Y-%m-%dT%H:%M:%SZ")}
    (data.get("setting_up") or {}).pop(repo, None)
    if "setting_up" in data and not data["setting_up"]:
        data.pop("setting_up")
    save_state(host, data)
    return install_watch(host, rep, repo=repo)


def what_setup_left(gh: GitHub, host: Host, repo: str) -> list[str]:
    """R155 (rule 11, rule 24): what a setup cut part-way left of itself, each part read now from the thing itself
    (the user, the folder, the units, GitHub's list), never from the record alone. A read that gave no answer is
    named as not read, never left out."""
    data = load_state(host)
    entry = (data.get("setting_up") or {}).get(repo) or (data.get("runners") or {}).get(repo) or {}
    items = []
    if host.run(["id", "-u", RUNNER_USER])[0] == 0:
        items.append(f"the user {RUNNER_USER} and its home")
    for what, path in (("the runner's folder", entry.get("folder")), ("the job hooks", hook_dir(repo))):
        there = root_test(host, path) if path else False
        if there is not False:
            items.append(f"{what} {path}" + ("" if there else " (not read: admin rights are not open in this "
                                                                "terminal, so it is named, not taken for gone)"))
    units = host.listdir_or_none("/etc/systemd/system")
    unit = entry.get("unit") or (unit_name(entry["folder"], entry["name"]) if entry.get("folder") and entry.get("name")
                                 else "")
    if units is None:
        items.append("the services: not read")
    else:
        items += [f"the service {u}" for u in (unit, f"{WATCH_UNIT}.timer") if u and u in units]
    if entry.get("name"):
        s, r = read_runner(gh, repo, entry["name"])
        if r is not None:
            items.append(f"on GitHub, the runner {entry['name']} registered to {repo} ({r.get('status') or 'status not read'})")
        elif s != 200:
            items.append(f"on GitHub, whether {entry['name']} is registered: not read (HTTP {s})")
    if data.get("dependency_packages"):
        items.append("system packages the runner's own dependency script added ("
                     + ", ".join(data["dependency_packages"]) + "; these stay after `uninstall` too, as another program "
                     "may use them)")
    return items


def say_what_setup_left(gh: GitHub, host: Host, repo: str, rep: Report) -> None:
    """R155 (the stop-line rule, rule 11): setup's last line after a stop names what it left and the one next step."""
    items = what_setup_left(gh, host, repo)
    if not items:  # the record stays: a read can be wrong, and setup or uninstall finishes from it (R71)
        rep.stop(f"setup of {repo} stopped, and nothing of it was found made, on this computer or on GitHub (read "
                 f"now). Your next step: settle what the line above says, then run `setup {repo}` again")
        return
    rep.stop(f"setup of {repo} stopped part-way, and this is left of it: {'; '.join(items)}. Your next step: settle "
             f"what the line above says, then `setup {repo}` again finishes it; or `uninstall {repo}` takes it away, "
             "read back")


# ---------------------------------------------------------------------------------------------------- G4 keep ready

HOOKS_ROOT = "/usr/local/lib/windstorm-repo-minions"
DISK_FLOOR = 5 * 1024 ** 3  # G4.3: under this much free space for the work folders, the runner is to be held
JOB_HOURS_MOST = 6  # GitHub's default job time limit; a lock left by a runner that died ends by itself after this
HELD_LABEL = "minions-held"
RECORD_NAME = "repo-minions-record"  # G6.10: in the person's home, theirs
RECORD_README = ("What Windstorm Repo Minions did on this computer, kept for you by its uninstall.\n"
                 "journal.log: every step it took, written before the step ran.\n"
                 "jobs-*.log: each job's start and end on this computer (start or end, the time in seconds since 1970, "
                 "the run, its attempt, the memory limit's kill count, the job).\n"
                 "Nothing secret is in here. The folder is yours: keep it, or delete it whenever you like.\n")
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
    "chmod": "modes", "stat": "reading modes back", "id": "your group", "rm": "removing files", "test": "a path exists", "sh": "runs `test -e` with its answer marked (R167)",
    "bash": "the runner's own scripts", "systemd-inhibit": "the two locks during a job",
    "journalctl": "the runner's own log lines", "sha256sum": "the runner's checksum", "tar": "unpacking the runner",
    "mktemp": "a root-only folder for the download", "find": "emptying the runner's records, as the runner",
    "rmdir": "an emptied folder", "crontab": "the runner user's crontab off", "atq": "its at jobs, read",
    "atrm": "its at jobs off", "loginctl": "its linger off", "cp": "the runner's service script, as the runner",
    "dpkg-query": "the system's package list, read as you", "rpm": "the system's package list, read as you",
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


def keep_jobs_record(host: Host, records: str, rep: Report) -> str | bool:
    """G6.10: before a runner's jobs' records are deleted, they are added to the person's record. "kept" when they
    were; "nothing" when there was no file of the hooks' to keep (no job ran, or a job put a link there); False when
    they could not be read or written: then they stay where they are (rule 6), and the removal goes on without them."""
    path = records.rstrip("/") + "/jobs.log"
    # R128 (rule 28): the runner user writes this folder, so a job can put a link where jobs.log was. It is read as
    # the runner user only (never as root, never as you), and only when it is a plain file: a link is not what the
    # hooks wrote, so it is named, never followed, and deleted with the folder (rule 23).
    rc, kind = as_runner(host, ["stat", "-c", "%F", path])
    gone = "no such file" in said(kind).lower()  # R168: stat says so on its standard error
    kind = kind.strip()
    if rc != 0 and gone:
        return "nothing"  # no job ever ran: nothing to keep
    if rc == 0 and kind not in ("regular file", "regular empty file"):
        rep.warn(f"{path} is a {kind}, not the file this tool's job hooks write: it is not followed or copied to "
                 "your record, and is deleted with its folder")
        return "nothing"
    text = None
    if rc == 0:
        rc, out = as_runner(host, ["cat", path])
        text = out if rc == 0 else None
    if text is None:
        if runner_user_state(host)[0] == "absent":  # R167 (seen): no step reads them now, so none is printed
            rep.warn(f"the jobs' records in {records} are kept where they are, not deleted: the user "
                     f"{RUNNER_USER} is gone, so nothing reads them as that user, and root never reads what a job "
                     "could write (a job can put a link there). They are yours to read or delete")
            return False
        rep.warn(f"the jobs' records in {records} could not be read, so they are kept where they are, not deleted; "
                 "the same command run again in your own terminal moves them to your record")
        return False
    try:
        if text:
            host.keep_in_record(f"jobs-{os.path.basename(records.rstrip('/'))}.log", text if text.endswith("\n")
                                else text + "\n")
        host.keep_in_record("README.txt", RECORD_README, append=False)
    except OSError as e:
        rep.warn(f"the jobs' records in {records} could not be added to your record ({host.record_dir()}: "
                 f"{e.strerror or e}), so they are kept where they are, not deleted; free the space or the folder, "
                 "then run the same command again")
        return False
    return "kept"


def keep_journal_record(host: Host, rep: Report) -> bool:
    """G6.10: the journal of every step goes to the person's record before the tool's state folder is deleted."""
    path = os.path.join(host.state_dir(), "journal.log")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except FileNotFoundError:
        return True
    except OSError as e:
        rep.warn(f"the journal ({path}) could not be read ({e.strerror or e}), so this tool's state folder is kept "
                 "where it is; the same command run again moves it to your record")
        return False
    try:
        host.keep_in_record("journal.log", text)
        host.keep_in_record("README.txt", RECORD_README, append=False)
    except OSError as e:
        rep.warn(f"the journal could not be added to your record ({host.record_dir()}: {e.strerror or e}), so this "
                 "tool's state folder is kept where it is; free the space or the folder, then run the same command "
                 "again")
        return False
    return True


def delete_record_if_typed(host: Host, rep: Report) -> None:
    """G6.10: the person's record is deleted only on `--delete-record`, confirmed by a word typed at a terminal."""
    d = host.record_dir()
    if host.record_count() == (0, 0):
        rep.ok(f"no record of yours to delete ({d} is not there)")
        return
    if not host.isatty():
        rep.need(f"deleting your record ({d}) is your own choice, typed at your own terminal: run the same command "
                 "there and type delete when it asks; until then it is kept", blocks=False)
        return
    word = host.ask(f"Type delete to delete your record of what this tool did ({d}), or press Enter to keep it: ")
    if word.strip() != "delete":
        rep.ok(f"your record ({d}) is kept, as you chose")
        return
    left = host.delete_record() or []
    if host.record_count() == (0, 0):
        rep.ok(f"your record ({d}) is deleted, as you typed")
    elif left:
        rep.stayed["record"] = left  # R136 (rule 8): the person's, named in the census with why
        rep.ok(f"your record's files are deleted, as you typed; left in {d}, not written by this tool: "
               f"{', '.join(left)}")
    else:
        rep.warn(f"your record ({d}) could not be deleted whole; it is yours to delete by hand")


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
        rc, out = root_file(host, f"{d}/{name}", text, "0755")
        if rc != 0:
            rep.stop(f"could not write {d}/{name}: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
            return False
    rc, env = as_runner(host, ["cat", f"{folder}/.env"])  # R128: read as the runner user, never followed by root
    # R143: the locale every job gets is the tool's (C.UTF-8), never one that came from a person's shell
    keep = [x for x in (env.splitlines() if rc == 0 else [])
            if not x.startswith(("ACTIONS_RUNNER_HOOK_JOB_", "LANG=", "LANGUAGE=", "LC_"))]
    keep = ["LANG=C.UTF-8"] + keep
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


class PidsUnread(Exception):
    """R167 (rule 6): pgrep answered neither its list (exit 0) nor "none" (exit 1); the words say what it answered."""


def pgrep_read(host: Host, argv: list[str]) -> list[str]:
    """R167: pgrep's three answers: exit 0 is the list, exit 1 is none, anything else could not be read (raised)."""
    rc, out = host.run(argv)
    if rc == 0:
        return [x for x in out.split() if x.isdigit()]
    if rc == 1:
        return []
    # R167 (owner 3:51 PM ET 11-24, change 2): pgrep answers exit 2 for a user that does not exist (seen: procps-ng
    # 4.0.4, "pgrep: invalid user name: NAME"). Where the runner user reads absent (a setup cut before it was made, a
    # user removed by hand), no process of it runs: none. A user read as there, or not read, stays could not be read.
    if argv[1:3] == ["-u", RUNNER_USER] and runner_user_state(host)[0] == "absent":
        return []
    first = last_line(out)
    raise PidsUnread(f"pgrep answered code {rc}: " + (first[:160] or "nothing"))


def runner_pids(host: Host, folder: str, part: str = "Runner.") -> list[int]:
    """Processes of THIS runner (its own folder's programs), by exact PID. Read only; never a broad kill. Raises
    PidsUnread when pgrep could not answer: never taken for "no process"."""
    # R167 (owner 3:51 PM ET 11-24, answer 5): with no folder the pattern would be "/bin/Runner.", every runner of
    # the runner user's, another repository's too: no process read is ever made without this runner's own folder
    if not folder.startswith(RUNNERS_HOME) or len(folder) <= len(RUNNERS_HOME) or "/.." in folder:
        raise PidsUnread(f"no runner folder is known ({folder!r}), so no process is read for it")
    return [int(x) for x in pgrep_read(host, ["pgrep", "-u", RUNNER_USER, "-f", f"{folder}/bin/{part}"])]


def pids_or_unread(host: Host, folder: str, part: str = "Runner.") -> list[int] | bool:
    """R167: for a yes/no "does a process of it run?": a read that could not answer is True (it may run), never no."""
    try:
        return runner_pids(host, folder, part)
    except PidsUnread:
        return True


def set_labels(gh: GitHub, repo: str, runner_id, labels: list[str]) -> list[str] | None:
    s, d = gh.api("PUT", f"repos/{repo}/actions/runners/{runner_id}/labels", {"labels": labels})
    if s != 200:
        return None
    return sorted(x.get("name") for x in (d or {}).get("labels", []))


def stop_runner(host: Host, unit: str, folder: str, rep: Report, drained: bool) -> bool:
    """G4.6, G4.7: the one way the tool stops a runner. Only after a drain; SIGTERM, then read back that no process
    of this runner is left, before anything may start it again. R167: a read that could not answer stops here."""
    try:
        return _stop_runner(host, unit, folder, rep, drained)
    except PidsUnread as e:
        rep.stop(f"whether a process of the runner is left could not be read ({e}): it is not called stopped, and "
                 "nothing will start it again until that reads. Run the same command again")
        return False


STOPPED_WORDS, RUNNING_WORDS = ("inactive", "failed"), ("active", "activating", "deactivating", "reloading")


def _stop_runner(host: Host, unit: str, folder: str, rep: Report, drained: bool) -> bool:
    if not drained:
        rep.stop("the runner is stopped only after a drain (a stop cancels a running job within seconds): run "
                 "`drain <owner/repo>` first")
        return False
    if not unit:  # R96: a setup cut before it recorded the unit; the runner's own file names it
        rc, named = as_runner(host, ["cat", f"{folder}/.service"])  # R128: as the runner user
        unit = named.strip() if rc == 0 and re.fullmatch(UNIT_RE, named.strip()) else ""
    if not unit:
        left = runner_pids(host, folder)
        if left:
            rep.stop(f"the runner's service is not known here, and process(es) {', '.join(map(str, left))} of it run: "
                     f"end them (`sudo kill {' '.join(map(str, left))}`), then run the same command again")
            return False
        rep.ok("the runner had no service here (its setup was cut before one was made), and no process of it is left")
        return True
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
        as_runner(host, ["kill", "-TERM", str(pid)])  # R128: as the runner user, who can end only its own
    if left:
        host.sleep(2)
        left = runner_pids(host, folder)
    rc, active = host.run(systemctl("is-active", unit))
    if left:
        rep.stop(f"the runner did not stop: process(es) {', '.join(map(str, left))} are left; nothing will start "
                 "it again until they are gone")
        return False
    word = active.strip().splitlines()[0].strip() if active.strip() else ""
    if word in RUNNING_WORDS:  # R167 (rule 30): "stopped" only from a stopped word, as seen after real stops
        rep.stop(f"the runner's service reads {word} after its stop, though no process of it is left: it is not "
                 "called stopped, and nothing will start it again. Run the same command again")
        return False
    if word not in STOPPED_WORDS:
        rep.stop(f"whether the runner's service stopped could not be read (systemctl answered code {rc}: "
                 f"{word[:160] or 'nothing'}): it is not called stopped. Run the same command again")
        return False
    rep.ok(f"the runner is stopped: the service is {word}, and no process of it is left")
    return True


def start_runner(host: Host, unit: str, folder: str, rep: Report) -> bool:
    """G4.7: started only when no process of this runner is left, so two copies never run."""
    rc, active = host.run(systemctl("is-active", unit))
    if active.strip() == "active":
        return True
    try:
        left = runner_pids(host, folder)
    except PidsUnread as e:  # R167: never started over a read that could not answer, so two copies never run
        rep.stop(f"whether a process of this runner is still there could not be read ({e}): it is not started "
                 "again, so two copies never run. Run the same command again")
        return False
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


def hold_runner(gh: GitHub, host: Host, repo: str, data: dict, rep: Report, why: str = "drain",
                moved: list | None = None) -> dict | None:
    """The hold (Part 7 C3): our runner carries only `minions-held`, so no new job lands on it. Labels kept to lift it.
    R154: `moved` gets the repository only when this call moved the runner from live labels to held."""
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
        if moved is not None:
            moved.append(repo)
    elif moved is not None and mine.get("held_for") == why:
        moved.append(repo)  # held by this same road earlier (a `drain --restart` that had to wait), so still its own
    elif moved is not None:  # R154: already held before this call (read from GitHub's labels now); said as it is
        rep.ok(f"{mine['name']} for {repo} was already held (for {mine.get('held_for') or 'a hold this tool did not write down'}), "
               "read from GitHub now: it stays held after the restart; "
               f"`resume {repo}` lifts it")
        return r
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
        try:
            worker = runner_pids(host, mine["folder"], "Runner.Worker")
        except PidsUnread as e:  # R167: never "no job runs" from a read that could not answer
            rep.wait(f"whether a job process runs here could not be read ({e}). Held, nothing stopped. Next: "
                     f"`{next_cmd}` again")
            return "running"
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
    moved: list = []
    for rp in repos:
        if hold_runner(gh, host, rp, data, rep, "a restart" if restart else "drain",
                       moved if restart else None) is None:
            return rep.code()
        if not restart:  # R154: held by name now, so a restart must not lift it, and the hold is said as a drain
            end_restart_hold(host, rp, data, "held by `drain` by name")
            if runners[rp].get("held_for") == "a restart":
                runners[rp]["held_for"] = "drain"
            save_state(host, data)
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
        # R154: the restart's lift undoes only what this road held; a hold set before it stays the person's
        ours = sorted(set(moved) | set((data.get("restart_hold") or {}).get("repos") or []) & set(repos))
        if ours:
            data["restart_hold"] = {"time": host.now().strftime("%Y-%m-%dT%H:%M:%SZ"), "boot_id": boot_id(host),
                                    "repos": ours}
            journal(host, "restart hold", ",".join(ours))
        else:
            data.pop("restart_hold", None)
        save_state(host, data)
        if ours:
            rep.ok("online and idle: safe to restart now. The hold on " + ", ".join(ours) + " lifts by itself once "
                   "this computer has restarted and GitHub reads the runner online again (the watch does it)")
        else:
            rep.ok("online and idle: safe to restart now. Every runner here was already held before this, and stays "
                   "held after the restart until you `resume` it")
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
        rc, out = sudo(host, systemctl("enable", mine["unit"]))
        if rc != 0:  # R167: its exit is read; a service not enabled would not come back after a restart
            rep.stop(f"the runner's service could not be enabled again: {tail(out) or f'code {rc}'}. Settle what it "
                     f"says, then `resume {repo} --private-again` again")
            return rep.code()
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
    end_restart_hold(host, repo, data, "resumed by your word before a restart")
    save_state(host, data)
    return rep.code()


def end_restart_hold(host: Host, repo: str, data: dict, why: str) -> None:
    """R154: a restart hold kept for a repository ends when the person resumes it or holds it again by name, so a
    later restart never lifts a hold the person set (or keeps saying a hold that `resume` already lifted)."""
    rh = data.get("restart_hold")
    if not rh or repo not in (rh.get("repos") or []):
        return
    rh["repos"] = [r for r in rh["repos"] if r != repo]
    if not rh["repos"]:
        data.pop("restart_hold", None)
    journal(host, "restart hold ended", f"{repo}: {why}")


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
    try:
        worker = runner_pids(host, mine["folder"], "Runner.Worker")
    except PidsUnread:  # status's road: said as no job process, as before R167 (its own row, due 0.1.1)
        worker = []
    return answer_or_words(active), worker, host.now()


def both_sides_words(r: dict | None, gh_at: dt.datetime, active: str, worker: list[int], at: dt.datetime,
                     read: bool = True) -> tuple[str, str]:
    """G5.1, G5.2: one line, both sides with the time each was read; "idle" only when both agree. R160 (rule 6): a
    list that was not read is never "does not list it"."""
    if not read:
        gh_side = f"GitHub not read (at {hms(gh_at)})"
    elif r is None:
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


def copy_version_line(host: Host) -> tuple[str, str]:
    """G4.10 (R101): the version of the copy the watch runs, against this tool's own. Three answers (rule 6): the
    same ("ok"), different ("warn", which is older and the one step), or not read ("warn", never "same")."""
    text = host.read(TOOL_COPY)
    m = re.search(r'^VERSION = "(\d+\.\d+\.\d+)"', text or "", re.M)
    if not m:
        return "warn", (f"the version of the copy the watch runs ({TOOL_COPY}) could not be read"
                        + ("" if text is None else ": it names none") + "; `setup <owner/repo>` again puts this "
                        f"version's copy in place")
    theirs = m.group(1)
    if theirs == VERSION:
        return "ok", f"the watch runs this version of the tool ({VERSION})"
    older = tuple(int(x) for x in theirs.split(".")) < tuple(int(x) for x in VERSION.split("."))
    return "warn", (f"the watch runs version {theirs} of the tool, and this is {VERSION}: "
                    + ("the watch runs the older code. `setup <owner/repo>` again puts this version's copy in place"
                       if older else "this tool is the older one. Run the newer tool, or `setup <owner/repo>` "
                       "with this one to put its copy back"))


PLUGIN_CACHE = "/plugins/cache/"


def skill_from(src: str) -> dict:
    """R161 (rule 29 (a), (e)): where the skill that ran setup came from, as setup keeps it. From inside Claude Code's
    plugin cache: the path of Claude Code's own list of plugins and the plugin's name in it, never the version
    folder (an update moves the plugin to a new one and the old one is taken away later). From a clone: its folder."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(src)))
    if PLUGIN_CACHE in root + "/":
        base, rest = (root + "/").split(PLUGIN_CACHE, 1)
        parts = [x for x in rest.split("/") if x]
        return {"plugin": f"{parts[1]}@{parts[0]}", "list": os.path.join(base, "plugins", "installed_plugins.json")} \
            if len(parts) >= 2 else {}
    return {"clone": root}


def kept_skill_from(data: dict) -> dict:
    """R161: what setup kept; a record that kept a folder (written before R161) is read the same way."""
    if data.get("skill_from") is not None:
        return data["skill_from"] or {}
    old = data.get("skill_folder")
    return skill_from(os.path.join(old, "scripts", "minions.py")) if old else {}


def plugin_listed(host: Host, path: str, name: str) -> bool | None:
    """R161 (rule 29 (e), rule 6): whether Claude Code's own list names the plugin. False only when the list was read
    and has no key for it. None when the list is not there (a record carried to another computer names a list that
    computer never had) or not as Claude Code was seen to write it: a key with no row, or a row with no installPath."""
    text = host.read(path)
    if text is None:
        return None
    try:
        d = json.loads(text)
    except ValueError:
        return None
    plugins = d.get("plugins") if isinstance(d, dict) else None
    if not isinstance(plugins, dict):
        return None
    if name not in plugins:
        return False  # seen (rule 29 (e), Claude Code 2.1.331): `plugin uninstall` alone takes the key off
    rows = plugins[name]
    # seen: one row per install, with its installPath; a key with no row, or a row with no installPath, was not seen
    if isinstance(rows, list) and rows and all(isinstance(r, dict) and isinstance(r.get("installPath"), str)
                                               and r["installPath"] for r in rows):
        return True
    return None


def skill_folder_line(host: Host, data: dict) -> tuple[str, str] | None:
    """G6.12 (R107, rule 8; R161, rule 29 (e)): the runner is what the person set up, so it goes on after Claude Code's
    own `plugin uninstall`; status says the skill is gone and names the one command that takes the runner off. None
    while it is there. Three answers (rule 6). A plugin is "removed" only when Claude Code's own list was read and
    names it nowhere; a version folder's absence or mark is never read (an update leaves both behind)."""
    src = kept_skill_from(data)
    off = f"`python3 -I {TOOL_COPY} uninstall --all`"
    goes_on = ("this runner and its watch go on taking your repository's jobs, as you set them up. The one command "
               f"that takes them off, in your own terminal: {off}")
    if src.get("plugin"):
        named = plugin_listed(host, src["list"], src["plugin"])
        if named is None:
            return "warn", (f"whether the skill {src['plugin']} is still installed in Claude Code could not be read "
                            f"(its own list, {src['list']}, could not be read); if you removed it, {off} takes the "
                            "runner off")
        if named:
            return None
        return "warn", (f"Claude Code's own list of plugins ({src['list']}) names {src['plugin']} nowhere, as after "
                        f"Claude Code's own `plugin uninstall`: {goes_on}")
    root = src.get("clone")
    if not root:
        return None
    there = host.there(root)
    if there is None:
        return "warn", (f"whether the skill's folder ({root}) is still there could not be read; if you removed the "
                        f"skill, {off} takes the runner off")
    if there:
        return None
    return "warn", f"the skill's folder ({root}) is gone: {goes_on}"


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
    cut = sorted(r for r in {**(data.get("setting_up") or {}), **{k: v for k, v in runners.items()
                                                                   if v.get("unfinished")}} if not repo or r == repo)
    if cut:  # R155 (rule 11): a setup cut part-way is said as what is left of it, never as "no runner of ours"
        for rp in cut:
            items = what_setup_left(gh, host, rp)
            rep.stop(f"a setup of {rp} here was cut part-way, and this is left of it: "
                     + ("; ".join(items) if items else "nothing, read now")
                     + f". Your next step: `setup {rp}` finishes it; or `uninstall {rp}` takes it away, read back")
        return rep.code()
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
            first.append(("warn", f"GitHub could not be read for {rp}'s runners now "
                                  f"({getattr(gh, 'why', '') or 'HTTP 0' if not s_r else 'HTTP ' + str(s_r)}): what this says "
                                  "of GitHub's side is not read; `status` again later"))
        elif r is None:
            if mine.get("gone_on_github") or active in ("active", "activating"):
                first.append(("warn", gone_words(rp, mine)))
        word, sides = both_sides_words(r, gh_at, active, worker, at, s_r == 200)
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
        rest.append(last_job_line(host, rp, mine, now))  # G5.17 (R105)
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
    rest.append(copy_version_line(host))  # G4.10: a watch running older code is said
    gone = skill_folder_line(host, data)  # G6.12: the skill removed, the runner goes on, said with the way off
    if gone:
        first.append(gone)
    w = data.get("watch") or {}
    every = int(w.get("every_s") or WATCH_EVERY_S)
    for rp in repos:  # G5.15: a blind watch is a stop line, first, never "ok"
        b = (w.get("blind") or {}).get(rp)
        if b:
            first.insert(0, ("stop", blind_words(rp, b, every)))
    gate = gh.login_gate()  # R160: this run's own gh has no login it can read: said first, with the one step
    if gate:
        rp0 = repos[0] if repos else None
        first.insert(0, ("stop", f"{gate}. So nothing GitHub would say is read in this `status`. Your way out: "
                                 + (f"in your own terminal, {login_step(host)} (run `look {rp0 or 'REPO'}` first: "
                                    "it says where your login is now, and what that step keeps where, before you "
                                    "choose it)" if NO_LOGIN in gate else
                                    "settle what it says, then `status` again")))
    if not ok_:
        first.insert(0, ("stop", "the watch is not running, so the job hook here refuses each job before its first step " + HOOK_CAVEAT
                                 + " until the watch reads the repository private again. Your way out: "
                                 f"`sudo systemctl start {WATCH_UNIT}.timer`, or `setup` again"))
    cc = data.get("cost_change")
    if cc and not cc.get("told"):  # G5.10: told once, at the next status, first
        first.insert(0, ("warn", cost_change_words(cc)))
        fresh = load_state(host)  # never write back an older copy of what other steps saved meanwhile
        if fresh.get("cost_change"):
            fresh["cost_change"]["told"] = stamp(now)
            save_state(host, fresh)
    rh = data.get("restart_hold")
    if rh:
        rest.append(("ok", f"held for a restart since {rh['time']} ({', '.join(rh.get('repos') or [])}): the hold lifts by itself once this computer has "
                           "restarted and GitHub reads the runner online"))
    for kind, text in first + rest:
        {"ok": rep.ok, "warn": rep.warn, "stop": rep.stop, "need": rep.need}[kind](text)
    if any("stopped by this computer's memory limit" in text for _, text in first + rest):
        rep.tell(MEMORY_TRADE_OFF)  # G7.14: said where a job the cap stopped is named
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


def last_job_line(host: Host, repo: str, mine: dict, now: dt.datetime) -> tuple[str, str]:
    """G5.17 (R105): when GitHub last handed a job to this runner, from this computer's own record of each start. A
    job GitHub never hands over (a schedule that is late or does not come) is GitHub's side, and this says so."""
    path = records_dir_of(mine.get("folder") or "") + "/jobs.log"
    text = host.read(path)
    if text is None and host.there(path) is not False:  # no admin rights: the watch runs this too
        return "warn", f"when GitHub last handed a job of {repo} to this runner could not be read here ({path})"
    starts = [x.split(" ", 5) for x in (text or "").splitlines() if x.startswith("start ")]
    starts = [x for x in starts if len(x) == 6 and x[1].isdigit()]
    if not starts:
        return "ok", (f"GitHub has not handed this runner a job of {repo} yet. A job GitHub does not hand over (a "
                      "schedule there can be late, or not come) is on GitHub's side; the lines above show whether "
                      "this runner is connected and waiting")
    t = dt.datetime.fromtimestamp(int(starts[-1][1]), dt.timezone.utc)
    return "ok", (f"the last job GitHub handed this runner started at {hms(t)}, "
                  f"{minutes(max(0.0, (now - t).total_seconds()))} ago ('{starts[-1][5]}' of {repo})")


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
        if j["end"] is None and nxt is None and pids_or_unread(host, mine["folder"], "Runner.Worker"):
            continue  # still running, or not read (R167: never named cut from a read that could not answer)
        key = f"{j['start']['run']}/{j['start']['attempt']}/{j['start']['t']}"
        done_ = settled.get(key)
        if done_ is not None:  # R90: a finished job is read from GitHub once, then judged from what was kept
            if not done_.get("verdict"):
                continue
            g, verdict, kept = ({"id": done_["id"], "name": done_.get("name"), "workflow": done_.get("workflow")},
                                tuple(done_["verdict"]), True)
            if not g["workflow"]:  # G5.18: settled before the workflow was kept; read it once now
                g["workflow"] = workflow_of_run(gh, repo, j["start"]["run"])
                if g["workflow"]:
                    done_["workflow"] = g["workflow"]
                    fresh_settled = True
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
            if verdict is not None:
                g["workflow"] = workflow_of_run(gh, repo, j["start"]["run"])  # G5.18 (R118): which of the person's jobs
            settled[key] = {"id": g["id"], "name": g.get("name"), "workflow": g.get("workflow"),
                            "verdict": list(verdict) if verdict else None}
            fresh_settled = True
        if verdict is None and kept:
            verdict = (kept["kind"], kept["why"])  # named once, while this computer still had its record
        if verdict is None:
            continue
        if not kept:
            data.setdefault("named", {})[str(g["id"])] = {"kind": verdict[0], "why": verdict[1], "run": j["start"]["run"],
                                                         "time": stamp(host.now())}
            if verdict[0] == "killed":  # R93: the limit it was killed under, kept; a later change never rewrites it
                data["named"][str(g["id"])]["limit"] = (mine.get("limits") or {}).get("MemoryMax")
            save_state(host, data)
            journal(host, "named", f"{repo} job {g['id']}: {verdict[0]} by {verdict[1]}")
        named[int(g["id"])] = g
        when = hms(dt.datetime.fromtimestamp(j["start"]["t"], dt.timezone.utc))
        which = stopped_job_words(g, j["start"]["run"], when)
        done = int(g["id"]) in (data.get("reruns") or [])
        again = (" It was run again once already." if done else
                 f" To run it again, once: `status {repo} --rerun {g['id']}`.")
        if verdict[0] == "refused":
            lines.append(("warn", f"{which} was refused before "
                                  f"its first step by this computer's guard: {verdict[1]}. No step of it ran here; "
                                  f"GitHub shows it as failed, not a failing test of yours.{again}"))
        elif verdict[0] == "killed":
            lim = ((data.get("named") or {}).get(str(g["id"])) or {}).get("limit")  # R93: as named, or no number
            lines.append(("warn", f"{which} was stopped by "
                                  f"this computer's memory limit{' (' + gb(int(lim)) + ')' if lim else ''}, not by your "
                                  "code failing; GitHub shows it as failed. The one way to change the limit: "
                                  f"`setup {repo} --limits <processors>,<memory %>`.{again}"))
        else:
            lines.append(("warn", f"{which} was cut by "
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


def our_leftover_holds(host: Host, pids: list[str]) -> list[str]:
    """R91: which of the runner user's processes are a job's sleep hold of ours: `systemd-inhibit` with our name, or
    the `sleep` it runs. Read from /proc (argv and parent), never by a pattern over every process."""
    def argv(pid: str) -> list[str]:
        return [x for x in (host.read(f"/proc/{pid}/cmdline") or "").split("\0") if x]
    def ppid(pid: str) -> str:
        st = host.read(f"/proc/{pid}/stat") or ""
        rest = st.rsplit(")", 1)[-1].split()
        return rest[1] if len(rest) > 1 else ""
    inhibitors = {p for p in pids if (a := argv(p)) and os.path.basename(a[0]) == "systemd-inhibit"
                  and "--who=Windstorm Repo Minions" in a}
    sleeps = {p for p in pids if argv(p)[:1] == ["sleep"] and ppid(p) in inhibitors}
    return sorted(inhibitors | sleeps, key=int)


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
         "slept_s": host.slept_s(),
         # G5.19: each pass counted, so a pass is told from the one before it even in the same second
         "pass": int((data.get("watch") or {}).get("pass") or 0) + 1}
    if blind:  # G5.15: counted, kept with its reason, and said: a blind pass ends `failed`, never in silence
        w["blind"] = {rp: {"why": why, "passes": (old.get(rp) or {}).get("passes", 0) + 1,
                           "since": (old.get(rp) or {}).get("since") or stamp(host.now())} for rp, why in blind.items()}
        if any(NO_LOGIN in why or "auth login" in why or "401" in why for why in blind.values()):
            step = login_step(host)  # R160: as this gh can take it
            for b in w["blind"].values():
                b["step"] = step
    cost_weekly(host, data)
    gone = skill_folder_line(host, data)  # G6.12: said in the watch's own log when it changes
    w["skill_folder"] = gone[1] if gone else ""
    if w["skill_folder"] != ((data.get("watch") or {}).get("skill_folder") or ""):
        if gone:
            said.append(gone[1])
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
    step = (f"in your own terminal, {b.get('step') or '`gh auth login --insecure-storage`'} (run `look {repo}` first: "
            "it says where your login is now, and what that step keeps where, before you choose it); the next pass "
            "reads again by itself")
    way = ("nothing to do now: it comes back by itself at the time named; if it is spent again, something else logged "
           "in as this account uses it up" if "API limit" in b["why"] else
           step if NO_LOGIN in b["why"] or "auth login" in b["why"] or "401" in b["why"] else  # R160
           "check the repository's name and this login's access to it on GitHub; the next pass reads again by itself"
           if "HTTP 404" in b["why"] else
           "the reason is GitHub's answer named here; the next pass reads again by itself"
           if "GitHub answered HTTP" in b["why"] else
           "make `gh` reachable, then run `setup` again from a terminal where `gh` works (it finds gh's folder again)"
           if "could not be found" in b["why"] or "could not be run" in b["why"] else
           "check this computer's network; the next pass reads again by itself")
    return (f"the watch could not read whether {repo} is still private, {b['passes']} pass(es) in a row since "
            f"{b['since']}: {b['why']}. "
            + (f"So the job hook here refuses each job of it before its first step {HOOK_CAVEAT}, until the watch "
               "reads the repository private again. " if held else
               f"After {SEEN_PASSES} such passes ({SEEN_PASSES * every_s // 60} minutes) the job hook here refuses its "
               f"jobs {HOOK_CAVEAT} until the watch reads it private again. ")
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
        write_seen(host, mine, "public")  # G5.15: the hook refuses at once (unless a job switched it off, R133)
        public_guard(gh, host, repo, data, rep)
        return f"{repo}: no longer private: every job of it refused here", ""
    why = ""
    if s == 200 and d is not None and d.get("private") is True:
        why = write_seen(host, mine, str(int(host.now().timestamp())))
    else:
        why = gh.why or (f"GitHub answers that {repo} is not there for this login (HTTP 404): it was renamed or "
                         "deleted, or the login lost its access to it" if s == 404 else
                         f"GitHub answered HTTP {s} for the repository")
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
    try:
        worker = runner_pids(host, mine.get("folder") or "", "Runner.Worker")
    except PidsUnread:  # R167: nothing written down from a read that could not answer
        return
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


def workflow_of_run(gh: GitHub, repo: str, run_id) -> str | None:
    """The workflow file a run came from (GitHub's own `path`), or None when it cannot be read."""
    s, d = gh.api("GET", f"repos/{repo}/actions/runs/{run_id}")
    path = (d or {}).get("path") if s == 200 else None
    return path.split("@")[0] if isinstance(path, str) and path else None


def stopped_job_words(g: dict, run_id, when: str) -> str:
    """G5.18 (R118): a stopped job is named by its workflow file and its own name as well as the run, the person's
    first question after a kill being "which of my jobs?"; when the workflow cannot be read, it says so."""
    if g.get("workflow"):
        return f"job '{g.get('name')}' of {g['workflow']} (run {run_id}, started {when})"
    return f"job '{g.get('name')}' (run {run_id}, started {when}; which workflow it came from could not be read)"


def memory_held_words(repo: str, mine: dict, recs: list[dict]) -> str:
    """R87: the job held at our memory limit, by name and run where this computer's record has it, and the two ways
    out that are the person's."""
    h = mine["memory_held"]
    run = next((j["start"] for j in reversed(recs) if j["end"] is None), None)
    what = f"job '{run['job']}' (run {run['run']})" if run else "the job running here"
    high = ((mine.get("limits") or {}).get("MemoryHigh") or "")
    limit = f" ({gb(int(high))})" if str(high).isdigit() else ""
    stall = f"; its tasks all stood still {h['full_pct']:.0f} % of the last minute" if h.get("full_pct") is not None else ""
    # G8.16, the owner's ruling (11-23): no install since jobs were kept out of swap reaches this state, so no command
    # here was run as printed; the line names the run's own page on GitHub, where its Cancel button is.
    cancel = (f"on GitHub, from the run's page: https://github.com/{repo}/actions/runs/{run['run']}" if run else
              "on GitHub, from the run's page")
    return (f"{what} has been held at this computer's memory limit for jobs{limit} since {h['since'][11:19]}Z, "
            f"{h['passes']} watch passes in a row: it needs more memory than jobs here may use, so it barely moves"
            f"{stall}, and it may go on like this until GitHub's own time limit. Your two ways out: cancel it "
            f"({cancel}), or give jobs more memory: `setup {repo} --limits <processors>,<memory %>`")


def offline_while_running(host: Host, mine: dict, r: dict, data: dict) -> None:
    """G11.4 (R74): since when GitHub has shown our runner offline while its service runs here with no job process.
    The watch only writes it down (it has no admin rights and restarts nothing); `status` names it first."""
    rc, active = host.run(systemctl("is-active", mine.get("unit") or "none"))
    stuck = (r.get("status") != "online" and active.strip() == "active"
             and not pids_or_unread(host, mine.get("folder") or "", "Runner.Worker"))
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
    """G5.4 (INV1): a repository no longer private. The hook refuses every job of it unless a job that ran here
    switched the hook off (R133: a gate a job can switch off is not a wall; the hold no job can touch is GitHub's
    side, here: labels off, then the runner deleted once idle) (the record says
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
        return  # GitHub not reached: only the hook, if no job switched it off, refuses meanwhile; next pass tries again
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
    ps["service"] = answer_or_words(active)
    return ps


# R133 (rule 28, A GATE IS NOT KEPT BY THE ONE IT GATES): the start hook is named in the runner's .env, which the
# runner user (so any job) can rewrite; every line that leans on the hook says so.
HOOK_CAVEAT = "(unless a job that already ran here switched that hook off)"


def public_words(repo: str, ps: dict) -> str:
    return (f"{repo} is no longer private (read {ps['time']}): the job hook here refuses each job of it before its "
            f"first step {HOOK_CAVEAT}"
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
    if pids_or_unread(host, mine["folder"], "Runner.Worker"):
        return  # a job process still runs here (or could not be read, R167): named once it ends
    rc, active = host.run(systemctl("is-active", mine["unit"]))
    journal(host, "gone on GitHub", f"{repo} {mine['name']}: its service is {answer_or_words(active)}")
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
           + f"ExecStart=/usr/bin/python3 -I {TOOL_COPY} watch\nNice=10\n")  # R147: -I, as every start of the tool
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
        rc, out = root_file(host, path, text)
        if rc != 0:
            rep.stop(f"could not write {path}: {tail(out)}. Settle what it says, then run the same command again: what was done is read back, never done twice")
            return False
    for argv in (systemctl("daemon-reload"), systemctl("enable", "--now", f"{WATCH_UNIT}.timer")):
        journal(host, " ".join(argv))
        sudo(host, argv)
    rc, active = host.run(systemctl("is-active", f"{WATCH_UNIT}.timer"))
    if active.strip() != "active":
        rep.stop(f"the watch's timer is not active ({answer_or_words(active)}). Settle what it says, then run the same command again: what was done is read back, never done twice")
        return False
    data = load_state(host)
    data["watch"] = dict(data.get("watch") or {}, every_s=every_s)
    data["programs"] = found
    if os.path.abspath(src) != TOOL_COPY:  # G6.12, R161: where the skill came from, never a plugin's version folder
        data["skill_from"] = skill_from(src)
        data.pop("skill_folder", None)
    save_state(host, data)
    rep.ok(f"the watch runs every {every_s // 60} minutes as you (a system timer, read back); `status` says when it "
           f"last ran. The programs it runs by name are found where this terminal finds them (gh: {found['gh']})")
    if repo is None:
        return True
    # G5.15: one real pass now, as the timer runs it, and what it read, read back.
    before = (load_state(host).get("watch") or {}).get("pass")  # G5.19: the count, never the time to the second
    journal(host, "the watch's first pass")
    sudo(host, systemctl("start", f"{WATCH_UNIT}.service"))
    w = load_state(host).get("watch") or {}
    b = (w.get("blind") or {}).get(repo)
    seen = (host.read(seen_path(load_state(host)["runners"][repo]["folder"])) or "").strip()
    if w.get("pass") in (None, before):
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
    try:
        worker = runner_pids(host, folder, "Runner.Worker")
    except PidsUnread as e:
        rep.wait(f"whether a job process of {mine['name']} runs here could not be read ({e}); nothing is changed. "
                 f"Next: `setup {repo}` again")
        return False
    if worker:
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
        why = (" (a workflow file needs the `workflow` permission on your GitHub login; adding it is your step: read "
               f"{GH_SCOPE_DOCS})") \
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


def move_lines(s: dict) -> list[str]:
    """move's block as a list: one source for the agent's block and the pull request's own words (G7.17)."""
    lines = _Lines()
    before_move_words(s, lines)
    return list(lines)


def pr_body(moved: list, left: list, publish: list, here: list, first: list[str] | None = None) -> str:
    # G7.17 (R116): the page the person reads when they decide carries move's lines, word for word, above all else.
    out = (["## Before you merge", ""] + [f"- {x}" for x in first] + [""]) if first else []
    out += ["This pull request changes only `runs-on` lines, so these jobs run on your own computer's runner "
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
    pr = open_move_pr(gh, host, repo, base, changed, pr_body(moved, left, publish, here, move_lines(survey_)), rep)
    if pr is None:
        return rep.code()
    data = load_state(host)
    data.setdefault("moves", {})[repo] = {"pr": pr["number"], "branch": pr["branch"], "files": plan, "base": base,
                                          "time": host.now().strftime("%Y-%m-%dT%H:%M:%SZ")}
    save_state(host, data)
    rep.ok(f"pull request #{pr['number']} opened: {len(moved)} job(s) to move, {len(left) + len(publish)} left "
           "as they are and named in it")
    before_move_words(survey_, rep)  # G7.14: its own block, the last thing before the merge it asks for
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
    elsewhere: set[str] = set()  # G3.7: other computers of ours that ran a moved job
    listed: dict | None = None
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
                whose = "this computer"
                if where != mine["name"] and "minions" in labels and j.get("runner_group_name") != "GitHub Actions":
                    if listed is None:
                        listed = runners_by_name(gh, repo)
                    kind = runner_of_ours(listed, where)
                    if kind is None:  # G3.7: the third answer, said as such
                        rep.stop(f"{path} job {w['id']} ran on {where}, a runner GitHub does not list for {repo} now, "
                                 "so this tool cannot tell whether it is one of yours: it is not called moved. Run the "
                                 "same command again after the next run of that workflow")
                        all_ok = False
                        continue
                    if kind:
                        whose = "another computer of yours with this tool's runner"
                        elsewhere.add(where)
                if whose == "this computer" and (where != mine["name"] or "minions" not in labels):
                    rep.stop(f"{path} job {w['id']} ran on {where} (labels {', '.join(labels) or 'none'}), not on a "
                             f"runner of ours: it did not move. Check that job's `runs-on`, then run the same command again")
                    all_ok = False
                elif j.get("conclusion") == "success":
                    rep.ok(f"moved: {path} job {w['id']} ran on {where} ({whose}) and succeeded (run {run['id']})")
                else:
                    rep.warn(f"moved: {path} job {w['id']} ran on {where} ({whose}) and ended {j.get('conclusion')}: "
                             f"that is the job's own result; its log is at {j.get('html_url')}")
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
        if elsewhere:
            rep.ok(f"moved: every moved job of {repo} ran on a computer of yours with this tool's runner (here, or "
                   f"{', '.join(sorted(elsewhere))})")
        else:
            rep.ok(f"moved: every moved job of {repo} ran on this computer")


def runners_by_name(gh: GitHub, repo: str) -> dict | None:
    """The repository's self-hosted runners by name, with their label names; None when GitHub's list cannot be read."""
    s, d = gh.api("GET", f"repos/{repo}/actions/runners?per_page=100")
    if s != 200:
        return None
    return {r.get("name"): {x.get("name", "").lower() for x in r.get("labels") or []} for r in (d or {}).get("runners", [])}


def runner_of_ours(listed: dict | None, name: str) -> bool | None:
    """G3.7 (R114): True for a runner this tool registered (listed for the repository with only our labels: `minions`
    and `minions-<size>`, none of GitHub's defaults, as setup registers it); False for a runner that is not ours;
    None when it cannot be told (the list unread, or the runner no longer listed)."""
    if listed is None or name not in listed:
        return None
    labels = listed[name]
    return "minions" in labels and all(x == "minions" or x.startswith("minions-") for x in labels)


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
    # R128 (rule 28): a path in the runner user's home is read as the runner user, so a link a job planted there
    # leads nowhere root can see; root's own folders are read as root.
    how = as_runner if path.startswith(f"/home/{RUNNER_USER}/") else sudo
    # R167 (rule 6): the answer is the test's OWN mark, never sudo's exit or words. sudo can write a warning and still
    # run the command (seen: "sudo: unable to resolve host NAME: ...", on a computer whose name does not resolve, with
    # the test's exit passed on), and its own refusal is an exit 1 with words; neither is taken for the test's answer.
    # No mark, or both: could not be read.
    got = marked_answer(*how(host, marked_test(path)))
    if got is None and how is as_runner:
        # R167 (seen, a stock Ubuntu 24.04 with the runner user removed by hand): sudo cannot run anything as a user
        # that is gone ("sudo: unknown user"), so every read in its home was "could not be read" and counted as there,
        # and uninstall stopped at its folder on every run. The home itself is root's to read (a folder in /home, which
        # a job cannot replace): where it is not there, nothing in it is.
        home = marked_answer(*sudo(host, marked_test(f"/home/{RUNNER_USER}")))
        return False if home is False else None
    return got


def marked_answer(rc: int, out: str) -> bool | None:
    """R167: the marked test's own answer: there, not there, or None (no mark, both, or an exit other than 0)."""
    marks = {x.strip() for x in out.splitlines()} & {TEST_THERE, TEST_NOT_THERE}
    if rc != 0 or len(marks) != 1:
        return None
    return TEST_THERE in marks


TEST_THERE, TEST_NOT_THERE = "minions-test: there", "minions-test: not there"
TEST_SH = f'if test -e "$1"; then echo "{TEST_THERE}"; else echo "{TEST_NOT_THERE}"; fi'


def marked_test(path: str) -> list[str]:
    """R167: `test -e PATH`, run by sh with the path as its one argument (never in the script's text), printing its
    own mark for each answer."""
    return ["sh", "-c", TEST_SH, "sh", path]


def empty_then_rmdir(host: Host, path: str) -> None:
    """R128 (rule 28): a folder of root's that the runner user writes into (a repository's jobs' records): its
    contents deleted as the runner user, then the empty folder removed by root with rmdir, which follows nothing."""
    as_runner(host, ["find", path, "-mindepth", "1", "-delete"])
    sudo(host, ["rmdir", path])


def exists_as_root(host: Host, path: str) -> bool:
    """A path that could not be read counts as there, so a removal never reads "gone" from a refused read (R71)."""
    return root_test(host, path) is not False


def unit_path(unit: str) -> str:
    return f"/etc/systemd/system/{unit}"


RUNNING_STATES = ("active", "activating", "deactivating", "reloading", "refreshing")


def unit_read(host: Host, unit: str) -> tuple[str, str]:
    """R167 (family rule 30, rule 6's three answers): one read of a unit, as systemd answers it now: ("gone", state)
    when systemd no longer has it loaded and nothing of it is active (G6.5's lesson); ("stopped", state) when it is
    loaded but not running (inactive, or failed: R132); ("running", state); or ("unread", why) when systemctl did not
    answer (no manager reached, a timeout, an answer without both fields) or answered a word neither running nor
    stopped: never taken as gone or stopped. A slice is
    never "not-found": systemd makes any slice from its name alone (seen on a stock Ubuntu 24.04, systemd 255: a slice
    never made reads LoadState=loaded). So a slice is gone when it is inactive with no file and no drop-in loaded."""
    rc, out = host.run(systemctl("show", unit, "-p", "LoadState", "-p", "ActiveState", "-p", "FragmentPath",
                                 "-p", "DropInPaths"))
    got = dict(x.split("=", 1) for x in out.splitlines() if "=" in x)
    if rc != 0 or not got.get("ActiveState") or not got.get("LoadState"):
        first = last_line(out)
        return "unread", f"systemctl answered code {rc}: " + (first[:160] or "nothing")
    act = got["ActiveState"]
    if act in RUNNING_STATES:
        return "running", act
    if act == "inactive" and (got["LoadState"] == "not-found" or (
            unit.endswith(".slice") and not got.get("FragmentPath") and not got.get("DropInPaths"))):
        return "gone", act
    if act in ("inactive", "failed"):
        return "stopped", act
    # R167 (owner 3:51 PM ET 11-24, change 3): stopped is said only from the two words seen; any other word (one a
    # later systemd adds, too) is named and could not be read
    return "unread", f"systemctl answered ActiveState={act[:60]}, a word this tool does not read as stopped"


def unit_words(unit: str, read: tuple[str, str]) -> str:
    """What was read of one unit, as the stop lines say it."""
    kind, what = read
    return f"{unit}: could not be read ({what})" if kind == "unread" else f"{unit}: {kind} ({what})"


def gone_or_stop(host: Host, unit: str, rep: Report, again: str) -> bool:
    """After a unit's removal: read gone, or a stop that says what was read (a read with no answer is said so)."""
    read = unit_read(host, unit)
    if read[0] == "gone":
        return True
    if read[0] == "unread":
        rep.stop(f"whether {unit} is gone could not be read ({read[1]}). Your next step: `{again}`: it reads again "
                 "and finishes a removal that was not finished")
    else:
        rep.stop(f"{unit} is still there after its removal ({read[0]}, {read[1]}). Run the same command again: it "
                 "finishes a removal that was not finished")
    return False


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
            rc, out = as_runner(host, [*RUNNER_ENV, "bash", "-c", REMOVE_SCRIPT, "minions-remove", folder],
                                input=token + "\n")  # R143
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


def remove_service(host: Host, mine: dict, rep: Report, again: str = "uninstall") -> bool:
    """R127 (rule 28): the runner's unit and our drop-in, taken out by this tool as root's own files (never through
    the runner's svc.sh); the runner's own note of its unit removed as the runner user; read back gone. Safe to run
    again. The runner's `config.sh remove` refuses while that note is there, so this comes before it."""
    unit, folder = mine.get("unit") or "", mine.get("folder") or ""
    if not unit and folder:
        rc, named = as_runner(host, ["cat", f"{folder}/.service"])
        unit = named.strip() if rc == 0 else ""
    if unit and not re.fullmatch(UNIT_RE, unit):
        rep.stop(f"the runner's unit name {unit!r} is not one this tool writes: nothing is removed. Check the "
                 "runner's .service file (a job can change it), then run the same command again")
        return False
    if folder and exists_as_root(host, f"{folder}/.service"):
        journal(host, "remove", f"{folder}/.service")
        as_runner(host, ["rm", "-f", f"{folder}/.service"])
    if unit:
        if exists_as_root(host, unit_path(unit)):
            journal(host, "remove unit", unit)
            sudo(host, systemctl("disable", unit))
            sudo(host, ["rm", "-f", unit_path(unit)])
        if exists_as_root(host, unit_path(unit) + ".d"):
            journal(host, "remove drop-in", f"{unit_path(unit)}.d")
            sudo(host, ["rm", "-rf", unit_path(unit) + ".d"])
        sudo(host, systemctl("daemon-reload"))
        sudo(host, systemctl("reset-failed", unit))  # R132: a failed unit stays loaded until its failure is forgotten
        if not gone_or_stop(host, unit, rep, again):
            return False
    rep.ok("the runner's service and our drop-in are gone, read back")
    return True


def remove_folders(host: Host, repo: str, mine: dict, rep: Report) -> bool:
    """The runner's own folders (only under the runner user's runners folder), its hooks and its jobs' records."""
    folder = mine.get("folder") or ""
    records = records_dir_of(folder) if folder else ""
    kept = keep_jobs_record(host, records, rep) if records and exists_as_root(host, records) else "nothing"
    if kept is False:
        records = ""  # G6.10: not kept in the person's record, so not deleted (rule 6)
    paths = [x for x in (folder if folder.startswith(RUNNERS_HOME) and "/.." not in folder else "",
                         hook_dir(repo), records) if x]
    for path in paths:
        if exists_as_root(host, path):
            journal(host, "delete", path)
            # R128 (rule 28): what the runner user can write is deleted as the runner user, who can take only what it
            # could already; root then removes an emptied records folder with rmdir, which follows nothing.
            if path == folder:
                as_runner(host, ["rm", "-rf", "--one-file-system", path])
            elif path == records:
                empty_then_rmdir(host, path)
            else:
                sudo(host, ["rm", "-rf", "--one-file-system", path])
    left = [x for x in paths if exists_as_root(host, x)]
    if left:
        rep.stop(f"could not delete {', '.join(left)}. Run the same command again: it finishes a removal that was not finished")
        return False
    rep.ok("the runner's folders and its hooks are deleted, read back"
           + (f"; its jobs' records are in your record ({host.record_dir()})" if kept == "kept" else ""))
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
    folder = mine.get("folder") or runner_folder(repo)  # R167 (answer 5): load_state gives it back to a hand edit
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
    elif folder:
        try:
            worker = runner_pids(host, folder, "Runner.Worker")
        except PidsUnread as e:  # R167: never "no job runs" from a read that could not answer
            rep.wait(f"whether a job process of this runner runs here could not be read ({e}), while GitHub no "
                     f"longer lists it; nothing is cut. Next: `{next_cmd}` again")
            return False
        if worker:
            rep.wait(f"a job process of this runner still runs here, while GitHub no longer lists it; nothing is cut. "
                     f"Next: `{next_cmd}` again")
            return False
    unit = mine.get("unit") or ""
    if not unit and folder:  # R96: a setup cut before it recorded the unit; the runner's own file names it
        rc, named = as_runner(host, ["cat", f"{folder}/.service"])
        unit = named.strip() if rc == 0 and re.fullmatch(UNIT_RE, named.strip()) else ""
    again = f"python3 -I {tool_path()} {next_cmd}"
    if pids_or_unread(host, folder) or (unit and unit_read(host, unit)[0] != "gone"):
        if not stop_runner(host, unit, folder, rep, drained=True):
            return False
    if unit:  # R167 (rule 30): read stopped before anything of it is removed; not read stopped, nothing is
        read = unit_read(host, unit)
        if read[0] not in ("stopped", "gone"):
            rep.stop(f"the runner's service was told to stop and reads {unit_words(unit, read)}: nothing of it was "
                     f"removed. Your next step: `{again}`")
            return False
    # The runner's own `config.sh remove` refuses while its service is installed, so the unit goes first, taken out
    # by this tool (R127); the GitHub side still goes before anything of its folders is deleted (G6.6).
    if not remove_service(host, mine, rep, again):
        return False
    if not remove_github_side(gh, host, repo, mine, rep):
        return False
    if not remove_folders(host, repo, mine, rep):
        return False
    journal(host, "removed", f"{repo} {mine.get('name')}")
    end_restart_hold(host, repo, data, "its runner was removed")  # R154: never said held for a runner gone or set up anew
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
        rep.warn(f"the watch's timer is {answer_or_words(active)}: `setup` again puts it back")
        broken.append("the watch")
    kind, words = copy_version_line(host)  # G4.10 (R101)
    (rep.ok if kind == "ok" else rep.warn)(words)
    if kind != "ok" and "could not be read" not in words:
        broken.append("the watch's copy of the tool")
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
                           "(" + ", ".join(f"`sudo gpasswd -d {RUNNER_USER} {g}`" for g in extra) + ")")
    else:
        rep.ok(f"{repo}: {RUNNER_USER} is in its own group only, no admin rights")
    rc, mode = sudo(host, ["stat", "-c", "%a", f"/home/{RUNNER_USER}"])
    if rc != 0:
        rep.warn(f"{repo}: the mode of /home/{RUNNER_USER}: not read" + (  # R168: stat's own refusal, its words
            "" if "a password is required" in said(mode) else f": {last_line(mode)[:160] or 'nothing'}"))
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
    rc, out = as_runner(host, ["stat", "-c", "%a %U %n"] + files)  # R128: read as the runner user
    rows = [x.split(None, 2) for x in out.splitlines() if x.strip()]
    if "a password is required" in said(out):  # R168: sudo's refusal is on its standard error
        rep.warn(f"{repo}: the credential files: not read")
    elif rc != 0 or len(rows) != 3 or any(r[0] != "600" or r[1] != RUNNER_USER for r in rows):
        bad("credential files", f"the runner's credential files are not 600 and its own ({tail(out, 3)}): "
                                f"`sudo -u {RUNNER_USER} chmod 600 {' '.join(files)}`, or {again}")
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
    rc, out = host.run(systemctl("show", SLICE, "-p", "CPUQuotaPerSecUSec", "-p", "MemoryMax", "-p", "MemoryHigh",
                                 "-p", "MemorySwapMax"))
    sl = dict(x.split("=", 1) for x in out.splitlines() if "=" in x)
    want_l = {"CPUQuotaPerSecUSec": f"{lim.get('cores')}s", "MemoryHigh": str(lim.get("MemoryHigh")),
              "MemoryMax": str(lim.get("MemoryMax"))}
    if "MemorySwapMax" in lim:  # set up before 0.1.0's no-swap default: not recorded, so not compared
        want_l["MemorySwapMax"] = str(lim["MemorySwapMax"])
    off = [f"{k}={sl.get(k)!r}" for k, v in want_l.items() if sl.get(k) != v]
    if lim.get("swap_kept"):  # R94: kept at setup, so a change since is a promise not kept
        kind, line = swap_cap_read(host)
        if kind != "kept":
            bad("limits", f"{line.split('. ')[0]}, though setup read it kept: `setup {repo} --limits "
                          f"{lim.get('cores')},{lim.get('pct')}` writes it again")
    elif lim and "MemorySwapMax" in lim:
        rep.warn(f"{repo}: {swap_cap_read(host)[1]}")
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
    if "a password is required" in said(out):  # R168: sudo's refusal is on its standard error
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
    try:
        listeners = runner_pids(host, folder, "Runner.Listener")
    except PidsUnread:  # doctor's road: as before R167 (its own row, due 0.1.1)
        listeners = []
    if len(listeners) > 1:
        bad("one copy", f"{len(listeners)} copies of the runner run ({', '.join(map(str, listeners))}): "
                        f"`drain {repo} --stop`, then `resume {repo}`")
    # G2.6, G2.7, G2.10: GitHub's side and the service, with only our labels (or our hold's).
    s, r = read_runner(gh, repo, name)
    rc, active = host.run(systemctl("is-active", unit))
    active = answer_or_words(active)
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


def watch_off(host: Host, rep: Report) -> bool:
    """R167 (family rule 30, Linux): before `uninstall --all` removes anything, the watch's timer and service are told
    to stop and both are READ stopped (or gone). If either is not, nothing was removed: the stop says what was read of
    each and the one step as a whole command line."""
    units = (f"{WATCH_UNIT}.timer", f"{WATCH_UNIT}.service")
    for unit in units:  # R167: the stop is sent every run, file or no file
        journal(host, "stop", unit)
        sudo(host, systemctl("stop", unit))
    reads = {u: unit_read(host, u) for u in units}
    if all(r[0] in ("stopped", "gone") for r in reads.values()):
        return True
    rep.stop("the watch was told to stop and reads " + "; ".join(unit_words(u, r) for u, r in reads.items())
             + f": nothing was removed. Your next step: `python3 -I {tool_path()} uninstall --all`")
    return False


def remove_shared(host: Host, rep: Report) -> bool:
    """G6.2: the watch, the slice (stopped first, G6.5), the tool's own folders, then the runner user."""
    ok = True
    for unit in (f"{WATCH_UNIT}.timer", f"{WATCH_UNIT}.service"):
        sudo(host, systemctl("stop", unit))  # R167: sent every run, file or no file, so a run again can finish
        if exists_as_root(host, unit_path(unit)):
            journal(host, "remove", unit)
            sudo(host, systemctl("disable", unit))
            sudo(host, ["rm", "-f", unit_path(unit)])
    sudo(host, systemctl("daemon-reload"))
    # R132 (seen 2026-11-24): a watch whose last pass stopped (exit 2, a repository read public) stays loaded as
    # "failed" after its file is gone, until systemd is told to forget that failure.
    for unit in (f"{WATCH_UNIT}.timer", f"{WATCH_UNIT}.service"):  # R167 (rule 30): one call, one unit
        sudo(host, systemctl("reset-failed", unit))
    for unit in (f"{WATCH_UNIT}.timer", f"{WATCH_UNIT}.service"):
        if not gone_or_stop(host, unit, rep, f"python3 -I {tool_path()} uninstall --all"):
            return False  # R167: a unit not read gone, so this run removes nothing more
    if ok:
        rep.ok("the watch's timer and service are gone, read back")
    if not remove_slice(host, rep):
        return False  # the user stays, so `uninstall --all` run again finishes from here (G6.4)
    # G6.10: any runner's jobs' records still here go to the person's record first; one that cannot stays.
    names = host.listdir_or_none(RECORDS_ROOT)
    if names is None:
        rep.warn(f"{RECORDS_ROOT} could not be read, so the jobs' records in it are kept where they are, not deleted")
        kept_here = [RECORDS_ROOT]
    else:
        kept_here = [x for x in (f"{RECORDS_ROOT}/{n}" for n in names if n != os.path.basename(SEEN_ROOT))
                     if not keep_jobs_record(host, x, rep)]
    # R167 (owner 3:51 PM ET 11-24, answer 6): the tool's copy (HOOKS_ROOT) is the one the person is told to run
    # uninstall from (the skill's own words); it goes LAST (remove_copy), so every step that stops before it names a
    # copy that is still there
    for path in [SEEN_ROOT] if kept_here else [RECORDS_ROOT]:
        if exists_as_root(host, path):
            journal(host, "delete", path)
            if path == RECORDS_ROOT:  # R128: each repository's records folder is the runner user's to empty
                for n in names or []:
                    if n != os.path.basename(SEEN_ROOT):
                        empty_then_rmdir(host, f"{RECORDS_ROOT}/{n}")
                for sub in [SEEN_ROOT]:
                    sudo(host, ["rm", "-rf", "--one-file-system", sub])
                sudo(host, ["rmdir", path])
            else:
                sudo(host, ["rm", "-rf", "--one-file-system", path])
    if any(exists_as_root(host, x) for x in ([SEEN_ROOT] if kept_here else [RECORDS_ROOT])):
        rep.stop(f"could not delete {RECORDS_ROOT}. Run the same command again: it finishes a removal that was not "
                 "finished")
        ok = False
    else:
        rep.ok("the jobs' records are " + (f"kept where they are, not deleted: {', '.join(kept_here)}" if kept_here
                                           else f"in your record ({host.record_dir()}), and {RECORDS_ROOT} is deleted"))
    return ok and remove_runner_user(host, rep)


def journal_copy_removal(host: Host) -> None:
    """R167 (answer 6): the copy's removal is journaled before the journal goes to the person's record."""
    if exists_as_root(host, HOOKS_ROOT):
        journal(host, "delete", f"{HOOKS_ROOT} (the tool's copy and the job hooks: the very last step)")


def remove_copy(host: Host, rep: Report, src: dict) -> bool:
    """R167 (owner 3:51 PM ET 11-24, answer 6): the tool's copy and the job hooks, the very last removal of
    `uninstall --all`: the skill tells the person to run uninstall from this copy, so nothing that may stop comes
    after it. A copy still there names itself; one not read names the clone the person set up from."""
    if exists_as_root(host, HOOKS_ROOT):
        sudo(host, ["rm", "-rf", "--one-file-system", HOOKS_ROOT])
    t = root_test(host, HOOKS_ROOT)
    if t is None:
        rep.stop(f"whether {HOOKS_ROOT} (the tool's copy and the job hooks) is deleted could not be read: everything "
                 f"else is off. Your next step, to run it again: {copy_gone_step(src)}; it finishes here")
        return False
    if t:
        rep.stop(f"could not delete {HOOKS_ROOT} (the tool's copy and the job hooks): everything else is off. Run the "
                 "same command again: it finishes a removal that was not finished")
        return False
    rep.ok(f"the tool's copy and the job hooks are deleted ({HOOKS_ROOT}), the very last removal")
    return True


def copy_gone_step(src: dict) -> str:
    """R167 (owner 4:36 PM ET 11-24, change 2): the step when the installed copy may be gone, as a whole command line:
    the clone setup was run from (kept by setup, R161), or this running copy when it is not the installed one. A
    plugin's version folder is never named (rule 29): Claude Code's own skill is the step. With neither known, the
    installed copy's own line, said as what to try if that copy is still there."""
    if src.get("clone"):
        return f"`python3 -I {os.path.join(src['clone'], 'scripts', 'minions.py')} uninstall --all`"
    here = tool_path()
    if not here.startswith(HOOKS_ROOT + "/") and PLUGIN_CACHE not in here:
        return f"`python3 -I {here} uninstall --all`"
    if src.get("plugin"):
        return f"in Claude Code, ask the skill {src['plugin']} to run its uninstall --all (from the plugin's own folder)"
    return f"`python3 -I {TOOL_COPY} uninstall --all`, if that copy is still there"


PUBLIC_TMP = ("/tmp", "/var/tmp", "/dev/shm")  # the places any user can write outside its home


def remove_runner_user(host: Host, rep: Report) -> bool:
    """R130 (rule 28, the last uninstall): nothing of the runner user outlives it, and a later user of the same name
    inherits nothing. In order: the service is stopped (before this); no process of that user alive (a job's sleep
    hold named as such; the rest ended by that user's id, as that user); its crontab and at jobs off; linger off; what
    it owns in the places anyone can write deleted AS that user, never by root; then userdel -r. Each read back; what
    could not be removed is named, never said gone (rule 6)."""
    kind, uid = runner_user_state(host)
    if kind == "unread":
        rep.stop(f"could not read whether the user {RUNNER_USER} is on this computer ({uid}). Run the same command "
                 "again: it finishes from here")
        return False
    if kind == "there":
        if not load_state(host).get("runner_user_made"):  # R129 (rule 28): never a user this tool did not make
            rep.ok(f"the user {RUNNER_USER} is left as it is: this tool's record here does not say it made it, so it "
                   "is yours (named in the census)")
            rep.stayed["user"] = True  # R136 (rule 8): a foreign thing does not stop a leave
            return True
        if not end_runner_users_processes(host, rep):
            return False
        left = []
        if host.which("crontab"):
            journal(host, "crontab off", RUNNER_USER)
            sudo(host, ["crontab", "-r", "-u", RUNNER_USER])
            rc, out = sudo(host, ["crontab", "-l", "-u", RUNNER_USER])
            if rc == 0 or "no crontab" not in said(out).lower():  # R168: crontab says so on its standard error
                left.append(f"its crontab ({tail(out) or 'still listed'})")
        if host.which("atq"):
            rc, out = sudo(host, ["atq"])
            jobs = [x.split()[0] for x in out.splitlines() if x.split() and x.split()[-1] == RUNNER_USER]
            if rc != 0:  # R167: an answer that is not atq's list is not "no at job"
                left.append(f"its at jobs (could not be read: atq answered code {rc}: {tail(out) or 'nothing'})")
            elif jobs:
                journal(host, "at jobs off", f"{RUNNER_USER}: {', '.join(jobs)}")
                sudo(host, ["atrm", *jobs])
                rc, out = sudo(host, ["atq"])
                still = [x.split()[0] for x in out.splitlines() if x.split() and x.split()[-1] == RUNNER_USER]
                if rc != 0:
                    left.append(f"its at jobs {', '.join(jobs)} (whether they are off could not be read: atq "
                                f"answered code {rc}: {tail(out) or 'nothing'})")
                elif still:
                    left.append(f"its at jobs {', '.join(still)}")
        linger = f"/var/lib/systemd/linger/{RUNNER_USER}"
        if exists_as_root(host, linger):
            journal(host, "linger off", RUNNER_USER)
            sudo(host, ["loginctl", "disable-linger", RUNNER_USER])
            if exists_as_root(host, linger):
                left.append("its linger")
        journal(host, "delete", f"what {RUNNER_USER} owns in {', '.join(PUBLIC_TMP)} (as {RUNNER_USER})")
        as_runner(host, ["find", *PUBLIC_TMP, "-xdev", "-user", RUNNER_USER, "-delete"])
        rc, out = as_runner(host, ["find", *PUBLIC_TMP, "-xdev", "-user", RUNNER_USER, "-print"])
        files = [x for x in out.splitlines() if x.startswith(PUBLIC_TMP)]
        # R167: find exits 1 when a folder it may not enter was met (systemd's private folders in /tmp, on any stock
        # system), and still lists the rest; any other exit is not a list
        if rc not in (0, 1):
            left.append(f"its files in {', '.join(PUBLIC_TMP)} (could not be read: find answered code {rc}: "
                        f"{tail(out) or 'nothing'})")
        if files:
            left.append(f"its files {', '.join(files[:5])}" + (f" and {len(files) - 5} more" if len(files) > 5 else ""))
        if left:
            rep.stop(f"the user {RUNNER_USER} is not deleted: {'; '.join(left)} could not be removed. Settle what it "
                     "says, then run the same command again: it finishes from here")
            return False
        rep.ok(f"nothing of {RUNNER_USER} is left running or scheduled: no process, no crontab, no at job, no linger, "
               f"no file of it in {', '.join(PUBLIC_TMP)}, read back")
        # R128 (rule 28): the home is the runner user's to empty (as that user, following no link); userdel -r then
        # removes an empty folder, so root deletes nothing a job could have placed or re-pointed.
        journal(host, "delete", f"/home/{RUNNER_USER}'s contents (as {RUNNER_USER})")
        as_runner(host, ["find", f"/home/{RUNNER_USER}", "-mindepth", "1", "-delete"])
        journal(host, "userdel", RUNNER_USER)
        sudo(host, ["userdel", "-r", RUNNER_USER])
    if runner_user_state(host)[0] != "absent" or exists_as_root(host, f"/home/{RUNNER_USER}"):
        rep.stop(f"the user {RUNNER_USER} or its home is still there. Run the same command again: it finishes a removal that was not finished")
        return False
    known = load_state(host)
    if known.pop("runner_user_made", None):  # R129: a later user of this name is not ours unless this tool makes it
        save_state(host, known)
    rep.ok(f"the user {RUNNER_USER} and its home are deleted, read back")
    return True


def pids_list_or_none(host: Host) -> list[str] | None:
    """The runner user's processes: the list, or None when pgrep could not answer (R167)."""
    try:
        return pgrep_read(host, ["pgrep", "-u", RUNNER_USER])
    except PidsUnread:
        return None


def end_runner_users_processes(host: Host, rep: Report) -> bool:
    """R130: every process of the runner user ended, read back. A job's sleep hold is named as one (R91); the rest is
    a job's too (the user is this tool's own), and is ended by that user's id: `kill -KILL -1` run AS that user, which
    reaches every process of it and nothing of anyone else's."""
    try:  # R167: pgrep's exit is read: 0 the list, 1 none, anything else could not be read
        left = pgrep_read(host, ["pgrep", "-u", RUNNER_USER])
    except PidsUnread as e:
        rep.stop(f"whether processes of {RUNNER_USER} run could not be read ({e}): the user is not deleted. Run "
                 "the same command again: it finishes from here")
        return False
    ours = our_leftover_holds(host, left)
    if ours:  # R91: a job's sleep hold left behind (a service stopped mid-job): ours, so ended here, by exact PID
        journal(host, "end", f"leftover sleep holds of ours: {', '.join(ours)}")
        as_runner(host, ["kill", "-TERM", *ours])  # R128: as the runner user
        for _ in range(25):
            left = pids_list_or_none(host)
            if left is None or not set(ours) & set(left):
                break
            host.sleep(0.2)
        if left is None:
            rep.stop(f"whether a job's sleep hold ({', '.join(ours)}) is ended could not be read: the user is not "
                     "deleted. Run the same command again: it finishes from here")
            return False
        rep.ok(f"a job's sleep hold left behind by a stopped service ({', '.join(ours)}) is ended")
    if left:
        what = ", ".join(f"{x} ({(host.read(f'/proc/{x}/comm') or '?').strip()})" for x in left)
        journal(host, "end", f"processes of {RUNNER_USER} left by its jobs: {what}")
        for _ in range(3):
            as_runner(host, ["kill", "-KILL", "-1"])
            for _ in range(10):
                left = pids_list_or_none(host)
                if left is None:
                    rep.stop(f"whether processes of {RUNNER_USER} still run could not be read: the user is not "
                             "deleted. Run the same command again: it finishes from here")
                    return False
                if not left:
                    break
                host.sleep(0.2)
            if not left:
                break
        if left:
            what = ", ".join(f"{x} ({(host.read(f'/proc/{x}/comm') or '?').strip()})" for x in left)
            rep.stop(f"processes of {RUNNER_USER} still run after being ended with SIGKILL: {what}. The user is not "
                     "deleted. A process that outlives SIGKILL is waiting on a disk or the network: wait a minute, then "
                     "run the same command again: it finishes from here")
            return False
        rep.ok(f"processes of {RUNNER_USER} left by its jobs ({what}) are ended, read back")
    return True


def last_line(out: str) -> str:
    """R168: the line shown for a refusal: the program's own last line, both streams read (a warning written before
    the command ran, as sudo's, comes first and is not the one shown)."""
    return next((x.strip() for x in reversed(said(out).splitlines()) if x.strip()), "")


def answer_or_words(out: str) -> str:
    """R168 (owner 4:36 PM ET 11-24, words lost): a program's answer for a line the person reads; where it gave none
    on standard output, "not read" with its own last line (its refusal is on standard error), never "not read" alone."""
    return out.strip() or ("not read: " + (last_line(out)[:160] or "nothing"))


def first_words(rc: int, out: str) -> str:
    return f"systemctl answered code {rc}: " + (last_line(out)[:160] or "nothing")


def slice_running_units(host: Host) -> tuple[list[str] | None, str]:
    """R167 (owner 3:51 PM ET 11-24, change 4; rule 30): a stop of the slice ends every unit in it, a job too. So its
    own units are read first: the list of those that read running, or None and why when that could not be read.
    Its units are systemd's own list of them (SliceOf, seen on systemd 255; an older systemd that does not know it
    prints nothing for it) and every unit of our names whose Slice= is ours (a runner whose record was lost)."""
    rc, out = host.run(systemctl("show", SLICE, "-p", "SliceOf"))
    if rc != 0:
        return None, f"the units in {SLICE} could not be read ({first_words(rc, out)})"
    inside = {u for x in out.splitlines() if x.startswith("SliceOf=") for u in x[len("SliceOf="):].split()}
    rc, out = host.run(systemctl("list-units", "--all", "--plain", "--no-legend", "actions.runner.*", "minions*"))
    if rc != 0:
        return None, f"systemd's units of ours could not be listed ({first_words(rc, out)})"
    for u in sorted({x.split()[0] for x in out.splitlines() if x.strip() and not x.startswith("0 ")} - {SLICE} - inside):
        rc, got = host.run(systemctl("show", u, "-p", "Slice"))
        named = [x[len("Slice="):].strip() for x in got.splitlines() if x.startswith("Slice=")]
        if rc != 0 or not named:
            return None, f"which slice {u} is in could not be read ({first_words(rc, got)})"
        if named[0] == SLICE:
            inside.add(u)
    running = []
    for u in sorted(inside):
        kind, what = unit_read(host, u)
        if kind == "unread":
            return None, f"{u} could not be read ({what})"
        if kind == "running":
            running.append(f"{u} ({what})")
    return running, ""


def remove_slice(host: Host, rep: Report) -> bool:
    """G6.5: a slice whose file is deleted stays loaded and active after a reload until it is stopped; so stop it
    first, then delete the file, reload, and read back that it is gone. R167 (rule 30): a stop that would end a
    running job is never sent: the slice's own units are read first, and while one reads running it is not stopped."""
    path = unit_path(SLICE)
    running, why = slice_running_units(host)
    if running is None:
        rep.stop(f"{why}: {SLICE} is not stopped, as its stop would end whatever runs in it, and its file is not "
                 f"deleted. Your next step: `python3 -I {tool_path()} uninstall --all`: it reads again and finishes")
        return False
    if running:
        rep.stop(f"{SLICE} still holds {', '.join(running)}: it is not stopped, as its stop would end what runs there "
                 f"(a job of yours too), and its file is not deleted. Your next step, once no job of yours runs on it "
                 f"(GitHub shows that runner idle or gone): stop that service yourself; then "
                 f"`python3 -I {tool_path()} uninstall --all` finishes")
        return False
    journal(host, "stop", SLICE)
    sudo(host, systemctl("stop", SLICE))
    read = unit_read(host, SLICE)  # R167 (rule 30): read stopped before its file is deleted
    if read[0] not in ("stopped", "gone"):
        rep.stop(f"{SLICE} was told to stop and reads {unit_words(SLICE, read)}: its file was not deleted. Your next "
                 f"step: `python3 -I {tool_path()} uninstall --all`")
        return False
    if exists_as_root(host, path):
        journal(host, "delete", path)
        sudo(host, ["rm", "-f", path])
    sudo(host, systemctl("daemon-reload"))
    read = unit_read(host, SLICE)
    if read[0] == "unread":
        rep.stop(f"whether {SLICE} is gone could not be read ({read[1]}). Your next step: `python3 -I {tool_path()} "
                 "uninstall --all`: it reads again and finishes a removal that was not finished")
        return False
    if read[0] != "gone":
        rep.stop(f"{SLICE} is still loaded or active after its removal. Run the same command again: it finishes a removal that was not finished")
        return False
    rep.ok(f"{SLICE} was stopped before its file was deleted, and is gone, read back")
    return True


def census(gh: GitHub, host: Host, repos: list[str], everything: bool, rep: Report,
           state_left: bool | None = None, skill_folder: dict | None = None, packages: dict | None = None) -> None:
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
    units = sorted({x.split()[0] for x in out.splitlines() if x.strip() and not x.startswith("0 ")}) if rc == 0 else []
    if rc != 0:  # R167: a list systemd did not give is not "none"
        rep.warn(f"census: systemd's units of ours: not read (systemctl answered code {rc}: {tail(out) or 'nothing'})")
    mark = len(unread)
    files = [u for u in ("minions.slice", f"{WATCH_UNIT}.timer", f"{WATCH_UNIT}.service")
             if there(unit_path(u))]
    shared = {SLICE, f"{WATCH_UNIT}.timer", f"{WATCH_UNIT}.service"}
    # R78 (G1.12): a runner service that runs as neither our runner user nor the person is counted, never named,
    # as look does; and the slice is said to be kept for other runners only when runners of ours are left.
    # G1.16, rule 18: only this tool's own (run as its runner user) are named; the person's own are counted.
    named, yours, others = [], 0, 0
    for u in (u for u in units if u.startswith("actions.runner.")):
        urc, who = host.run(systemctl("show", "-p", "User", "--value", u))
        if urc == 0 and who.strip() == RUNNER_USER:
            named.append(u)
        elif urc == 0 and who.strip() == host.user():
            yours += 1
        else:
            others += 1
    if named or not (others or yours):
        line("runner services", named)
    if yours:
        rep.ok(f"census: {plural(yours, 'runner service')} of yours here that this tool did not make (kept; not "
               "named: their names may be your projects')")
    if others:
        rep.warn(f"census: {plural(others, 'runner service')} here not running as you or as {RUNNER_USER} "
                 "(not named: they may be another person's; this tool did not make them)")
    ours_left = bool(load_state(host).get("runners"))
    line("the slice and the watch (scheduled checks)", sorted(set(u for u in units if u in shared) | set(files)),
         "" if everything else ("kept for this tool's other runners here; `uninstall --all` takes them" if ours_left
                                else "kept until `uninstall --all`, which takes them"), mark)
    rc, _ = host.run(["id", RUNNER_USER])
    theirs = rc == 0 and rep.stayed.get("user")  # R136 (rule 8): a user this tool did not make stays, as the person's
    line("the runner user", [RUNNER_USER] if rc == 0 else [],
         "kept: its record here does not say this tool made it, so it is yours" if theirs else
         "" if everything or not ours_left else "kept for this tool's other runners here; `uninstall --all` takes it")
    mark = len(unread)
    folders = [x for x in (HOOKS_ROOT, RECORDS_ROOT, f"/home/{RUNNER_USER}") if there(x)]
    if theirs and f"/home/{RUNNER_USER}" in folders:
        folders.remove(f"/home/{RUNNER_USER}")
        rep.ok(f"census: the folder /home/{RUNNER_USER}: kept: it is the home of the user {RUNNER_USER}, which this "
               "tool did not make; it is yours")
    line("folders", folders, "" if everything else "kept; `uninstall --all` takes them", mark)
    if state_left is not None:
        foreign = rep.stayed.get("state")
        line("this tool's state folder", [host.state_dir()] if state_left else [],
             (f"kept: it holds {', '.join(foreign)}, which this tool did not write; that and the folder are yours"
              if foreign else "kept: its journal could not be moved to your record (said above)") if state_left else "")
    if everything and packages is not None:  # R139 (rule 8): what was added while the dependency script ran stays
        names, unread = packages.get("dependency_packages") or [], packages.get("dependency_packages_unread")
        head = "census: system packages added while the runner's dependency script ran: "
        why = "installed for the whole system, and another program may use them now"
        if names and unread:  # an error is never an absence: both are said
            rep.ok(f"{head}{', '.join(names)}; at one setup the list could not be read, so there may be more (kept, "
                   f"whatever they are: {why})")
        elif names:
            rep.ok(f"{head}{', '.join(names)} (kept: {why})")
        elif unread or packages.get("dependency_packages") is None:  # not read, or a setup that did not record it
            rep.ok(f"{head}not {'read' if unread else 'recorded'} at setup (kept, whatever they are: {why})")
        else:
            rep.ok(f"{head}none")
    if everything:  # G6.13 (R117, rule 8): the folder the skill was downloaded into is named, so nobody guesses at it
        skill_folder_census(host, skill_folder, rep)
    # G6.10, INV6: last, the person's record, kept: theirs.
    n = host.record_count()
    if n is None:
        rep.warn(f"census: your record of what this tool did ({host.record_dir()}): not read")
    elif rep.stayed.get("record") and n != (0, 0):  # R136 (rule 8): the record's own files went; the person's stay
        rep.ok(f"census: your record folder: {host.record_dir()}, kept: it holds {', '.join(rep.stayed['record'])}, "
               "which this tool did not write; that and the folder are yours")
    elif n != (0, 0):
        rep.ok(f"census: your record of what this tool did: {host.record_dir()}, {plural(n[1], 'line')} in "
               f"{plural(n[0], 'file')}, kept: it is yours. Delete it whenever you like, or run `uninstall --all "
               "--delete-record` in your own terminal")


def skill_folder_census(host: Host, src: dict | None, rep: Report) -> None:
    """G6.13 (R117; R161, rule 29 (e)): after `uninstall --all` the census names where the skill came from and whose
    it is: a plugin by its name and what Claude Code's own list says of it (never its version folder); a clone by its
    folder, still there or gone; or not known here. An agent fills a silence (run J: it offered to delete the clone,
    a step the tool never printed), so nothing of the skill's is left unnamed."""
    src = src or {}
    if src.get("plugin"):
        named = plugin_listed(host, src["list"], src["plugin"])
        if named is None:
            rep.warn(f"census: whether the skill {src['plugin']} is still installed in Claude Code: not read (its own "
                     f"list, {src['list']}, could not be read)")
        elif named:
            rep.ok(f"census: the skill {src['plugin']}: still installed in Claude Code (its own list names it); "
                   "Claude Code's own `plugin uninstall` takes it off that list and leaves its folder in Claude Code's cache; "
                   "this tool touches neither")
        else:
            rep.ok(f"census: the skill {src['plugin']}: Claude Code's own list names it nowhere, so it is not "
                   "installed there now")
        return
    root = src.get("clone")
    if not root:
        rep.warn("census: the folder this skill was downloaded into: not known on this computer (setup did not "
                 "write it down)")
        return
    there = host.there(root)
    if there is None:
        rep.warn(f"census: the folder this skill was downloaded into ({root}): not read")
    elif not there:
        rep.ok(f"census: the folder this skill was downloaded into ({root}): gone")
    else:
        rep.ok(f"census: the folder this skill was downloaded into: {root}, still there; it is yours to keep or "
               "delete, and this tool does not touch it")


def uninstall(repo: str | None, everything: bool = False, wait_s: int = DRAIN_WAIT_S, poll_s: float = 10,
           host: Host | None = None, gh: GitHub | None = None, out=None, interactive: bool | None = None,
           delete_record: bool = False) -> int:
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
    target = "--all" if everything else (repo or "")
    rec = " --delete-record" if delete_record else ""
    if not persons_own_eyes(host, rep, uninstall_lines(host, repo, everything, data), interactive,
                            f"`python3 -I {tool_path()} uninstall {target}{rec}`"):
        return rep.code()
    if not ask_admin_once(host, rep, interactive, "uninstall"):
        return rep.code()
    repos = [repo] if repo and not everything else sorted(runners)
    known = sorted(set(repos) | set(data.get("moves") or {}) | ({repo} if repo else set()))
    next_cmd = "uninstall --all" if everything else f"uninstall {repo}"
    if everything and not watch_off(host, rep):
        return rep.code()
    for rp in repos:
        if not remove_one(gh, host, rp, data, rep, wait_s, poll_s, next_cmd):
            return rep.code()
    if not everything and not (load_state(host).get("runners") or {}):
        # R130 (rule 28): the last runner is gone, so its user goes too: nothing a job of this repository left behind
        # (a crontab, an at job, a process, a file in /tmp) is there for the next repository set up here.
        if not remove_runner_user(host, rep):
            return rep.code()
    if everything:
        if not remove_shared(host, rep):
            return rep.code()
        for rp in sorted(data.get("moves") or {}):
            put_back(gh, host, rp, data, rep)
        journal_copy_removal(host)
        journal(host, "delete", f"{host.state_dir()} (the last step but the tool's copy)")
        if keep_journal_record(host, rep):  # G6.10: the journal is the person's record; not kept, not deleted
            left = delete_ours(host.state_dir(), STATE_NAMES)  # R129 (rule 23): only ours, by name
            if left:
                rep.ok(f"this tool's files in {host.state_dir()} are deleted; left there, not written by this tool: "
                       f"{', '.join(left)}")
                rep.stayed["state"] = left  # R136 (rule 8): the person's, named in the census with why
        if delete_record:
            delete_record_if_typed(host, rep)
        remove_copy(host, rep, kept_skill_from(data))  # R167 (answer 6): the copy the person runs uninstall from goes last
        # Rule 8: the census is the last thing printed; it names this tool's state folder and the person's record.
        census(gh, host, known, True, rep, state_left=os.path.isdir(host.state_dir()),
               skill_folder=kept_skill_from(data), packages={k: data.get(k) for k in (
                   "dependency_packages", "dependency_packages_unread")})
        if rep.stayed and not rep.stopped:  # R136 (rule 8): the leave went to its end; what stayed is the person's
            rep.ok("this tool is off this computer; what stayed is yours, named above with why each stayed")
        return rep.code()
    if delete_record:
        delete_record_if_typed(host, rep)
    census(gh, host, known, False, rep)
    return rep.code()


def look(repo: str, start: bool, host: Host | None = None, gh: GitHub | None = None, out=None,
         show_names: bool = False) -> int:
    host = host or Host()
    gh = gh or GitHub(host)
    rep = Report(out)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        rep.stop(f"not a repository name: {repo!r} (write owner/name)")
        return rep.code()
    found: dict = {}
    try:
        return _look(repo, start, host, gh, rep, found, show_names)
    finally:
        try:
            record_look(host, repo, rep, found.get("survey"))
        except OSError as e:
            rep.warn(f"could not write this look down in {host.state_dir()}: {e}; setup will ask for a new look")


def _look(repo: str, start: bool, host: Host, gh: GitHub, rep: Report, found: dict, show_names: bool = False) -> int:
    if not check_github_cli(host, rep, repo):
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
    lives = what_lives_here(host, rep, show_names)
    s["serves"], s["serves_what"] = lives["serves"], lives["serves_what"]  # R72: setup reads it
    check_workflows(gh, repo, s, rep)
    cost = check_cost_rule(host, rep)  # its own line first: a line of another kind would end the block
    before_setup_words(repo, s, lim, floor, rep)
    rep.tell(cost)
    return rep.code()


LONG_SLEEP_MIN = 10  # G7.9: GitHub failed a job about 10 minutes after the computer fell asleep (seen 2026-11-22)
LAPTOP_WORDS = ("a laptop takes jobs only while it is awake, on the network and (by default) on mains power; "
                "`resume <owner/repo> --on-battery-too` lets it take jobs on battery as well")


MEMORY_TRADE_OFF = ("A job that needs more memory than this computer's limit for jobs fails when it reaches it, and "
                    "`status` names it, instead of running slowly; jobs are kept out of this computer's swap where it "
                    "holds that.")
JOB_TOOLS = ("A job here has only the tools installed on this computer: a job that stops with \"command not found\" "
             "needs that tool installed here.")  # G7.12: what GitHub's computers carry is only good to know (README)


def before_move_words(s: dict, rep: Report) -> None:
    """G7.14 (R111): what the person must hear before their merge moves jobs here, printed by `move` as its own
    numbered block just before it asks for that merge (the owner's ruling, 2026-11-23: each line at the moment it
    matters; nine lines before setup were more than the smallest model carried whole)."""
    rep.tell("While this computer is off, asleep or held, your checks wait, and GitHub cancels a check that has "
             f"waited {GITHUB_GIVES_UP_H} hours.")
    rep.tell("A restart of this computer cancels the job running on it, a short sleep only delays it, and a sleep of "
             f"more than about {LONG_SLEEP_MIN} minutes fails it on GitHub, which `status` names as cut, not as your "
             "test failing; while a job runs, this computer does not fall asleep by itself, but a sleep you ask for, "
             f"the lid or a low battery still puts it to sleep, {sleep_delay_words(s.get('sleep_delay'))}.")
    rep.tell(JOB_TOOLS)


def before_setup_words(repo: str, s: dict, lim: dict, floor: str | None, rep: Report) -> None:
    """G7.10 (P1a run B): what the person must hear before setup, printed by the tool for the agent to pass on word
    for word. A list in the skill file was skipped by a small model; a line the tool prints is not. Only what matters
    before setup (G7.14): the rest is said where it matters."""
    rep.tell(f"Anyone who can push to {repo} can run code on this computer, as its own user {RUNNER_USER}, which "
             "has no admin rights.")
    rep.tell(f"This is for a private repository only: the tool refuses a public one, and if {repo} is made public "
             "later its runner stops taking jobs, because on a public one a stranger's pull request could run code on "
             "this computer.")
    rep.tell(floor or limits_in_words(lim))
    # G7.14 (R111): waiting checks, restarts and sleep, and a job's tools are said by `move`, before the merge that
    # moves jobs here; the memory cap's trade-off by `status`, where it names a job the cap stopped.
    if s.get("battery"):
        rep.tell("This computer has a battery: " + LAPTOP_WORDS + ".")
    elif s.get("lid"):
        rep.tell("This computer has a lid: " + LAPTOP_WORDS + ".")
    rep.tell("This version serves one repository per computer.")  # G7.12: no promise about a later version



class _Lines(list):
    """The lines of a block, collected by the same functions that tell them (one wording, G7.15)."""

    def tell(self, text: str) -> None:
        self.append(text)


ADMIN_MAKES = (f"With your admin password, setup makes the user {RUNNER_USER}, GitHub's runner as a service that starts "
               "at boot, a slice that holds the limits, job hooks and the watch (a timer that runs this tool's own copy "
               "every 5 minutes), and it writes each step first in a journal that is kept for you as your record.")
MEMORY_BEFORE_SETUP = ("A job that needs more memory than this computer's limit for jobs is stopped when it reaches it, "
                       "and `status` names it; where this computer cannot keep jobs out of its swap, such a job may run "
                       "slowly instead, and setup says so.")  # true either way: whether swap is held is read only later
LEAVING_KEEPS = ("`uninstall --all` takes all of it off this computer again and keeps your record of what it did; "
                 "deleting that record is only ever your own choice, typed by you.")


def setup_lines(repo: str, s: dict, lim: dict) -> list[str]:
    """G7.15 (R112): what the person must hear before setup changes anything, read on their own screen: look's block,
    the memory cap's trade-off, move's block, what admin rights make, what leaving takes and keeps, the cost pointer."""
    first, then = _Lines(), _Lines()
    before_setup_words(repo, s, lim, None, first)
    before_move_words(s, then)
    one = first.pop()  # "This version serves one repository per computer."
    return [*first[:3], MEMORY_BEFORE_SETUP, *first[3:], *then, one, ADMIN_MAKES, LEAVING_KEEPS, COST_POINTER]


def uninstall_lines(host: Host, repo: str | None, everything: bool, data: dict) -> list[str]:
    """G7.15 (R112): what the person must hear before uninstall changes anything: what it takes off, that their record
    is kept, and the pull request that puts moved jobs back (theirs to merge; its own checks start when it opens)."""
    record = os.path.join(host.home(), RECORD_NAME)
    moves = data.get("moves") or {}
    if everything:
        lines = ["This takes off this computer every runner of this tool, the watch, the slice, the user "
                 f"{RUNNER_USER} and this tool's copy, holding new jobs first so that none starts here while it works."]
    elif set(data.get("runners") or {}) <= {repo}:  # R130: the last runner here; its user goes with it
        lines = [f"This takes the runner for {repo} off this computer, holding new jobs first so that none starts here "
                 f"while it works, then the user {RUNNER_USER} with everything its jobs left running or scheduled; "
                 "the watch and the slice stay until `uninstall --all`."]
    else:
        lines = [f"This takes the runner for {repo} off this computer, holding new jobs first so that none starts here "
                 "while it works, and leaves the watch, the slice and the user until `uninstall --all`."]
    lines.append(f"Your record of what this tool did is kept in {record}; deleting it is only ever your own choice.")
    if everything and moves:
        lines.append("Then it opens a pull request that puts each moved job back where it ran before, which you merge "
                     "and which starts its own checks when it opens, as any pull request does; until you merge it, "
                     "those jobs wait for a runner.")
    elif not everything and repo in moves:
        lines.append(f"The jobs of {repo} moved here still ask for this tool's runner, so they wait for one until a "
                     "pull request puts them back; `uninstall --all` opens it.")
    return lines


def persons_own_eyes(host: Host, rep: Report, lines: list[str], interactive: bool, command: str) -> bool:
    """G7.15 (R112, rule 7): the person's own terminal shows what they must hear before anything changes, and the
    command goes on only when they press Enter there. Where nobody can press a key (an agent's shell), nothing is
    changed and the command for the person is printed. `command` is the backticked line, as printed."""
    if not interactive:
        rep.need(f"this step is yours, in your own terminal: run {command} there. Before it changes anything it shows "
                 "you what you must read, and it goes on only when you press Enter")
        return False
    rep.here = True
    for text in lines:
        rep.tell(text)
    rep.need("read the lines above, then press Enter here to go on; Ctrl-C stops it, and nothing has been changed yet",
             blocks=False, answered_here=True)
    rep.here = False
    if not host.read_enter():
        rep.stop("no Enter was read: nothing was changed. Run the same command again in your own terminal")
        return False
    rep.ok("you read the lines and pressed Enter")
    return True


def tool_path() -> str:
    """The path of this running copy of the tool, for a command the person types (G7.15)."""
    return os.path.realpath(__file__)

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
    lk.add_argument("--show-names", action="store_true",
                    help="name the containers and other runner services found here (their names may be your projects')")
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
    rm.add_argument("--delete-record", action="store_true",
                    help="also delete your record of what this tool did; asks you to type delete at your terminal")
    rm.add_argument("--wait-minutes", type=int, default=DRAIN_WAIT_S // 60, help="how long to wait for a job to end")
    return ap


PYTHON_FLOOR = (3, 10)


def python_floor_line(v: tuple) -> str:
    return (f"stop this tool needs Python {PYTHON_FLOOR[0]}.{PYTHON_FLOOR[1]} or newer, and this computer's python3 is "
            f"{v[0]}.{v[1]}.{v[2]}; nothing was changed. This version runs where the system's own Python is new enough "
            "(Ubuntu 22.04 or newer, Debian 12 or newer): use such a computer for its jobs")


def umask_floor() -> int:
    """R134 (rule 28, NOTHING A SKILL MAKES IS WRITABLE BY ANOTHER ACCOUNT): whatever umask the tool was started with
    (a launcher can start it with 000), 022 is added before anything is made, so no file or folder of this tool, and
    nothing root makes for it (sudo starts from this umask too), is writable by another account. Returns the umask now."""
    given = os.umask(0o022)
    os.umask(given | 0o022)
    return given | 0o022


def main(argv: list[str] | None = None, host: Host | None = None, out=None, gh: GitHub | None = None) -> int:
    """G8.3's table: the command's own code (0, 2, 3, 4); 64 typed wrong; 1 the tool failed; 130 interrupted."""
    a = parser().parse_args(argv)
    host = host or Host()
    out = out or sys.stdout
    umask_floor()
    if sys.version_info < PYTHON_FLOOR:  # R95: before any step, so nothing is made that this Python cannot finish
        print(python_floor_line(sys.version_info), file=out)
        return EXIT_STOP
    try:
        if a.cmd == "look":
            return look(a.repo, a.start_test, host=host, gh=gh, out=out, show_names=a.show_names)
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
                          out=out, delete_record=a.delete_record)
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
