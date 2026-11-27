"""Tests for scripts/minions.py, one per codon of the plan (DNA-STRAND-MASTER-PLAN.md), plus helpers.

GitHub is stood in for by answers recorded from a real private repository (tests/fixtures/*.jsonl, scrubbed by
tests/fixtures/scrub.py); the computer by FakeHost. Run: python tests/test_minions.py -v
"""
import ast
import atexit
import copy
import importlib.util
import inspect
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
import tempfile
import threading
import unicodedata
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
import machine_guard  # noqa: E402  rule 17 (R142): first, so no test here reaches this machine
import version_places  # noqa: E402  R194: the gates' reads of a version from a name, one place

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import minions as M  # noqa: E402
M.VERSION_ON_DISK = M.VERSION

FIX = os.path.join(ROOT, "tests", "fixtures")
TMP = tempfile.mkdtemp(prefix="minions-test-")
atexit.register(shutil.rmtree, TMP, True)
REPO = "example-org/example-repo"


def _read(*args, **kw):
    """R177: a file's text, the file closed again (open(...).read() leaves it to the collector: a ResourceWarning)."""
    with open(*args, **kw) as f:
        return f.read()


def close_pipes(p):
    """R177: a child's pipes, closed once it has ended (Popen leaves them to the collector: a ResourceWarning)."""
    for f in (p.stdin, p.stdout, p.stderr):
        if f is not None:
            try:
                f.close()
            except OSError:  # words still buffered for a child that has ended
                pass
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

# Rule 31 (v), 2026-11-24: a command's line is read where it is printed. Every Out a road prints to
# is kept here as printed, and ZRule31 (the last class) reads every line of every road the suite ran. The word checks
# of the classes before it read the same output folded back: each "the command below" with its command in backticks.
PRINTED: list = []  # (test id, Out), every Out made
HOOKS: list = []  # (test id, lines): what the job hooks printed into a job's log, run for real by hook_run
REF = re.compile(r"the (?:(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+th) )?command below")


def unfold(lines):
    """Each printed sentence with the command lines that follow it, as many as it names."""
    out, i = [], 0
    while i < len(lines):
        n = len(REF.findall(lines[i]))
        out.append((lines[i], lines[i + 1:i + 1 + n]))
        i += 1 + n
    return out


def folded(lines):
    out = []
    for sentence, commands in unfold(lines):
        it = iter(commands)
        out.append(REF.sub(lambda m: "`" + next(it, "MISSING") + "`", sentence))
    return out


def as_said(text):
    """A text of the tool's as its line reads folded back (for a check against one of its own constants)."""
    return "\n".join(folded(M.render(text)))


def test_of_stack():
    f = sys._getframe(1)
    while f is not None:
        me = f.f_locals.get("self")
        if isinstance(me, unittest.TestCase):
            return me.id()
        f = f.f_back
    return "?"


class Out(io.StringIO):
    """What a road prints. getvalue() is the words folded back; printed() is the lines as printed."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        PRINTED.append((test_of_stack(), self))

    def printed(self):
        """The lines as a terminal gets them: split on a line feed only, so a carriage return, a form feed or
        U+2028 a value carried stays inside its line where ZRule31 reads it (str.splitlines would split on them)."""
        raw = super().getvalue()
        return (raw[:-1] if raw.endswith("\n") else raw).split("\n") if raw else []

    def getvalue(self):
        raw = super().getvalue()
        return "\n".join(folded(raw.splitlines())) + ("\n" if raw.endswith("\n") else "")


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


def systemd_words(value):
    """A stand-in for how systemd reads a unit's value (R190): its specifiers first ("%%" is one percent sign; any
    other % is refused here, it would name something else), then words split at blanks, a double-quoted word kept
    whole with its C escapes (here only \\\\ and \\"). None when the value is not read as written words."""
    out, i = [], 0
    while i < len(value):
        if value[i] == "%":
            if value[i + 1:i + 2] != "%":
                return None
            out.append("%"); i += 2
        else:
            out.append(value[i]); i += 1
    v, words, i = "".join(out), [], 0
    while i < len(v):
        if v[i] in " \t":
            i += 1
            continue
        word = []
        if v[i] == '"':
            i += 1
            while i < len(v) and v[i] != '"':
                if v[i] == "\\":
                    if v[i + 1:i + 2] not in ("\\", '"'):
                        return None
                    word.append(v[i + 1]); i += 2
                else:
                    word.append(v[i]); i += 1
            if i >= len(v):
                return None  # a quote never closed
            i += 1
            if i < len(v) and v[i] not in " \t":
                return None  # a word that goes on after its closing quote: not the one value meant
        else:
            while i < len(v) and v[i] not in " \t":
                if v[i] in '"\\':
                    return None
                word.append(v[i]); i += 1
        words.append("".join(word))
    return words


def bus_stand_in(host):
    """busctl's answers (R190) from the unit text this tool last wrote, read as systemd_words reads it."""
    def unit_text_of(unit):
        path = M.unit_path(unit) if unit.startswith("actions.runner.") else f"/etc/systemd/system/{unit}"
        for argv, text in reversed(host.fed):
            if argv[-2:] == ("tee", path):
                return text
        return None

    def answer(argv, input=None):
        argv = list(argv)
        if "LoadUnit" in argv:
            return 0, json.dumps({"type": "o", "data": ["/org/freedesktop/systemd1/unit/" + argv[-1]]})
        if "get-property" in argv:
            unit, prop = argv[argv.index("get-property") + 2].rsplit("/", 1)[-1], argv[-1]
            text = unit_text_of(unit)
            if text is None:
                return 1, "Failed to get property: Unit not loaded."
            vals = [x.split("=", 1)[1] for x in text.splitlines() if x.startswith(prop + "=")]
            if prop == "ExecStart":
                ws = [systemd_words(x) for x in vals]
                data = [[w[0], w] for w in ws if w]
            elif prop == "Environment":
                data = [w for x in vals for w in (systemd_words(x) or ["(not read as written)"])]
            else:
                data = vals[-1] if vals else ""
            return 0, json.dumps({"type": "x", "data": data})
        return 1, ""
    return answer


STREAM_WARNING = "warning: a line on standard error that is not part of any answer (R168)\n"


class FakeHost(M.Host):
    def __init__(self, system="Linux", files=None, runs=None, which=None, uid=1000,
                 groups=("users",), dirs=None, links=None, page="", free=(100 * 1024 ** 3, 500 * 1024 ** 3)):
        self._system, self.files, self.runs = system, dict(files or {}), dict(runs or {})
        # R160: gh has a login it can read with no session bus (a made-up value, one line, as `gh auth token` prints)
        self.runs.setdefault(("gh", "auth", "token"), (0, "made-up-login-value\n"))
        # R208: the watch's first pass is read to have ended before setup writes again
        self.runs.setdefault(tuple(M.systemctl("show", "-p", "ActiveState", "--value", f"{M.WATCH_UNIT}.service")),
                             (0, "inactive\n"))
        # R172: a computer with our runner on it has its user (setup_host starts without it, until useradd)
        self.runs.setdefault(("getent", "passwd", "minions-runner"),
                             (0, "minions-runner:x:1001:1001::/home/minions-runner:/bin/bash\n"))
        self.runs.setdefault(("gh", "auth", "login", "--hostname", "github.com", "--help"), (0, GH_LOGIN_HELP))
        self.runs.setdefault(M.BUS, bus_stand_in(self))  # R190: what systemd parsed, from the text the tool wrote
        if which is None:  # every program the tool and its hooks run (G8.9); no container engine
            which = ("gh", "sudo", "systemctl", *M.PROGRAMS, *M.HOOK_PROGRAMS)
        self._which, self._uid, self._groups = set(which), uid, list(groups)
        self.dirs, self.links, self.page, self.free = dict(dirs or {}), dict(links or {}), page, free
        self.ran, self.fed = [], []  # every argv run; (argv, standard input) of every run_in
        self._state = tempfile.mkdtemp(dir=TMP)
        self.home_mode, self.download, self.validated, self.tty = 0o750, b"a stand-in runner archive", 0, True
        self.enter, self.read_enters = True, 0  # G7.15: the person's own terminal; Enter pressed
        self.broken = False  # rule 7's twice-taken test: a stand-in made to fail until the printed step is taken

    def _answer(self, argv, input=None):
        if list(argv[:2]) == ["pgrep", "-u"] and str(argv[2]).isdigit():
            # R172: the tool asks pgrep by the runner user's number; a stand-in written for its name answers it
            argv = [*argv[:2], "minions-runner", *argv[3:]]
        for k in sorted(self.runs, key=len, reverse=True):
            if tuple(argv[:len(k)]) == k:
                a = self.runs[k]
                return a(argv, input) if callable(a) else a
        return 1, ""

    def run_in(self, argv, input, timeout=600):
        self.ran.append(tuple(argv))
        self.fed.append((tuple(argv), input))
        return self.warned(argv, *self.answer_marked(argv, input))

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
        return self.warned(argv, *self._answer(["own", *argv]))

    def marked(self, argv):
        """R167: root_test's marked test -e (sh -c SCRIPT sh PATH) is answered by the stand-ins for `test -e PATH`
        under the same sudo; their (0, "") / (1, "") become the test's own mark, any other answer is passed on with no
        mark. A stand-in set for the marked form itself is used as it is."""
        argv = list(argv)
        for i in range(len(argv) - 4):
            if argv[i:i + 2] == ["sh", "-c"] and argv[i + 2] == M.TEST_SH and len(argv) == i + 5:
                if any(len(k) > i and tuple(argv[:len(k)]) == k for k in self.runs):
                    return None
                return argv[:i] + ["test", "-e", argv[i + 4]]
        return None

    def answer_marked(self, argv, input=None):
        inner = self.marked(argv)
        if inner is None:
            return self._answer(argv, input)
        rc, out = self._answer(inner, input)
        if rc in (0, 1) and not out.strip():
            return 0, (M.TEST_THERE if rc == 0 else M.TEST_NOT_THERE) + "\n"
        return rc, out

    def warned(self, argv, rc, out):
        """R167: sudo's own warning, written before the command's words, on every sudo call (as a computer whose name
        does not resolve gets it: the command runs and its exit is passed on).
        R168 (MINIONS_TEST_STREAMS=1, the whole suite run again by test_G6_28): each answer on the stream its program
        writes it on: standard output at exit 0, standard error otherwise, but for the words systemctl's is-active and
        is-enabled print whatever their exit; and a warning line on standard error at EVERY command."""
        w = getattr(self, "sudo_warning", "")
        if w and argv[:1] in (["sudo"], ("sudo",)):
            out = M.Out(out, w + out.err) if isinstance(out, getattr(M, "Out", ())) else w + out  # sudo's: standard error
        if os.environ.get("MINIONS_TEST_STREAMS") == "1" and isinstance(out, getattr(M, "Out", ())) and \
                tuple(argv[:3]) == ("gh", "auth", "token") and rc != 127:
            return rc, M.Out(out, STREAM_WARNING + out.err)  # R204: gh auth token's planted streams, a warning first too
        if os.environ.get("MINIONS_TEST_STREAMS") != "1" or isinstance(out, getattr(M, "Out", ())):
            return rc, out
        a = list(argv)
        words_on_out = rc == 0 or any(a[i:i + 2] in (["systemctl", "is-active"], ["systemctl", "is-enabled"],
                                                     ["gh", "api"]) for i in range(len(a)))  # gh api: the answer too
        # R210: list-unit-files writes its list and its count line on standard output at exit 1 too (none listed;
        # seen, systemd 255.4); its refusals go to standard error
        words_on_out = words_on_out or ("list-unit-files" in a and "unit files listed." in out)
        stdout, stderr = (out, STREAM_WARNING) if words_on_out else ("", STREAM_WARNING + out)
        if not hasattr(M, "Out"):  # the code before R168 (a red run): its Host joined the two streams, as here
            return rc, stdout + stderr
        return rc, M.Out(stdout, stderr)

    def run(self, argv, timeout=20):
        self.ran.append(tuple(argv))
        rc, out = self.warned(argv, *self.answer_marked(argv))
        if rc == 0 and argv[:3] == ["sudo", "-n", "install"] and argv[-1] == M.TOOL_COPY:  # G4.10: the copy is there
            with open(argv[-2], encoding="utf-8") as f:
                self.files[M.TOOL_COPY] = f.read()
        return rc, out

    def read(self, path):
        return self.files.get(path)

    def read_checked(self, path):
        """R136 (item 4): (text, why it could not be read): a path in `unreadable` answers as a refused read does."""
        if path in getattr(self, "unreadable", ()):
            return None, f"[Errno 13] Permission denied: '{path}'"
        return self.files.get(path), None

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

    def keep_private(self, name, data):  # R209: as the real one: never over an earlier one, 600 from its first byte
        path = f"{self.record_dir()}/{name}"
        if getattr(self, "record_unwritable", False):
            raise OSError(28, "No space left on device")
        if path in self.files:
            raise FileExistsError(17, "File exists", path)
        self.files[path] = data.decode("utf-8", "surrogateescape")
        self.modes = {**getattr(self, "modes", {}), path: 0o600}
        return path

    def read_private(self, path):
        return self.files[path].encode("utf-8", "surrogateescape")

    def record_names(self):
        return sorted(k[len(self.record_dir()) + 1:] for k in self.files if k.startswith(self.record_dir() + "/"))

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

    def account(self):  # R190: the account by its number; a test that plants LOGNAME sets another
        return getattr(self, "_account", "person")

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


LUF_NONE = "UNIT FILE STATE PRESET\n\n0 unit files listed.\n"  # R210: systemd 255's list-unit-files with no match


def linux_host(**kw):
    files = {"/etc/os-release": 'PRETTY_NAME="Example Linux 1"\nID=ubuntu\nID_LIKE=debian\n',
             "/proc/meminfo": MEMINFO, "/proc/net/route": ROUTE, "/proc/net/tcp": TCP,
             "/sys/devices/nvme0n1/queue/rotational": "0\n"}
    files.update(kw.pop("files", {}))
    runs = {("gh", "auth", "status"): (0, SCOPES), ("gh", "api", "-i", "user"): (0, USER_OK), ("sudo", "-n", "true"): (0, ""),
            ("systemctl", "is-enabled"): (0, "enabled\nenabled\n"),
            # R210: as systemd 255 answers on a computer with none of these: list-units prints nothing, exit 0;
            # list-unit-files its legend and "0 unit files listed.", exit 1 (seen on this suite's own machine, 255.4)
            ("systemctl", "list-units"): (0, ""), ("systemctl", "list-unit-files"): (1, LUF_NONE)}
    runs.update(kw.pop("runs", {}))
    links = {"/sys/dev/block/259:2": "/sys/devices/nvme0n1/nvme0n1p2"}
    with open(os.path.join(FIX, "billing-page-excerpt.html"), encoding="utf-8") as f:
        page = f.read()
    dirs = {M.SYSTEMD_RUN_DIR: []}  # R155: systemd runs this stand-in computer, as it does a stock Ubuntu
    dirs.update(kw.pop("dirs", {}))
    return FakeHost(files=files, runs=runs, links=links, page=kw.pop("page", page), dirs=dirs, **kw)


def look(gh, host, start=False):
    out = Out()
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

    def test_G1_22_a_stopped_look_changes_only_its_own_entry(self):
        # Row R165, read in the code 2026-11-24 with R160's cure: on a computer that already holds the tool's record,
        # a look that stops writes that repository's look entry, marked stopped with an empty survey, over the
        # earlier one, as designed. Held here: every other byte of the record, and of its copy, is the same.
        import json
        host, _ = doctor_world()
        data = M.load_state(host)
        data["looks"]["other-org/other-repo"] = {"time": "2026-01-01T00:00:00Z", "stopped": False,
                                                 "survey": {"system": "Linux", "cores": 2}}
        M.save_state(host, data)
        path, copy = M.state_path(host), M.copy_path(host)
        before = _read(path, encoding="utf-8")
        self.assertEqual(before, json.dumps(json.loads(before), indent=1, sort_keys=True))  # the writer's own form
        self.assertFalse(json.loads(before)["looks"][REPO]["stopped"])
        rows = with_repo(self.recent, lambda d: d.update(visibility="public", private=False))
        code, lines = look(StandIn(rows), host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        after = _read(path, encoding="utf-8")
        entry = json.loads(after)["looks"][REPO]
        self.assertTrue(entry["stopped"], entry)  # so setup asks for a new look
        self.assertEqual(entry["survey"], {})
        want = json.loads(before)
        want["looks"][REPO] = entry
        self.assertEqual(after, json.dumps(want, indent=1, sort_keys=True))  # byte for byte, but for that entry
        self.assertEqual(_read(copy, encoding="utf-8"), after)

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
        self.assertIn("ok your login has the workflow scope (needed to change workflow files)", lines)
        # R97: gh 2.4.0 (Ubuntu 22.04's) prints no scopes; GitHub's own header on one answer gives them.
        old_gh = "github.com\n  ✓ Logged in to github.com as example-person (/home/x/.config/gh/hosts.yml)\n" \
                 "  ✓ Token: *******************\n"
        header = "HTTP/2.0 200 OK\r\nX-Oauth-Scopes: gist, read:org, repo, workflow\r\nX-Github-Media-Type: github.v3\r\n\r\n{}"
        host = linux_host(runs={("gh", "auth", "status"): (0, old_gh), ("gh", "api", "-i", "user"): (0, header)})
        self.assertEqual(M.token_scopes(host), {"gist", "read:org", "repo", "workflow"})
        _, lines = look(StandIn(self.recent), host)
        self.assertIn("ok your login has the workflow scope (needed to change workflow files)", lines)
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
        self.assertTrue(any(x.startswith("stop start test: " + as_said(M.zero_job_words(REPO))) for x in lines), lines)
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
            out = Out()
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
                                                               "workflow without workflow scope"})})
        code, lines = look(gh, linux_host(), start=True)
        self.assertTrue(any("the workflow scope is missing" in x for x in lines), lines)
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
        self.assertIn(f"warn 1 container(s) of yours running: not named here (`look {REPO} --show-names` names "
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
        out = Out()
        M.main(["look", REPO], host=host, out=out, gh=StandIn(self.recent))
        said = out.getvalue()
        self.assertNotIn("client-acme-db", said)
        self.assertNotIn("box-r", said)
        self.assertIn("1 container(s) of yours running: not named here", said)
        self.assertIn("1 other runner service(s) of yours here: not named here", said)
        out = Out()
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
        self.assertIn(f"warn 1 other runner service(s) of yours here: not named here (`look {REPO} --show-names` "
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
        out = Out()
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
        out = Out()
        M.check_admin(mac, M.Report(out))
        self.assertIn("member of admin", out.getvalue())


# ------------------------------------------------------------------------------------------------------------ G2

FOLDER = "/home/minions-runner/runners/example-org-example-repo"
UNIT = "actions.runner.example-org-example-repo.minions-box-1.service"
R_TEST = ("sudo", "-n", "-u", "minions-runner", "--", "test", "-e")
R_ROOT = ("sudo", "-n", "test", "-e")  # root_test of a path of root's own, as the stand-ins answer it
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
        # R172: getent's own code for a key it did not find, until setup made the user (or a record says it did)
        ("getent", "passwd", "minions-runner"): lambda argv, input=None: (
            (0, "minions-runner:x:1001:1001::/home/minions-runner:/bin/bash\n")
            if any(a[:3] == ("sudo", "-n", "useradd") for a in host.ran) or M.load_state(host).get("runner_user_made")
            or any(k[:3] == ("pgrep", "-u", "minions-runner") for k in host.runs)  # a test of its processes has it
            else (2, "")),
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
        ("sudo", "-n", "-u", "minions-runner", "--", "sh", "-c", M.ENV_SH, "sh", f"{FOLDER}/.env"):
            (0, "minions-env: readable\n"),  # R206
        ("sudo", "-n", "-u", "minions-runner", "--", "cat", f"{FOLDER}/.env"): (0, "LANG=C.UTF-8\n"),
        ("sudo", "-n", "-u", "minions-runner", "--", "tee", f"{FOLDER}/.env"): (0, ""),
        ("sudo", "-n", "stat", "-c", "%a %U", f"{M.hook_dir(REPO)}/job-started.sh"): (0, "755 root\n755 root\n"),
        ("sudo", "-n", "-u", "minions-runner", "--", f"{FOLDER}/bin/Runner.Listener", "--version"): (0, "2.338.2\n"),
    })
    # G5.15: setup's first watch pass runs the real watch, as the timer would, against a GitHub that reads it private.
    def first_pass(argv, inp):
        M.watch(host=host, gh=StatusGH(), out=Out())
        return 0, ""
    host.runs[("sudo", "-n", "systemctl", "start", "minions-watch.service")] = first_pass
    # R167 (rule 30): the runner's unit as systemd answers it: active once set up, inactive once told to stop, not
    # found once its file is deleted; `show` with LoadState and ActiveState answers those two fields only, as real
    # systemctl does (the setup reads above ask for other fields, and SHOW answers those).
    unit_now = {"active": True, "file": True}
    def unit_said(key, value):
        def f(argv, inp):
            unit_now[key] = value
            return 0, ""
        return f
    def unit_show(argv, inp):
        # its file is there while this stand-in says so to root's own test -e (a test may say it is not)
        there = unit_now["file"] and host._answer(["sudo", "-n", "test", "-e", M.unit_path(UNIT)])[0] == 0
        if not there and not unit_now["active"]:  # systemd keeps an active unit loaded after its file goes
            return 0, "LoadState=not-found\nActiveState=inactive\nFragmentPath=\nDropInPaths=\n"
        return 0, (f"LoadState=loaded\nActiveState={'active' if unit_now['active'] else 'inactive'}\n"
                   f"FragmentPath={M.unit_path(UNIT)}\nDropInPaths=\n")
    host.runs[("systemctl", "show", UNIT, "-p", "LoadState", "-p", "ActiveState")] = unit_show
    host.runs[("sudo", "-n", "systemctl", "stop", UNIT)] = unit_said("active", False)
    for verb in (("start",), ("restart",), ("enable", "--now")):
        host.runs[("sudo", "-n", "systemctl", *verb, UNIT)] = unit_said("active", True)
    host.runs[("sudo", "-n", "rm", "-f", M.unit_path(UNIT))] = unit_said("file", False)
    host.runs[("systemctl", "is-active", UNIT)] = lambda argv, inp: ((0, "active\n") if unit_now["active"] and
                                                                     unit_now["file"] else (3, "inactive\n"))
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
    out = Out()
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
    out = Out()
    code = M.doctor(REPO, host=host, gh=setup_gh(sha), out=out, interactive=kw.pop("interactive", False), **kw)
    return code, out.getvalue().splitlines()


class G2(unittest.TestCase):
    def test_G6_12_the_runner_says_the_skill_was_removed_and_the_way_off(self):
        # The family's rule 8 (2026-11-23): a runner is what the person set up, so it goes on after
        # Claude Code's own plugin uninstall, and its own report says so, with the one command that takes it off.
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
            M.watch(host=host, gh=setup_gh(sha), out=Out())
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
        # rule 29 (e): a key with no row, or a row with no installPath, was not seen: could not be read, never "removed"
        for odd in ([], [{"scope": "user", "version": "0.1.0"}], [{"installPath": ""}], {"installPath": old}):
            host.files[lst] = json.dumps({"version": 2, "plugins": {"repo-minions@the-catalog": odd}})
            self.assertTrue(lines_now() and "could not be read" in lines_now()[0] and "nowhere" not in lines_now()[0],
                            (odd, lines_now()))
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
            self.assertIn(f"`setup {REPO}` again puts this version's copy in place", line)
            self.assertTrue(lines[-1].startswith("stop ") and "the watch's copy of the tool" in lines[-1], lines[-1])
            self.assertTrue(any("the watch runs the older code" in x for x in status()))
            # The step that line names is true: setup again on the installed runner puts this version's copy in
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
        skill = _read(os.path.join(ROOT, "skills", "repo-minions", "SKILL.md"))
        self.assertIn("This version serves one repository per computer.", skill)
        self.assertNotIn("next version", skill)

    def test_G6_41_a_look_stamped_later_than_the_clock_is_said_so(self):
        # Row R213, seen 2026-11-26 in a stand-in trial: "the last look of ... is -4848 minutes old". A look stamped
        # later than this computer's clock (a clock moved back after it) is said as such, with both times, never as a
        # negative age, and is not taken for a fresh look: the step is a new look; nothing is touched.
        for minutes in (-4848, -2, -1):
            host, sha = setup_host()
            fresh_look(host, minutes=minutes)
            stamped = M.load_state(host)["looks"][REPO]["time"]
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual((code, len(lines)), (M.EXIT_STOP, 1), lines)
            line = lines[0]
            self.assertNotRegex(line, r"-[0-9]+ minutes", line)
            self.assertNotIn("minutes old", line)
            self.assertIn(f"the last look of {REPO} is stamped {stamped}, later than this computer's clock (", line)
            now = re.search(r"later than this computer's clock \(([0-9TZ:-]+)\)", line).group(1)
            self.assertLess(now, stamped, line)
            self.assertIn(f"`look {REPO}`", line)
            self.assertEqual(admin_steps(host), [], "nothing is touched")
        # a look within the hour, stamped before the clock, still passes this check (G2.1 holds the rest)
        host, sha = setup_host()
        fresh_look(host, minutes=5)
        rep = M.Report(out=Out())
        self.assertIsNotNone(M.check_fresh_look(host, REPO, rep), rep.lines)

    def test_G2_1_setup_needs_a_fresh_clean_look(self):
        host, sha = setup_host()
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue(lines[-1].startswith(f"stop no look of {REPO}"), lines)
        for kw, want in ((dict(minutes=61), "minutes old"), (dict(stopped=True), "ended with a stop"),
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
        self.assertIn("ended with a stop", lines[-1])

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
                                         ("getent", "passwd", "minions-runner"): (0, "minions-runner:x:998:998::/home/minions-runner:/bin/bash\n")})
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
        rep = M.Report(Out())
        rel = M.runner_release(StandIn(load("setup-release.jsonl")), REPO, linux_host(), rep)
        self.assertEqual(rel["version"], "2.338.2")
        self.assertEqual(rel["sha256"], "baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40")
        self.assertTrue(rel["url"].startswith("https://github.com/actions/runner/releases/download/v2.338.2/"))
        # the two published checksums disagree: stop before anything is downloaded
        rows = load("setup-release.jsonl")
        rows[1]["data"]["body"] = rows[1]["data"]["body"].replace(rel["sha256"], "0" * 64)
        rep = M.Report(Out())
        self.assertIsNone(M.runner_release(StandIn(rows), REPO, linux_host(), rep))
        self.assertIn("disagree", folded(rep.lines)[-1])
        rows[1]["data"]["body"] = "no markers here"
        rep = M.Report(Out())
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
        out = Out()
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
        out = Out()
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
        self.assertIn(f"run `look {REPO}` first; then setup again", lines[-1])  # rule 7: the change, named

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
        ran_scripts = [a for a in root if (a[:1] in (("bash",), ("sh",)) or (a and a[0].endswith(".sh")))
                       and a[:3] != ("sh", "-c", M.TEST_SH)]  # R167: the tool's own marked test -e, its text fixed
        self.assertNotIn("$", M.TEST_SH.replace('"$1"', ""))  # its one argument is the path, never in its text
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
        for answer in ((0, "minions-runner:x:1001:1001::/home/minions-runner:/bin/bash\n"), (3, "getent: enumeration not supported\n")):
            host, sha = setup_host(runs={("getent", "passwd", "minions-runner"): answer})
            fresh_look(host)
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertEqual(admin_steps(host), [], "nothing is touched")
            self.assertIn("this computer holds what carries this tool's own names, and this tool's record here, "
                          if answer[0] == 0 else "Not read: whether the user minions-runner is here (", lines[-1])
            if answer[0] != 0:  # R210: the unread said, with getent's own words (after a warning line, if one)
                self.assertIn("enumeration not supported", lines[-1])
            if answer[0] == 0:
                # never "open an issue" for a user that may be the person's own: what is true, and where that leaves them
                self.assertNotIn("open an issue", lines[-1])
                self.assertIn("If it is someone else's user, this version cannot run on this computer: its runner's "
                              f"user must be named {M.RUNNER_USER}, and that name is taken here", lines[-1])
        host, sha = setup_host(runs={("getent", "passwd", "minions-runner"): (0, "minions-runner:x:1001:1001::/home/minions-runner:/bin/bash\n")})
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

    def test_G2_25_a_version_the_dependency_step_changed_is_recorded(self):
        # R186 (seen on a stock Ubuntu 24.04 machine and an earlier fresh install of the same image): the
        # runner's dependency script installed a package that was already there, which moved it to the archive's
        # newer version, and the record and the census named only the packages it added, because the lists read
        # before and after it were compared by name only. Now by name and version: the one added and the one that
        # changed are named, the changed one with its version before and after; the one that stayed, not at all.
        old, new = "1:1.3.dfsg-3.1ubuntu2.2", "1:1.3.dfsg-3.1ubuntu2.3"
        said = f"zlib1g from {old} to {new}"
        why = "installed for the whole system, and another program may use them now"
        def lists(changed=True, added=True, after_fails=False):
            calls = []
            def answer(argv, inp=None):
                calls.append(argv)
                after = len(calls) > 1
                if after and after_fails:
                    return 1, "dpkg-query: error"
                out = "installed bash 5.2.21-2ubuntu4\nconfig-files oldpkg 1.0-1\n"
                out += f"installed zlib1g {new if after and changed else old}\n"
                if after and added:
                    out += "installed libkrb5-3 1.20.1-6ubuntu2\n"
                return 0, out
            return answer
        def setup_with(**kw):
            host, sha = setup_host(runs={("dpkg-query",): lists(**kw)})
            fresh_look(host)
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual(code, M.EXIT_OK, lines)
            with open(os.path.join(host.state_dir(), "journal.log")) as f:
                return host, sha, lines, f.read()
        host, sha, lines, journal = setup_with()
        # the version is read with each name: the two reads are of name and version
        self.assertTrue(all("${Version}" in a[-1] for a in host.ran if a[:1] == ("dpkg-query",)))
        self.assertIn("ok the runner's own dependency script ran (from root's checked copy of the download); these "
                      "system packages were added while it ran: libkrb5-3; these changed version while it ran: "
                      f"{said}; they stay on this computer, at those versions, when this tool leaves, because another "
                      "program may come to use them", lines)
        state = M.load_state(host)
        self.assertEqual(state["dependency_packages"], ["libkrb5-3"])
        self.assertEqual(state["dependency_packages_changed"], {"zlib1g": [old, new]})
        self.assertIn("added to the system while the runner's dependency script ran: libkrb5-3", journal)
        self.assertIn(f"changed version while the runner's dependency script ran: {said}", journal)
        for x in lines + [journal]:  # the one that stayed: named nowhere
            self.assertNotIn("5.2.21", x)
        left = M.what_setup_left(setup_gh(sha), host, REPO)
        self.assertIn("system packages whose version changed while the runner's own dependency script ran ("
                      f"{said}; these stay at those versions after uninstall too, as another program may use them)",
                      left)
        # only a changed one
        host, sha, lines, journal = setup_with(added=False)
        self.assertIn("ok the runner's own dependency script ran (from root's checked copy of the download); no "
                      f"system packages were added while it ran; these changed version while it ran: {said}; they "
                      "stay at those versions when this tool leaves, because another program may come to use them",
                      lines)
        # nothing changed: said as before, and nothing of a change is kept
        host, sha, lines, journal = setup_with(changed=False, added=False)
        self.assertTrue(any(x.startswith("ok the runner's own dependency script ran")
                            and x.endswith("no system packages were added while it ran") for x in lines), lines)
        self.assertFalse([x for x in lines if "changed version" in x], lines)
        self.assertNotIn("changed version", journal)
        self.assertNotIn("dependency_packages_changed", M.load_state(host))
        # the list after could not be read: said as could not be read, never as nothing changed
        host, sha, lines, journal = setup_with(after_fails=True)
        self.assertTrue(any(x.startswith("ok the runner's own dependency script ran")
                            and x.endswith("which were added while it ran could not be read here") for x in lines),
                        lines)
        self.assertFalse([x for x in lines if "no system packages" in x or "changed version" in x], lines)
        self.assertTrue(M.load_state(host)["dependency_packages_unread"])
        # over two setups: the first version before and the latest after
        M.record_packages(host, {"zlib1g": "1"}, {"zlib1g": "2"})
        M.record_packages(host, {"zlib1g": "2"}, {"zlib1g": "3"})
        self.assertEqual(M.load_state(host)["dependency_packages_changed"]["zlib1g"], ["1", "3"])
        # uninstall --all's census names the changed one beside the added ones, kept, and removes nothing
        for unread, added_said in ((None, f"libkrb5-3 (kept: {why})"),
                                   (True, f"could not be read at setup (kept, whatever they are: {why})")):
            gh, world, host = remove_setup()
            d = M.load_state(host)
            d["dependency_packages"] = [] if unread else ["libkrb5-3"]
            d["dependency_packages_changed"] = {"zlib1g": [old, new]}
            if unread:
                d["dependency_packages_unread"] = True
            M.save_state(host, d)
            code, lines = run_uninstall(gh, host, everything=True)
            self.assertIn(f"ok census: system packages added while the runner's dependency script ran: {added_said}",
                          lines)
            self.assertIn("ok census: system packages whose version changed while the runner's dependency script "
                          f"ran: {said} (kept at those versions: {why})", lines)
            self.assertFalse([x for x in lines if x.endswith("dependency script ran: none")], lines)
            self.assertFalse([a for a in host.ran if {"apt", "apt-get", "dpkg", "rpm", "dnf", "yum", "zypper"} & set(a)])
            self.assertTrue(lines[-1].startswith("ok census: your record of what this tool did"), lines[-1])

    def test_G2_15_every_root_step_is_on_the_list(self):
        # R135 (rule 28): docs/root-steps.md lists every place the tool runs a command as root, each with its answers
        # (runs a job's file? a job's path? what it creates, with mode and owner). A root step not on the list, or a
        # row the tool no longer has, fails here.
        src = _read(os.path.join(ROOT, "scripts", "minions.py"))
        code = root_sites(src)
        doc = _read(os.path.join(ROOT, "docs", "root-steps.md"))
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
        main_ = next(n for n in ast.parse(_read(os.path.join(ROOT, "scripts", "minions.py"))).body
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
        # R73: resume and `drain --stop` ask for the password themselves before their admin step, never sending
        # the person back to a terminal where nothing was asked.
        host = setup_host(runs={("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner."): (1, ""),
                                ("systemctl", "is-active", UNIT): (3, "inactive\n")})[0]
        held_state(host)
        host.runs[("sudo", "-n", "true")] = (1, "sudo: a password is required\n")
        for run, cmd in ((lambda **k: M.resume(REPO, host=host, gh=DrainGH(busy=(False,)), **k), f"`resume {REPO}`"),
                         (lambda **k: M.drain(REPO, stop=True, host=host, gh=DrainGH(busy=(False,)), **k),
                          f"`drain {REPO} --stop`")):
            host.ran.clear()
            out = Out()
            self.assertEqual(run(out=out, interactive=False), M.EXIT_NEED, out.getvalue())
            last = out.getvalue().splitlines()[-1]
            self.assertEqual(last, f"need you: this needs this computer's admin password once: run `sudo -v` in your "
                                   f"terminal, then run {cmd} again from that same terminal")
            self.assertFalse([a for a in host.ran if "systemctl" in a and ("start" in a or "stop" in a)], host.ran)
        # Asked in this terminal: the password once, then the service started.
        host.ran.clear()
        out = Out()
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
        self.assertIn("no job asking for self-hosted can land on it", lines[-1])

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
        self.assertTrue(lines[-1].startswith("stop systemd does not run this computer: systemctl is here, but "
                                             f"{M.SYSTEMD_RUN_DIR} is not"), lines)
        self.assertIn("Nothing was changed, on this computer or on GitHub. Your next step:", lines[-1])
        self.assertEqual(admin_steps(host), [])
        self.assertFalse([c for c in gh.calls if c[0] != "GET"], gh.calls)
        self.assertNotIn("setting_up", M.load_state(host))
        # look says it too, before setup is asked; an answer that could not be read is not a "no" (rule 11).
        rep = M.Report(Out())
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
        # change 4: before useradd, the reads of what a gone user left scheduled (marked tests, find): reads, not writes
        writes = [a for a in steps if a[2:4] != ("sh", "-c") and a[2] != "find"]
        self.assertEqual(writes[0][:3], ("sudo", "-n", "useradd"))  # the first write
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
    """GitHub for move: the workflow files, our runner, the Git Data API, pull requests and runs, with every
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
    out = Out()
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
        self.assertIn("--include-job=release.yml:publish", lw)
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

    def test_G3_12_move_says_what_look_says_of_a_file_with_no_job(self):
        # Row R203 (the maintainer's sighting on a copy of the tool, 2026-11-25): a workflow file whose plain jobs:
        # key has no job under it. look said no job was read; move named it nowhere on the terminal. Now move says
        # what look says; the file was read, so it is not on the page among the files not read, and nothing of it
        # is written; the other file moves as before.
        path = ".github/workflows/empty.yml"
        for what, text in (("nothing after the key", "name: e\non: [push]\njobs:\n"),
                           ("only a comment under it", "name: e\non: [push]\njobs:\n  # none yet\n")):
            with self.subTest(empty=what):
                self.assertEqual(M.read_workflow(text), [])
                files = {".github/workflows/ci.yml": CI_YML, path: text}
                gh = MoveGH(files)
                code, lines = run_move(gh, move_host())
                self.assertEqual(code, M.EXIT_NEED, lines)
                self.assertEqual([x for x in lines if path in x], [f"warn {path}: no job read"], lines)
                written = [e["path"] for m, p, b in gh.writes if p.endswith("/git/trees") for e in b["tree"]]
                self.assertEqual(written, [".github/workflows/ci.yml"], lines)
                self.assertNotIn(path, pr_body_of(gh))  # read, so never among the files not read
                # look's own words for the same file, said the same way
                rep = M.Report(Out())
                M.check_workflows(MoveGH(files), REPO, {}, rep)
                self.assertIn(f"warn {path}: no job read", folded(rep.lines))

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

    def test_G8_23_the_move_pages_step_moves_that_one_job(self):
        # Row R193 (found by the maintainer on 2a4fcc7): the step the move page prints for a publishing job is taken
        # from the body the real move opened, split as a shell splits it, and run with exactly that word: that one
        # job moves, and no other. GitHub gives each file by its path; the step names the file's own name, as look's
        # line does and as move reads it.
        base_body = pr_body_of((lambda gh: (run_move(gh, move_host()), gh)[1])(MoveGH(self.FILES)))
        moved_jobs = lambda body: set(re.findall(r"job `([^`]+)`", body.split("## Moved")[1].split("\n## ")[0]))
        base = moved_jobs(base_body)
        pub = base_body.split("## Left as they are (they publish or deploy)")[1].split("\n## ")[0]
        steps = re.findall(r"to move it: `(move [^`]+)`", pub)
        self.assertEqual(len(steps), 4, pub)  # publish, site, npm, pypi: one step each
        for step in steps:
            argv = shlex.split(step)  # as a shell hands it over, then read by the tool's own parser (R200)
            self.assertEqual(argv[:2], ["move", REPO], step)
            a = M.parser().parse_args(argv)
            self.assertEqual(len(a.include_job), 1, step)
            gh = MoveGH(self.FILES)
            run_move(gh, move_host(), include=a.include_job)
            self.assertEqual(moved_jobs(pr_body_of(gh)) - base, {a.include_job[0].split(":", 1)[1]}, step)
        # look's line and the page give the same step for the same job (one function builds both)
        s = {"system": "Linux", "machine": "x86_64", "cores": 8, "mem": 16384000 * 1024}
        (job,) = M.read_workflow(RELEASE_YML)
        lw = M.look_job_line(".github/workflows/release.yml", M.read_triggers(RELEASE_YML), job, s, REPO)[1]
        self.assertIn(f"{M.C0}move {REPO} --include-job=release.yml:publish{M.C1}", lw)
        self.assertIn(f"`move {REPO} --include-job=release.yml:publish`", pub)
        # a file name no command can name (a character that cannot be seen): the page holds no command for it, and
        # says what look says: rename the file on GitHub, then look again
        files = dict(self.FILES)
        files[".github/workflows/rel​ease.yml"] = files.pop(".github/workflows/release.yml")
        gh = MoveGH(files)
        run_move(gh, move_host())
        pub = pr_body_of(gh).split("## Left as they are (they publish or deploy)")[1].split("\n## ")[0]
        line = next(x for x in pub.splitlines() if "job `publish`" in x)
        self.assertNotIn("--include-job", line)
        self.assertIn("rename the file on GitHub to a name without it, then run", line)
        self.assertIn(f"`look {REPO}`", line)

    def test_G8_24_a_step_never_carries_a_name_no_command_may_and_every_step_runs_as_printed(self):
        # Row R200 (the maintainer's ruling (A), 2026-11-25): a name holding a backtick or a dollar sign before a
        # parenthesis gets no step, in look's line and on the move page alike, and both say to rename the file; on the
        # page every such name stays inside its own code span, shown changed by the terminal's function with its note.
        # A step for a name that begins with a dash runs as printed: the word is joined to its option by "=".
        s = {"system": "Linux", "machine": "x86_64", "cores": 8, "mem": 16384000 * 1024}
        (job,) = M.read_workflow(RELEASE_YML)
        moved_jobs = lambda body: set(re.findall(r"^- `([^`]+)` job `([^`]+)`", body.split("## Moved")[1].split("\n## ")[0], re.M))
        for name, step_given in (("-rel.yml", True), ("release.yml", True), ("re`l.yml", False), ("re$(x).yml", False)):
            path = f".github/workflows/{name}"
            files = {".github/workflows/ci.yml": CI_YML, path: RELEASE_YML}
            # look's line, as printed
            kind, words = M.look_job_line(path, M.read_triggers(RELEASE_YML), job, s, REPO)
            printed = M.render(f"{path} job publish: " + words)
            gh = MoveGH(files)
            run_move(gh, move_host())
            body = pr_body_of(gh)
            line = next(x for x in body.split("## Left as they are (they publish or deploy)")[1].splitlines()
                        if " job `publish`" in x)
            self.assertEqual(line.split(": ", 1)[0].count("`"), 4, line)  # the file and the job: two spans, whole
            if not step_given:
                self.assertEqual(printed[1:], [f"look {REPO}"], printed)  # the one step is look's, alone
                self.assertNotIn("--include-job", printed[0])
                self.assertIn("rename the file on GitHub to a name without it", printed[0])
                self.assertTrue(printed[0].endswith(M.SHOWN_CHANGED), printed[0])
                self.assertNotIn("--include-job", line)
                self.assertIn("rename the file on GitHub to a name without it", line)
                self.assertTrue(line.endswith(M.SHOWN_CHANGED), line)
                for x in body.splitlines():
                    self.assertNotIn("$(", x)
                continue
            # the step as printed by look, and as copied from the page's code span: a shell's words, read by the tool's
            # own parser, move that one job and no other
            (look_step,) = printed[1:]
            (page_step,) = re.findall(r"to move it: `(move [^`]+)`", line)
            self.assertEqual(look_step, page_step)
            a = M.parser().parse_args(shlex.split(look_step))
            self.assertEqual((a.cmd, a.repo, a.include_job),
                             ("move", REPO, [f"{name}:publish"]), look_step)
            gh2 = MoveGH(files)
            code, lines = run_move(gh2, move_host(), include=a.include_job)
            self.assertEqual(code, M.EXIT_NEED, lines)
            self.assertEqual(moved_jobs(pr_body_of(gh2)) - moved_jobs(body), {(path, "publish")}, look_step)
        # a job id is read only as GitHub allows it ([A-Za-z0-9_-]); planted at the function itself, the same holds
        self.assertIsNone(M.include_step(REPO, ".github/workflows/rel.yml", "pu`b"))
        self.assertIsNone(M.include_step(REPO, ".github/workflows/rel.yml", "pu$(b)"))
        self.assertIn("the name of its job (rel.yml:pu`b)", M.include_unnamable(".github/workflows/rel.yml", "pu`b", "look"))
        page_line = M.md_job(".github/workflows/rel.yml", "pu`b", "words")
        self.assertEqual(page_line, f"- `.github/workflows/rel.yml` job `pu'b`: words {M.SHOWN_CHANGED}")
        # The page's other lists, through a real move (the maintainer's ruling on R198, 2026-11-25): a planted name
        # stays inside its own span, shown changed with the note, in "Moved", in "Left as they are (they cannot run
        # on this computer)" and in "Already on your computer", as it does under "they publish or deploy" above.
        three = ("name: three\non: [push]\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - run: make\n"
                 "  mac:\n    runs-on: macos-latest\n    steps:\n      - run: sw_vers\n"
                 "  mine:\n    runs-on: [minions]\n    steps:\n      - run: make\n")
        for name, shown in (("th`ree.yml", "th'ree.yml"), ("th$(x).yml", "th$ (x).yml")):
            gh = MoveGH({".github/workflows/ci.yml": CI_YML, f".github/workflows/{name}": three})
            code, lines = run_move(gh, move_host())
            self.assertEqual(code, M.EXIT_NEED, lines)
            body = pr_body_of(gh)
            for heading, job in (("## Moved", "build"),
                                 ("## Left as they are (they cannot run on this computer)", "mac"),
                                 ("## Already on your computer", "mine")):
                part = body.split(heading + "\n")[1].split("\n## ")[0]
                found = [x for x in part.splitlines() if f" job `{job}`" in x and x.startswith("- `.github/workflows/th")]
                self.assertEqual(len(found), 1, (heading, part))
                (line,) = found
                head = line.split(": ", 1)[0]
                self.assertEqual(head, f"- `.github/workflows/{shown}` job `{job}`", (heading, line))
                self.assertEqual(head.count("`"), 4, (heading, line))  # the file and the job: two spans, whole
                self.assertTrue(line.endswith(M.SHOWN_CHANGED), (heading, line))
            self.assertNotIn("$(", body)
            self.assertNotIn(name, body)

    def test_G8_25_setup_says_the_command_below_only_where_it_hands_over_a_step(self):
        # Seen 2026-11-26 on a stock VM (row R217): setup, run from the agent's shell, printed the limits line with
        # "the command below" and a placeholder command before the need-you line and the step's own; the walk took the
        # first for the step. The owner's ruling (2026-11-27, (a)): setup's limits line says the real numbers and
        # where the way to change them is shown, and carries no command, on both roads; where setup hands over a step,
        # its need-you line is the only one of the run that says "the command below", and that command is the last
        # line printed; on the person's own terminal the need-you line carries no command, and the lines read before
        # Enter may name a command for later (a laptop's battery, leaving), each on its own line, none of them a step.
        survey = {"system": "Linux", "machine": "x86_64", "cores": 8, "mem": 16384000 * 1024}
        where = f"; the way to change that is shown in what look prints for {REPO} and in this tool's README"
        limits = ("ok jobs here get at most 4 of this computer's 8 processors and 60 % of its memory "
                  f"({M.gb(int(M.slice_limits(survey)['MemoryMax']))})" + where)

        def world(kind="none", home=0o750, serves=False, runs=None):
            host, sha = setup_host(runs=runs or {})
            host.home_mode = home
            fresh_look(host)
            data = M.load_state(host)
            data["looks"][REPO]["survey"].update(battery=kind == "battery", lid=kind == "lid", serves=serves,
                                                 serves_what="port(s) open to the network: 8088" if serves else "")
            M.save_state(host, data)
            return host, sha

        def printed(host, sha, interactive):
            out = Out()
            code = M.setup(REPO, host=host, gh=setup_gh(sha), out=out, interactive=interactive)
            return code, out.printed()

        def limits_said(said, road):
            got = [x for x in said if "jobs here get at most" in x]
            for x in got:
                self.assertNotRegex(x, REF, road)
                self.assertNotIn("--limits", x, road)
                self.assertTrue(x.endswith(where) and "4 of this computer's 8 processors and 60 % of its memory (" in x,
                                (road, x))
            return got

        # (1) the roads where setup hands the person a step: the agent's shell (on a desktop, a laptop's battery or
        # lid alike), and the login, beside-live and home-folder needs (each from the person's terminal too)
        steps = [(f"the agent's shell ({k})", world(k), False, f"python3 -I {M.tool_path()} setup {REPO}", True)
                 for k in ("none", "battery", "lid")]
        open_home = world(home=0o755)
        steps += [("an open home folder", open_home, True, f"chmod 750 {open_home[0].home()}", True),
                  ("something live", world(serves=True), True, f"setup {REPO} --beside-live-too", False),
                  ("no login", world(runs={("gh", "api", "-i", "user"): (4, NO_LOGIN)}), True, "gh auth login", False)]
        for road, (host, sha), interactive, step, after_limits in steps:
            code, said = printed(host, sha, interactive)
            self.assertEqual(code, M.EXIT_NEED, (road, said))
            ref = [i for i, x in enumerate(said) if REF.search(x)]
            self.assertEqual(len(ref), 1, (road, said))
            self.assertTrue(said[ref[0]].startswith("need you: "), (road, said))
            self.assertEqual(REF.findall(said[ref[0]]), ["the command below"], (road, said))
            self.assertEqual(said[ref[0] + 1:], [step], (road, said))  # its command, and no line after it
            self.assertEqual(limits_said(said, road), [limits] if after_limits else [], (road, said))
            self.assertEqual(admin_steps(host), [], road)
        # (2) the person's own terminal: the need-you line carries no command; the lines read before Enter name the
        # limits with no command, and a laptop's battery or lid and the leaving, each with its command on its own line
        for kind in ("none", "battery", "lid"):
            road = f"the person's terminal ({kind})"
            code, said = printed(*world(kind), True)
            self.assertEqual(code, M.EXIT_OK, (road, said))
            needs = [x for x in said if x.startswith("need you: ")]
            self.assertTrue(needs[0].startswith("need you: read the lines above, then press Enter here"), needs)
            for x in needs:
                self.assertNotRegex(x, REF, road)
            self.assertIn(limits, limits_said(said, road), road)  # setup's own ok line, and (below) its copy
            tells = [(i, x) for i, x in enumerate(said) if TELL.match(x)]
            self.assertEqual([TELL.sub("", x) for _, x in tells if "jobs here get at most" in x], [limits[len("ok "):]],
                             road)  # the same words as setup's own ok line
            named = {said[i + 1] for i, x in tells if REF.search(x)}
            self.assertEqual(named, {"uninstall --all"} | ({f"resume {REPO} --on-battery-too"} if kind != "none" else
                                                           set()), (road, said))
            enter = said.index(needs[0])
            self.assertTrue(all(i < enter for i, x in tells if REF.search(x)), road)
        # (3) the places the limits line names show the way to change them: look's own line and the README
        code, said = look(StandIn(load("look-recent-runs.jsonl")), linux_host())
        self.assertTrue(any(f"`setup {REPO} --limits PROCESSORS,PERCENT`" in x for x in said), said)
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r"`setup [A-Z]+/[A-Z]+ --limits PROCESSORS,")

    def test_G3_8_an_include_job_word_that_matches_no_job_stops_before_any_write(self):
        # Row R197 (read in the code by the maintainer, 2026-11-25): a word of --include-job that matched no job was
        # passed in silence, and move opened its pull request as if it had not been given. Now it stops before the
        # first write: no branch, no pull request; each unmatched word named (shown changed where it holds a
        # character a terminal acts on), and look as the one step, the command last on its line.
        for words, bad in (([".github/workflows/release.yml:publish"], [".github/workflows/release.yml:publish"]),
                           (["relase.yml:publish"], ["relase.yml:publish"]),
                           (["release.yml:publish", "deploy.yml:sit"], ["deploy.yml:sit"]),
                           (["deploy.yml:sit", "relase.yml:publish", "deploy.yml:sit"], ["deploy.yml:sit", "relase.yml:publish"]),
                           (["rel`ease.yml:publish"], ["rel'ease.yml:publish"])):
            gh = MoveGH(self.FILES)
            out = Out()
            code = M.move(REPO, host=move_host(), gh=gh, out=out, include=words)
            printed = out.printed()
            self.assertEqual(code, M.EXIT_STOP, printed)
            self.assertEqual(gh.writes, [], words)  # nothing is opened: no branch, no commit, no pull request
            self.assertFalse([c for c in gh.calls if c[0] != "GET"], words)
            (stop,) = [x for x in printed if x.startswith("stop ")]
            n = len(bad)
            self.assertIn(f"--include-job: {n} word{'s' if n > 1 else ''} given {'match' if n > 1 else 'matches'} "
                          f"no job of {REPO} ({', '.join(bad)})", stop)
            self.assertIn("no pull request was opened", stop)
            self.assertNotIn("release.yml:publish,", stop.replace("relase.yml:publish", ""))  # the good word is not named
            self.assertEqual(printed[-1], f"look {REPO}", printed)  # the one step, alone and last
            self.assertEqual("(shown changed" in stop, "`" in words[0], stop)
        # a word for a job in a workflow file this run could not read (not UTF-8) is not known to match no job, and
        # the stop says so for that word; a plain typo beside it still matches no job; one stop, look's step last
        gh = MoveGH({".github/workflows/ci.yml": CI_YML, ".github/workflows/ff.yml": ""})
        real = gh.api

        def api(method, path, body=None):
            if method == "GET" and path.split("?")[0].endswith("/contents/.github/workflows/ff.yml"):
                gh.calls.append((method, path))
                return 200, {"content": M.base64.b64encode(b"name: x\xff\non: [push]\n").decode()}
            return real(method, path, body)
        gh.api = api
        out = Out()
        code = M.move(REPO, host=move_host(), gh=gh, out=out, include=["ff.yml:build", "ci.yml:tset"])
        printed = out.printed()
        self.assertEqual(code, M.EXIT_STOP, printed)
        self.assertEqual(gh.writes, [])
        self.assertFalse([c for c in gh.calls if c[0] != "GET"])
        (stop,) = [x for x in printed if x.startswith("stop ")]
        self.assertIn(f"--include-job: 1 word given matches no job of {REPO} (ci.yml:tset); and 1 word given names a "
                      "workflow file this run could not read (ff.yml:build), so whether it names a job there is not "
                      "known (the line above for that file says why, and its step)", stop)
        self.assertNotIn("ff.yml:build)", stop.split("; and ")[0])  # never "matches no job" for the unread file
        warn = next(i for i, x in enumerate(printed) if x.startswith(f"warn .github/workflows/ff.yml: {M.NOT_TEXT_IS}"))
        self.assertLess(warn, printed.index(stop))
        self.assertEqual(printed[-1], f"look {REPO}", printed)
        # a word that names a job that would move anyway is matched and says nothing; the pull request is opened
        gh = MoveGH(self.FILES)
        code, lines = run_move(gh, move_host(), include=["ci.yml:test"])
        self.assertEqual(code, M.EXIT_NEED, lines)
        self.assertFalse([x for x in lines if "--include-job" in x or "ci.yml:test" in x], lines)
        self.assertTrue(any(p.endswith("/pulls") for _, p, _ in gh.writes))

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
        # R76: the same wait while GitHub shows our runner offline: said as that, with resume named first
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
    out = Out()
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
        out = Out()
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
                    if os.path.exists(log) and len(_read(log).splitlines()) == 2:
                        break
                    M.time.sleep(0.1)
                self.assertEqual(_read(log).splitlines(),
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
                        if _read(f"/proc/{pid}/stat").split()[2] == "Z":
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
                    if _read(f"/proc/{p}/stat").split()[2] == "Z":
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
        code = M.resume(REPO, host=host, gh=gh, out=Out())
        self.assertEqual(code, M.EXIT_OK)
        self.assertEqual(gh.puts[-1], ["minions", "minions-medium"])
        self.assertNotIn("labels_before_hold", M.load_state(host)["runners"][REPO])
        # And the stop helper itself refuses without a drain.
        rep = M.Report(Out())
        self.assertFalse(M.stop_runner(host, UNIT, FOLDER, rep, drained=False))
        self.assertIn("only after a drain", folded(rep.lines)[-1])

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
        rep = M.Report(Out())
        self.assertTrue(M.stop_runner(host, UNIT, FOLDER, rep, drained=True))
        # by exact PID, SIGTERM, never SIGINT; as the runner user, who can end only its own (R128)
        self.assertIn(("sudo", "-n", "-u", "minions-runner", "--", "kill", "-TERM", "777"), host.ran)
        self.assertFalse(any("-INT" in a or "-9" in a or "pkill" in a or "killall" in a for a in host.ran))
        self.assertEqual(folded(rep.lines)[-1], "ok the runner is stopped: the service is inactive, and no process of it is left")
        # A process that does not go: stop, and nothing starts the runner again (never two copies).
        host = setup_host(runs={("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner."): (0, "777\n"),
                                ("systemctl", "is-active", UNIT): (3, "inactive\n")})[0]
        rep = M.Report(Out())
        self.assertFalse(M.stop_runner(host, UNIT, FOLDER, rep, drained=True))
        rep = M.Report(Out())
        self.assertFalse(M.start_runner(host, UNIT, FOLDER, rep))
        self.assertIn("two copies never run", folded(rep.lines)[-1])
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
                self.assertTrue(any(x.startswith(f"warn this computer has a {what}: " + as_said(M.laptop_words(REPO))) for x in lines),
                                (what, lines))
                self.assertIn(f"This computer has a {what}: " + as_said(M.laptop_words(REPO)) + ".", tells)
            else:
                self.assertIn("ok no battery and no lid: not a laptop", lines)
                self.assertFalse([x for x in tells if "laptop" in x])
        self.assertEqual(as_said(M.laptop_words(None)), "a laptop takes jobs only while it is awake, on the network and "
                                         "(by default) on mains power; `resume OWNER/REPO --on-battery-too` lets it take "
                                         "jobs on battery as well (with your repository's name (owner/name) in place of "
                                         "OWNER/REPO)")

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
        out = Out()
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
        with mock.patch("sys.stderr", Out()), self.assertRaises(SystemExit) as e:
            M.main(["setup", REPO, "--limits", "0,95"], host=linux_host())
        self.assertEqual(e.exception.code, M.EXIT_USAGE)
        words = as_said(M.limits_in_words(M.slice_limits({"cores": 8, "mem": 16 * gib})))
        self.assertEqual(words, "jobs here get at most 4 of this computer's 8 processors and 60 % of its memory "
                                "(9.6 GB); the one way to change that is `setup OWNER/REPO --limits "
                                "PROCESSORS,PERCENT`, with the number of processors and the percent of memory in place "
                                "of PROCESSORS and PERCENT, and your repository's name (owner/name) in place of "
                                "OWNER/REPO")
        # One processor (the owner's ruling, 2026-11-23): "1 of 1", said plainly, never refused.
        words = as_said(M.limits_in_words(M.slice_limits({"cores": 1, "mem": 8 * gib})))
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
    out = Out()
    code = fn(*a, out=out, **kw)
    return code, out.getvalue().splitlines()


def printed_system_commands():
    """G8.16: every backticked system command in a string the tool can print (docstrings left out), its f-string
    fields read as {}."""
    import ast
    tree = ast.parse(_read(os.path.join(ROOT, "scripts", "minions.py"), encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body and \
                isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            docs.add(id(node.body[0].value))
    texts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):  # rule 31 (B): a command is marked by C0 and C1, read here as backticks
            texts.append("".join(v.value if isinstance(v, ast.Constant) else
                                 "`" if isinstance(v.value, ast.Name) and v.value.id in ("C0", "C1") else "{}"
                                 for v in node.values))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "cmd" and node.args:
            a = node.args[0]
            if isinstance(a, ast.Constant):
                texts.append("`" + a.value + "`")
            elif isinstance(a, ast.JoinedStr):
                texts.append("`" + "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in a.values) + "`")
            elif isinstance(a, ast.IfExp):
                texts += ["`" + x.value + "`" for x in (a.body, a.orelse) if isinstance(x, ast.Constant)]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            texts.append(node.value)
    found = set()
    for t in texts:
        for m in re.finditer(r"`((?:sudo|gh|chmod|journalctl|python3|mv) [^`]*)`", t):
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
                text = f.read()
            clones = version_places.clone_options(text)  # R194: one place for the gates' reads
            # three answers, never two: ours whole, a listed other, or the test fails naming the line (R194)
            self.assertEqual(version_places.clones_not_read(text), [], f"{rel}: a clone this test cannot read as ours")
            for opts in clones:
                if M.VERSION == "0.0.0":
                    continue  # before the first release: main's README has no install lines (rule 15)
                self.assertIn(opts, (f"--branch v{M.VERSION}", f"--branch release-{M.VERSION}"),
                              f"{rel}: a clone line names this version's tag or release branch, never main")
        # Rule 10 (the family's one version): the README's own status heading is one of the places. A README that
        # carries install lines says, in its status heading, the version they install; so 0.1.1 cannot ship with a
        # README that still says 0.1.0, or "not released" (found 2026-11-24 in the release README before the tag).
        with open(os.path.join(root, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        if M.VERSION != "0.0.0" and version_places.clones_ours(readme):
            status = re.search(r"^## Status: (.*)$", readme, re.M)
            self.assertIsNotNone(status, "README: a README with install lines has a status heading")
            self.assertTrue(version_places.heading_names(status.group(1), M.VERSION),
                            "README: the status heading names the version its install lines install")


class G5(unittest.TestCase):
    def test_G5_20_no_line_says_the_hook_holds_without_its_caveat(self):
        # R133: a job can blank the start hook in the runner's own .env (seen on a stock VM, 2026-11-24), so no line,
        # shown or written, says the hook refuses a job without saying "unless a job that already ran here switched
        # that hook off"; the README says the watch is the gate and names the window.
        src = _read(os.path.join(ROOT, "scripts", "minions.py")).splitlines()
        for i, line in enumerate(src):
            if re.search(r"hook here refuses", line) and not line.lstrip().startswith("#"):
                self.assertIn("HOOK_CAVEAT", " ".join(src[i:i + 3]), f"scripts/minions.py:{i + 1}")
        docs = [os.path.join(ROOT, "docs", "root-steps.md")]
        readme = " ".join(_read(os.path.join(ROOT, "README.md")).split())
        if "nothing can be installed yet" not in readme:  # the release branch's README (main's has no install lines)
            docs.append(os.path.join(ROOT, "README.md"))
            self.assertIn("nothing a job can write is ever run, sourced or followed by root", readme)
        for p in docs:
            text = " ".join(_read(p).split())
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

    def test_G5_1_status_never_says_idle_from_a_job_process_read_that_could_not_answer(self):
        # Row R170 (AN ERROR IS NEVER AN ABSENCE), read in the code 2026-11-24: status took a pgrep that could not answer (an exit other
        # than 0 or 1) as no job process. Now this computer's side is said as not read, with what pgrep answered.
        for bad in ((2, "pgrep: cannot read /proc\n"), (127, "Command timed out after 20 seconds"), (3, "")):
            host, _ = g5_host(runs={("pgrep", "-u", "minions-runner", "-f"): bad})
            code, lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())
            line = next(x for x in lines if " minions-box-1 for " in x)
            self.assertTrue(line.startswith("warn "), line)
            self.assertIn(f"for {REPO}: this computer's side could not be read.", line)
            self.assertIn(f"its job processes not read (pgrep answered code {bad[0]}", line)
            self.assertIn("GitHub says online, not busy", line)
            self.assertNotIn("idle", line)
            self.assertNotIn("no job process", line)
        # the read that answers is said as before
        host, _ = g5_host()
        line = next(x for x in run_cmd(M.status, REPO, host=host, gh=StatusGH())[1] if " minions-box-1 for " in x)
        self.assertIn(f"for {REPO}: idle.", line)

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
        # Held: the queue line says what holds it and the way on; nothing before it says ok.
        host, _ = g5_host()
        data = M.load_state(host)
        data["runners"][REPO].update(held_for="low disk", held_at="2026-11-23T05:31:41Z")
        M.save_state(host, data)
        gh = StatusGH(queued=[job])
        gh.r[REPO]["labels"] = ["minions-held"]
        lines = run_cmd(M.status, REPO, host=host, gh=gh)[1]
        self.assertTrue(lines[0].startswith("warn queue for"), lines)
        self.assertIn("behind our hold (low disk). The hold lifts by itself", lines[0])
        # Online and idle with a job just queued: plain ok, nothing to warn of.
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
        T = "the watch's timer, read at {}: systemd reads it active and enabled; heartbeat: "  # R152: the timer read now first
        clock.sleep(14 * 60)
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
        self.assertIn("ok " + T.format("23:14:00Z") + "the watch ran 14 min ago, at 23:00:00Z (every 5 min)", lines)
        clock.sleep(2 * 60)
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
        self.assertIn("warn " + T.format("23:16:00Z") + "the watch is not running: its last heartbeat was 16 min ago, "
                      "at 23:00:00Z (it runs every 5 min)", lines)
        # R83: the same 16 minutes, 12 of them asleep (seen on a stock machine): the timer counts only waking time,
        # so the watch is not called stopped, and no stop line comes first.
        host.slept = 12 * 60
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
        self.assertIn("ok " + T.format("23:16:00Z") + "the watch ran 16 min ago, at 23:00:00Z; this computer slept 12 "
                      "min since, and the watch's timer counts only waking time: its next pass comes within 5 min of "
                      "waking time", lines)
        self.assertFalse([x for x in lines if "the watch is not running" in x], lines)
        # Asleep only 30 s of the 16 minutes: 15.5 minutes awake with no pass is still a stopped watch.
        host.slept = 30
        lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
        self.assertTrue(lines[0].startswith("stop the watch is not running"), lines)

    def test_G5_22_status_reads_the_watch_timer_not_only_its_heartbeat(self):
        # Row R152 (A SETTING SAYS WHAT WAS ASKED FOR, NOT WHAT IS THERE), read in the code 2026-11-24: the
        # watch line came from the heartbeat alone, so a timer stopped or disabled was said ok for up to three
        # intervals. Now the timer is read at that moment, in three answers, and the heartbeat is said beside it.
        ACT, EN = ("systemctl", "is-active", "minions-watch.timer"), ("systemctl", "is-enabled", "minions-watch.timer")
        beat = "heartbeat: the watch ran 2 min ago, at 22:58:00Z (every 5 min)"
        for act, en, kind, words in (
                ((0, "active\n"), (0, "enabled\n"), "ok", "systemd reads it active and enabled; " + beat),
                ((3, "inactive\n"), (0, "enabled\n"), "warn", "systemd reads it inactive and enabled: the watch is not "
                 "running now. `sudo systemctl start minions-watch.timer`, or setup again; " + beat),
                ((3, "failed\n"), (1, "disabled\n"), "warn", "systemd reads it failed and disabled: the watch is not running"),
                ((0, "active\n"), (1, "disabled\n"), "warn", "systemd reads it active and disabled: it runs now, and does not "
                 "start again when this computer restarts. Setup again turns it back on; " + beat),
                # never "ok" from a read that could not answer (its last line is named: under test_G6_28, a warning)
                ((1, "Failed to connect to bus: No such file or directory\n"), (0, "enabled\n"), "warn",
                 "whether it runs could not be read (systemctl answered code 1: "),
                ((0, "active\n"), (0, ""), "warn", "systemd reads it active; whether it starts when this computer restarts "
                 "could not be read (systemctl answered code 0: ")):
            host, clock = g5_host(runs={ACT: act, EN: en})
            lines = run_cmd(M.status, REPO, host=host, gh=StatusGH())[1]
            line = next((x for x in lines if "the watch's timer, read at" in x), None)
            self.assertIsNotNone(line, lines)
            self.assertTrue(line.startswith(f"{kind} the watch's timer, read at 23:00:00Z: "), line)
            self.assertIn(words, folded([line])[0], (act, en))
            if act[0] == 1:
                self.assertIn("; systemd reads it enabled: so whether the watch runs is not said; status again later; ",
                              line)
            if en == (0, ""):
                self.assertIn("): so whether the watch runs is not said; status again later; ", line)
            self.assertIn(beat, line)  # the heartbeat's time beside it, fresh in every case
            self.assertIn(tuple(M.systemctl("is-active", "minions-watch.timer")), host.ran)
            self.assertIn(tuple(M.systemctl("is-enabled", "minions-watch.timer")), host.ran)

    def test_G5_23_lifts_by_itself_is_said_only_with_the_timer_enabled(self):
        # Row R153 (A SETTING SAYS WHAT WAS ASKED FOR, NOT WHAT IS THERE), read in the code 2026-11-24: `drain --restart` and status's held-for-a-restart line said
        # "the hold lifts by itself once this computer has restarted" without reading whether the watch's timer starts
        # after the restart. Now is-enabled is read at that moment; otherwise what was read and what lifts it instead.
        EN = ("systemctl", "is-enabled", "minions-watch.timer")
        for en, kind, words in (
                ((0, "enabled\n"), "ok", "lifts by itself once this computer has restarted and GitHub reads the runner "
                 "online again (the watch does it; its timer is read enabled now)"),
                ((1, "disabled\n"), "warn", "does not lift by itself: the watch's timer is read disabled now, so it does "
                 f"not start when this computer restarts. After the restart, `resume {REPO}` lifts it (setup again turns "
                 "the watch back on)"),
                ((0, "enabled-runtime\n"), "warn", "does not lift by itself: the watch's timer is read enabled-runtime now"),
                ((1, "Failed to connect to bus: No such file or directory\n"), "warn",
                 "is lifted by the watch after the restart only if its timer starts then, and whether it starts when this "
                 "computer restarts could not be read (systemctl answered code 1: ")):
            host, clock = g5_host(runs={EN: en})
            host.files["/proc/sys/kernel/random/boot_id"] = "boot-one\n"
            gh = StatusGH()
            code, lines = run_cmd(M.drain, None, host=host, gh=gh, restart=True)
            self.assertEqual(code, M.EXIT_OK, lines)
            self.assertTrue(lines[-1].startswith(f"{kind} online and idle: safe to restart now. The hold on {REPO} "), lines[-1])
            self.assertIn(words, folded([lines[-1]])[0], en)
            self.assertEqual(M.load_state(host)["restart_hold"]["repos"], [REPO])  # the hold is set either way
            line = next(x for x in run_cmd(M.status, REPO, host=host, gh=gh)[1]
                        if x.split(" ", 1)[1].startswith("held for a restart since"))
            self.assertTrue(line.startswith(kind + " "), line)
            self.assertIn("the hold " + words, folded([line])[0], en)
            if en[0] == 1 and "disabled" not in en[1]:
                self.assertTrue(folded([line])[0].endswith(f"). If the hold stays after the restart, `resume {REPO}` "
                                                           "lifts it"), line)
            if kind == "warn":
                self.assertNotIn("lifts by itself", line)
                self.assertNotIn("lifts by itself", lines[-1])

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
        # Coming back after the removal: the person's word, GitHub says private, then setup registers again.
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
        # R167 (rule 6): the enable's exit is read; refused, nothing goes on and it is said (G6.27)
        enable = ("sudo", "-n", "systemctl", "enable", UNIT)
        host3.runs[enable] = (1, "Failed to enable unit: Access denied\n")
        code, lines = run_cmd(M.resume, REPO, host=host3, gh=gh, private_again=True)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn("could not be enabled again: ", lines[-1])
        self.assertIn("Failed to enable unit: Access denied", lines[-1])  # R168: a warning before it is shown too
        self.assertFalse(any(x[:4] == ("sudo", "-n", "systemctl", "start") for x in host3.ran))
        del host3.runs[enable]
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
        # R154 (found 2026-11-24 reading rule 24's lines): `drain --restart`, then resume without restarting, left the
        # restart hold kept; status still said "held for a restart ... lifts by itself", and a later `drain REPO`
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
                                           "restart until you resume it"), lines[-1])
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
        data["runner_user_made"] = True  # R178: a runner set up here, so the user setup made is here (getent: there)
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

    def test_G6_36_status_rerun_while_another_holds_the_record_asks_github_nothing_and_names_the_holder(self):
        # R208: status, while another of this tool's processes holds the
        # record, reads it and saves nothing (condition d); with --rerun it still asked GitHub to run the job again,
        # and the "once" could not be kept, so a second ask ran it again. The holder is most often the watch's own
        # pass. Now: nothing is asked of GitHub that the record cannot keep; a stop names the holder, says the job
        # was not run again, and gives the same command again. Plain status stays as built.
        t0 = int(M.parse_stamp("2026-11-22T22:40:00Z").timestamp())
        start = f"start {t0} 555 1 0 check\n"
        cut = {"conclusion": "failure", "steps": self.CUT_STEPS}
        stopped = {("systemctl", "show", "actions.runner.example-org-example-repo.minions-box-1.service", "-p",
                    "ActiveEnterTimestamp"): (0, "ActiveEnterTimestamp=@%d\nInactiveEnterTimestamp=@%d\n" % (t0 + 30, t0 + 20))}
        host, gh, _ = self._named(start, cut, stopped, notes=[M.CANCELED])
        before = file_bytes(M.state_path(host))
        code = ("import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600); "
                "fcntl.flock(fd, fcntl.LOCK_EX); print('ready', flush=True); sys.stdin.readline()")
        holder = subprocess.Popen([sys.executable, "-I", "-c", code, M.lock_path(host)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        self.addCleanup(close_pipes, holder)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "ready")
        for _ in range(2):  # asked twice while held: GitHub is asked nothing either time
            code_, lines = run_cmd(M.status, REPO, host=host, gh=gh, rerun=9001)
            self.assertEqual(code_, M.EXIT_STOP, lines)
            self.assertEqual(gh.reruns, [])
            stop = [x for x in lines if x.startswith("stop ") and "9001" in x]
            self.assertEqual(len(stop), 1, lines)
            self.assertIn(f"this tool's record is held by process {holder.pid}", stop[0])
            self.assertIn("job 9001 was not run again", stop[0])
            self.assertIn(f"`status {REPO} --rerun 9001`", stop[0])
            self.assertEqual(file_bytes(M.state_path(host)), before)
        code_, lines = run_cmd(M.status, REPO, host=host, gh=gh)  # plain status, as built: reads, saves nothing
        self.assertFalse([x for x in lines if "was not run again" in x], lines)
        self.assertTrue([x for x in lines if "status read it as it stands and saved nothing" in x], lines)
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=10)
        code_, lines = run_cmd(M.status, REPO, host=host, gh=gh, rerun=9001)  # the same command, once it has ended
        self.assertEqual(gh.reruns, [9001])
        self.assertIn(9001, M.load_state(host)["reruns"], "the once is kept")

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
                                     ["minions", "minions-medium"], M.Report(Out()), wait_s=0))
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
                                 f"change the limit: `setup {REPO} --limits PROCESSORS,PERCENT`, with the number of processors and the "
                                 "percent of memory in place of PROCESSORS and PERCENT. To run it again, once: "
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


# R136's last line (condition 2: what stays is "not made by this tool or its runner user", never "the person's")
R136_LAST = ("ok this tool is off this computer; what stayed was not made by this tool or its runner user, or holds what "
             "was not, named above with why each stayed")


# R136: the walk's worlds (the owner's (A) and (B) lists, 2026-11-25)
LEAVE_TOOL_WORLDS = {"a foreign plain file": ({"zz.txt": ("root", "f", "a1")}, True),
        "a foreign folder holding a file": ({"zzdir": ("root", "d", None), "zzdir/f": ("root", "f", "a2")}, True),
        "the control": ({}, False)}
LEAVE_RUNNER_WORLDS = {"a root file": ({"root.txt": ("root", "f", "b1")}, True),
          "a root folder with a file": ({"rd": ("root", "d", None), "rd/f": ("root", "f", "b2")}, True),
          "a root empty folder": ({"empty": ("root", "d", None)}, True),
          "a file of the person's": ({"p.txt": ("person", "f", "b4")}, True),
          "a runner folder holding a foreign file": ({"sub": ("minions-runner", "d", None), "sub/f": ("person", "f", "b5")},
                                                     True),
          "a runner link to a folder outside": ({"ln": ("minions-runner", "l", None)}, False),
          "the control": ({"mine.txt": ("minions-runner", "f", None)}, False)}


class World:
    """A computer with our runner on it, changed only by the commands the tool runs (through sudo and systemctl).
    It behaves as seen on a real one: `config.sh remove` refuses while the service is installed, and a slice whose
    file is deleted stays loaded and active after a reload until it is stopped (Part 7 C7)."""

    def __init__(self, gh, own_removal_works=True, interrupt_on=None):
        self.gh, self.own_removal_works, self.interrupt_on = gh, own_removal_works, interrupt_on
        self.paths = {*M.PUBLIC_TMP, M.RUNNERS_HOME.rstrip("/"), FOLDER, f"{FOLDER}/.runner", f"{FOLDER}/.service", f"/etc/systemd/system/{UNIT}",
                      f"/etc/systemd/system/{UNIT}.d", SLICE_FILE, *WATCH_FILES, M.HOOKS_ROOT, M.hook_dir(REPO),
                      M.RECORDS_ROOT, M.records_dir_of(FOLDER), "/home/minions-runner"}
        self.active = {UNIT: "active", M.SLICE: "active", f"{M.WATCH_UNIT}.timer": "active"}
        self.loaded = set(self.active) | {f"{M.WATCH_UNIT}.service"}
        # R210: what setup enabled (the runner's unit has WantedBy=multi-user.target, the watch's timer
        # WantedBy=timers.target): `restart` starts each that is enabled with its file there, as systemd does at boot
        self.enabled = {UNIT, f"{M.WATCH_UNIT}.timer"}
        self.user, self.listener, self.worker, self.order = True, True, False, []
        self.slice_of, self.old_systemd = {UNIT: M.SLICE}, False  # R167: which units are in our slice (Slice=)
        self.files, self.links = {}, set()  # remove_setup shares the host's files; links a job planted (R128)
        self.plain = set()  # R136: plain files put here by someone else (rmdir refuses them, as on a real one)
        self.dirs = {*M.PUBLIC_TMP, M.RUNNERS_HOME.rstrip("/"), FOLDER, f"/etc/systemd/system/{UNIT}.d", M.HOOKS_ROOT, M.hook_dir(REPO), M.RECORDS_ROOT,
                     M.records_dir_of(FOLDER), "/home/minions-runner"}  # rm -f refuses these, as on a real one
        # R136: who owns what (the runner user's own folders and what is in them; root's or the person's when said),
        # and the disks mounted inside (another file system: find -xdev does not enter it)
        self.owner, self.mounts = {}, set()
        # R136 (the owner's fault 1, 2026-11-25): file modes, as the kernel answers them. `ro`: folders whose owner has
        # no write right (Go's module cache leaves its folders so): nothing in one is unlinked until its owner gives the
        # right back. `stuck`: a thing of the runner user's that no unlink takes (a stand-in for whatever else refuses)
        self.ro, self.stuck = set(), set()
        # R136 (the owner's change 2): folders of someone else's the runner user may not enter (0700): its own finds
        # say "Permission denied" for each (exit 1) and list nothing below it; root's walk enters them
        self.closed = set()
        # R136 (the owner's word 2026-11-25): another program's files in /tmp that it deletes while a walk is under way
        # (`vanish`: gone before find reads them; findutils 4.9.0 says "No such file or directory", exit 1, unless
        # -ignore_readdir_race), and a place there root itself may not enter (`root_closed`: a FUSE mount of another
        # user's answers root "Permission denied"; not seen on a real machine, named)
        self.vanish, self.root_closed = set(), set()
        # find's line as the real 4.9.0 writes it (the owner's trial and vm/run64, 2026-11-25): the path in curly quotes
        # in the tool's C.UTF-8, in straight quotes in plain C; a FOLDER gone still gives its line WITH the option
        self.quotes, self.gone_words = ("\u2018", "\u2019"), "No such file or directory"

    def restart(self):
        """R210: the computer restarted. Every unit stops; each enabled one whose file is there starts again, as
        systemd does at boot; a runner unit whose slice file is gone starts in a slice systemd makes from its name
        (Slice=minions.slice), with none of the tool's limits."""
        self.order.append("restart")
        self.active = {}
        for u in sorted(self.enabled):
            if f"/etc/systemd/system/{u}" in self.paths:
                self.active[u] = "active"
                self.loaded.add(u)
                if u == UNIT:
                    self.listener = True
                    self.active[M.SLICE] = "active"
        self.loaded = {u for u in self.loaded if f"/etc/systemd/system/{u}" in self.paths or u in self.active}

    def owner_of(self, path):
        if path in self.owner:
            return self.owner[path]
        if path in self.mounts:
            return "root"  # a disk mounted there shows its own top's owner: root's, for a tmpfs mounted with no uid=
        runner = (FOLDER, M.records_dir_of(FOLDER), "/home/minions-runner")
        return "minions-runner" if any(path == r or path.startswith(r + "/") for r in runner) else "root"

    def dev_of(self, path):
        on = [m for m in self.mounts if path == m or path.startswith(m + "/")]
        return str(1 + sorted(self.mounts).index(max(on, key=len))) if on else "0"

    def under(self, top):
        return sorted(p for p in self.paths if p.startswith(top.rstrip("/") + "/"))

    def walk(self, top, prune=False):
        """find -xdev -P's walk below `top`, in order: nothing under a disk mounted there or under a link is entered;
        with `prune`, nothing under what the runner user did not make either (find's -prune)."""
        out, top = [], top.rstrip("/")
        for p in self.under(top):
            parts = p[len(top) + 1:].split("/")
            anc = [top + "/" + "/".join(parts[:i]) for i in range(1, len(parts))]
            if any(x in self.mounts or x in self.links or (prune and self.owner_of(x) != "minions-runner") for x in anc):
                continue
            out.append(p)
        return out

    def raced(self, a):
        """find's answer for what vanished under its walk: ([its lines], code)."""
        lines, code, (q0, q1) = [], 0, self.quotes
        for p in sorted(self.vanish):
            folder = self.is_dir(p)  # seen on 4.9.0: the option covers a file gone, never a folder gone
            self.paths -= {p, *self.under(p)}
            if folder or "-ignore_readdir_race" not in a:
                lines.append(f"find: {q0}{p}{q1}: {self.gone_words}\n")
                code = 1
        for p in sorted(self.root_closed):  # `vanish` stays: the other program goes on, so every walk meets it
            lines.append(f"find: {q0}{p}{q1}: Permission denied\n")
            code = 1
        return lines, code

    def kind_of(self, p):
        return "l" if p in self.links else "d" if self.is_dir(p) else "f"

    def unlink_refused(self, p, parent):
        """As the kernel answers an unlink by the runner user: refused in a folder its owner may not write (EACCES),
        and for a thing no unlink takes (`stuck`)."""
        return parent in self.ro or p in self.stuck

    def give_rights_back(self, starts, inside_only=False):
        """find STARTS -xdev [-mindepth 1] -type d -user R ! -perm -u+rwx -exec chmod u+rwx {} ; as the runner user:
        only folders it owns (a link is never -type d; a start that is a link is the link itself), one file system.
        Without -mindepth 1 (the owner's change 1) each start is a folder it may give the right back on too."""
        for t in starts:
            self.order.append(f"chmod u+rwx in {t}")
            for p in ([] if inside_only else [t]) + self.walk(t):
                if self.owner_of(p) == "minions-runner" and p not in self.links:
                    self.ro.discard(p)
        return 0, ""

    def runner_find(self, a):
        """find as the runner user, the forms the uninstall uses (as findutils 4.9.0 does them)."""
        top = a[1]
        if a[2:] == ["-maxdepth", "0", "-printf", "%D %y\n"]:  # a start that is a link is the link itself (find -P)
            y = "l" if top in self.links else "d" if self.is_dir(top) else "f"
            return ((0, f"{self.dev_of(top)} {y}\n") if top in self.paths or top in self.links else
                    (1, f"find: '{top}': No such file or directory"))
        if "chmod" in a:
            return self.give_rights_back([top], inside_only="-mindepth" in a)
        if "-prune" in a and a[-1].endswith("\\0"):
            # R136 (faults 1 and 4): every record ends with a NUL; what the runner user did not make is named and not
            # entered ("F"), what it still owns is named too ("O"): `! -user R -prune -printf F... -o -printf O...`
            out = []
            for p in self.walk(top, prune=True):
                k = "O" if self.owner_of(p) == "minions-runner" else "F"
                out.append(f"{k}\t{self.dev_of(p)}\t{self.owner_of(p)}\t{self.kind_of(p)}\t{p[len(top.rstrip('/')) + 1:]}\0")
            return 0, "".join(out)
        def tops():  # what the runner user did not make, not entered; nor what is on another file system
            out = []
            for p in self.under(top):
                rel = p[len(top.rstrip("/")) + 1:]
                parts = rel.split("/")
                anc = [top.rstrip("/") + "/" + "/".join(parts[:i]) for i in range(1, len(parts))]
                if any(self.owner_of(x) != "minions-runner" or x in self.mounts or x in self.links for x in anc):
                    continue
                if self.owner_of(p) != "minions-runner" or p in self.mounts:
                    out.append(p)
            return out
        if "-delete" in a:
            self.order.append(f"empty {top}")
            failed = 0
            for p in sorted(self.under(top), key=lambda x: -x.count("/")):  # depth first, as -delete does
                parent = p.rsplit("/", 1)[0]
                if p not in self.paths or self.owner_of(p) != "minions-runner" or p in self.mounts:
                    continue
                if any(p.startswith(m + "/") for m in self.mounts) or any(p.startswith(l + "/") for l in self.links):
                    continue  # -xdev; a link is never followed
                if self.owner_of(parent) != "minions-runner" and parent != top:
                    failed = 1  # a folder of root's: its entries are not the runner user's to unlink
                    continue
                if self.unlink_refused(p, parent):
                    failed = 1  # "cannot delete ...: Permission denied", as findutils 4.9.0 said it (the owner's trial)
                    continue
                if p not in self.links and self.under(p):
                    failed = 1  # not empty: what stayed in it holds it
                    continue
                self.paths.discard(p)
                self.links.discard(p)
            return failed, ""
        if "-prune" in a:
            out = "".join(f"{self.dev_of(p)}\t{self.owner_of(p)}\t"
                          f"{'l' if p in self.links else 'd' if self.is_dir(p) else 'f'}\t{p[len(top.rstrip('/')) + 1:]}\n"
                          for p in tops())
            return 0, out
        return None

    def public_find(self, a):
        """find as the runner user in /tmp, /var/tmp and /dev/shm, as findutils 4.9.0 does them: -delete unlinks what
        that user owns where it may (a sticky place's own top, or a folder of its own), a folder only once empty, a link
        never followed; -print lists what it owns still; the two reads of what holds a thing (R136)."""
        R, tops = "minions-runner", M.PUBLIC_TMP
        starts = []
        for x in a[1:]:
            if x.startswith(("-", "!")):
                break
            starts.append(x)
        def walk():
            out = []
            for t in tops:
                for p_ in self.under(t):
                    if any(p_.startswith(m + "/") for m in self.mounts) or any(p_.startswith(l + "/") for l in self.links):
                        continue  # -xdev; a link is never followed
                    if any(p_.startswith(c + "/") for c in self.closed):
                        continue  # a folder it may not enter
                    out.append(p_)
            return out
        shut = 1 if self.closed else 0  # find's exit for "Permission denied" on a folder it may not enter
        if "chmod" in a:
            return self.give_rights_back(starts, inside_only="-mindepth" in a)
        if "-delete" in a:
            self.order.append("find -delete in /tmp")
            failed = 0
            for p_ in sorted(walk(), key=lambda x: -x.count("/")):
                if self.owner_of(p_) != R or p_ not in self.paths:
                    continue
                parent = p_.rsplit("/", 1)[0]
                if parent not in tops and self.owner_of(parent) != R:
                    failed = 1  # a folder of someone else's: the runner user may not unlink in it
                    continue
                if self.unlink_refused(p_, parent):
                    failed = 1
                    continue
                if p_ not in self.links and self.under(p_):
                    failed = 1  # not empty
                    continue
                self.paths.discard(p_)
                self.links.discard(p_)
            return failed or shut, ""
        end = "\0" if a[-1].endswith("\\0") or "-print0" in a else "\n"  # R136 (item 4): each record ends with a NUL
        if ("-print" in a or "-print0" in a) and starts == list(tops):
            self.order.append("find -print in /tmp")
            said_, raced = self.raced(a) if self.vanish else ([], 0)
            listed = "".join(p_ + end for p_ in walk() if self.owner_of(p_) == R)
            return shut or raced, (M.Out(listed, "".join(said_)) if said_ else listed)  # find's lines: standard error
        if "-maxdepth" in a and a[-1] in ("%u\t%p\n", "%u\t%p\\0"):
            return 0, "".join(f"{self.owner_of(x)}\t{x}{end}" for x in starts if x in self.paths)
        if "-mindepth" in a:
            held = {x for x in starts if x not in self.links and any(
                q.rsplit("/", 1)[0] == x and self.owner_of(q) != R for q in self.paths)}
            return 0, "".join(x + end for x in sorted(held))
        return 1, f"find: the fake does not know {a}"

    def is_dir(self, path):
        return path in self.dirs or any(p.startswith(path.rstrip("/") + "/") for p in self.paths)

    def gone(self, path):
        self.paths = {p for p in self.paths if p != path and not p.startswith(path.rstrip("/") + "/")}

    def answer(self, argv, input=None):
        a = list(argv)
        if self.interrupt_on and self.interrupt_on(a):
            self.interrupt_on = None
            raise KeyboardInterrupt
        if a[:2] == ["sudo", "-n"]:
            a = a[2:]
            if a[:3] == ["-u", "minions-runner", "--"] and not self.user:  # as sudo 1.9.15p5 answered (seen, R167)
                return 1, "sudo: unknown user minions-runner\nsudo: error initializing audit plugin sudoers_audit\n"
            if a[:3] == ["-u", "minions-runner", "--"] and a[3:5] != ["bash", "-c"] and a[3:5] != ["env", "-i"]:
                a = a[3:]  # R128: the runner user's own steps (read, rm, find, kill) in its own folders
                if a[:3] == ["stat", "-c", "%F"]:  # R128: what kind of thing a job may have left there
                    if a[3] in self.links:
                        return 0, "symbolic link\n"
                    if a[3] in self.files or a[3] in self.paths:
                        return 0, "regular file\n"
                    return 1, f"stat: cannot statx '{a[3]}': No such file or directory"
                if a[:2] == ["sh", "-c"] and a[2] == M.ENV_SH:  # R206, R209: what stands at a runner .env
                    p_ = a[4]
                    word = ("a link" if p_ in self.links else "not readable" if p_ in getattr(self, "unreadable", ())
                            else "readable" if p_ in self.files else "not a file" if p_ in self.dirs else "not there")
                    return 0, f"{M.ENV_MARK} {word}\n"
                if a[:3] == ["stat", "-c", "%s"] and a[3] in self.files:
                    return 0, f"{len(self.files[a[3]].encode('utf-8', 'surrogateescape'))}\n"
                if a[:1] == ["sha256sum"] and a[1] in self.files:
                    return 0, f"{M.env_sum(self.files[a[1]])}  {a[1]}\n"
                if a[:1] == ["cat"]:
                    if a[1] in self.files:
                        return 0, self.files[a[1]]
                    return (0, UNIT + "\n") if a[1] in self.paths else (1, f"cat: {a[1]}: No such file or directory")
                if a[:1] == ["find"] and any(a[1] == t or a[1].startswith(t + "/") for t in M.PUBLIC_TMP):
                    return self.public_find(a)  # R130, R136: what the runner user owns in /tmp, /var/tmp, /dev/shm
                if a[:2] == ["kill", "-KILL"]:
                    self.order.append("kill -KILL -1 (as the runner)")
                    return 0, ""
                if a[:1] == ["find"] and a[2:3] in (["-xdev"], ["-maxdepth"]):
                    return self.runner_find(a)
                if a[:1] == ["find"]:
                    self.order.append(f"empty {a[1]}")
                    self.paths = {p for p in self.paths if not p.startswith(a[1].rstrip("/") + "/")}
                    return 0, ""  # as findutils 4.9.0 answered, started from / (the tool's admin steps start there)
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
            if not any(x.startswith("-") and "r" in x for x in a[1:-1]) and self.is_dir(a[-1]):
                return 1, f"rm: cannot remove '{a[-1]}': Is a directory"  # as coreutils 9.4 answers rm -f on a folder
            self.order.append(f"rm {a[-1]}")
            self.gone(a[-1])
            return 0, ""
        if a[:1] == ["rmdir"]:
            if a[1] in self.plain:  # as coreutils 9.4 answers rmdir on a file
                return 1, f"rmdir: failed to remove '{a[1]}': Not a directory"
            if any(p.startswith(a[1].rstrip("/") + "/") for p in self.paths):
                return 1, f"rmdir: failed to remove '{a[1]}': Directory not empty"
            self.order.append(f"rmdir {a[1]}")
            self.gone(a[1])
            return 0, ""
        if a[:1] == ["userdel"]:
            self.order.append("userdel")
            self.user = False
            if "-r" in a:
                self.gone("/home/minions-runner")
            return 0, ""
        if a[:1] == ["find"] and ("-uid" in a or "-nouser" in a):
            # R136: root's find of what the runner user's number owns (the owner's fault 3: it prints each path it
            # names, as findutils 4.9.0 does, a NUL after each with -print0); with chown, each is handed to root.
            # Forms: a walk below a folder (-xdev; -mindepth 1 leaves the start itself out), or the exact starts
            # (-maxdepth 0; `! -type l` leaves a link out); -nouser: owned by a number no user has now.
            starts = []
            for x in a[1:]:
                if x.startswith(("-", "!")):
                    break
                starts.append(x)
            end = "\0" if "-print0" in a or (a[-1].endswith("\\0")) else "\n"
            hand = "chown" in a
            self.order.append(("to root " if hand else "read for the number ") + " ".join(starts))
            found = []
            for s in starts:
                if s not in self.paths and s not in self.links:
                    continue
                if "-maxdepth" in a:
                    group = [s]
                else:
                    group = ([] if "-mindepth" in a else [s]) + self.walk(s)
                for p in group:
                    if "!" in a and p in self.links:
                        continue
                    mine = self.owner_of(p) == "minions-runner"
                    if "-nouser" in a and (not mine or self.user):
                        continue
                    if mine:
                        found.append(p)
            for p in found:
                if hand:
                    self.owner[p] = "root"
            if any(s_.startswith("/tmp") for s_ in starts) and (self.vanish or self.root_closed):
                said_, code = self.raced(a)
                return code, M.Out("".join(p + end for p in found), "".join(said_))  # find's lines: standard error
            if "-printf" in a:
                return 0, "".join(f"{self.kind_of(p)}\t{p}{end}" for p in found)
            return 0, "".join(p + end for p in found)
        if a[:1] == ["systemctl"] and argv[0] == "sudo":
            verb, rest = a[1], [x for x in a[2:] if not x.startswith("--")]
            gone = [u for u in rest if verb == "disable" and f"/etc/systemd/system/{u}" not in self.paths]
            if gone:  # as systemd 255 answered (rule 30, ONE CALL, ONE UNIT): no unit of that call is stopped
                return 1, f"Failed to disable unit: Unit file {gone[0]} does not exist.\n"
            if verb == "disable":
                self.enabled -= set(rest)
            if verb == "enable":
                self.enabled |= set(rest)
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
            # tells a loaded file is FragmentPath. Only the fields asked for, in systemd's own order; a field this
            # systemd does not know prints nothing (seen, 255), as `old_systemd` stands in for SliceOf.
            frag = f"/etc/systemd/system/{u}" if u in self.loaded else ""
            in_slice = sorted(x for x in self.loaded if self.slice_of.get(x) == M.SLICE)
            have = {"LoadState": "loaded" if u in self.loaded or u.endswith(".slice") else "not-found",
                    "ActiveState": self.active.get(u, "inactive") if u in self.loaded else "inactive",
                    "FragmentPath": frag, "DropInPaths": "",
                    "Slice": "-.slice" if u.endswith(".slice") else self.slice_of.get(u, "system.slice")}
            if u == M.SLICE and not self.old_systemd:
                have["SliceOf"] = " ".join(in_slice)
            asked = [a[i + 1] for i in range(3, len(a) - 1) if a[i] == "-p"]
            return 0, "".join(f"{k}={have[k]}\n" for k in asked if k in have)
        if a[:2] == ["systemctl", "is-enabled"] and (a[2].startswith("actions.runner.") or a[2].startswith("minions")):
            if f"/etc/systemd/system/{a[2]}" not in self.paths:  # as systemd 255 answers for a unit with no file
                return 1, f"Failed to get unit file state for {a[2]}: No such file or directory\n"
            return (0, "enabled\n") if a[2] in self.enabled else (1, "disabled\n")
        if a[:2] == ["systemctl", "is-active"]:
            st = self.active.get(a[2], "inactive")
            return (0 if st == "active" else 3), st + "\n"
        if a[:2] == ["systemctl", "list-units"]:
            # R210: systemd keeps loaded what runs, what is enabled and what a loaded unit pulls in (the watch's
            # service by its timer); a unit disabled and not running is let go, and list-units --all no longer
            # names it (seen on systemd 255: 36 of 39 disabled, inactive service files not in the list)
            held = [u for u in sorted(self.loaded) if self.active.get(u) == "active" or u in self.enabled
                    or u.endswith(".slice") or (u == f"{M.WATCH_UNIT}.service" and f"{M.WATCH_UNIT}.timer" in self.loaded)]
            return 0, "".join(f"{u} loaded {self.active.get(u, 'inactive')}\n" for u in held)
        if a[:2] == ["systemctl", "list-unit-files"]:  # R210: by the files there, as systemd 255 answers
            pats = [x for x in a[2:] if not x.startswith("--")]
            have = sorted(p_.rsplit("/", 1)[1] for p_ in self.paths if p_.startswith("/etc/systemd/system/")
                          and p_.endswith((".service", ".timer", ".slice")) and p_.count("/") == 4
                          and any(__import__("fnmatch").fnmatch(p_.rsplit("/", 1)[1], q) for q in pats))
            if not have:
                return 1, LUF_NONE
            return 0, ("UNIT FILE STATE PRESET\n" + "".join(f"{u} {'enabled' if u in self.enabled else 'disabled'} "
                                                           "enabled\n" for u in have) + f"\n{len(have)} unit files listed.\n")
        if a[:1] == ["getent"]:  # R172: as glibc's getent answers: its line, or exit 2 for a key not found
            return (0, "minions-runner:x:1001:1001::/home/minions-runner:/bin/bash\n") if self.user else (2, "")
        if a[:2] == ["pgrep", "-u"]:
            if not self.user and a[2] != "1001":  # as procps-ng 4.0.4 answers for a user that does not exist (seen)
                return 2, "pgrep: invalid user name: minions-runner\n"
            if not self.user:
                return 1, ""  # by number: no process of that number
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
    host.isdir = world.is_dir
    host.there = lambda path, h=host: path in world.paths or path in h.files or path in h.dirs  # R210: as the person
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
    out = Out()
    code = M.uninstall(repo, host=host, gh=gh, out=out, interactive=True, **kw)
    return code, out.getvalue().splitlines()


class G6(unittest.TestCase):
    def test_G6_33_the_census_names_ghs_plain_text_login(self):
        # Row R166, seen 2026-11-24 on a stock Ubuntu 24.04 virtual machine (steered): after the step R160 prints, gh
        # kept the login in hosts.yml as plain text, and uninstall --all said nothing of it, though it outlives the
        # tool. Now the census says so in one line, never the value, and the file is left as it is.
        login = "github.com:\n    oauth_token: made-up-login-value\n    git_protocol: https\n    user: example-person\n"
        for everything in (True, False):
            with self.subTest(everything=everything):
                gh, world, host = remove_setup()
                path = os.path.join(host.gh_env()["GH_CONFIG_DIR"], "hosts.yml")
                host.files[path] = login
                code, lines = (run_uninstall(gh, host, repo=None, everything=True) if everything
                               else run_uninstall(gh, host))
                said = [x for x in lines if "hosts.yml" in x]
                self.assertEqual(said, [f"ok census: gh's login for github.com, kept as plain text in {path}: it is "
                                        "gh's; this tool did not write it and does not remove it, and it stays (your "
                                        "account and this computer's admins can read it)"], lines)
                self.assertFalse([x for x in lines if "made-up-login-value" in x])
                self.assertEqual(host.files.get(path), login)  # left as it is
                self.assertFalse([a for a in host.ran if any(path in str(w) for w in a)], "never touched by a command")
                self.assertFalse([x for x in lines if "gh auth logout" in x])  # gh's own way: not named before a proof
        # No login in it, or no file: no line. A file that could not be read: said so, never taken for none.
        for text in ("github.com:\n    git_protocol: https\n", None):
            gh, world, host = remove_setup()
            path = os.path.join(host.gh_env()["GH_CONFIG_DIR"], "hosts.yml")
            if text is not None:
                host.files[path] = text
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertFalse([x for x in lines if "census: gh's" in x], lines)
        gh, world, host = remove_setup()
        path = os.path.join(host.gh_env()["GH_CONFIG_DIR"], "hosts.yml")
        host.files[path] = login
        host.unreadable = {path}
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        said = [x for x in lines if "census: gh's" in x]
        self.assertEqual(len(said), 1, lines)
        self.assertTrue(said[0].startswith(f"warn census: gh's file {path}: not read ('[Errno 13] Permission denied"), said)
        self.assertIn("so whether it holds a login for github.com as plain text is not known", said[0])

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
        procs = [i for i, a in enumerate(ran) if a == ("pgrep", "-u", "1001")]  # R172: by the user's number
        cron = ran.index(("sudo", "-n", "crontab", "-r", "-u", "minions-runner"))
        self.assertTrue([i for i in procs if i < cron], "no process of that user is read back before its crontab goes")
        self.assertTrue(any("nothing of minions-runner is left running or scheduled" in x for x in lines), lines)
        ran = [a[5:] for a in host.ran if a[2:5] == ("-u", "minions-runner", "--") and a[5:6] == ("find",)]
        self.assertIn(("find", *M.PUBLIC_TMP, "-xdev", "-user", "minions-runner", "-delete"), ran)
        # a process that outlives SIGKILL stops the uninstall before userdel, and is named
        gh, world, host = remove_setup()
        real = host._answer
        host._answer = lambda argv, input=None: ((0, "4444\n") if list(argv) == ["pgrep", "-u", "1001"]
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
            if list(argv[5:6]) == ["find"] and argv[-1] == "-print0":  # R136 (item 4): each name ends with a NUL
                return 0, "/tmp/kept-by-a-job\0"
            return real2(argv, input)
        host._answer = left
        code, lines = run_uninstall(gh, host)
        self.assertTrue(world.user)
        self.assertIn("/tmp/kept-by-a-job", " ".join(lines))

    def test_G6_17_with_the_user_there_its_crontab_and_at_jobs_go_before_userdel_or_the_leave_stops(self):
        # Change 4, 2026-11-24: this tool's own uninstall is never the road that leaves a
        # crontab or an at job for the next user of that name (userdel leaves both, seen on run61). With the user
        # there, both are removed while crontab -r -u and atrm still answer, and read back; one that stays stops the
        # leave before userdel, named, the user kept.
        def world_with(cron_goes=True, at_goes=True, cron_says="no crontab for minions-runner\n"):
            gh, world, host = remove_setup()
            host._which.add("atq")
            sched = {"cron": True, "at": True}
            real = host._answer
            def answer(a, i=None):
                a = list(a)
                if a[:3] == ["sudo", "-n", "crontab"]:
                    world.order.append(" ".join(a[2:4]))
                    if a[3] == "-r" and cron_goes:
                        sched["cron"] = False
                    return ((0, "* * * * * echo left-by-a-job\n") if sched["cron"] else
                            (1, cron_says)) if a[3] == "-l" else (0, "")
                if a[:3] == ["sudo", "-n", "atq"]:
                    return 0, "7\tTue Nov 24 23:33:00 2026 a minions-runner\n" if sched["at"] else ""
                if a[:3] == ["sudo", "-n", "atrm"]:
                    world.order.append(" ".join(a[2:]))
                    if at_goes:
                        sched["at"] = False
                    return 0, ""
                return real(a, i)
            host._answer = answer
            return gh, world, host
        gh, world, host = world_with()
        code, lines = run_uninstall(gh, host)
        self.assertEqual(code, M.EXIT_OK, lines)
        o = world.order
        self.assertLess(o.index("crontab -r"), o.index("userdel"), o)
        self.assertLess(o.index("atrm 7"), o.index("userdel"), o)
        self.assertTrue(any("no crontab, no at job" in x for x in lines), lines)
        for cron_goes, at_goes, named in ((False, True, "its crontab"), (True, False, "its at jobs 7")):
            gh, world, host = world_with(cron_goes, at_goes)
            code, lines = run_uninstall(gh, host)
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertIn(f"the user {M.RUNNER_USER} is not deleted", lines[-1])
            self.assertIn(named, lines[-1])
            self.assertNotIn("userdel", world.order)
            self.assertTrue(world.user)
        # Point 4 and rule 31 (iv): crontab's own words carry a backtick ("user `minions-runner' unknown", seen on
        # run61). Where they are printed they are shown changed, and the line says so.
        gh, world, host = world_with(cron_says="crontab:  user `minions-runner' unknown\n")
        code, lines = run_uninstall(gh, host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn("user 'minions-runner' unknown", lines[-1])
        self.assertTrue(lines[-1].endswith(M.SHOWN_CHANGED), lines[-1])
        self.assertNotIn("userdel", world.order)

    def test_G2_14_setup_with_the_user_gone_reads_what_it_left_scheduled_before_useradd(self):
        # Change 4 (seen on a stock Ubuntu 24.04.5, vm/run61): userdel, with or without -r, leaves the user's crontab
        # (cron runs it by the NAME) and its at jobs (at runs them as the NUMBER, which useradd gives again): a new user
        # of that name ran the old crontab line each minute and the at job. Setup, with the user read absent, reads both
        # as root after the one admin ask and before useradd, three answers each, and stops naming what it found, each
        # rehearsed step its own; taken twice it is the same stop with its step named first; the steps taken, it goes on.
        cron = f"/var/spool/cron/crontabs/{M.RUNNER_USER}"
        at = "/var/spool/cron/atjobs"
        def no_runner_gh(sha):  # GitHub as it is: no runner until this run registers one
            listing = ("GET", f"repos/{REPO}/actions/runners?per_page=100")
            token = ("POST", f"repos/{REPO}/actions/runners/registration-token")
            gh = setup_gh(sha)
            gh.overrides[listing] = lambda body: runners_answer() if token in gh.calls else (200, {"total_count": 0,
                                                                                                 "runners": []})
            return gh
        def world(cron_file=True, at_dir=True, listing="a0000401c8a2cd 1001\n=0000401c8a2cd 1001\n"):
            host, sha = setup_host()
            fresh_look(host)
            host.runs[R_ROOT + ("/var/spool/cron/crontabs",)] = (0, "")
            host.runs[R_ROOT + (cron,)] = (0 if cron_file else 1, "")
            host.runs[R_ROOT + (at,)] = (0 if at_dir else 1, "")
            host.runs[("sudo", "-n", "find", at)] = (0, listing)
            return host, sha
        host, sha = world()
        for n in (1, 2):
            code, lines = run_setup(no_runner_gh(sha), host)
            self.assertEqual(code, M.EXIT_STOP, (n, lines))
            self.assertFalse([a for a in host.ran if a[2:3] == ("useradd",)], "no user is made")
            x = next(x for x in lines if x.startswith(f"stop the user {M.RUNNER_USER} is not on this computer"))
            self.assertIn(f"the crontab {cron}", x)
            self.assertIn("at job 4 (its file's owner is number 1001, which no user on this computer has)", x)
            self.assertIn(f"`sudo rm {cron}`", x)
            self.assertIn("`sudo atrm 4`", x)
            self.assertEqual(x.count("sudo atrm"), 1, "the running copy (=) is the same job, named once")
            self.assertIn("remove them first", x)
            # 2026-11-24: with no record that this tool made the user, what is true and
            # the way on, never "open an issue" for something of the person's own
            self.assertNotIn("open an issue", x)
            self.assertIn("If they are something else of yours, they are yours: setup goes on once they are no longer "
                          "there, removed as above or moved away", x)
        host.runs[R_ROOT + (cron,)] = (1, "")  # the steps taken as printed
        host.runs[("sudo", "-n", "find", at)] = (0, "")
        self.assertEqual(run_setup(no_runner_gh(sha), host)[0], M.EXIT_OK)
        # Three answers each: no cron folder or no at folder is "none" (the folder's own absence); a read that gave
        # no answer stops, saying what was not read, never "none".
        host, sha = world(cron_file=False, at_dir=False)
        host.runs[R_ROOT + ("/var/spool/cron/crontabs",)] = (1, "")
        self.assertEqual(run_setup(no_runner_gh(sha), host)[0], M.EXIT_OK)
        fails = {"cron's folder": (R_ROOT + ("/var/spool/cron/crontabs",), "whether cron's folder"),
                 "the crontab": (R_ROOT + (cron,), f"whether {cron} is there"),
                 "at's folder": (R_ROOT + (at,), "whether at's folder"),
                 "find": (("sudo", "-n", "find", at), "the at jobs in /var/spool/cron/atjobs (find answered code 1)")}
        for name, (key, words) in fails.items():
            with self.subTest(read=name):
                host, sha = world()
                host.runs[key] = (1, "sudo: unable to resolve host box\n") if key[2] == "find" else (
                    2, "test: extra argument\n")
                code, lines = run_setup(no_runner_gh(sha), host)
                self.assertEqual(code, M.EXIT_STOP, lines)
                x = next(x for x in lines if x.startswith(f"stop could not read what a user named {M.RUNNER_USER}"))
                self.assertIn(words, x)
                self.assertNotIn("none", x)
                self.assertFalse([a for a in host.ran if a[2:3] == ("useradd",)])
        # A file name that is not at's own form is a value this tool did not write: could not be read, never a job.
        host, sha = world(listing="a0000401c8a2cd 1001\n$(reboot) 1001\n")
        code, lines = run_setup(no_runner_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        x = next(x for x in lines if x.startswith(f"stop could not read what a user named {M.RUNNER_USER}"))
        self.assertIn("1 file(s) in /var/spool/cron/atjobs whose owner has no user and whose name is not at's own form", x)
        self.assertNotIn("reboot", " ".join(lines))
        # A kind of system whose layout this tool does not know: said in a line of its own, never "none".
        host, sha = setup_host(files={"/etc/os-release": 'ID=gentoo\n'})
        fresh_look(host)
        code, lines = run_setup(no_runner_gh(sha), host)
        said_ = [x for x in lines if x.startswith("warn not read: where cron and at keep their jobs")]
        self.assertTrue(said_, lines)
        # 2026-11-24: when it matters, in the line
        self.assertIn("this matters only where a user of that name was on this computer before", said_[0])

    def test_G6_32_the_runner_archive_is_ours_by_its_name_on_linux_and_on_a_mac(self):
        # Row R195: the runner's archive is downloaded into this tool's own folder under GitHub's own file name, and a
        # download cut short leaves it there. The names this tool deletes there as its own (R129) held Linux's form
        # only, so on a Mac an uninstall left the archive, and the folder with it. One pattern now names the archive:
        # the download takes only a name of that form, and the leave deletes by it. GitHub's recorded list is read.
        rows = load("setup-release.jsonl")
        names = [x["filename"] for x in rows[0]["data"]]
        self.assertTrue(any("-osx-" in n for n in names) and any("-linux-" in n for n in names), names)
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "state")
            os.mkdir(state)
            for n in names + ["minions.json", "journal.log"]:
                open(os.path.join(state, n), "w").close()
            self.assertEqual(M.delete_ours(state, M.STATE_NAMES), [], "every archive GitHub names is this tool's own")
            self.assertFalse(os.path.exists(state), "and the folder goes with them")
            os.mkdir(state)
            others = ["actions-runner-osx-x64-2.338.2.tar.gz.part", "actions-runner-win-x64-2.338.2.zip", "notes.txt"]
            for n in others + ["actions-runner-osx-arm64-2.338.2.tar.gz"]:
                open(os.path.join(state, n), "w").close()
            self.assertEqual(M.delete_ours(state, M.STATE_NAMES), sorted(others), "what is not ours is left, named")
        # The download takes only a name of the archive's form, on a Mac too: never a path, never another kind.
        mac = FakeHost(system="Darwin", which=("gh",), groups=("staff", "admin"))
        rel = M.runner_release(StandIn(rows), REPO, mac, M.Report(Out()))
        self.assertEqual(rel["filename"], "actions-runner-osx-x64-2.338.2.tar.gz")
        for bad in ("../actions-runner-osx-x64-2.338.2.tar.gz", "x/actions-runner-osx-x64-2.338.2.tar.gz",
                    "actions-runner-osx-x64-2.338.2.zip", "actions-runner-osx-x64-2.338.2.tar.gz\n"):
            changed = copy.deepcopy(rows)
            for x in changed[0]["data"]:
                if (x["os"], x["architecture"]) == ("osx", "x64"):
                    x["filename"] = bad
            rep = M.Report(Out())
            self.assertIsNone(M.runner_release(StandIn(changed), REPO, mac, rep), bad)
            self.assertIn("not in the shape this tool knows", folded(rep.lines)[-1], bad)

    def test_G6_30_uninstall_with_the_user_gone_names_what_it_left_scheduled(self):
        # Change 4: uninstall with the user absent runs nothing as it and deletes none of its schedules; it names them
        # as what stayed, with the rehearsed steps, in its warning and in the census; the leave finishes.
        cron = f"/var/spool/cron/crontabs/{M.RUNNER_USER}"
        gh, world, host = remove_setup(moved=False)
        world.user, world.listener = False, False
        world.paths |= {"/var/spool/cron/crontabs", cron, "/var/spool/cron/atjobs"}
        real = host._answer
        host._answer = lambda a, i=None: ((0, "a0000401c8a2cd 1001\n") if list(a[:4]) == [
            "sudo", "-n", "find", "/var/spool/cron/atjobs"] else real(a, i))
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn(cron, world.paths, "not deleted")
        census = [x for x in lines if x.startswith("ok census: left by a user named minions-runner")]
        warned = [x for x in lines if x.startswith("warn left by a user named minions-runner")]
        for x in census + warned:
            self.assertIn(f"`sudo rm {cron}`", x)
            self.assertIn("`sudo atrm 4`", x)
        self.assertEqual((len(census), len(warned)), (1, 1), lines)

    def test_G6_17_a_stop_at_the_last_runner_names_a_step_that_finishes(self):
        # R130 (the owner's item 7, 2026-11-25; the lane's own finding): the stops of the last uninstall of one
        # repository said "run the same command again", and the same command found no runner of that repository and
        # said there was nothing to uninstall, the user still there. A stop names a step that finishes: the same
        # command, run again once what it says is settled, goes on to the user and ends with exit 0.
        R, home = "minions-runner", f"/home/{M.RUNNER_USER}"
        def crontab_once(world, host):
            real, left = host._answer, [True]
            def answer(argv, input=None):
                if list(argv[2:5]) == ["crontab", "-l", "-u"] and left[0]:
                    return 0, "* * * * * true\n"
                return real(argv, input)
            host._answer = answer
            return lambda: left.__setitem__(0, False)
        def home_unread_once(world, host):
            real, left = host._answer, [True]
            def answer(argv, input=None):
                a = list(argv)
                if a[:6] == ["sudo", "-n", "-u", R, "--", "find"] and a[6:7] == [home] and "-prune" in a and left[0]:
                    return 1, "find: '/home/minions-runner/x': Permission denied\n"
                return real(argv, input)
            host._answer = answer
            return lambda: left.__setitem__(0, False)
        for name, make in (("a crontab that is still listed", crontab_once), ("its home not read", home_unread_once)):
            gh, world, host = remove_setup(moved=False)
            settle = make(world, host)
            code, lines = run_uninstall(gh, host)
            self.assertEqual(code, M.EXIT_STOP, (name, lines))
            stop = [x for x in lines if x.startswith("stop ")]
            self.assertTrue(stop and "run the same command again" in stop[0], (name, lines))
            self.assertTrue(world.user, name)
            settle()
            code, lines = run_uninstall(gh, host)  # the step the stop names
            self.assertEqual(code, M.EXIT_OK, (name, lines))
            self.assertFalse(world.user, (name, lines))
            self.assertNotIn(home, world.paths, (name, lines))
            self.assertIn(f"ok the user {M.RUNNER_USER} and its home are deleted, read back", lines)

    def test_G6_18_a_unit_that_failed_is_not_left_loaded_after_uninstall(self):
        # R132: a failed oneshot unit stays loaded as "failed" after its file is deleted, until reset-failed (seen on
        # a stock Ubuntu 24.04 virtual machine, 2026-11-24): uninstall resets the units it deleted after the reload.
        gh, world, host = remove_setup()
        code, lines = run_uninstall(gh, host, everything=True)
        self.assertIn("ok census: the slice and the watch (scheduled checks): none", lines)
        ran = [a[2:] for a in host.ran if a[2:3] == ("systemctl",)]
        for units in ((UNIT,), (f"{M.WATCH_UNIT}.timer",), (f"{M.WATCH_UNIT}.service",)):  # one call each (rule 30)
            reset = [i for i, a in enumerate(ran) if a[1] == "reset-failed" and set(units) <= set(a)]
            self.assertTrue(reset, units)
            self.assertTrue(any(a[1] == "daemon-reload" for a in ran[:reset[0]]), units)

    def test_G6_19_a_foreign_file_does_not_stop_a_leave(self):
        # The fake's deletes answer as the real commands did on a stock Ubuntu 24.04 (coreutils 9.4, findutils 4.9.0,
        # sudo 1.9.15p5; 2026-11-25, the harness's own scratch folders): rmdir refuses a file, rm -f refuses a folder,
        # and the runner's find deletes and exits 0 when started from / (1 from the person's home, which is closed to
        # it: the tool starts its admin steps in /, held below).
        gh, world, host = remove_setup(moved=False)
        RU = ["sudo", "-n", "-u", "minions-runner", "--"]
        f, d, full = f"{M.RECORDS_ROOT}/a-file", f"{M.RECORDS_ROOT}/a-folder", M.records_dir_of(FOLDER)
        world.paths |= {f, d, f"{full}/x"}
        world.plain.add(f)
        world.dirs.add(d)
        for argv, want, there in (
                (["sudo", "-n", "rmdir", f], (1, f"rmdir: failed to remove '{f}': Not a directory"), [f]),
                (["sudo", "-n", "rm", "-f", d], (1, f"rm: cannot remove '{d}': Is a directory"), [d]),
                (RU + ["rm", "-f", d], (1, f"rm: cannot remove '{d}': Is a directory"), [d]),
                (["sudo", "-n", "rmdir", full], (1, f"rmdir: failed to remove '{full}': Directory not empty"), [full]),
                (RU + ["find", full, "-mindepth", "1", "-delete"], (0, ""), [full]),
                (["sudo", "-n", "rmdir", full], (0, ""), []),
                (["sudo", "-n", "rmdir", d], (0, ""), []),
                (["sudo", "-n", "rm", "-f", f], (0, ""), [])):
            self.assertEqual(world.answer(tuple(argv)), want, argv)
            for x in there:
                self.assertIn(x, world.paths, argv)
        self.assertFalse({f, d, full, f"{full}/x"} & world.paths)
        # The tool's admin steps start in /, a folder every user may enter, so a runner-user command never fails on the
        # person's working folder (closed to that user) and a real failure is never hidden behind that one.
        seen = []
        def fake_run(argv, **kw):
            seen.append((argv[0], kw.get("cwd")))
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        with mock.patch.object(M.subprocess, "run", fake_run):
            M.Host().run(["sudo", "-n", "-u", "minions-runner", "--", "find", "/x", "-mindepth", "1", "-delete"])
            M.Host().run_in(["sudo", "-n", "true"], "")
            M.Host().run(["uname", "-s"])
        self.assertEqual(seen, [("sudo", "/"), ("sudo", "/"), ("uname", None)])
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
        self.assertIn("ok census: the runner user: minions-runner (kept: this tool's record here does not name it as "
                      "one this tool made)", census)
        self.assertIn("ok census: the folder /home/minions-runner: kept: it is the home of the user minions-runner, "
                      "which this tool's record here does not name as one this tool made", census)
        self.assertIn(f"ok census: this tool's state folder: {host.state_dir()} (kept: it holds my-notes.txt, which "
                      "this tool did not write; that and the folder are yours)", census)
        self.assertIn("ok census: the slice and the watch (scheduled checks): none", census)
        self.assertTrue(os.path.exists(os.path.join(host.state_dir(), "my-notes.txt")))
        self.assertEqual(sorted(os.listdir(host.state_dir())), ["my-notes.txt"])  # ours went, by name
        for x in census:
            if "kept:" in x and "your record of what this tool did" not in x:
                self.assertNotIn("`", x, "no command for what is the person's")
        # R210: the runner user stays, none of its processes runs and no service of this tool's form is here: the
        # off line, naming it as what this tool's record does not name, never as not made by this tool.
        self.assertEqual(lines[-1], "ok this tool is off this computer: nothing of this tool's names here runs now or "
                         "starts when this computer restarts, as read. What stayed is named above with why each "
                         "stayed; of it, this tool's record here does not name: the user minions-runner; the folder "
                         "/home/minions-runner")
        # Seen on a stock Ubuntu 24.04 machine (2026-11-25, the end part, part 3): a file put by hand, as root, at the
        # top of the jobs' records folder stopped uninstall --all after the slice ("could not delete
        # /var/lib/windstorm-repo-minions"), exit 2, with the runner user and the tool's copy still there. It is not
        # this tool's: it stays with that folder, named in the census with why, and the leave goes to its end.
        gh, world, host = remove_setup(moved=False)
        planted = f"{M.RECORDS_ROOT}/my-notes.txt"
        world.paths.add(planted)
        world.plain.add(planted)
        code, lines = run_uninstall(gh, host, everything=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertFalse([x for x in lines if x.startswith("stop ")], lines)
        self.assertIn(planted, world.paths)  # never deleted, never moved
        self.assertNotIn(M.records_dir_of(FOLDER), world.paths)  # ours went, by name
        self.assertNotIn(M.SEEN_ROOT, world.paths)
        self.assertIn("userdel", world.order)  # the leave went to its end: the user this tool made is gone
        census = [x for x in lines if x.startswith(("ok census:", "warn census:"))]
        self.assertIn(f"ok census: the folder {M.RECORDS_ROOT}: kept: it holds my-notes.txt, which this tool did not "
                      "write; that stays, and the folders above it with it", census)
        self.assertFalse([x for x in census if M.RECORDS_ROOT in x and "`" in x], "no command for what is the person's")
        self.assertEqual(lines[-1], R136_LAST)
        # nothing foreign: the census stays last, as before (G6.10)
        gh, world, host = remove_setup(moved=False)
        code, lines = run_uninstall(gh, host, everything=True)
        self.assertEqual(code, M.EXIT_OK)
        self.assertTrue(lines[-1].startswith("ok census: your record of what this tool did"), lines[-1])
        # setup is where a foreign thing stops (G2.14)
        host, sha = setup_host(runs={("getent", "passwd", "minions-runner"): (0, "minions-runner:x:1001:1001::/home/minions-runner:/bin/bash\n")})
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
            self.assertEqual(M.watch(host=h, gh=StatusGH(), out=Out()), M.EXIT_OK, form)

    def test_G6_40_a_cut_after_the_journal_is_kept_does_not_add_it_twice(self):
        # Row R212, seen 2026-11-26 in a stand-in trial: a cut after the journal was added to the person's record
        # and before journal.log was deleted made the next uninstall --all add it again (59 lines for 34).
        class Cut(BaseException):
            pass
        real = M.delete_ours
        seen = {}

        def cut_once(folder, names):
            if folder == host.state_dir() and "first" not in seen:
                with open(os.path.join(folder, "journal.log"), encoding="utf-8") as f:
                    seen["first"] = f.read()
                raise Cut()
            if folder == host.state_dir():
                with open(os.path.join(folder, "journal.log"), encoding="utf-8") as f:
                    seen["second"] = f.read()
            return real(folder, names)

        gh, world, host = remove_setup()
        rec = f"{host.record_dir()}/journal.log"
        host.files[rec] = "2026-01-01T00:00:00Z an earlier setup's journal, the person's\n"
        earlier = host.files[rec]
        with mock.patch.object(M, "delete_ours", cut_once):
            with self.assertRaises(Cut):
                run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(host.files[rec], earlier + seen["first"], "the journal was added before the cut")
            code, out = run_uninstall(gh, host, repo=None, everything=True)
        self.assertTrue(seen["second"].startswith(seen["first"]), "the second run's own steps came after it")
        self.assertGreater(len(seen["second"]), len(seen["first"]))
        self.assertEqual(host.files[rec], earlier + seen["second"], "once: nothing doubled, nothing lost")
        self.assertFalse(os.path.isdir(host.state_dir()))
        # A journal that does not continue what the record ends with is added whole
        host2 = linux_host()
        host2.files[f"{host2.record_dir()}/journal.log"] = "x 1\ny 2\n"
        self.assertEqual(M.journal_kept_already(host2, "y 2\nz 3\n"), len("y 2\n"))
        self.assertEqual(M.journal_kept_already(host2, "q 9\n"), 0)
        self.assertEqual(M.journal_kept_already(linux_host(), "q 9\n"), 0, "no journal in the record yet")

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
        M.main(["uninstall", "--all"], host=host, out=Out(), gh=gh)
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
        o = Out()
        M.uninstall(None, everything=True, host=host, gh=gh, out=o, interactive=True, delete_record=True)
        self.assertNotEqual(host.record_count(), (0, 0))
        self.assertTrue(any(x.startswith("need you: deleting your record") and "your own terminal" in x
                            for x in o.getvalue().splitlines()))
        self.assertTrue(o.getvalue().splitlines()[-1].startswith("ok census: your record"))
        for typed, kept in (("", True), ("yes", True), ("delete", False)):
            gh, world, host = world_with_jobs()
            host.tty, host.answer = True, typed
            o = Out()
            M.uninstall(None, everything=True, host=host, gh=gh, out=o, interactive=True, delete_record=True)
            self.assertIn("Type delete", host.asked)
            self.assertEqual(host.record_count() != (0, 0), kept, typed)
            self.assertIn("census:", o.getvalue().splitlines()[-1])

    def test_G6_11_the_tools_own_copy_alone_runs_uninstall_all_to_the_end(self):
        # Rule 8 (2026-11-23): a person may leave by Claude Code's own plugin uninstall, which leaves our runner
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
            out = Out()
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
        # A job still running at the deadline: waiting, exit 4, the next command named, nothing taken away.
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
        # R167 (answer 6): then the tool's copy, the one the person runs uninstall from, the very last removal
        # R130 (2026-11-25): userdel without -r, then the home's rmdir once it is empty; the copy's folder by rmdir last
        self.assertEqual(o[-3:], ["userdel", "rmdir /home/minions-runner", f"rmdir {M.HOOKS_ROOT}"])
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
        # A slice still loaded from its file after its removal is a stop, never "gone".
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
        # R167 (rule 30): read running after its stop, so its file is not deleted
        self.assertIn(f"{M.SLICE} was told to stop and reads {M.SLICE}: running (active): its file was not deleted", lines[-1])
        self.assertIn(SLICE_FILE, world.paths)

    def test_G6_27_every_unit_is_read_stopped_before_anything_is_removed(self):
        # R167 (family rule 30, Linux, 2026-11-24): a skill's uninstall said "off" and ended 0 while systemd
        # still held its service; ours read a systemctl that answered nothing (no manager reached, a timeout) as
        # "gone". Three answers (rule 6): a unit is read gone, stopped or running, or it could not be read; and
        # every unit of ours is stopped and READ stopped before anything is removed; not read stopped, nothing is.
        W_SVC, W_TMR = f"{M.WATCH_UNIT}.service", f"{M.WATCH_UNIT}.timer"
        class H:
            def __init__(self, a): self.a = a
            def run(self, argv, timeout=20): return self.a
        for ans, want in (((1, "Failed to connect to bus: No such file or directory\n"), "unread"),
                          ((1, "System has not been booted with systemd as init system (PID 1). Can't operate.\n"), "unread"),
                          ((124, ""), "unread"), ((0, ""), "unread"), ((0, "LoadState=loaded\n"), "unread"),
                          ((1, "LoadState=not-found\nActiveState=inactive\n"), "unread"),
                          ((0, "LoadState=loaded\nActiveState=active\n"), "running"),
                          ((0, "LoadState=loaded\nActiveState=deactivating\n"), "running"),
                          ((0, "LoadState=not-found\nActiveState=inactive\n"), "gone"),
                          ((0, "LoadState=loaded\nActiveState=inactive\nFragmentPath=/etc/systemd/system/x.service\n"), "stopped"),
                          ((0, "LoadState=not-found\nActiveState=failed\n"), "stopped"),
                          # change 3 (2026-11-24): stopped only from the two words seen; another word is unread
                          ((0, "LoadState=loaded\nActiveState=maintenance\n"), "unread"),
                          ((0, "LoadState=loaded\nActiveState=\u00abnew\u00bb\n"), "unread")):
            self.assertEqual(M.unit_read(H(ans), W_SVC)[0], want, ans)
        self.assertIn("ActiveState=maintenance", M.unit_read(H((0, "LoadState=loaded\nActiveState=maintenance\n")),
                                                             W_SVC)[1])
        def deleted_none(world):
            self.assertIn(FOLDER, world.paths)
            self.assertTrue(set(WATCH_FILES) <= world.paths, world.paths)
            self.assertIn(SLICE_FILE, world.paths)
        bus = (1, "Failed to connect to bus: No such file or directory\n")
        # The watch's service cannot be read (uninstall --all): nothing is removed, the runner's folder included.
        for unit in (W_SVC, W_TMR):
            gh, world, host = remove_setup()
            host._answer = (lambda real, u: lambda a, i=None: bus if list(a[:3]) == ["systemctl", "show", u]
                            else real(a, i))(world.answer, unit)
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertIn(f"{unit}: could not be read", lines[-1])
            self.assertIn("nothing was removed", lines[-1])
            self.assertIn(f"`python3 -I {M.tool_path()} uninstall --all`", lines[-1])
            deleted_none(world)
            self.assertFalse(any(m == "DELETE" for m, p in gh.calls), gh.calls)
        # The watch's service still runs after it was told to stop: the same, with what was read of each.
        gh, world, host = remove_setup()
        world.active[W_SVC] = "active"
        host._answer = (lambda real: lambda a, i=None: (0, "LoadState=loaded\nActiveState=active\n")
                        if list(a[:3]) == ["systemctl", "show", W_SVC] else real(a, i))(world.answer)
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn(f"{W_SVC}: running (active)", lines[-1])
        self.assertIn(f"{W_TMR}: stopped", lines[-1])
        self.assertIn("nothing was removed", lines[-1])
        deleted_none(world)
        # The runner's own service cannot be read (one repository): nothing of that runner is removed.
        gh, world, host = remove_setup()
        host._answer = (lambda real: lambda a, i=None: bus if list(a[:3]) == ["systemctl", "show", UNIT]
                        else real(a, i))(world.answer)
        code, lines = run_uninstall(gh, host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn(f"{UNIT}: could not be read", lines[-1])
        self.assertIn("nothing of it was removed", lines[-1])
        self.assertIn(f"`python3 -I {M.tool_path()} uninstall {REPO}`", lines[-1])
        self.assertIn(FOLDER, world.paths)
        self.assertIn(f"/etc/systemd/system/{UNIT}", world.paths)
        self.assertFalse(any(m == "DELETE" for m, p in gh.calls), gh.calls)
        # And a unit removed but not read gone afterwards is "could not be read", never "still there".
        gh, world, host = remove_setup()
        seen = {"n": 0}
        def slice_unread_after(real):
            def ans(a, i=None):
                if list(a[:3]) == ["systemctl", "show", M.SLICE] and f"rm {SLICE_FILE}" in world.order:
                    return bus
                return real(a, i)
            return ans
        host._answer = slice_unread_after(world.answer)
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn(f"whether {M.SLICE} is gone could not be read", lines[-1])
        self.assertNotIn("still loaded or active", lines[-1])

    def test_G6_27_a_pgrep_that_cannot_answer_is_never_no_process(self):
        # R167 (rule 6, each read failed in turn): pgrep's exit 0 is the list, 1 is none, anything else could not be
        # read (a user that does not exist answers 2 with one line, seen; a timeout is the host's 127). No stop,
        # start, removal or "no job runs" takes that third answer for none.
        PG = ("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner.")
        # (Here the runner user reads there: an exit 2 for a user read absent is none, test_G6_27_an_absent_user...)
        THERE = {("getent", "passwd", "minions-runner"): (0, "minions-runner:x:998:998::/home/minions-runner:/bin/bash\n")}
        for bad in ((2, "pgrep: invalid user name: minions-runner\n"), (127, "Command timed out after 20 seconds"),
                    (3, "")):
            host = setup_host(runs={PG: bad, ("systemctl", "is-active", UNIT): (3, "inactive\n"), **THERE})[0]
            rep = M.Report(Out())
            self.assertFalse(M.stop_runner(host, UNIT, FOLDER, rep, drained=True), bad)
            self.assertIn("could not be read", folded(rep.lines)[-1])
            self.assertIn(f"pgrep answered code {bad[0]}", folded(rep.lines)[-1])
            self.assertNotIn("is stopped", folded(rep.lines)[-1])
            rep = M.Report(Out())
            self.assertFalse(M.start_runner(host, UNIT, FOLDER, rep), bad)
            self.assertIn("could not be read", folded(rep.lines)[-1])
            self.assertFalse(any(a[:4] == ("sudo", "-n", "systemctl", "start") for a in host.ran))
            # The runner user's processes, before the user is deleted.
            host = setup_host(runs={("pgrep", "-u", "minions-runner"): bad, **THERE})[0]
            rep = M.Report(Out())
            self.assertFalse(M.end_runner_users_processes(host, rep), bad)
            self.assertIn("could not be read", folded(rep.lines)[-1])
            self.assertIn("the user is not deleted", folded(rep.lines)[-1])
        # The drain inside uninstall: a job's process not read is never "no job runs": held, nothing stopped.
        bad = (2, "pgrep: invalid user name: minions-runner\n")
        gh, world, host = remove_setup()
        host._answer = (lambda real: lambda a, i=None: bad if list(a[:2]) == ["pgrep", "-u"] and
                        a[-1].endswith("Runner.Worker") else real(a, i))(world.answer)
        code, lines = run_uninstall(gh, host)
        self.assertEqual(code, M.EXIT_WAIT, lines)
        self.assertIn("whether a job process runs here could not be read", lines[-1])
        self.assertNotIn(f"stop {UNIT}", world.order)
        self.assertIn(FOLDER, world.paths)
        self.assertFalse(any(m == "DELETE" for m, p in gh.calls), gh.calls)

    def test_G6_27_an_absent_user_has_no_process_and_the_uninstall_finishes(self):
        # R167 (2026-11-24): pgrep answers exit 2 for a user that does not exist (seen). With a
        # runner in the record and the runner user absent (a setup cut before the user was made; a user removed by
        # hand), "could not be read" could never finish. Where the user reads absent, no process of it runs: none.
        # A user that could not be read stays could not be read.
        for everything in (False, True):
            gh, world, host = remove_setup()
            world.user, world.listener = False, False
            world.gone(f"/home/{M.RUNNER_USER}")  # its home and the runner's folder went with the user
            code, lines = run_uninstall(gh, host, repo=None if everything else REPO, everything=everything)
            self.assertNotIn(code, (M.EXIT_STOP, M.EXIT_WAIT), lines)
            self.assertFalse([x for x in lines if "could not be read" in x], lines)
            self.assertFalse([x for x in lines if "run again" in x and "records" in x], lines)  # no step that cannot be
            self.assertNotIn(f"/etc/systemd/system/{UNIT}", world.paths, lines)
            if everything:
                self.assertIn("ok census: the runner user: none", lines)
        # The runner user not read: pgrep's exit 2 stays could not be read.
        host = setup_host(runs={("pgrep", "-u", "minions-runner"): (2, "pgrep: invalid user name: minions-runner\n"),
                                ("getent", "passwd", "minions-runner"): (3, "")})[0]
        self.assertIsNone(M.pids_list_or_none(host))
        host.runs[("getent", "passwd", "minions-runner")] = (2, "")
        self.assertEqual(M.pids_list_or_none(host), [])
        self.assertFalse([a for a in host.ran if a[:1] == ("pgrep",)], "R172: no pgrep for a user read absent")

    def test_G6_30_a_user_removed_outside_the_tool_leaves_its_home_named_and_the_leave_finishes(self):
        # R167, R173 (2026-11-24; seen on a stock Ubuntu 24.04): the runner user removed by
        # hand, its home kept. Every read in that home was made AS that user, who was gone, so it was "could not be
        # read", counted as there, and uninstall stopped at the folder with "Run the same command again" (2 of 2).
        # Now: nothing is tried as that user, nothing inside its home is deleted, the leave goes to its end (GitHub's
        # side, the units, the hooks, the state, the copy), and the home is named as what stayed, with why and the
        # one whole command; the end is "finished, something stayed" (exit 0), never STOP or WAIT, in every run.
        home = f"/home/{M.RUNNER_USER}"
        for everything in (True, False):
            gh, world, host = remove_setup(moved=False)  # no put-back pull request to merge
            world.user, world.listener = False, False  # its home and the runner's folder are kept
            for n in (1, 2):
                code, lines = run_uninstall(gh, host, repo=None if everything else REPO, everything=everything)
                self.assertEqual(code, M.EXIT_OK, (n, lines))
                self.assertFalse([x for x in lines if x.startswith(("stop", "waiting"))], (n, lines))
                self.assertFalse([x for x in lines if "run the same command again" in x.lower()
                                  or "run it again" in x.lower()], (n, lines))
                self.assertFalse([a for a in host.ran if a[2:5] == ("-u", M.RUNNER_USER, "--")],
                                 "nothing is tried as a user that is gone")
                self.assertIn(home, world.paths)
                self.assertIn(FOLDER, world.paths, "nothing inside its home is deleted")
                named = [x for x in lines if f"{home} stayed" in x or f"the folder {home}: stayed" in x]
                self.assertTrue(named, lines)
                for x in named:
                    self.assertIn("`sudo rm -rf --one-file-system /home/minions-runner`", x)
                    # change 2: what was read, then how, from the record. This world's says the tool made the user.
                    # R222 (a changed promise of R173's and R180's): uninstall --all deletes the tool's state folder,
                    # and its first run's section of the person's list says so (made that user, no longer here), so
                    # its second run says the same, with the step of that form.
                    read = ("the user minions-runner is not on this computer, the folder is, and this tool deletes in "
                            "a runner's home only as that user; ")
                    self.assertIn(read + "this tool made that user and it is no longer here", x)
                    self.assertIn("Your next step: delete that folder first: `sudo rm", x)
                    self.assertNotIn("If you know", x)
                    self.assertNotIn("outside this tool", x)
                if everything:
                    self.assertTrue([x for x in lines if x.startswith(f"ok census: the folder {home}: stayed")], lines)
                    self.assertEqual(lines[-1], R136_LAST)
                    self.assertNotIn(M.HOOKS_ROOT, world.paths)
            self.assertIsNone(gh.runner)  # GitHub's side went
            self.assertNotIn(f"/etc/systemd/system/{UNIT}", world.paths)
            self.assertNotIn(M.hook_dir(REPO), world.paths)
        # A disk mounted inside that home is named first: the command skips it (seen in the rehearsal, exit 1).
        gh, world, host = remove_setup(moved=False)  # no put-back pull request to merge
        world.user, world.listener = False, False
        host.files["/proc/self/mountinfo"] = (f"30 1 0:5 / / rw - ext4 /dev/vda1 rw\n"
                                              f"99 30 0:60 / {home}/mnt rw - tmpfs tmpfs rw\n")
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        x = next(x for x in lines if f"{home} stayed" in x)
        self.assertIn(f"a disk is mounted inside it ({home}/mnt)", x)
        self.assertLess(x.index("unmount it first"), x.index("`sudo rm -rf"))

    def test_G6_19_a_foreign_thing_in_a_public_place_does_not_stop_a_leave(self):
        # R136 (the owner's ruling 2026-11-25, item 2): /tmp, /var/tmp and /dev/shm are a place a job can write. A
        # foreign thing a job's own folder there holds, or a file of the runner user's in a folder of someone else's,
        # never stops the leave: it stays, what holds it is handed to root by its exact name (never a link, never
        # followed), and it is named in the census with why; the leave ends with success and R136's last line. Root
        # deletes nothing there. Each world alone, with the bytes of everything not the runner user's summed before
        # and after. Seen red first at 01fe753 (the stop "the user minions-runner is not deleted: its files ...").
        R, J, P_ = "minions-runner", "/tmp/job-1", "/tmp/person-dir"
        worlds = {  # name: (paths, {path: owner}, plain files with their bytes, dirs, links, what must stay named)
            "a root file": ([f"{J}/root.txt"], {f"{J}/root.txt": "root"}, {f"{J}/root.txt": "r1"}, [], [], [J]),
            "a root folder with a file": ([f"{J}/rd", f"{J}/rd/f"], {f"{J}/rd": "root", f"{J}/rd/f": "root"},
                                          {f"{J}/rd/f": "r2"}, [f"{J}/rd"], [], [J]),
            "a root empty folder": ([f"{J}/empty"], {f"{J}/empty": "root"}, {}, [f"{J}/empty"], [], [J]),
            "a file of the person's": ([f"{J}/p.txt"], {f"{J}/p.txt": "person"}, {f"{J}/p.txt": "p4"}, [], [], [J]),
            "a runner folder holding a foreign file": ([f"{J}/sub", f"{J}/sub/f"], {f"{J}/sub": R, f"{J}/sub/f": "person"},
                                                       {f"{J}/sub/f": "p5"}, [f"{J}/sub"], [], [J, f"{J}/sub"]),
            "a runner link to a folder outside": ([f"{J}/ln", "/srv/outside", "/srv/outside/data"],
                                                  {f"{J}/ln": R, "/srv/outside": "person", "/srv/outside/data": "person"},
                                                  {"/srv/outside/data": "o6"}, ["/srv/outside"], [f"{J}/ln"], []),
            "the control": ([f"{J}/mine.txt"], {f"{J}/mine.txt": R}, {}, [], [], []),
            "a runner file in a folder of someone else's": ([P_, f"{P_}/r.txt"], {P_: "person", f"{P_}/r.txt": R},
                                                            {}, [P_], [], [f"{P_}/r.txt"]),
            # the owner's change 2: root's walk from /tmp itself hands what the runner user could not even list (a
            # folder of someone else's it may not enter), and a link of that number itself, never where it points
            "a runner file it could not list, in someone else's closed folder": (
                [P_, f"{P_}/r.txt"], {P_: "person", f"{P_}/r.txt": R}, {}, [P_], [], []),
            "a runner link in someone else's folder, to a runner file outside": (
                [P_, f"{P_}/ln", "/srv/outside", "/srv/outside/r.txt"],
                {P_: "person", f"{P_}/ln": R, "/srv/outside": "person", "/srv/outside/r.txt": R},
                {}, [P_, "/srv/outside"], [f"{P_}/ln"], [f"{P_}/ln"]),
        }
        unlisted_worlds = {"a runner file it could not list, in someone else's closed folder": [f"{P_}/r.txt"]}
        for name, (paths, owner, plain, dirs, links, named) in worlds.items():
            gh, world, host = remove_setup(moved=False)
            theirs = "someone else's" in name
            world.paths |= {J, *paths} if not theirs else set(paths)
            world.owner.update({J: R} if not theirs else {}, **owner)
            if "closed" in name:
                world.closed.add(P_)
            world.dirs |= {J, *dirs}
            world.plain |= set(plain)
            world.links |= set(links)
            world.files.update(plain)
            def summed():
                return hashlib.sha256("".join(f"{k}={world.files.get(k)}" for k in sorted(plain)).encode()).hexdigest()
            before = summed()
            foreign = sorted(p_ for p_ in world.paths if world.owner_of(p_) != R and p_.startswith(("/tmp/", "/srv/")))
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_OK, (name, lines))
            self.assertFalse([x for x in lines if x.startswith("stop ")], (name, lines))
            self.assertEqual(summed(), before, name)
            self.assertTrue(set(foreign) <= world.paths, (name, sorted(set(foreign) - world.paths)))
            self.assertIn("userdel", world.order, name)
            left = [p_ for p_ in world.paths if p_.startswith(("/tmp/", "/var/tmp/", "/dev/shm/"))
                    and world.owner_of(p_) == R]
            self.assertEqual(left, [], (name, "still owned by the runner user's number in a public place"))
            if "/srv/outside/r.txt" in owner:  # the link itself is handed; where it points is not followed
                self.assertEqual(world.owner_of("/srv/outside/r.txt"), R, name)
            hands = [a for a in host.ran if a[2:3] == ("find",) and "chown" in a and "/tmp" in a]
            self.assertEqual([a[3:6] for a in hands], [("/tmp", "/var/tmp", "/dev/shm")], (name, hands))  # one walk,
            # started at root's own tops: no start path of root's passes through a folder of another user's
            census = [x for x in lines if x.startswith("ok census: in /tmp, /var/tmp, /dev/shm: ")]
            if name in unlisted_worlds:
                n = len(unlisted_worlds[name])
                self.assertEqual(n, 1, name)
                self.assertEqual(census, ["ok census: in /tmp, /var/tmp, /dev/shm: 1 thing of the runner user's number "
                                          "handed to root, as that user is deleted, it was not in that user's own "
                                          "listing (it could not read where it sits)"], (name, lines))
                self.assertEqual(world.owner_of(unlisted_worlds[name][0]), "root", name)
            elif named:
                self.assertEqual(len(census), 1, (name, lines))
                for p_ in named:
                    self.assertIn(p_ + " (", census[0], name)
                    self.assertEqual(world.owner_of(p_), "root", (name, p_))
                # the owner's fault 3: the whole sentence, with its count (the stand-in's root find prints what it hands)
                self.assertIn(f"; that stays; {M.plural(len(named), 'thing')} of the runner user's number handed to "
                              "root, as that user is deleted", census[0], name)
                self.assertTrue(census[0].endswith("as that user is deleted"), (name, census[0]))
                self.assertTrue(lines[-1].startswith("ok this tool is off this computer; what stayed"), (name, lines[-1]))
            else:
                self.assertEqual(census, [], (name, lines))  # nothing stayed there and nothing was handed
                self.assertFalse([p_ for p_ in world.paths if p_ == J or p_.startswith(J + "/")], name)
            self.assertFalse([a for a in host.ran if a[:3] == ("sudo", "-n", "rm") and str(a[-1]).startswith("/tmp")],
                             name)  # root deletes nothing there
        # The owner's fault 3 and change 2: another user's folder above a thing of the runner user's is put back as a
        # link (to a folder that holds a same-named thing of its own) between the listing and root's hand. Root's walk
        # starts at /tmp itself and follows no link: nothing where the link points changes owner, none is handed, and
        # the census says so in a whole sentence.
        gh, world, host = remove_setup(moved=False)
        world.paths |= {P_, f"{P_}/r.txt", "/srv/elsewhere", "/srv/elsewhere/r.txt"}
        world.owner.update({P_: "person", f"{P_}/r.txt": R, "/srv/elsewhere": "person", "/srv/elsewhere/r.txt": "person"})
        world.dirs |= {P_, "/srv/elsewhere"}
        real = host._answer
        def swapped(argv, input=None):
            if list(argv[:3]) == ["sudo", "-n", "find"] and "-uid" in argv and "chown" in argv and "/tmp" in argv:
                world.links.add(P_)
            return real(argv, input)
        host._answer = swapped
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        census = [x for x in lines if x.startswith("ok census: in /tmp, /var/tmp, /dev/shm: ")]
        self.assertEqual(census, [f"ok census: in /tmp, /var/tmp, /dev/shm: kept: {P_}/r.txt (the runner user's, in a "
                                  "folder of person's, which it may not write); that stays; nothing of the runner "
                                  "user's number was left there to hand to root (root's walk, read back)"], lines)
        self.assertEqual(world.owner_of("/srv/elsewhere/r.txt"), "person")
        self.assertEqual(world.owner_of(P_), "person")
        # The owner's word (2026-11-25, seen with the real find 4.9.0: 7 of 300 walks beside a program that makes and
        # deletes files ended with exit 1): another program's file that is gone before find reads it never stops the
        # leave and is never "could not be read" (-ignore_readdir_race on the runner user's listing, root's hand and
        # root's read-back); with a held thing there too, so root's walk has something to hand.
        for held in (False, True):
            gh, world, host = remove_setup(moved=False)
            world.paths |= {"/tmp/other-cache", "/tmp/other-cache/tmp.1"}
            world.owner.update({"/tmp/other-cache": "person", "/tmp/other-cache/tmp.1": "person"})
            world.dirs.add("/tmp/other-cache")
            world.vanish.add("/tmp/other-cache/tmp.1")
            if held:
                world.paths |= {J, f"{J}/root.txt"}
                world.owner.update({J: R, f"{J}/root.txt": "root"})
                world.dirs.add(J)
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_OK, (held, lines))
            self.assertFalse([x for x in lines if x.startswith("stop ") or "could not be read" in x], (held, lines))
            self.assertFalse(world.user, held)
            races = [a for a in host.ran if a[2:3] == ("find",) and "/tmp" in a and ("-uid" in a or "-print0" in a)]
            self.assertTrue(races and all("-ignore_readdir_race" in a for a in races), races)
        # The owner's decision (2026-11-25; seen on vm/run64: WITH the option a FOLDER another program deleted still
        # gave find's "not there" line and code 1, 50 of 300 walks, every list exact): at root's read-back, code 1 with
        # only such lines below a walked top is taken as read and said with its count; in the tool's C.UTF-8 (curly
        # quotes) and in plain C (straight), with sudo's own warning beside it (R167: not part of the answer).
        WARN_LINE = "sudo: unable to resolve host box-1: Name or service not known\n"  # R167
        for quotes, warning in ((("‘", "’"), ""), (("'", "'"), ""), (("‘", "’"), WARN_LINE)):
            gh, world, host = remove_setup(moved=False)
            world.quotes, host.sudo_warning = quotes, warning
            world.paths |= {"/tmp/other-cache", "/tmp/other-cache/tmp.7", "/tmp/other-cache/tmp.7/f"}
            world.owner.update({p_: "person" for p_ in world.paths if p_.startswith("/tmp/other-cache")})
            world.dirs |= {"/tmp/other-cache", "/tmp/other-cache/tmp.7"}
            world.vanish.add("/tmp/other-cache/tmp.7")
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_OK, (quotes, warning, lines))
            self.assertFalse([x for x in lines if x.startswith("stop ") or "could not be read" in x], lines)
            self.assertFalse(world.user, quotes)
            self.assertIn("ok census: in /tmp, /var/tmp, /dev/shm: 1 thing of other programs went away while root's walk "
                          "ran (find said it was no longer there); the walk's list is taken as read", lines)
        # Anything else stops as before, with find's words and the one next step: a place root may not enter (a FUSE
        # mount of another user's; not seen on a real machine, named), beside a folder gone too, or with the words
        # "No such file or directory" in its own name; and find's line in another language (named, not built).
        for closed, words in (("/tmp/fuse-mount", None), ("/tmp/No such file or directory", None),
                              (None, "Datei oder Verzeichnis nicht gefunden")):
            gh, world, host = remove_setup(moved=False)
            world.paths |= {"/tmp/other-cache", "/tmp/other-cache/tmp.7"}
            world.dirs |= {"/tmp/other-cache", "/tmp/other-cache/tmp.7"}
            world.vanish.add("/tmp/other-cache/tmp.7")
            if closed:
                world.root_closed.add(closed)
            if words:
                world.gone_words = words
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_STOP, (closed, words, lines))
            stop = [x for x in lines if x.startswith("stop ")]
            named = f"‘{closed}’: Permission denied" if closed else f"‘/tmp/other-cache/tmp.7’: {words}"
            self.assertTrue(stop and "the user minions-runner is not deleted" in stop[0] and named in stop[0]
                            and "run the same command again" in stop[0], (named, lines))
            self.assertTrue(world.user)
            self.assertNotIn("userdel", world.order)
        # The rule's own reading, line by line: anchored at both ends, below a walked top, find's own lines only.
        tops, gone = ["/tmp", "/var/tmp", "/dev/shm"], "find: ‘/tmp/a/b’: No such file or directory\n"
        for err, n in (("", 0), (WARN_LINE, 0), (gone, 1), (gone * 2, 2), (WARN_LINE + gone, 1),
                       ("find: '/var/tmp/x\\n y': No such file or directory\n", 1),
                       ("/usr/bin/find: ‘/tmp/a’: No such file or directory\n", 0),
                       ("find: ‘/tmp’: No such file or directory\n", 0),
                       ("find: ‘/tmpx/a’: No such file or directory\n", 0),
                       ("find: ‘/home/user/a’: No such file or directory\n", 0),
                       ("find: ‘/tmp/a'", 0), ("find: ‘/tmp/a': No such file or directory\n", 0),
                       (gone + "find: ‘/tmp/c’: Permission denied\n", 0),
                       ("find: ‘/tmp/a’: No such file or directory’: Permission denied\n", 0),
                       ("find: ‘/tmp/a’: No such file or directory.\n", 0),
                       ("x find: ‘/tmp/a’: No such file or directory\n", 0)):
            self.assertEqual(M.gone_only(err, tops), n, err)

    def test_G6_19_a_name_read_from_the_state_or_journal_is_checked_before_a_delete(self):
        # R136 (the owner's ruling 2026-11-25, item 4): a runner folder's name read from the state or from a journal
        # line is deleted in only after it is checked: absolute, no ".." part, directly under the runners' own folder,
        # the shape this tool gives a runner's folder, and not a link. A line that fails is named and not deleted. A
        # state or journal that could not be read is the third answer: nothing is deleted on its word, and the stop
        # names the file. Seen red first (the fix branch before this check deleted in the planted folders).
        # A journal line that points outside (two folders deep, so its last part is a name the person keeps in seen):
        # the name is not taken, the person's file stays, and the line is named.
        bad = [f"{M.RUNNERS_HOME}sub/notes.txt", "/srv/data", f"{M.RUNNERS_HOME}x/../../../etc", f"{M.RUNNERS_HOME}no shape!"]
        gh, world, host = remove_setup(moved=False)
        jl = os.path.join(host.state_dir(), "journal.log")
        host.files[jl] = "".join(f"2026-11-25T10:00:0{n}Z unpack {f}\n" for n, f in enumerate(bad))
        mine = f"{M.SEEN_ROOT}/notes.txt"
        world.paths |= {M.SEEN_ROOT, mine}
        world.dirs.add(M.SEEN_ROOT)
        world.plain.add(mine)
        world.owner[mine] = "person"
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertIn(mine, world.paths, lines)  # never deleted on an unchecked name
        self.assertFalse([a for a in host.ran if mine in a], "a command named the person's file")
        for f in bad[:1] + bad[2:]:  # the line that points outside the runners' folder is not one of ours at all
            self.assertTrue([x for x in lines if f in x and "not deleted" in x], (f, lines))
        # A link where a runner's folder should be: not followed, not deleted, named.
        gh, world, host = remove_setup(moved=False)
        world.paths |= {"/srv/target", "/srv/target/data"}
        world.links.add(FOLDER)
        world.owner.update({"/srv/target": "person", "/srv/target/data": "person"})
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertIn("/srv/target/data", world.paths)
        self.assertTrue([x for x in lines if FOLDER in x and "a link" in x], lines)
        # the owner's item 6: the line says what was read, never "the runner's folders ... are deleted"
        self.assertFalse([x for x in lines if "the runner's folders and its hooks are deleted" in x], lines)
        self.assertTrue([x for x in lines if x.startswith(f"ok {FOLDER} was read as a link, not a folder: not followed, "
                                                          "and nothing where it points is deleted; its hooks are deleted, "
                                                          "read back")], lines)
        # The owner's change 3 (2026-11-25): left-behind.txt is a source of names. A line not of the shape of a folder
        # this tool empties is not read (/etc, a path with ".."); a shaped name that is not there changes nothing; by a
        # shaped name that is there root removes only the two fixed hook files and the EMPTY folder of that name, in
        # this tool's root-owned folders; nothing else by that name (seen/<name> stays). What root ran, exactly.
        gh, world, host = remove_setup(moved=False)
        H, old = M.HOOKS_ROOT, f"{M.HOOKS_ROOT}/old-repo"
        host.files[M.left_behind_path(host)] = (
            "# 2026-11-25T10:00:00Z uninstall: what stayed, not made by this tool or its runner user\n"
            "/etc: it holds shadow (a file of root)\n"
            f"{H}/a-b/../../../etc: it holds x (a file of root)\n"
            f"{H}/ghost-repo: it holds x (a file of root)\n"
            f"{old}: it holds x (a file of root)\n")
        seen_ = f"{M.SEEN_ROOT}/old-repo"
        world.paths |= {old, f"{old}/job-started.sh", f"{old}/job-completed.sh", M.SEEN_ROOT, seen_}
        world.dirs |= {old, M.SEEN_ROOT}
        world.plain |= {f"{old}/job-started.sh", f"{old}/job-completed.sh", seen_}
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        changed = [a for a in host.ran if a[:2] == ("sudo", "-n") and a[2] not in ("sh", "true", "systemctl")
                   and any("old-repo" in str(x) or "ghost-repo" in str(x) or ".." in str(x) or str(x).endswith("/etc")
                           for x in a)]
        self.assertEqual(changed, [("sudo", "-n", "rm", "-f", f"{old}/job-completed.sh"),
                                   ("sudo", "-n", "rm", "-f", f"{old}/job-started.sh"),
                                   ("sudo", "-n", "rmdir", old)], changed)
        self.assertIn(seen_, world.paths)
        # A state, a journal, or left-behind.txt that could not be read: a stop that names the file, nothing changed,
        # exit 2; nothing deleted on their word.
        for which in ("state", "journal", "left-behind.txt"):
            gh, world, host = remove_setup(moved=False)
            before = set(world.paths)
            if which == "state":
                with open(M.state_path(host), "w", encoding="utf-8") as f:
                    f.write("{ not json")
                named = M.state_path(host)
            else:
                named = (os.path.join(host.state_dir(), "journal.log") if which == "journal" else
                         M.left_behind_path(host))
                host.unreadable = {named}
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_STOP, (which, lines))
            stop = [x for x in lines if x.startswith("stop ")]
            self.assertTrue(stop and named in stop[0] and "nothing is deleted on its word" in stop[0], (which, lines))
            self.assertEqual(world.paths, before, which)
            self.assertFalse([a for a in host.ran if a[:2] == ("sudo", "-n") and a[2:3] != ("true",)], which)

    def leave_world(self, rules, top, kind, name, plant, named):
        """One world of the walk (test_G6_19_every_folder_the_leave_empties_keeps_what_is_not_ours), alone."""
        R = "minions-runner"
        gh, world, host = remove_setup(moved=False)
        world.paths |= {"/srv/outside", "/srv/outside/data"}
        world.owner.update({"/srv/outside": "person", "/srv/outside/data": "person"})
        world.files["/srv/outside/data"] = "o6"
        if top not in world.paths:
            world.paths.add(top)
            world.dirs.add(top)
        for rel, (who, y, text) in plant.items():
            p_ = f"{top}/{rel}"
            world.paths.add(p_)
            world.owner[p_] = who
            if y == "d":
                world.dirs.add(p_)
            elif y == "l":
                world.links.add(p_)
            else:
                world.plain.add(p_)
                if text:
                    world.files[p_] = text
        theirs = sorted([f"{top}/{r}" for r, (who, _, _) in plant.items() if who != R and not (
            kind == "tool" and False)] + ["/srv/outside", "/srv/outside/data"])
        def summed():
            return hashlib.sha256("".join(f"{k}={world.files.get(k)}" for k in theirs).encode()).hexdigest()
        before = summed()
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        what = (top, name)
        self.assertEqual(code, M.EXIT_OK, (what, lines))
        self.assertFalse([x for x in lines if x.startswith("stop ")], (what, lines))
        self.assertEqual(summed(), before, what)
        self.assertEqual([p_ for p_ in theirs if p_ not in world.paths], [], what)
        census = [x for x in lines if x.startswith("ok census: the folder ")]
        if named:
            for f in [t for t, _, _ in rules if t == top or top.startswith(t + "/")]:
                said = [x for x in census if x.startswith(f"ok census: the folder {f}: kept: ")]
                self.assertEqual(len(said), 1, (what, f, census))
                self.assertNotIn("`", said[0], what)  # no command
            self.assertEqual(lines[-1], R136_LAST, what)
        else:
            self.assertFalse([x for x in census if f"the folder {top}:" in x], (what, census))
            self.assertNotIn(top, world.paths, what)
            self.assertFalse([p_ for p_ in world.paths if p_.startswith(top + "/")], what)

    def test_G6_19_every_folder_the_leave_empties_keeps_what_is_not_ours(self):
        # R136 (the owner's word 2026-11-25): the test walks EVERY folder the uninstall deletes or empties, from the
        # code's own list (leave_folders). In each, alone: (A) a folder only this tool writes: a foreign plain file, a
        # foreign folder holding a file, the control; (B) a folder a job can write: a root file, a root folder with a
        # file, a root EMPTY folder (it stays), a file of the person's, a runner-owned folder holding a foreign file, a
        # runner-owned link to a folder outside (the link goes; where it points is summed), the control. Each: exit 0,
        # no stop, the bytes of what is not ours the same before and after, every such thing still there, the folder
        # and each folder of the list above it that stayed named in the census with why and no command, R136's last
        # line; in the control the folder is gone.
        R = "minions-runner"
        gh, world, host = remove_setup(moved=False)
        rules = M.leave_folders(host, M.Report(Out()))
        self.assertEqual(sorted(k for _, k, _ in rules).count("runner"), 3, rules)  # its folder, records, the home
        self.assertEqual(len(rules), 8, rules)
        tool, runner = LEAVE_TOOL_WORLDS, LEAVE_RUNNER_WORLDS
        n = 0
        for top, kind, _ in rules:
            for name, (plant, named) in (tool if kind == "tool" else runner).items():
                n += 1
                self.leave_world(rules, top, kind, name, plant, named)
        self.assertEqual(n, 5 * 3 + 3 * 7)  # every folder, every world

    def test_G6_30_a_home_left_by_this_tools_own_userdel_is_said_from_the_record(self):
        # Change 2 (2026-11-24), as R136 reworded it (2026-11-25): the runner user's home holds a file of root's. The
        # runner user deletes only what it made there; userdel runs WITHOUT -r; the home stays, named with what it holds
        # and why, and no command (the owner's condition 2: "not made by the runner user", never "the person's"). The
        # next runs say, from the record (runner_home_left), how it came to stay, again with no command, and exit 0.
        home = f"/home/{M.RUNNER_USER}"
        gh, world, host = remove_setup(moved=False)
        world.paths |= {f"{home}/left.txt", f"{home}/.cache", f"{home}/.cache/x"}
        world.plain.add(f"{home}/left.txt")
        world.owner[f"{home}/left.txt"] = "root"
        for n, (repo, everything) in enumerate(((REPO, False), (REPO, False), (None, True))):
            code, lines = run_uninstall(gh, host, repo=repo, everything=everything)
            self.assertEqual(code, M.EXIT_OK, (n, lines))
            self.assertFalse([x for x in lines if x.startswith("stop ")], (n, lines))
            census = [x for x in lines if x.startswith(f"ok census: the folder {home}: kept: ")]
            self.assertEqual(len(census), 1, (n, lines))
            self.assertIn("it holds left.txt (a file of root), which the runner user did not make; that stays", census[0])
            if n == 0:
                self.assertIn("userdel", world.order)
                self.assertNotIn(f"{home}/.cache/x", world.paths)  # what the runner user made is deleted
            else:  # R136 (the owner's fault 2): said again as the first run said it, from the person's record
                self.assertTrue(census[0].endswith(f"found it: this tool's list in {host.record_dir()}/left-behind.txt)"),
                                census[0])
                if everything:
                    self.assertIn(f"ok the folder {home} stays: the user minions-runner is not on this computer, the "
                                  "folder is, and this tool deletes in a runner's home only as that user; this tool made "
                                  "that user and removed it; the folder holds what the runner user did not make (the whole "
                                  f"list is in {host.record_dir()}/left-behind.txt), so it stays", lines)
            self.assertIn(f"{home}/left.txt", world.paths, n)  # never deleted, by any run
            self.assertFalse([x for x in lines if "rm -rf" in x or "Your next step" in x], (n, lines))
            self.assertFalse([x for x in lines if "outside this tool" in x or "It is yours" in x], (n, lines))
            self.assertTrue(M.load_state(host).get("runner_home_left") or everything, n)
            self.assertNotIn("runner_user_made", M.load_state(host))
        self.assertNotIn("userdel -r", " ".join(world.order))
        # The third answer, reached by a cause that is not a foreign file: the emptied home's rmdir is refused once.
        # That run stops with the present stop line; the next run's rmdir finishes it and exits 0, read back.
        gh, world, host = remove_setup(moved=False)
        real = world.answer
        def busy(argv, input=None, once=[True]):
            if list(argv) == ["sudo", "-n", "rmdir", home] and once[0]:
                once[0] = False
                return 1, f"rmdir: failed to remove '{home}': Device or resource busy"
            return real(argv, input)
        host._answer = busy
        code, lines = run_uninstall(gh, host, repo=REPO)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertTrue([x for x in lines if x.startswith(f"stop the user {M.RUNNER_USER} is deleted, read back; its "
                                                         f"home {home} held nothing left of anyone")], lines)
        self.assertTrue([x for x in lines if x.startswith("stop ") and "then run `uninstall --all`: it finishes" in x],
                        lines)
        code, lines = run_uninstall(gh, host, repo=None, everything=True)  # the step the stop names
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertNotIn(home, world.paths)
        self.assertIn(f"ok the user {M.RUNNER_USER} and its home are not on this computer, read back", lines)

    def test_G6_19_a_read_only_tree_the_runner_user_made_goes_before_its_user(self):
        # R136 (the owner's fault 1, 2026-11-25; GNU find 4.9.0 tried by the owner on a tree of the shape Go gives its
        # module cache): the runner user's own folders with no write right for it. Its delete leaves them ("cannot
        # delete ...: Permission denied"), and a read-back for what is not its own finds nothing. The runner user first
        # gives itself back the rights on the folders it owns inside the folder being cleared (as itself, one file
        # system, no link followed; root changes no mode), then deletes. In its own folder, the records folder, its
        # home and a public place, each alone, both ways (--all, one repository): all of it goes, then the user.
        R, J, home = "minions-runner", "/tmp/job-1", f"/home/{M.RUNNER_USER}"
        places = {"the runner's own folder": f"{FOLDER}/_work/go", "the records folder": f"{M.records_dir_of(FOLDER)}/go",
                  "the home": f"{home}/go", "a public place": f"{J}/go"}
        for name, top in places.items():
            for everything in (True, False):
                gh, world, host = remove_setup(moved=False)
                tree = [top, f"{top}/pkg", f"{top}/pkg/mod", f"{top}/pkg/mod/x@v1.0.0", f"{top}/pkg/mod/x@v1.0.0/go.mod"]
                above = [J] if top.startswith("/tmp/") else [f"{FOLDER}/_work"] if top.startswith(FOLDER) else []
                world.paths |= {*above, *tree}
                world.dirs |= {*above, *tree[:-1]}
                world.plain.add(tree[-1])
                world.owner.update({p_: R for p_ in above + tree})
                world.ro |= set(tree[2:4])  # dr-xr-xr-x, as in the owner's trial
                code, lines = run_uninstall(gh, host, repo=None if everything else REPO, everything=everything)
                what = (name, everything)
                self.assertEqual(code, M.EXIT_OK, (what, lines))
                self.assertFalse([x for x in lines if x.startswith("stop ")], (what, lines))
                self.assertEqual([p_ for p_ in above + tree if p_ in world.paths], [], what)
                self.assertFalse(world.user, what)
                self.assertNotIn(home, world.paths, what)
                self.assertEqual([p_ for p_ in world.paths if world.owner_of(p_) == R], [], what)
                self.assertFalse([a for a in host.ran if a[:3] == ("sudo", "-n", "chmod")], "root changes no mode")
        # The owner's change 1 (2026-11-25): the folder being cleared is itself read-only (dr-xr-xr-x, a job's chmod):
        # the runner user gives itself the right back on that folder too, as itself, and everything goes; both ways.
        for name, top in (("the runner's own folder", FOLDER), ("the records folder", M.records_dir_of(FOLDER)),
                          ("the home", home)):
            for everything in (True, False):
                gh, world, host = remove_setup(moved=False)
                inside = f"{top}/made-by-a-job.txt"
                world.paths |= {top, inside}
                world.dirs.add(top)
                world.plain.add(inside)
                world.owner.update({top: R, inside: R})
                world.ro.add(top)
                code, lines = run_uninstall(gh, host, repo=None if everything else REPO, everything=everything)
                what = (name, "a read-only top", everything)
                self.assertEqual(code, M.EXIT_OK, (what, lines))
                self.assertFalse([x for x in lines if x.startswith("stop ")], (what, lines))
                self.assertNotIn(inside, world.paths, what)
                self.assertNotIn(top, world.paths, what)
                self.assertFalse(world.user, what)
                self.assertFalse([a for a in host.ran if a[:3] == ("sudo", "-n", "chmod")], "root changes no mode")
        # A thing of the runner user's that no unlink takes, held by nothing foreign: the third answer, named; in the
        # home and a public place the stop comes BEFORE userdel, so a second run finishes as that user. Then the same
        # command again (what the stop names): exit 0, the user and the thing gone.
        for name, held in (("the runner's own folder", f"{FOLDER}/_work/held.txt"), ("the home", f"{home}/held.txt"),
                           ("the records folder", f"{M.records_dir_of(FOLDER)}/held.txt"), ("a public place", "/tmp/held.txt")):
            gh, world, host = remove_setup(moved=False)
            world.paths |= {held, *([f"{FOLDER}/_work"] if held.startswith(FOLDER) else [])}
            world.plain.add(held)
            world.owner[held] = R
            world.stuck.add(held)
            code, lines = run_uninstall(gh, host)
            self.assertEqual(code, M.EXIT_STOP, (name, lines))
            stop = [x for x in lines if x.startswith("stop ")]
            self.assertTrue(stop and os.path.basename(held) in stop[0], (name, lines))
            self.assertIn("run the same command again", stop[0], name)
            self.assertLessEqual(stop[0].count("read back after its delete"), 1, (name, stop[0]))  # the owner's (a)
            self.assertTrue(world.user, (name, "the user is not deleted while a thing of its own sits there"))
            self.assertNotIn("userdel", world.order, name)
            world.stuck.clear()  # settled
            code, lines = run_uninstall(gh, host)  # the step the stop names
            self.assertEqual(code, M.EXIT_OK, (name, lines))
            self.assertFalse(world.user, (name, lines))
            self.assertNotIn(held, world.paths, name)
            self.assertNotIn(home, world.paths, name)

    def test_G6_19_a_later_run_says_what_stayed_as_the_first_run_did(self):
        # R136 (the owner's fault 2, 2026-11-25, run on this stand-in at bb1218b): uninstall --all deletes the state, so
        # a second run said a home that holds root's file was "yours" with a root rm -rf of it, and a records folder of
        # ours was one "this tool did not write". At every later run what stayed is said as the first run said it
        # (read only from this tool's own files in the person's record: left-behind.txt and the kept journal), and no run
        # prints a delete command, or "yours", for a folder the record says holds what is not ours. Every (B) world, run
        # three times both ways (--all three times; one repository, --all, --all), with and without the journal line a
        # real setup leaves.
        R, J, home = "minions-runner", "/tmp/job-1", f"/home/{M.RUNNER_USER}"
        rec = M.records_dir_of(FOLDER)
        worlds = {"a root file in the records folder": {f"{rec}/root.txt": ("root", "f")},
                  "a root file in the runner's own folder": {f"{FOLDER}/_work": (R, "d"), f"{FOLDER}/_work/root.txt": ("root", "f")},
                  "a root file in the home": {f"{home}/left.txt": ("root", "f")},
                  "a root file in a job's folder in /tmp": {J: (R, "d"), f"{J}/root.txt": ("root", "f")}}
        orders = {"--all three times": [(None, True)] * 3, "one repository, --all, --all": [(REPO, False), (None, True), (None, True)]}
        for name, plant in worlds.items():
            for order, runs in orders.items():
                for unpack in (False, True):
                    gh, world, host = remove_setup(moved=False)
                    if unpack:
                        host.files[os.path.join(host.state_dir(), "journal.log")] = f"2026-11-25T10:00:00Z unpack {FOLDER}\n"
                    for p_, (who, y) in plant.items():
                        world.paths.add(p_)
                        world.owner[p_] = who
                        (world.dirs if y == "d" else world.plain).add(p_)
                    theirs = [p_ for p_, (who, _) in plant.items() if who != R]
                    mine_before = {p_ for p_ in world.paths if world.owner_of(p_) == R}
                    first = None
                    for n, (repo, everything) in enumerate(runs, 1):
                        what = (name, order, unpack, n)
                        code, lines = run_uninstall(gh, host, repo=repo, everything=everything)
                        self.assertEqual(code, M.EXIT_OK, (what, lines))
                        self.assertFalse([x for x in lines if x.startswith("stop ")], (what, lines))
                        self.assertTrue(set(theirs) <= world.paths, what)
                        said = [x.split("; that stays")[0] for x in lines
                                if x.startswith(("ok census: the folder ", "ok census: in /tmp"))]
                        if n == 1:
                            first = said
                            self.assertTrue(first, (what, lines))
                            handed = sorted(p_ for p_ in mine_before if p_ in world.paths and world.owner_of(p_) == "root")
                            if handed and not name.endswith("/tmp"):
                                counts = [int(m) for x in lines for m in re.findall(
                                    r"\((\d+) of the runner user's there handed to root, as that user is deleted\)", x)]
                                self.assertEqual(sum(counts), len(handed), (what, handed, lines))
                            continue
                        for x in first:  # what stayed, said again as the first run said it
                            # R222 (a changed promise of R136's): only where root tests it as there in this run. The
                            # public places, and a folder inside the gone user's home (root does not read inside it;
                            # the home's own line names what it holds), are not said again from the list
                            unread = x.startswith(("ok census: in /tmp", f"ok census: the folder {home}/"))
                            if unread:
                                self.assertNotIn(x, said, (what, lines))
                                self.assertFalse([y for y in said if y.startswith("ok census: in /tmp")], (what, lines))
                            else:
                                self.assertIn(x, said, (what, lines))
                        if name.endswith("own folder"):  # the home's own line names the file inside it
                            self.assertTrue([y for y in said if y.startswith(f"ok census: the folder {home}: kept: it "
                                                                              "holds runners/")], (what, lines))
                        for x in lines:  # no delete command and no "yours" for what the record says holds what is not ours
                            if x.startswith(("ok census: your record of what this tool did", "ok census: your record folder")):
                                continue
                            self.assertNotIn("`", x, (what, x))
                            self.assertNotIn("yours", x, (what, x))

    def test_G6_45_what_uninstall_and_setup_say_stayed_is_true_of_this_computer_at_that_run(self):
        # Row R222 (seen 2026-11-27 on a stock Ubuntu 24.04.5 virtual machine at the tag v0.1.0): a leave that left the
        # runner user's home and a job's folder in /tmp (each holding a file of root's), then root's hand took both
        # away, a fresh setup made the user again, and every later run read the list of what stayed whole: uninstall
        # said of the new user's home "this tool made that user and removed it; the folder holds what the runner user
        # did not make", and setup said so beside a step that belongs to "made it and it is no longer here"; the census
        # said the job's folder "stays", read from the list alone, while it was gone. Now the list is read by its
        # latest section (a leave that reaches its census writes one, also when nothing stayed, carrying only what
        # root tested as there in that run); a setup since ends the home's entry; nothing in the public places is
        # said again from the list. The list file is only added to; nothing more is deleted.
        R, home, J = "minions-runner", f"/home/{M.RUNNER_USER}", "/tmp/job-lf"
        removed = "this tool made that user and removed it"
        no_longer = "this tool made that user and it is no longer here"
        step = ("Your next step: delete that folder first: `sudo rm -rf --one-file-system /home/minions-runner`; then "
                "run the same command again")
        lb = lambda h: f"{h.record_dir()}/left-behind.txt"
        public = lambda lines: [x for x in lines if x.startswith("ok census: in /tmp") or J in x]

        def plant(world, things):
            for p_, (who, y) in things.items():
                world.paths.add(p_)
                world.owner[p_] = who
                (world.dirs if y == "d" else world.plain).add(p_)

        def leave_that_left_things():
            gh, world, host = remove_setup(moved=False)
            plant(world, {f"{home}/left.txt": ("root", "f"), J: (R, "d"), f"{J}/root.txt": ("root", "f")})
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_OK, lines)
            self.assertTrue([x for x in lines if x.startswith(f"ok census: the folder {home}: kept: it holds left.txt")], lines)
            self.assertTrue([x for x in lines if x.startswith("ok census: in /tmp")], lines)
            return gh, world, host

        def setup_says(record, made):
            """setup with the home there and its user not (root's hand), the list as given, the state as said."""
            host, sha = setup_host()
            fresh_look(host)
            if made:
                st = M.load_state(host)
                st["runner_user_made"] = True  # a setup of this tool made the user after the leave that wrote the list
                M.save_state(host, st)
            host.runs[("getent", "passwd", "minions-runner")] = (2, "")  # root's hand removed it: getent's own "not found"
            host.dirs[home] = True
            host.files[lb(host)] = record
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual(code, M.EXIT_STOP, lines)
            return lines[-1]

        def a_new_user_removed_by_root(record):
            """A fresh setup made the user (the state says so), then root's hand removed it; its home stays."""
            gh, world, host = remove_setup(moved=False)
            host.files[lb(host)] = record
            world.user = False
            return gh, world, host

        _, _, host1 = leave_that_left_things()
        first = host1.files[lb(host1)]
        self.assertEqual(sorted(M.stayed_on_record(host1)[0]), [home, M.PUBLIC_KEY])

        # The seen sequence, the whole leave between left out: root's hand took the home and the job's folder; a fresh
        # setup made the user; root's hand removed it, its home kept. uninstall --all, then setup.
        # As seen: uninstall of the one repository twice, then uninstall --all (which deletes the state), then setup.
        gh, world, host = a_new_user_removed_by_root(first)
        for repo, everything in ((REPO, False), (REPO, False), (None, True)):
            code, lines = run_uninstall(gh, host, repo=repo, everything=everything)
            self.assertEqual(code, M.EXIT_OK, (repo, lines))
            self.assertFalse([x for x in lines if removed in x or "holds what the runner user did not make" in x],
                             (repo, lines))
            self.assertTrue([x for x in lines if no_longer in x], (repo, lines))
            self.assertEqual(public(lines), [], "the job's folder is not said again from the list")
            self.assertTrue(host.files[lb(host)].startswith(first), "the list is only added to")
        self.assertIn(home, world.paths)
        self.assertEqual([f for f in M.stayed_on_record(host)[0] if f == home or f.startswith(home + "/")
                          or f == M.PUBLIC_KEY], [], "the leaves since end the home's entry and the public one")
        self.assertTrue(M.home_made_on_record(host), "the leave's own section says so, for a run with no state")
        after = host.files[lb(host)]
        x = setup_says(after, made=False)  # the state is gone with --all; the list's latest section says it
        self.assertIn(no_longer, x)
        self.assertNotIn(removed, x)
        self.assertIn(step, x)
        self.assertNotIn("If you know it is left", x)
        x = setup_says(first, made=True)  # the same before any leave since: a setup since ends the home's entry
        self.assertIn(no_longer, x)
        self.assertNotIn(removed, x)
        self.assertIn(step, x)

        # The same with the whole leave between, which ended off: it leaves nothing, and writes its own section, so
        # every entry before it ends (a leave that left nothing after one that did).
        gh, world, host = remove_setup(moved=False)  # a fresh setup made the user again; nothing foreign anywhere
        host.files[lb(host)] = first
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertFalse(world.user, lines)
        self.assertNotIn(home, world.paths)
        self.assertFalse([x for x in lines if x.startswith(f"ok census: the folder {home}")], lines)
        self.assertEqual(public(lines), [], lines)
        self.assertFalse([x for x in lines if "stays" in x or "what stayed" in x], lines)  # nothing stayed
        ended = host.files[lb(host)]
        self.assertTrue(ended.startswith(first) and len(ended) > len(first), "a section of its own, only added")
        self.assertEqual(M.stayed_on_record(host), ({}, ""))
        # what a leave acts by is read from every section, as before (nothing more is deleted than before)
        self.assertEqual(sorted(M.stayed_on_record(host, whole=True)[0]), [home, M.PUBLIC_KEY])
        gh, world, host = a_new_user_removed_by_root(ended)
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertFalse([x for x in lines if removed in x], lines)
        self.assertTrue([x for x in lines if no_longer in x], lines)
        self.assertEqual(public(lines), [], lines)
        for made, why in ((True, no_longer), (False, "this tool's record does not say it made a user of that name")):
            x = setup_says(ended, made=made)
            self.assertIn(why, x, made)
            self.assertNotIn(removed, x, made)

        # The public entry: the leave again, with the job's folder still there; root reads nothing in a place every
        # user writes, so it is not said again from the list (past or present); the home, which root tests as there,
        # is said again as the first run found it, and carried into each later section with that first time.
        gh, world, host = leave_that_left_things()
        stamp = M.stayed_on_record(host)[0][home][0]
        for n in (2, 3):
            before = host.files[lb(host)]
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_OK, (n, lines))
            self.assertIn(J, world.paths)
            self.assertEqual(public(lines), [], (n, lines))
            self.assertTrue([x for x in lines if x.startswith(f"ok census: the folder {home}: kept: it holds left.txt")
                             and f"as the uninstall of {stamp} found it" in x], (n, lines))
            self.assertTrue(host.files[lb(host)].startswith(before), n)
            self.assertEqual(M.stayed_on_record(host)[0], {home: (stamp, ["left.txt (a file of root)"])}, n)
        self.assertIn(f"{home}/left.txt", world.paths)

    def test_G6_19_a_name_with_a_line_feed_is_one_name(self):
        # R136 (the owner's item 4, 2026-11-25, read in code): a name a job or another user chose, read from find by
        # lines, was two items when it held a line feed; in a public place the second could be any text, reaching the
        # census as an untrue line and root's find as an argument. Every find whose output carries a name that is not
        # ours ends each record with a NUL and is split there; left-behind.txt's lines are cleaned as a printed line is
        # (rule 31 B). A name with a line feed, a tab, a backtick and a dollar-parenthesis, in a (B) folder and in a
        # public place.
        R, home = "minions-runner", f"/home/{M.RUNNER_USER}"
        odd_ = "a\nb\tc`d$(e)"
        shown = "a?b c'd$ (e)"
        jd = "/tmp/x\ny"
        gh, world, host = remove_setup(moved=False)
        world.paths |= {f"{home}/{odd_}", jd, f"{jd}/f"}
        world.plain |= {f"{home}/{odd_}", f"{jd}/f"}
        world.dirs.add(jd)
        world.owner.update({f"{home}/{odd_}": "root", jd: R, f"{jd}/f": "root"})
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertFalse([x for x in lines if x.startswith("stop ")], lines)
        homes = [x for x in lines if x.startswith(f"ok census: the folder {home}: kept: ")]
        self.assertEqual(len(homes), 1, lines)
        self.assertIn(f"it holds {shown} (a file of root)", homes[0])
        public = [x for x in lines if x.startswith("ok census: in /tmp, /var/tmp, /dev/shm: kept: ")]
        self.assertEqual(len(public), 1, lines)
        self.assertIn("/tmp/x?y (a folder of the runner user's that holds what it did not make)", public[0])
        self.assertIn("; that stays; 1 thing of the runner user's number handed to root, as that user is deleted",
                      public[0])
        self.assertEqual(world.owner_of(jd), "root")
        walks = [a for a in host.ran if "chown" in a and "/tmp" in a]  # change 2: root's one walk from its own tops
        self.assertEqual([a[3:6] for a in walks], [("/tmp", "/var/tmp", "/dev/shm")], walks)
        self.assertFalse([x for x in walks[0] if "/tmp/x" in str(x)], walks)  # no name of a job's is an argument
        kept = host.files.get(f"{host.record_dir()}/left-behind.txt", "")
        body = [x for x in kept.split("\n") if x and not x.startswith("# ")]
        self.assertEqual(len(body), 2, kept)
        for x in body:
            self.assertFalse(set(x) & {"\t", "`"} or "$(" in x, x)

    def test_G6_19_a_cut_at_the_hand_to_root_is_finished_by_the_next_run(self):
        # R136 (the owner's item 5, 2026-11-25): the hand to root ran only in the run that deleted the user, with the
        # number read before, and its find's code was not read; a run cut between userdel and the hand left things
        # owned by a number no user has, and no later run knew it. The hand now comes BEFORE userdel (every process of
        # the user is ended by then, and what is handed stays anyway); the home's own folder, which stays with what it
        # holds, is handed after userdel by its number having no user (-nouser), which a later run reads again. A cut
        # at each of those steps, then uninstall --all again: nothing of the runner user's number is left, exit 0.
        R, J, home = "minions-runner", "/tmp/job-1", f"/home/{M.RUNNER_USER}"
        cuts = {"the hand": lambda a: a[2:3] == ["find"] and "-uid" in a and "chown" in a,
                "userdel": lambda a: a[2:3] == ["userdel"],
                "the home's own hand": lambda a: a[2:3] == ["find"] and "-nouser" in a}
        for name, cut in cuts.items():
            gh, world, host = remove_setup(interrupt_on=cut, moved=False)
            plant = {f"{FOLDER}/_work": R, f"{FOLDER}/_work/root.txt": "root", f"{home}/left.txt": "root", J: R,
                     f"{J}/root.txt": "root"}
            world.paths |= set(plant)
            world.owner.update(plant)
            world.dirs |= {f"{FOLDER}/_work", J}
            world.plain |= {p_ for p_, w in plant.items() if w == "root"}
            code = M.main(["uninstall", "--all"], host=host, out=Out(), gh=gh)
            self.assertIn(code, (M.EXIT_INTERRUPTED, M.EXIT_OK), name)
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_OK, (name, lines))
            self.assertFalse([x for x in lines if x.startswith("stop ")], (name, lines))
            self.assertFalse(world.user, name)
            self.assertEqual(sorted(p_ for p_ in world.paths if world.owner_of(p_) == R), [], name)
            self.assertTrue({p_ for p_, w in plant.items() if w == "root"} <= world.paths, name)

    def test_G2_14_setup_with_a_kept_home_and_no_record_says_only_what_it_read(self):
        # Change 2 (case c): setup with the home there, the user not, and no record of this tool: the stop says what
        # was read and that the record does not say this tool made such a user; the rm line is offered only as "if
        # you know it is left from this tool's runner", and moving it away as the other step (as R210's words say it).
        home = f"/home/{M.RUNNER_USER}"
        host, sha = setup_host()
        host.dirs[home] = True
        fresh_look(host)
        self.assertFalse({"runner_user_made", "runner_home_left"} & set(M.load_state(host)), "no record of this tool")
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        x = lines[-1]
        self.assertIn(f"the user {M.RUNNER_USER} is not on this computer, the folder is, and this tool deletes in a "
                      "runner's home only as that user; this tool's record does not say it made a user of that name", x)
        self.assertNotIn("outside this tool", x)
        self.assertNotIn("Your next step: delete", x)
        self.assertIn("If you know it is left from this tool's runner, your next step: delete that folder first: "
                      "`sudo rm -rf --one-file-system /home/minions-runner`; then run the same command again", x)
        self.assertIn("If it is something else of yours, move it away first; then run the same command again", x)
        self.assertLess(x.index("If you know"), x.index("If it is something else"))

    def test_G2_14_setup_refuses_a_home_left_by_a_user_removed_outside_the_tool(self):
        # R173 (2026-11-24; R130): with the runner user absent and its home there, setup went on
        # to useradd, which keeps a folder that is there, so a new user of that name took over what a job had left
        # in it. Now setup stops before any admin step, names the folder and the one whole command, and goes on once
        # that folder is gone.
        home = f"/home/{M.RUNNER_USER}"
        host, sha = setup_host()
        host.dirs[home] = True
        fresh_look(host)
        for n in (1, 2):
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertEqual(admin_steps(host), [], "nothing is touched, before any admin step")
            self.assertEqual(host.validated, 0, "no password asked")
            self.assertIn(f"the folder {home} is here", lines[-1])
            self.assertIn("first: `sudo rm -rf --one-file-system /home/minions-runner`; then run the same command "
                          "again", lines[-1])
        del host.dirs[home]  # the step taken as printed
        self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)

    def test_G6_30_doctor_with_the_runner_user_gone_tries_nothing_as_it_and_names_why(self):
        # R178 (AN ERROR IS NEVER AN ABSENCE): doctor read the user with `id -nG` and took any exit but 0 as "not there"; with the user
        # gone, its read of the credential files as that user got sudo's refusal and said they "are not 600 and its
        # own", and the version was "could not read" with no why. Now the user is getent's exit code; with it gone
        # nothing is tried as it, and each read skipped says why; getent that could not answer is "not read".
        host, sha = doctor_world()
        host.runs[("getent", "passwd", "minions-runner")] = (2, "")
        before = len(host.ran)
        code, lines = run_doctor(host, sha)
        self.assertFalse([a for a in host.ran[before:] if a[:4] == ("sudo", "-n", "-u", "minions-runner")],
                         "nothing is tried as a user that is gone")
        self.assertFalse([a for a in host.ran[before:] if a[:2] == ("id", "-nG")], "id's exit is never the answer")
        gone = "the user minions-runner is not there (removed outside this tool), so nothing is tried as it"
        self.assertIn(f"warn {REPO}: the credential files: not read: {gone}", lines)
        self.assertIn(f"warn could not read the runner's version: {gone}; GitHub's own self-update stays on", lines)
        self.assertTrue(any(f"the user {M.RUNNER_USER} is not there: `setup {REPO}` again" in x for x in lines), lines)
        self.assertFalse([x for x in lines if "are not 600" in x], lines)
        host, sha = doctor_world()
        host.runs[("getent", "passwd", "minions-runner")] = (3, "getent: enumeration not supported\n")
        code, lines = run_doctor(host, sha)
        self.assertTrue(any(x.startswith(f"warn {REPO}: whether the user {M.RUNNER_USER} is here: not read (")
                            and "getent: enumeration not supported" in x for x in lines), lines)
        self.assertFalse([x for x in lines if f"the user {M.RUNNER_USER} is not there" in x], lines)

    def test_G6_30_setup_with_the_runner_user_and_its_home_gone_starts_fresh(self):
        # R178: setup again for a runner of this tool, with the runner user removed outside the tool and its home
        # with it, went on as if the runner were there: re-registered in place as the user that is gone (sudo's
        # refusal), or took the runner as installed. Now what the tool recorded of it is taken away first (nothing
        # tried as that user), then setup starts fresh: the user made again, one registration, exit 0.
        for gone_on_github in (True, False):
            host, sha = setup_host()
            fresh_look(host)
            data = M.load_state(host)
            data["runners"] = {REPO: {"name": "minions-box-1", "folder": FOLDER, "unit": UNIT,
                                      "labels": ["minions", "minions-medium"], "gone_on_github": gone_on_github}}
            M.save_state(host, data)
            gh_state = {"labels": ["minions", "minions-medium"], "deleted": False}
            def runners(_body, host=host, gone_on_github=gone_on_github, gh_state=gh_state):
                if any(M.CONFIG_SCRIPT in a for a in host.ran):  # registered fresh by this setup
                    return runners_answer()
                if gone_on_github or gh_state["deleted"]:
                    return 200, {"total_count": 0, "runners": []}
                return runners_answer(labels=gh_state["labels"])
            def put_labels(body, gh_state=gh_state):
                gh_state["labels"] = list(body["labels"])
                return 200, {"labels": [{"name": x, "type": "custom"} for x in body["labels"]]}
            def delete(_body, gh_state=gh_state):
                gh_state["deleted"] = True
                return 204, None
            gh = setup_gh(sha, {("GET", f"repos/{REPO}/actions/runners?per_page=100"): runners,
                                ("PUT", f"repos/{REPO}/actions/runners/7/labels"): put_labels,
                                ("DELETE", f"repos/{REPO}/actions/runners/7"): delete})
            code, lines = run_setup(gh, host)
            self.assertEqual(code, M.EXIT_OK, (gone_on_github, lines[-3:]))
            self.assertTrue(any("taken away first, read back, then setup starts fresh" in x for x in lines), lines)
            self.assertNotIn(("sudo", "-n", "-u", "minions-runner", "--", "rm", "-f", f"{FOLDER}/.runner",
                              f"{FOLDER}/.credentials", f"{FOLDER}/.credentials_rsaparams"), host.ran)
            self.assertTrue([a for a in host.ran if a[:3] == ("sudo", "-n", "useradd")], "the user made again")
            self.assertEqual(len([a for a in host.ran if M.CONFIG_SCRIPT in a]), 1)

    # Rule 7 (THE SAME STOP MUST NOT COME BACK; A WAIT THAT MAY NOT END): the words that name, in a stop's FIRST
    # printing, what must change or what a second run can find different, and what to do when the same lines come back
    NAMED_CHANGE = re.compile(r"\bfirst\b|if these same lines come back|type the password|in your own terminal|"
                              r"once no job", re.I)

    def twice(self, make, fix, run):
        """One world taken twice in the unchanged world, and once with the printed step taken (fix). A stop or wait
        whose second run in the unchanged world ends in the same words must carry a named change; with the change
        made, the second run must end differently (the promise is real). Returns the first run's end line."""
        def end(lines):
            stops = [x for x in lines if x.startswith(("stop ", "waiting ", "need you"))
                     and "press Enter here to go on" not in x and "where sudo asks for it below" not in x]
            return stops[-1] if stops else None
        w = make()
        code1, lines1 = run(w)
        e1 = end(lines1)
        code2, lines2 = run(w)  # the unchanged world
        if e1 is not None and end(lines2) == e1:
            self.assertTrue(self.NAMED_CHANGE.search(e1), f"the same stop came back with no change named: {e1}")
        for x in lines2:
            if x.startswith("warn ") and re.search(r"\bagain\b", x):
                self.assertTrue(self.NAMED_CHANGE.search(x), f"a 'run again' warning with no change named: {x}")
        if e1 is not None:
            self.assertIsNotNone(fix, f"a stop with no step to take in the suite's world: {e1}")
            w = make()
            run(w)
            fix(w)
            self.assertNotEqual(end(run(w)[1]), e1, f"the step taken as printed ended in the same stop: {e1}")
        return code1, e1

    def test_G7_19_every_stop_of_uninstall_taken_as_printed_twice_does_not_come_back(self):
        # R174 (family rule 7, THE SAME STOP MUST NOT COME BACK): each stop and "run it again" line, taken as printed
        # twice with nothing changed between, ends differently or names what must change first.
        def gh_down(w):
            gh, world, host = w
            gh.down = True
        def gh_up(w):
            w[0].down = False
        def answer_wrap(match, said):
            def mutate(w):
                gh, world, host = w
                real = host._answer
                host._answer = lambda a, i=None: said if host.broken and match(list(a)) else real(a, i)
                host.broken = True
            return mutate
        def mended(w):
            w[2].broken = False
        def make_with(mutate):
            def make():
                gh, world, host = remove_setup(moved=False)
                real_api = gh.api
                gh.down, gh.spent = False, False
                def api(m, p, b=None):
                    if gh.spent and "actions/runners" in p:
                        gh.limited_until = M.parse_stamp("2026-11-24T23:23:21Z")
                        return 403, {"message": "API rate limit exceeded for user ID 1."}
                    return (502, {}) if gh.down and "actions/runners" in p else real_api(m, p, b)
                gh.api = api
                w = (gh, world, host)
                mutate(w)
                return w
            return make
        def spend(w):  # change 1: GitHub's API limit for this login spent, as G8.14 reads it from the headers
            w[0].spent = True
        def reset(w):
            w[0].spent, w[0].limited_until = False, None
        def gone(w):
            w[1].user, w[1].listener = False, False
        def stuck_unit(w):
            gh, world, host = w
            real = world.answer
            def answer(a, i=None):
                if host.broken and list(a[:4]) == ["sudo", "-n", "systemctl", "stop"] and UNIT in a:
                    return 0, ""  # told to stop, and it does not
                return real(a, i)
            host._answer = answer
            host.broken = True
        def run(w):
            gh, world, host = w
            return run_uninstall(gh, host, repo=None, everything=True)
        worlds = {
            "the runner user removed outside the tool, its home kept": (gone, None),
            "GitHub answers 502": (gh_down, gh_up),
            "GitHub answers 403 with no calls left": (spend, reset),
            "pgrep cannot answer": (answer_wrap(lambda a: a[:3] == ["pgrep", "-u", "1001"], (3, "")), mended),
            "getent cannot answer": (answer_wrap(lambda a: a[:1] == ["getent"], (3, "")), mended),
            "the service does not stop": (stuck_unit, mended),
            "systemd cannot be read": (answer_wrap(lambda a: a[:2] == ["systemctl", "show"],
                                                   (1, "Failed to connect to bus: No medium found\n")), mended),
            "the runner's folder is not deleted": (answer_wrap(lambda a: a[:1] == ["rm"] or a[5:6] == ["rm"],
                                                               (1, "rm: cannot remove: Operation not permitted\n")),
                                                   mended),
            "userdel refuses": (answer_wrap(lambda a: a[:1] == ["userdel"],
                                            (8, "userdel: user minions-runner is currently used by process 4444\n")),
                                mended),
            "the jobs' records cannot be read": (answer_wrap(lambda a: a[5:6] == ["cat"], (1, "cat: denied\n")),
                                                 mended),
        }
        ends = {}
        for name, (mutate, fix) in worlds.items():
            with self.subTest(world=name):
                code, e = self.twice(make_with(mutate), fix, run)
                ends[name] = (code, e)
        self.assertEqual(ends["the runner user removed outside the tool, its home kept"], (M.EXIT_OK, None))
        self.assertEqual(ends["the jobs' records cannot be read"][1], None)  # kept and named; the leave finishes
        limit = ends["GitHub answers 403 with no calls left"][1]  # change 1: the limit and its time, never rights
        self.assertIn("GitHub's API limit for this login is spent until 23:23Z", limit)
        self.assertIn("wait until 23:23Z first; then run the same command again", limit)
        self.assertNotIn("admin rights", limit)

    def test_G7_19_githubs_refusal_is_named_by_what_it_was(self):
        # Change 1 (2026-11-24): github_next said "give the account admin rights" for a spent API
        # limit (false twice: rights were not the refusal, and they change nothing). The limit is read first, for 403
        # and 429 alike; 401 is the saved login refused; 404 is no access, or the repository renamed or removed.
        class G:
            pass
        gh = G()
        gh.limited_until = M.parse_stamp("2026-11-24T23:23:21Z")
        for s in (403, 429):
            words = as_said(M.github_next(s, REPO, gh=gh))
            self.assertIn("GitHub's API limit for this login is spent until 23:23Z", words)
            self.assertIn("wait until 23:23Z first; then run the same command again", words)
            self.assertNotIn("admin rights", words)
        gh.limited_until = None
        self.assertIn("admin rights on", M.github_next(403, REPO, gh=gh))
        self.assertNotIn("admin", M.github_next(429, REPO, gh=gh))
        w401 = as_said(M.github_next(401, REPO, gh=gh))
        self.assertIn("GitHub refused the login gh saved for this tool (HTTP 401)", w401)
        self.assertIn("log in to GitHub again as yourself, `gh auth login`, first", w401)
        self.assertNotIn("admin", w401)
        w404 = as_said(M.github_next(404, REPO, gh=gh))
        self.assertIn(f"{REPO} is not there for this login (HTTP 404)", w404)
        self.assertIn("has no access to it, or it was renamed or removed", w404)
        self.assertNotIn("admin", w404)
        # The stops that named a refusal on their own say the limit first too: look's read of the repository.
        host = linux_host()
        gh = StandIn([], {("GET", f"repos/{REPO}"): (403, {"message": "API rate limit exceeded for user ID 1."})})
        gh.limited_until = M.parse_stamp("2026-11-24T23:23:21Z")
        rep = M.Report(Out())
        self.assertIsNone(M.check_repository(gh, host, REPO, rep))
        self.assertTrue(folded(rep.lines)[-1].startswith(f"stop could not read {REPO} on GitHub (HTTP 403): "), rep.lines)
        self.assertIn("spent until 23:23Z", folded(rep.lines)[-1])
        self.assertNotIn("check the name", folded(rep.lines)[-1])

    def test_G7_19_every_stop_of_setups_first_steps_taken_as_printed_twice_does_not_come_back(self):
        # R174: the same for setup's first steps: the look, the runner user, its home, the Enter and the admin password.
        home = f"/home/{M.RUNNER_USER}"
        def make_with(mutate, look=True):
            def make():
                host, sha = setup_host()
                if look:
                    fresh_look(host)
                mutate(host)
                return host, sha
            return make
        def run(w):
            host, sha = w
            return run_setup(setup_gh(sha), host)
        def getent(answer):
            return lambda host: host.runs.__setitem__(("getent", "passwd", "minions-runner"), answer)
        THERE = (0, "minions-runner:x:1001:1001::/home/minions-runner:/bin/bash\n")
        def no_password(host):
            host.runs[("sudo", "-n", "true")] = (1, "sudo: a password is required\n")
            host.sudo_validate = lambda: 1
        def password(w):
            del w[0].sudo_validate  # the class's own: sudo takes the password
        worlds = {
            "no look yet": (make_with(lambda h: None, look=False), lambda w: fresh_look(w[0])),
            "a look 90 minutes old": (make_with(lambda h: fresh_look(h, minutes=90)), lambda w: fresh_look(w[0])),
            "a user of that name this tool did not make": (make_with(getent(THERE)),
                                                           lambda w: getent((2, ""))(w[0])),
            "the user could not be read": (make_with(getent((3, ""))), lambda w: getent((2, ""))(w[0])),
            "a home left by a user removed outside the tool": (make_with(lambda h: h.dirs.__setitem__(home, True)),
                                                               lambda w: w[0].dirs.pop(home)),
            "your home open to every user": (make_with(lambda h: setattr(h, "home_mode", 0o755)),
                                             lambda w: setattr(w[0], "home_mode", 0o750)),
            "no Enter read": (make_with(lambda h: setattr(h, "enter", False)), lambda w: setattr(w[0], "enter", True)),
            "sudo did not take the password": (make_with(no_password), password),
        }
        for name, (make, fix) in worlds.items():
            with self.subTest(world=name):
                code, e = self.twice(make, fix, run)
                self.assertIsNotNone(e, f"{name}: the world was meant to stop")

    def test_G6_27_the_slice_is_never_stopped_while_a_unit_in_it_runs(self):
        # R167 (2026-11-24; rule 30): a stop of the slice ends every unit in it, a running job
        # too, a runner unit whose record was lost included. Its own units are read first (systemd's SliceOf, and,
        # for a systemd that does not know that field, each unit of our names whose Slice= is ours); while one reads
        # running the slice is not stopped and its file stays; a read with no answer is the same refusal.
        # (A runner unit of this tool's own naming form that no record names stops uninstall --all before any change,
        # R210, G6.38; this one is not of that form.)
        LOST = "actions.runner.example-org-lost.box-2.service"
        for old in (False, True):
            gh, world, host = remove_setup()
            world.old_systemd = old
            world.loaded.add(LOST)
            world.active[LOST], world.slice_of[LOST] = "active", M.SLICE
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertIn(f"{M.SLICE} still holds {LOST} (active): it is not stopped", lines[-1])
            self.assertNotIn(f"stop {M.SLICE}", world.order)
            self.assertNotIn(f"stop {LOST}", world.order)
            self.assertIn(SLICE_FILE, world.paths)
            # once that unit is stopped (by the person), the same command finishes
            world.active[LOST] = "inactive"
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertNotIn(code, (M.EXIT_STOP, M.EXIT_WAIT), lines)
            self.assertNotIn(SLICE_FILE, world.paths)
        # The slice's own list not read: nothing of the slice is stopped or deleted.
        for which, match in (("SliceOf", lambda a: a[:4] == ["systemctl", "show", M.SLICE, "-p"] and "SliceOf" in a),
                             ("list-units", lambda a: a[:2] == ["systemctl", "list-units"])):
            gh, world, host = remove_setup()
            host._answer = (lambda real, m: lambda a, i=None: (1, "Failed to connect to bus: No such file or "
                            "directory\n") if m(list(a)) else real(a, i))(world.answer, match)
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_STOP, lines)
            if which == "SliceOf":
                self.assertIn(f"{M.SLICE} is not stopped", lines[-1])
            else:  # R210: systemd's units not read: uninstall --all stops before any change, saying so
                self.assertIn("Not read: systemd's units (systemctl answered code 1", lines[-1])
                self.assertEqual(world.order, [])
            self.assertIn("systemctl answered code 1: Failed to connect to bus", lines[-1])
            self.assertNotIn(f"stop {M.SLICE}", world.order)
            self.assertIn(SLICE_FILE, world.paths)

    def test_G6_27_one_call_one_unit_and_each_unit_file_removed_by_hand(self):
        # R167 (rule 30, ONE CALL, ONE UNIT; 2026-11-24): every systemctl call that acts on units names one
        # unit; a stop is its own call by name, never only the --now of a disable (seen, systemd 255: `disable --now A
        # B` with A's file gone stopped neither, and the world answers so). Each unit file of ours is removed by hand
        # in turn under its running unit, and `uninstall --all` finishes in one run with every unit read gone.
        units = lambda a: [x for x in a if re.search(r"\.(service|timer|slice|target)$", x)]
        timer, service = f"{M.WATCH_UNIT}.timer", f"{M.WATCH_UNIT}.service"
        for unit, path in ((UNIT, f"/etc/systemd/system/{UNIT}"), (timer, WATCH_FILES[0]), (service, WATCH_FILES[1]),
                           (M.SLICE, SLICE_FILE)):
            gh, world, host = remove_setup()
            world.active[unit] = "active"  # the watch's service: a pass running when the file went
            world.gone(path)
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertNotIn(code, (M.EXIT_STOP, M.EXIT_WAIT), (unit, lines))
            self.assertIn(f"stop {unit}", world.order, unit)
            for u in (UNIT, timer, service, M.SLICE):
                self.assertEqual(M.unit_read(host, u)[0], "gone", (unit, u))
            many = [a for a in host.ran if "systemctl" in a and len(units(a)) > 1]
            self.assertEqual(many, [], unit)
            self.assertFalse([a for a in host.ran if "systemctl" in a and "--now" in a and "disable" in a], unit)
        # the sleep read too: one unit a call
        host = linux_host(runs={("systemctl", "is-enabled"): (0, "masked\n")})
        self.assertEqual(M.sleep_setting(host), "off (sleep and suspend masked)")
        reads = [a for a in host.ran if "is-enabled" in a]
        self.assertEqual([units(a) for a in reads], [["sleep.target"], ["suspend.target"]])

    def test_G6_27_no_process_read_is_made_without_the_runners_own_folder(self):
        # R167 (2026-11-24): with a record that lost its folder (none of the tool's roads writes
        # one so; a hand edit could), pgrep's pattern would have been "/bin/Runner.": every runner of the runner
        # user's, another repository's too, named as "of it". The folder is the repository's own; an empty one is
        # never read.
        gh, world, host = remove_setup()
        data = M.load_state(host)
        del data["runners"][REPO]["folder"]
        M.save_state(host, data)
        run_uninstall(gh, host)
        bare = [a for a in host.ran if a[:1] == ("pgrep",) and a[-1].startswith("/bin/")]
        self.assertEqual(bare, [])
        self.assertTrue(any(a[:1] == ("pgrep",) and a[-1] == f"{FOLDER}/bin/Runner." for a in host.ran), host.ran)
        self.assertEqual(M.runner_folder(REPO), FOLDER)
        for folder in ("", "/", M.RUNNERS_HOME, M.RUNNERS_HOME + "../x"):
            with self.assertRaises(M.PidsUnread):
                M.runner_pids(host, folder)

    def test_G6_27_the_copy_uninstall_runs_from_goes_last(self):
        # R167 (2026-11-24): the skill tells the person to run `uninstall --all` from the copy
        # setup installed. That copy was deleted before the runner user, whose stops say "run the same command
        # again": a command that was gone. Now every stop names a copy that is still there; the copy goes last.
        real = M.tool_path
        M.tool_path = lambda: M.TOOL_COPY
        try:
            for match in (lambda a: a == ["pgrep", "-u", "1001"],  # the runner user's processes (by number, R172)
                          lambda a: a[:2] == ["systemctl", "list-units"]):  # the slice's own units
                gh, world, host = remove_setup()
                host._answer = (lambda r, m: lambda a, i=None: (3, "") if m(list(a)) else r(a, i))(world.answer, match)
                code, lines = run_uninstall(gh, host, repo=None, everything=True)
                self.assertEqual(code, M.EXIT_STOP, lines)
                self.assertIn(M.HOOKS_ROOT, world.paths, lines[-1])
                self.assertNotIn(f"rmdir {M.HOOKS_ROOT}", world.order)
            gh, world, host = remove_setup()
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(world.order[-1], f"rmdir {M.HOOKS_ROOT}")
            self.assertTrue(any(x.endswith("the very last removal") for x in lines), lines)
        finally:
            M.tool_path = real

    def test_G6_28_a_refusal_on_standard_error_is_shown_in_its_own_words(self):
        # R168 (2026-11-24, words lost): where a line the person reads carries a program's answer, a
        # refusal on standard error (standard output empty) is shown in its own words, never "not read" alone. One
        # refusal at each such line, in the world that reaches it.
        TIMER, HOME_R = f"{M.WATCH_UNIT}.timer", f"/home/{M.RUNNER_USER}"
        def refuse(host, key, words):
            host.runs[key] = (1, M.Out("", words + "\n"))
        cases = [  # (world, the command refused, the words; the line that must show them)
            ("setup", ("id", "-nG", "minions-runner"), "id: WORDS-groups"),
            ("setup", ("sudo", "-n", "stat", "-c", "%a", HOME_R), "stat: WORDS-mode"),
            ("setup", ("systemctl", "is-active", TIMER), "Failed to connect to bus: WORDS-timer"),
            ("doctor", ("sudo", "-n", "stat", "-c", "%a", HOME_R), "stat: WORDS-doctor-mode"),
            ("doctor", ("systemctl", "is-active", TIMER), "Failed to connect to bus: WORDS-doctor-timer"),
            ("doctor", ("systemctl", "is-active", UNIT), "Failed to connect to bus: WORDS-doctor-unit"),
            ("status", ("systemctl", "is-active", UNIT), "Failed to connect to bus: WORDS-status-unit"),
        ]
        for world, key, words in cases:
            if world == "setup":
                host, sha = setup_host()
                fresh_look(host)
                refuse(host, key, words)
                code, lines = run_setup(setup_gh(sha), host)
            else:
                host, sha = doctor_world()
                refuse(host, key, words)
                code, lines = (run_doctor(host, sha) if world == "doctor"
                               else run_cmd(M.status, REPO, host=host, gh=setup_gh(sha)))
            self.assertTrue([a for a in host.ran if a[:len(key)] == key], (world, key))
            self.assertTrue([x for x in lines if words in x], (world, key, lines))
            self.assertFalse([x for x in lines if x.endswith(("(not read)", "not read")) and key[-1] in x
                              and words not in x], (world, key, lines))
        self.assertEqual(M.answer_or_words(M.Out("", "x: WORDS\n")), "not read: x: WORDS")
        self.assertEqual(M.answer_or_words(M.Out("inactive\n", "")), "inactive")

    def test_G6_27_a_copy_left_by_a_first_uninstall_all_is_finished_by_the_next_run(self):
        # R167 (2026-11-24): a first `uninstall --all` removed the state and then could
        # not delete the tool's copy (rm refused), or could not read whether it is gone; the second run, with no state
        # left, reaches the copy's removal again and finishes. Where it could not be read, the step names the clone
        # setup was run from, as a whole command line, and that step taken as printed finishes.
        CLONE = "/home/you/windstorm-repo-minions"
        for how in ("rm refused", "not read"):
            gh, world, host = remove_setup()
            data = M.load_state(host)
            data["skill_from"] = {"clone": CLONE}
            M.save_state(host, data)
            real = world.answer
            rm_sent = []
            def first(argv, input=None, real=real, how=how, once=[True], rm_sent=rm_sent):
                a = list(argv)
                if how == "rm refused" and a[:2] == ["sudo", "-n"] and a[2:3] == ["rmdir"] and a[-1] == M.HOOKS_ROOT \
                        and once[0]:
                    once[0] = False
                    return 1, f"rmdir: failed to remove '{M.HOOKS_ROOT}': Device or resource busy"
                if how == "not read" and a == ["sudo", "-n", "test", "-e", M.HOOKS_ROOT] and once[0] and rm_sent:
                    once[0] = False  # the read after the copy's removal
                    return 1, "sudo: a terminal is required to read the password"
                if a[2:3] == ["rmdir"] and a[-1] == M.HOOKS_ROOT:
                    rm_sent.append(1)
                return real(argv, input)
            host._answer = first
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_STOP, (how, lines))
            self.assertEqual(M.load_state(host).get("runners") or {}, {}, how)  # the state went before the copy
            if how == "not read":
                stop = [x for x in lines if x.startswith("stop ") and M.HOOKS_ROOT in x]
                self.assertTrue(stop and f"`python3 -I {CLONE}/scripts/minions.py uninstall --all`" in stop[0], lines)
            else:
                self.assertIn(M.HOOKS_ROOT, world.paths)
                stop = [x for x in lines if x.startswith("stop ") and M.HOOKS_ROOT in x]
                self.assertTrue(stop and "Run the same command again" in stop[0], lines)
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertNotIn(code, (M.EXIT_STOP, M.EXIT_WAIT), (how, lines))
            self.assertNotIn(M.HOOKS_ROOT, world.paths, how)
            self.assertTrue(any(x.endswith("the very last removal") for x in lines), (how, lines))

    def test_G6_29_the_words_the_tool_reads_are_never_in_the_persons_language(self):
        # R172 (2026-11-24, the language probe; seen on a stock Ubuntu 24.04 with language-pack-de and the
        # person's shell in German): id, stat, pgrep and sudo answered in German, setup stopped at its first step,
        # and uninstall with the runner user gone waited at every run. The real Host and GitHub are run here with the
        # programs stood in: each answers in German unless its own environment carries LC_ALL=C.UTF-8 and no
        # LANGUAGE (as seen: that setting gave English, through sudo too). The same ends as in English.
        seen = []
        GERMAN = {"id": "id: \u00bbminions-runner\u201c: Einen solchen Benutzer gibt es nicht\n",
                  "pgrep": "pgrep: Ung\u00fcltiger Benutzername: minions-runner\n",
                  "sudo": "sudo: Ein Passwort ist notwendig\n"}
        ENGLISH = {"id": "id: \u2018minions-runner\u2019: no such user\n",
                   "pgrep": "pgrep: invalid user name: minions-runner\n", "sudo": "sudo: a password is required\n"}
        user = {"line": ""}  # getent's line for the runner user, "" when it is not there
        def fake_run(argv, input=None, **kw):
            env = kw.get("env") if kw.get("env") is not None else dict(os.environ)
            seen.append((list(argv), env))
            c = env.get("LC_ALL") == "C.UTF-8" and "LANGUAGE" not in env
            words = ENGLISH if c else GERMAN
            name = argv[0]
            if name == "getent":  # glibc's getent: its line, or exit 2 with no words at all, in every language
                return subprocess.CompletedProcess(argv, 0 if user["line"] else 2, user["line"], "")
            if name == "pgrep" and not argv[2].isdigit() and not user["line"]:
                return subprocess.CompletedProcess(argv, 2, "", words["pgrep"])
            if name == "pgrep":
                return subprocess.CompletedProcess(argv, 1, "", "")
            if name in ("id", "sudo"):
                return subprocess.CompletedProcess(argv, 1, "", words[name])
            return subprocess.CompletedProcess(argv, 0, "HTTP/2.0 200 OK\r\n\r\n{}", "")
        shell = {"LANG": "de_DE.UTF-8", "LANGUAGE": "de_DE:de", "LC_MESSAGES": "de_DE.UTF-8", "HOME": "/home/you",
                 "PATH": "/usr/bin:/bin"}
        host = M.Host()
        with mock.patch.dict(os.environ, shell, clear=True), mock.patch.object(M.subprocess, "run", fake_run), \
                mock.patch.object(M.platform, "system", lambda: "Linux"):
            self.assertEqual(M.runner_user_state(host)[0], "absent")  # setup's first step reads it
            self.assertEqual(M.pids_list_or_none(host), [])  # uninstall with the user gone: none
            user["line"] = "minions-runner:x:998:998::/home/minions-runner:/bin/bash\n"
            self.assertEqual(M.runner_user_state(host), ("there", "998"))
            self.assertEqual(M.pids_list_or_none(host), [])  # pgrep's exit 1: none
            rc, out = M.sudo(host, ["true"])
            self.assertIn("sudo's permission ran out", M.said(out))  # sudo's refusal read as one
            host.run_in(["gh", "api", "x"], "{}")
            M.GitHub(host).api("GET", "repos/o/r")
            # THE LIMIT (rule 30 (ii)): the C language is on the call, never on the tool's own process
            self.assertEqual((os.environ.get("LANGUAGE"), os.environ.get("LC_ALL")), ("de_DE:de", None))
        self.assertGreaterEqual(len(seen), 6)
        # R172 (i): an exit code or a field first: whether the runner user is there is getent's exit code, and its
        # processes are asked by its NUMBER, so no word of id's or of pgrep's is read for either
        self.assertFalse([a for a, _ in seen if a[:1] == ["id"]], seen)
        self.assertIn(["pgrep", "-u", "998"], [a for a, _ in seen])
        self.assertFalse([a for a, _ in seen if a[:2] == ["pgrep", "-u"] and not a[2].isdigit()], seen)
        for argv, env in seen:
            self.assertEqual(env.get("LC_ALL"), "C.UTF-8", argv)
            self.assertNotIn("LANGUAGE", env, argv)
            self.assertEqual(env.get("LANG"), "de_DE.UTF-8", argv)  # nothing else of the shell changes
        # Rule 30 (A NAME IS READ WHOLE, (3)): on a Mac no call is moved (macOS 13 has no C.UTF-8 and falls to
        # plain C, ASCII, with no error, seen by the owner), so a name in an answer comes out whole there
        with mock.patch.object(M.platform, "system", lambda: "Darwin"):
            self.assertEqual(M.c_language(shell), shell)

    def test_G6_29_nothing_the_tool_starts_for_a_job_or_the_person_carries_the_c_language(self):
        # R172 (rule 30, WHAT IT NEVER TOUCHES): the runner's configuration and its service start from an environment
        # built by name (env -i, with the locale every job gets, LANG=C.UTF-8, R143); no unit, drop-in, .env or
        # job hook this tool writes carries LC_ALL or LANGUAGE, whatever the shell setup ran from had.
        shell = {"LANG": "de_DE.UTF-8", "LANGUAGE": "de_DE:de", "LC_ALL": "de_DE.UTF-8", "HOME": "/home/you",
                 "PATH": "/usr/bin:/bin"}
        with mock.patch.dict(os.environ, shell, clear=True):
            host, sha = setup_host()
            fresh_look(host)
            self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
        self.assertEqual(M.RUNNER_ENV[:2], ("env", "-i"))
        self.assertFalse([x for x in M.RUNNER_ENV if x.startswith(("LC_", "LANGUAGE"))], M.RUNNER_ENV)
        starts = [a for a in host.ran if "bash" in a and "-u" in a]
        self.assertTrue(starts and all(tuple(M.RUNNER_ENV) == a[a.index("--") + 1:][:len(M.RUNNER_ENV)]
                                       for a in starts), starts)
        written = [(a, i) for a, i in host.fed if i] + [(p, v) for p, v in host.files.items()
                                                        if isinstance(v, str) and p != M.TOOL_COPY]  # not its source
        self.assertTrue(written)
        for what, text in written:
            self.assertNotIn("LC_ALL", text, what)
            self.assertNotIn("LANGUAGE", text, what)
            self.assertNotIn("de_DE", text, what)

    def test_G6_29_every_phrase_matched_is_on_the_list(self):
        # R172 (iii): the repository holds the list of every phrase matched in another program's answer (program,
        # phrase, version seen), docs/phrases.md. Every literal the code matches against an answer (`in` with said(),
        # out, head, mode, words or entry on the right; re.search/match/fullmatch/findall on one of those) is on
        # the list, and every phrase on the list is still matched by the code.
        with open(os.path.join(ROOT, "scripts", "minions.py"), encoding="utf-8") as f:
            src = f.read()
        with open(os.path.join(ROOT, "docs", "phrases.md"), encoding="utf-8") as f:
            listed = set(re.findall(r"^\s*- phrase: `([^`]+)`", f.read(), re.M))
        own = {"could not be read"}  # doctor's own line (copy_version_line), not another program's answer
        subj = re.compile(r"\bsaid\(|^(out|head|mode|words|entry)(\.lower\(\)|\.strip\((BLANKS)?\))?$")
        matched = set()
        for n in ast.walk(ast.parse(src)):
            if isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], (ast.In, ast.NotIn)) \
                    and isinstance(n.left, ast.Constant) and isinstance(n.left.value, str) \
                    and subj.search(ast.get_source_segment(src, n.comparators[0])):
                matched.add(n.left.value)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) \
                    and n.func.value.id == "re" and n.func.attr in ("search", "match", "fullmatch", "findall") \
                    and len(n.args) >= 2 and isinstance(n.args[0], ast.Constant) \
                    and subj.search(ast.get_source_segment(src, n.args[1])):
                matched.add(n.args[0].value)
        self.assertIn("a password is required", matched)  # the finder finds
        self.assertIn("Stopping |Stopped |Failed with result|Deactivated", matched)
        self.assertEqual(sorted(own - matched), [], "an exemption no longer matched: take it out")
        self.assertEqual(sorted(matched - listed - own), [], "matched in the code, not on docs/phrases.md")
        self.assertEqual(sorted(listed - matched), [], "on docs/phrases.md, no longer matched in the code")

    def test_G6_28_a_warning_on_standard_error_is_never_part_of_an_answer(self):
        # R168 (2026-11-24; family rule 30's last paragraph, "a warning is not part of the answer"): every
        # run through Host joined standard output and standard error, so a warning (seen: sudo's "unable to resolve
        # host", the command run and its exit passed on) reached every parse: setup stopped at "mode sudo: unable
        # to resolve host ... 750". A parse reads standard output; standard error is kept for the words shown. Each
        # world is run with every answer on its own stream and a warning line on standard error at EVERY command
        # (sudo and plain): the same lines and the same end as without it. The whole suite is run so too, below.
        def worlds():
            host, sha = setup_host()
            fresh_look(host)
            yield "setup", host, run_setup(setup_gh(sha), host)
            host, sha = doctor_world()
            yield "doctor", host, run_doctor(host, sha)
            yield "status", host, run_cmd(M.status, REPO, host=host, gh=setup_gh(sha))
            yield "watch", host, run_cmd(M.watch, host=host, gh=setup_gh(sha))
            gh, world, host = remove_setup()
            yield "uninstall", host, run_uninstall(gh, host)
            gh, world, host = remove_setup()
            yield "uninstall --all", host, run_uninstall(gh, host, repo=None, everything=True)
        def seen(streams):
            was = os.environ.get("MINIONS_TEST_STREAMS")
            os.environ["MINIONS_TEST_STREAMS"] = streams
            try:
                # the clock read as it runs (status's "until 01:45:51Z") is not the streams' business: two runs a
                # second apart read two times (seen on CI, 3.10, ddc45e4), so it is compared as HH:MM:SS
                return {name: (code, [re.sub(r"(?<!\d)\d\d:\d\d:\d\dZ", "HH:MM:SSZ", x.replace(host.state_dir(), "STATE"))
                                      for x in lines])
                        for name, host, (code, lines) in worlds()}
            finally:
                if was is None:
                    os.environ.pop("MINIONS_TEST_STREAMS")
                else:
                    os.environ["MINIONS_TEST_STREAMS"] = was
        plain, warned = seen("0"), seen("1")
        for name in plain:
            self.assertEqual(warned[name], plain[name], name)
        self.assertEqual(plain["setup"][0], M.EXIT_OK, plain["setup"][1])
        # Standard output is the answer; both streams only through said(), for words.
        o = M.Out("750\n", "sudo: unable to resolve host box-1: Name or service not known\n")
        self.assertEqual(o.strip(), "750")
        self.assertIn("unable to resolve host", M.said(o))
        self.assertEqual(M.last_line(M.Out("", "sudo: unable to resolve host x\nFailed to enable unit: Access denied\n")),
                         "Failed to enable unit: Access denied")
        self.assertEqual(M.mask(M.Out("a s3cr3t", "b s3cr3t"), "s3cr3t").err, "b ***")

    @unittest.skipIf(os.environ.get("MINIONS_TEST_STREAMS") == "1", "this is the run it starts")
    def test_G6_28_the_whole_suite_with_a_warning_at_every_command(self):
        # R168: every test of this file run again with every answer on its own stream and a warning line on standard
        # error at every command: each world the suite has, the same end.
        env = dict(os.environ, MINIONS_TEST_STREAMS="1")
        p = subprocess.run([sys.executable, "-m", "unittest", "test_minions"], cwd=os.path.dirname(
            os.path.abspath(__file__)), env=env, capture_output=True, text=True, timeout=900)
        last = [x for x in p.stderr.splitlines() if x.strip()][-3:]
        self.assertEqual(p.returncode, 0, "\n".join(p.stderr.splitlines()[-40:]))
        self.assertTrue(last and last[-1].startswith("OK"), last)

    def test_G6_27_stopped_is_said_only_from_a_word_seen_after_a_real_stop(self):
        # R167 (rule 30): after the stop and no process left, the service's is-active word decides: "inactive" (every
        # word our run logs printed after a real stop: 107 of 107) and "failed" are stopped; a running word is not;
        # anything else, and a systemctl that could not reach the manager, is "could not be read".
        PG = ("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner.")
        for ans, ok, words in (((3, "inactive\n"), True, "the service is inactive"),
                               ((3, "failed\n"), True, "the service is failed"),
                               ((0, "active\n"), False, "reads active after its stop"),
                               ((0, "deactivating\n"), False, "reads deactivating after its stop"),
                               ((1, "Failed to connect to bus: No such file or directory\n"), False,
                                "could not be read (systemctl answered code 1: Failed to connect to bus"),
                               ((0, ""), False, "could not be read (systemctl answered code 0: nothing)")):
            host = setup_host(runs={PG: (1, ""), ("systemctl", "is-active", UNIT): ans})[0]
            rep = M.Report(Out())
            self.assertEqual(M.stop_runner(host, UNIT, FOLDER, rep, drained=True), ok, ans)
            self.assertIn(words, folded(rep.lines)[-1])

    def test_G6_27_a_watch_unit_not_read_gone_ends_the_run_and_its_stop_is_sent_every_run(self):
        # R167 (rule 30): a watch unit read still there after its removal: that run removes nothing more (the slice,
        # the tool's own folders and copy, the records and the runner user stay); and the stop to the watch's units is
        # sent on every run, file or no file, so the same command run again can finish.
        W_SVC = f"{M.WATCH_UNIT}.service"
        gh, world, host = remove_setup()
        host._answer = (lambda real: lambda a, i=None: (0, "LoadState=loaded\nActiveState=inactive\n"
                                                           f"FragmentPath=/etc/systemd/system/{W_SVC}\n")
                        if list(a[:3]) == ["systemctl", "show", W_SVC] else real(a, i))(world.answer)
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertIn(f"{W_SVC} is still there after its removal", lines[-1])
        self.assertIn(SLICE_FILE, world.paths)
        self.assertIn(M.HOOKS_ROOT, world.paths)
        self.assertTrue(world.user)
        self.assertNotIn(f"stop {M.SLICE}", world.order)
        self.assertNotIn(f"/etc/systemd/system/{W_SVC}", world.paths)  # its file went before the read
        n = len(world.order)
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertGreaterEqual(world.order[n:].count(f"stop {W_SVC}"), 2, world.order[n:])  # watch_off and the removal

    def test_G6_27_a_warning_of_sudo_is_never_taken_for_the_answer(self):
        # R167 (2026-11-24): sudo on a computer whose name does not resolve writes "sudo: unable to
        # resolve host NAME: ..." and runs the command, its exit passed on (seen: sudo 1.9.15p5, Ubuntu 24.04). Taken
        # for "could not be read", every absent path read "there": an uninstall that never finished. The answer is
        # the test's own mark; neither sudo's words nor its exit is taken for it.
        WARN = "sudo: unable to resolve host box-1: Name or service not known\n"
        said = {}
        for warn in ("", WARN):  # the same world with and without the warning: the same lines, the same end
            gh, world, host = remove_setup()
            host.sudo_warning = warn
            one = run_uninstall(gh, host)
            every = run_uninstall(gh, host, repo=None, everything=True)
            said[warn] = (one, every, sorted(world.paths), world.user)
        self.assertEqual(said[""][0][0], M.EXIT_OK, said[""][0][1])
        self.assertFalse(said[""][3])
        self.assertEqual(said[WARN], said[""])

    def test_G6_27_an_answer_that_is_not_the_list_is_never_none(self):
        # R167 (rule 6, the uninstall road's other reads, each failed in turn): root's test -e says "not there" only
        # with exit 1 and nothing written (sudo's own refusal is exit 1 with its words); atq's list only with exit 0;
        # find's only with exit 0 or 1 (1: a folder it may not enter, on any stock /tmp); systemd's unit list only
        # with exit 0. Anything else is "could not be read", and the runner user is not deleted over it.
        host = setup_host()[0]
        P = "/etc/systemd/system/x.service"
        for ans, want in (((0, ""), True), ((1, ""), False), ((1, "sudo: unable to resolve host box: Name or service "
                                                                   "not known\n"), None),
                          ((2, "test: extra argument\n"), None), ((127, "Command timed out"), None)):
            host.runs[("sudo", "-n", "test", "-e", P)] = ans
            self.assertIs(M.root_test(host, P), want, ans)
            self.assertEqual(M.exists_as_root(host, P), want is not False, ans)
        def with_(world, host, match, ans):
            host._answer = (lambda real: lambda a, i=None: ans if match(list(a)) else real(a, i))(world.answer)
        for match, ans, words in (
                (lambda a: a[-1:] == ["atq"], (1, "Can't open /var/run/atd.pid to signal atd. No atd running?\n"),
                 "its at jobs (could not be read: atq answered code 1"),
                (lambda a: "find" in a and a[-1] == "-print0", (2, "find: '/tmp': Input/output error\n"),
                 "(could not be read: find answered code 2")):
            gh, world, host = remove_setup()
            host._which.add("atq")
            with_(world, host, match, ans)
            code, lines = run_uninstall(gh, host)
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertIn(f"the user {M.RUNNER_USER} is not deleted", lines[-1])
            self.assertIn(words, lines[-1])
            self.assertTrue(world.user)
        # find's exit 1 with its list is the list (seen on every stock /tmp): the leave goes on.
        gh, world, host = remove_setup()
        with_(world, host, lambda a: "find" in a and a[-1] == "-print",
              (1, "find: '/tmp/systemd-private-x': Permission denied\n"))
        code, lines = run_uninstall(gh, host)
        self.assertEqual(code, M.EXIT_OK, lines)
        # The census: a unit list systemd did not give is said not read, never "none".
        gh, world, host = remove_setup()
        with_(world, host, lambda a: a[:2] == ["systemctl", "list-units"], (1, "Failed to connect to bus: x\n"))
        rep = M.Report(Out())
        M.census(gh, host, [REPO], False, rep)
        self.assertTrue([x for x in rep.lines if "census: systemd's units of ours: not read (systemctl answered code 1"
                         in x], rep.lines)

    def test_G6_2_uninstall_all_leaves_nothing_and_offers_the_workflows_back(self):
        gh, world, host = remove_setup()
        state = host.state_dir()
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_NEED, lines)
        self.assertEqual(world.paths - set(M.PUBLIC_TMP), set())
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
        row = json.dumps({"version": 2, "plugins": {"repo-minions@the-catalog": [
            {"installPath": "/home/you/.claude/plugins/cache/the-catalog/repo-minions/0.1.1", "version": "0.1.1"}]}})
        no_row = json.dumps({"version": 2, "plugins": {"repo-minions@the-catalog": []}})
        for src, files, there, said in (
                ({"clone": clone}, {}, True, f"ok census: the folder this skill was downloaded into: {clone}, still "
                                             "there; it is yours to keep or delete, and this tool does not touch it"),
                ({"clone": clone}, {}, False, f"ok census: the folder this skill was downloaded into ({clone}): gone"),
                (plugin, {lst: row}, None, "ok census: the skill repo-minions@the-catalog: still installed in Claude "
                                           "Code (its own list names it); Claude Code's own plugin uninstall takes "
                                           "it off that list and leaves its folder in Claude Code's cache; this tool "
                                           "touches neither"),
                (plugin, {lst: no_row}, None, "warn census: whether the skill repo-minions@the-catalog is still "
                                              f"installed in Claude Code: not read (its own list, {lst}, could not be read)"),
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
        # Something left is named as a warn, never "none".
        gh, world, host = remove_setup()
        gh.runner = dict(gh.runner, name="someone-elses-runner")
        world.user = True
        M.census(gh, host, [REPO], True, M.Report(Out()))
        out = Out()
        M.census(gh, host, [REPO], True, M.Report(out))
        self.assertIn(f"warn census: runners on GitHub for {REPO}: someone-elses-runner (not ours)", out.getvalue())
        self.assertIn("warn census: the runner user: minions-runner", out.getvalue())

    def test_G6_4_uninstall_again_finishes_an_interrupted_removal(self):
        # Cut right after the GitHub side was removed, at the first delete.
        gh, world, host = remove_setup(interrupt_on=lambda a: a[:6] == ["sudo", "-n", "-u", "minions-runner", "--", "find"]
                                          and "-delete" in a)  # R136: the runner user's first delete
        code = M.main(["uninstall", "--all"], host=host, out=Out(), gh=gh)
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
        self.assertEqual(world.paths - set(M.PUBLIC_TMP), set())
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
        # Seen on a stock VM (2026-11-23): sudo's permission ran out during the runner's configuration; setup again
        # stopped on the half-made folder and sent to uninstall, which said there was nothing to uninstall.
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
        self.assertIsNone(M._LOCK["fd"], "the lock is let go at the stop")
        self.assertEqual(M.load_state(host)["setting_up"][REPO]["folder"], FOLDER)
        # uninstall finds it, never "nothing to uninstall", and takes it away, read back.
        out = Out()
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
        # (Seen on the VM: an uninstall that stopped to ask for the password before it removed anything still
        # leaves the cut setup to be found by setup again.)
        sudo_true = host.runs[("sudo", "-n", "true")]
        host.runs[("sudo", "-n", "true")] = (1, "sudo: a password is required\n")
        self.assertEqual(M.uninstall(REPO, host=host, gh=gh, out=Out(), interactive=False), M.EXIT_NEED)
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
        rep = M.Report(Out())
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
        src = _read(M.__file__)
        ast.parse(src, feature_version=(3, 8))
        host = FakeHost()
        out = Out()
        with mock.patch.object(M.sys, "version_info", (3, 8, 10, "final", 0)):
            code = M.main(["look", REPO], host=host, out=out)
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue(out.getvalue().startswith("stop this tool needs Python 3.10 or newer, and this computer's "
                                                  "python3 is 3.8.10; nothing was changed."), out.getvalue())
        self.assertIn("Ubuntu 22.04 or newer, Debian 12 or newer", out.getvalue())
        self.assertEqual(host.ran, [])
        self.assertNotIn(" | {", src.replace(" ", " "))  # no dict union operator (3.9+) in the tool

    def test_G4_7_doctor_never_passes_one_copy_from_a_read_that_could_not_answer(self):
        # Row R171 (AN ERROR IS NEVER AN ABSENCE), read in the code 2026-11-24: doctor took a pgrep that could not answer as no copy of
        # the runner running, so its one-copy check passed unread. Now it is named not read, with what pgrep
        # answered, and the last line never counts it as kept.
        host, sha = doctor_world()
        host.runs[("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner.Listener")] = (2, "pgrep: cannot read /proc\n")
        code, lines = run_doctor(host, sha)
        self.assertIn(f"warn {REPO}: never two copies of the runner: not read (pgrep answered code 2: pgrep: cannot "
                      "read /proc)", lines)
        self.assertEqual(lines[-1], "ok every promise of the install read back as kept, of those that could be read "
                                    f"(not read: {REPO} one copy)")
        # Two copies read: still a promise not kept
        host, sha = doctor_world()
        host.runs[("pgrep", "-u", "minions-runner", "-f", f"{FOLDER}/bin/Runner.Listener")] = (0, "11\n12\n")
        code, lines = run_doctor(host, sha)
        self.assertTrue(lines[-1].startswith("stop 1 promise(s) not kept here:"), lines[-1])

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
        plan = ("look", "setup", "move", "lint", "status", "watch", "hold", "resume", "drain", "doctor", "restore",
                "uninstall", "self-update")
        sub = next(a for a in M.parser()._actions if isinstance(a, M.argparse._SubParsersAction))
        self.assertEqual(tuple(sub.choices), M.COMMANDS)
        self.assertTrue(set(M.COMMANDS) <= set(plan), M.COMMANDS)
        self.assertEqual(list(M.COMMANDS), sorted(M.COMMANDS, key=plan.index))
        # Six line kinds; the worst decides the code: 0 ok/warn/tell, 2 stop, 3 need you, 4 waiting.
        self.assertEqual(M.LINE_KINDS, ("ok", "warn", "stop", "need you", "waiting", "tell the person"))
        cases = [((), 0), (("warn",), 0), (("tell",), 0), (("wait",), 4), (("wait", "need"), 3),
                 (("wait", "need", "stop"), 2)]
        for kinds, code in cases:
            rep = M.Report(Out())
            rep.ok("x")
            for k in kinds:
                getattr(rep, k)("x")
            self.assertEqual(rep.code(), code, kinds)
            for line in rep.lines:  # a block's head and foot lines (G7.16) begin with -----
                self.assertTrue(any(line.startswith(k) for k in (*M.LINE_KINDS, "----- ")), line)
        # P1a: every need-you line is exit 3, also one that lets the steps after it run (an agent trusting exit
        # codes went on past an ask); only a password typed at once, in this same run, is not an open ask.
        for kw, code in (({"blocks": False}, M.EXIT_NEED), ({"blocks": False, "answered_here": True}, M.EXIT_OK)):
            rep = M.Report(Out())
            rep.need("x", **kw)
            rep.ok("y")
            self.assertEqual(rep.code(), code, kw)
        # The whole table: every way a command ends has its own code.
        self.assertEqual((M.EXIT_OK, M.EXIT_FAILED, M.EXIT_STOP, M.EXIT_NEED, M.EXIT_WAIT, M.EXIT_USAGE,
                          M.EXIT_INTERRUPTED), (0, 1, 2, 3, 4, 64, 130))
        # 64: typed wrong (an unknown command, a missing name, an unknown option), never 2.
        for argv in (["nonsense"], ["look"], ["setup", "o/r", "--no-such-option"], []):
            with mock.patch("sys.stderr", Out()) as err, self.assertRaises(SystemExit) as e:
                M.main(argv, host=linux_host())
            self.assertEqual(e.exception.code, 64, argv)
            self.assertIn("typed wrong", err.getvalue())
        # A command's own code passes through.
        host = linux_host()
        with mock.patch.object(M, "look", return_value=M.EXIT_NEED):
            self.assertEqual(M.main(["look", REPO], host=host, out=Out()), 3)
        # 1: the tool failed. One stop line names the error and the journal's last step.
        M.journal(host, "register", "o/r as box-1")
        out = Out()
        with mock.patch.object(M, "setup", side_effect=RuntimeError("a stand-in surprise")):
            self.assertEqual(M.main(["setup", REPO], host=host, out=out), 1)
        line = out.getvalue().strip()
        self.assertTrue(line.startswith("stop the tool itself failed: RuntimeError: a stand-in surprise"), line)
        self.assertIn("The journal's last step: register o/r as box-1", line)
        # 130: interrupted. The journal says which step it was on.
        out = Out()
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
    """GitHub as a watch sees it when gh cannot be run: no HTTP answer at all, and why."""

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
    HOOKS.append((test_of_stack(), (r.stdout + r.stderr).split("\n")))  # rule 31 (v): read by ZRule31
    return r.returncode, r.stdout + r.stderr


class P1a(unittest.TestCase):
    """What the two cold starts (P1a, 2026-11-22) found: each fix and its test."""

    def test_G4_9_the_delay_said_is_the_one_this_computer_gives(self):
        # R48: the words name logind's own delay, read without admin rights, never a fixed number.
        def move_words(host):  # G7.14: look reads the delay; move says it, from look's record of this computer
            lines = look(StandIn(load("look-recent-runs.jsonl")), host)[1]
            survey = next(iter(M.load_state(host)["looks"].values()))["survey"]
            rep = M.Report(Out())
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
        self.assertIn("This computer has a battery: " + as_said(M.laptop_words(REPO)) + ".", tells_of(lines))
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
            self.assertIn(words if words is limits else as_said(words), said)
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
        out = Out()
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
        out = Out()
        self.assertEqual(M.uninstall(None, everything=True, host=host, gh=gh, out=out, interactive=False), M.EXIT_NEED)
        self.assertIn(f"run `python3 -I {M.tool_path()} uninstall --all` there", out.getvalue().splitlines()[-1])

    def test_G8_20_a_printed_command_line_quotes_every_path_a_folder_with_a_space(self):
        # R175 (2026-11-24): every path in a printed command line is quoted for the shell, so
        # a copy of the tool in a folder with a space in its name is one argument when the line is pasted as printed.
        spaced = "/home/you/my projects/windstorm-repo-minions/scripts/minions.py"
        W_SVC = f"{M.WATCH_UNIT}.service"
        def argv_of(line, sub):
            cmds = [c for c in re.findall(r"`([^`]+)`", line) if sub in c]
            self.assertTrue(cmds, line)
            return shlex.split(cmds[0])
        bus = (1, "Failed to connect to bus: No such file or directory\n")
        with mock.patch.object(M, "tool_path", lambda: spaced):
            # the stop of uninstall --all, the watch's service unread
            gh, world, host = remove_setup()
            host._answer = (lambda real: lambda a, i=None: bus if list(a[:3]) == ["systemctl", "show", W_SVC]
                            else real(a, i))(world.answer)
            code, lines = run_uninstall(gh, host, repo=None, everything=True)
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertEqual(argv_of(lines[-1], "uninstall --all"), ["python3", "-I", spaced, "uninstall", "--all"])
            # the stop of uninstall <repo>, the runner's service unread
            gh, world, host = remove_setup()
            host._answer = (lambda real: lambda a, i=None: bus if list(a[:3]) == ["systemctl", "show", UNIT]
                            else real(a, i))(world.answer)
            code, lines = run_uninstall(gh, host)
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertEqual(argv_of(lines[-1], f"uninstall {REPO}"), ["python3", "-I", spaced, "uninstall", REPO])
            self.assertEqual(shlex.split(M.tool_cmd()), ["python3", "-I", spaced])
            # the step when the installed copy may be gone: the clone setup was run from, a space in its name
            step = M.copy_gone_step({"clone": "/home/you/my projects/windstorm-repo-minions"})
            self.assertEqual(shlex.split(as_said(step).strip("`")), ["python3", "-I", spaced, "uninstall", "--all"])
            self.assertEqual(shlex.split(as_said(M.copy_gone_step({})).strip("`")), ["python3", "-I", spaced, "uninstall",
                                                                           "--all"])

    def test_G8_23_a_printed_step_never_holds_a_name_in_angle_brackets(self):
        # R193 (family rule 7, the maintainer's ruling 2026-11-25): a printed step is taken as printed. Where the tool
        # knows the repository, the step carries it as one shell word; where it does not, the step holds the plain
        # word OWNER/REPO, the line's own words say to put the name there, and a paste as printed ends in this tool's
        # own stop line, never in a shell's.
        # (1) every string the tool can print (docstrings left out): no name in angle brackets anywhere
        import ast
        with open(M.__file__, encoding="utf-8") as f:
            src = f.read()
        docs, tree = set(), ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body \
                    and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                docs.add(id(node.body[0].value))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
                found += [(node.lineno, m) for m in re.findall(r"<[a-z][a-z0-9 /%:_.-]*>", node.value)]
        self.assertEqual(found, [], "a name in angle brackets in a string the tool prints")
        # (2) the placeholder itself, pasted with any command that takes a repository: the tool's own stop line,
        # before anything is read or changed
        for argv in (["look", "OWNER/REPO"], ["setup", "OWNER/REPO"], ["drain", "OWNER/REPO"], ["resume", "OWNER/REPO"],
                     ["status", "OWNER/REPO"], ["move", "OWNER/REPO"], ["doctor", "OWNER/REPO"],
                     ["uninstall", "OWNER/REPO"]):
            host, out = FakeHost(), Out()
            code = M.main(argv, host=host, out=out, gh=StandIn({}))
            lines = out.getvalue().splitlines()
            self.assertEqual(code, M.EXIT_STOP, (argv, lines))
            self.assertTrue(lines and lines[0].startswith("stop OWNER/REPO is the placeholder of a step this tool "
                                                          "printed"), (argv, lines))
            self.assertEqual(host.ran, [], f"{argv}: something was run")
            self.assertEqual(os.listdir(host.state_dir()), [], f"{argv}: something was written")
        def steps(text):
            return [shlex.split(c) for c in M._COMMAND.findall(text)]
        def pasted(argv):  # a step as printed, run: the tool's own answer (its stop line, or its parser's refusal)
            out, err = Out(), io.StringIO()
            with mock.patch("sys.stderr", err):
                try:
                    code = M.main(argv, host=FakeHost(), out=out, gh=StandIn({}))
                except SystemExit as e:
                    code = e.code
            return code, out.getvalue() + err.getvalue()
        lim = M.slice_limits({"cores": 4, "mem": 8 * 1024 ** 3})
        host = FakeHost()
        host.read = lambda p: None
        known = {
            "look --start-test": M.zero_job_words(REPO), "look --show-names": None,
            "resume --on-battery-too": M.laptop_words(REPO), "setup --limits": M.limits_in_words(lim, REPO),
            "setup (the watch's copy)": M.copy_version_line(host, REPO)[1],
            "move --include-job": M.pr_body([], [], [("w.yml", "deploy now", "it deploys")], [], repo=REPO),
        }
        # (3) a known repository: the step carries it as one shell word
        for what, text in known.items():
            if text is None:
                continue
            got = steps(text) or [shlex.split(c) for c in re.findall(r"`(move [^`]+)`", text)]
            self.assertTrue(got, (what, text))
            self.assertEqual(got[0][1], REPO, (what, text))
            self.assertNotIn("OWNER/REPO", text, what)
        self.assertEqual(shlex.split(re.findall(r"`(move [^`]+)`", known["move --include-job"])[0])[2:],
                         ["--include-job=w.yml:deploy now"])  # R200: one word, joined by an equals sign
        # (4) not known: the plain word, the line's own words saying to put the name there, and the paste as printed
        # ends in this tool's own refusal
        unknown = [M.zero_job_words(None), M.laptop_words(None), M.limits_in_words(lim, None),
                   M.copy_version_line(host, None)[1]]
        for text in unknown:
            self.assertIn("OWNER/REPO", text)
            self.assertIn("your repository's name (owner/name) in place of OWNER/REPO", text)
            for argv in steps(text):
                code, said = pasted(argv)
                if "--limits" in argv:
                    self.assertEqual(code, M.EXIT_USAGE, said)
                    self.assertIn("write --limits PROCESSORS,PERCENT", said)
                else:
                    self.assertEqual(code, M.EXIT_STOP, said)
                    self.assertTrue(said.startswith("stop OWNER/REPO is the placeholder"), said)
        # status on a computer with no runner of ours: its step holds the plain word and says to put the name there
        out = Out()
        M.status(None, host=FakeHost(), gh=StandIn({}), out=out)
        said = out.getvalue()
        self.assertIn("look OWNER/REPO", said)
        self.assertIn("your repository's name (owner/name) in place of OWNER/REPO", said)

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
            rep = M.Report(Out())
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
        out = Out()
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
        out = Out()
        code = M.setup(REPO, host=host, gh=setup_gh(sha), out=out, limits=(2, 40))
        self.assertEqual(code, M.EXIT_STOP, out.getvalue())
        self.assertIn("the new limits are not in force", out.getvalue())

    def test_G6_9_a_runner_whose_setup_was_cut_before_its_unit_was_recorded_is_stopped_by_name(self):
        # Seen on a stock VM (2026-11-23): uninstall after a setup cut before it recorded the unit printed
        # "the service is Failed to mangle name: Invalid argument" inside an ok line.
        unit = "actions.runner.example-org-example-repo.minions-box-1.service"
        host = FakeHost(runs={("sudo", "-n", "-u", "minions-runner", "--", "cat", f"{FOLDER}/.service"): (0, unit + "\n"),
                              ("systemctl", "is-active", unit): (3, "inactive\n")})
        out = Out()
        self.assertTrue(M.stop_runner(host, "", FOLDER, M.Report(out), True))
        self.assertIn(("sudo", "-n", "systemctl", "stop", unit, "--no-pager"), host.ran)
        self.assertIn("ok the runner is stopped: the service is inactive", out.getvalue())
        self.assertFalse([a for a in host.ran if "" in a[1:]], host.ran)  # never an empty unit name
        # No service file either: said as such, and nothing run against an empty name.
        host = FakeHost()
        out = Out()
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
                if list(argv) == ["pgrep", "-u", "1001"]:  # R172: by the runner user's number
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
                                   "give jobs more memory: `setup example-org/example-repo --limits PROCESSORS,"
                                   "PERCENT`, with the number of processors and the percent of memory in place of "
                                   "PROCESSORS and PERCENT")
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
        # Blind (gh not found): each pass ends stop, with the count and why, never in silence.
        for n in (1, 2, 3):
            clock.sleep(300)
            code, lines = run_cmd(M.watch, host=host, gh=BlindGH())
            self.assertEqual(code, M.EXIT_STOP, lines)
            self.assertIn(f"{n} pass(es) in a row", lines[-1])
            self.assertIn("could not be found with this PATH", lines[-1])
        self.assertIn(f"the job hook here refuses each job of it before its first step {M.HOOK_CAVEAT}", lines[-1])
        self.assertIn("run setup again from a terminal where gh works", lines[-1])
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
        # keyring, and the watch goes blind again), with look first to say what that keeps where
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
            M.watch(host=host, gh=BlindGH(), out=Out())
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
        # drain's wait ends at once with a waiting line instead of polling into the limit.
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
            self.assertIn("ended with a stop", lines[-1])
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
        out = Out()
        code = M.doctor(REPO, host=host, gh=setup_gh(sha, {listing: none}), out=out, interactive=False)
        self.assertEqual(code, M.EXIT_NEED, out.getvalue())
        self.assertIn(f"then run `doctor {REPO}` again", out.getvalue())
        self.assertIn(REPO, M.load_state(host)["setting_up"])
        host.runs[("sudo", "-n", "true")] = (0, "")
        out = Out()
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
        gh, world, host = remove_setup(interrupt_on=lambda a: a[:6] == ["sudo", "-n", "-u", "minions-runner", "--", "find"]
                                          and "-delete" in a)  # R136: the runner user's first delete
        self.assertEqual(M.main(["uninstall", REPO], host=host, out=Out(), gh=gh), M.EXIT_INTERRUPTED)
        self.assertTrue(M.load_state(host)["runners"][REPO]["removing"])
        out = Out()
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
        out = Out()
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
        # Every waiting line in the tool names the command to run next, or is one workflow's line that `move
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
                if "{C0}" not in text and "cmd(" not in text and not per_path:  # rule 31 (B): a marked command
                    bad.append((n.lineno, text[:80]))
        self.assertEqual(bad, [])


class G8Words(unittest.TestCase):
    NEXT = re.compile(r"`|\bagain\b|\bfirst\b|\bcheck\b|\bwrite owner|\bno step to take\b|\bopen an issue\b|\bask\b|"
                      r"\btype\b|\bpress Enter\b|\bFree some space\b|\bmerge\b|\bsettle\b|\bway out\b|\brun\b|\binstall\b", re.I)

    def test_G8_12_every_stop_and_need_you_line_names_what_happened_and_the_next_step(self):
        # Run B again: move stopped on "could not read it, so nothing is moved", with no answer and no way out, and a
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
                steps = {"settle_first", "came_back", "github_next", "unit_next", "kept_home_step", "no_road_words"}
                if any(isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id in steps
                       for x in ast.walk(a)):
                    continue  # rule 7: the step is built by one of the step helpers, each checked below
                # rule 31 (B): a command the person types is marked (C0, or cmd()); it is read here as a backtick
                words = " ".join("`" if isinstance(x, ast.Name) and x.id in ("C0", "C1") else
                                 "`" if isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "cmd"
                                 else x.value for x in ast.walk(a)
                                 if isinstance(x, ast.Constant) and isinstance(x.value, str) or
                                 isinstance(x, ast.Name) and x.id in ("C0", "C1") or
                                 isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "cmd")
                if words.strip() == "start test:":
                    continue  # its words are zero_job_words(), checked below
                # R155: a line that opens with another program's command in backticks (`systemctl daemon-reload`
                # failed: ...) named no step, yet its backtick passed as one. The command it quotes is not a step.
                if not self.NEXT.search(re.sub(r"^\s*`[^`]*`", "", words)):
                    lacking.append((n.lineno, words[:80]))
        self.assertEqual(lacking, [])
        for built in (M.settle_first(), M.came_back("x", "y", "z"), M.github_next(500, REPO), M.github_next(403, REPO),
                      M.unit_next("u", ("unread", "w"), "c"), M.unit_next("u", ("running", "active"), "c"),
                      M.zero_job_words(REPO), M.zero_job_words(None), M.public_words(REPO, {"time": "t"}),
                      M.no_road_words("c", None), M.no_road_words("c", "w"),
                      M.memory_floor_line(REPO, {"MemoryMax": 1, "pct": 60, "mem": 2 * 1024 ** 3, "cores": 1}),
                      M.memory_floor_line(REPO, {"MemoryMax": 1, "pct": 60, "mem": 16 * 1024 ** 3, "cores": 1})):
            self.assertTrue(self.NEXT.search(as_said(built)), built)
        # GitHub's own answer is named: move's stop on a workflow file it could not read (seen in run B again).
        class FileErr(StandIn):
            def api(self, method, path, body=None):
                if method == "GET" and path.endswith("/contents/.github/workflows"):
                    return 200, [{"name": "mac.yml", "path": ".github/workflows/mac.yml"}]
                return 502, {"message": "Server Error"}
        rep = M.Report(out=Out())
        self.assertIsNone(M.read_workflow_files(FileErr([]), REPO, None, rep))
        self.assertEqual(folded(rep.lines)[-1], ".github/workflows/mac.yml: could not read it (GitHub answered HTTP 502); "
                                        "nothing is changed. Run the same command again; if it stops here again, "
                                        "GitHub's answer named here is the reason".join(["stop ", ""]))

    def test_G8_13_a_reader_that_stops_reading_never_cuts_a_command_short(self):
        # Seen: `look ... | head -2` ended in a BrokenPipeError traceback. A closed pipe stops the printing only: the
        # command finishes its work and its exit code, with no traceback.
        r, w = os.pipe()
        os.close(r)
        out = os.fdopen(w, "w")
        def close_quietly():  # R177: closed at the end; its buffer may still hold words for the closed pipe
            try:
                out.close()
            except OSError:
                pass
        self.addCleanup(close_quietly)
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
        # a program other than gh: the environment as it is, in the C language (R172)
        self.assertEqual((seen[3][1].get("LC_ALL"), seen[3][1].get("HTTPS_PROXY"), seen[3][1].get("GH_TOKEN")),
                         ("C.UTF-8", "http://proxy.invalid:3128", "made-up-token-value"))
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
        # look: the stop, where gh keeps it, the one step, and six things to hear, before GitHub is asked anything
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
        self.assertEqual(len(tells), 6, lines)
        # owner 11-24 (run59): the git question, its own line, fifth; the choice stays last
        self.assertIn('If gh asks "Authenticate Git with your GitHub credentials?": this tool does not need it. '
                      'Answered Yes (gh\'s first choice), gh also makes itself git\'s helper for github.com and '
                      'gist.github.com, two settings in ~/.gitconfig, and git on this computer then uses this login too; '
                      'answered No, gh leaves git as it is.',
                      tells[4])
        self.assertIn("If you would rather not, stop here", tells[5])
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
        self.assertIn("gh names no place where it keeps one here, and it may have none saved yet",  # R204: also true
                      next(x for x in lines if x.startswith("stop ")))  # of a gh never logged in
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
                       (127, M.Out("", "[Errno 2] No such file or directory: 'gh'"))):
            self.assertEqual(kept(answer)[0], "unread", answer)
        # R204: planted as the real Host gives it (Python's words on the err side), and not called gh's: gh did not run
        not_run = kept((127, M.Out("", "[Errno 2] No such file or directory: 'gh'")))[1]
        self.assertIn("could not be found", not_run)
        self.assertIn("'[Errno 2] No such file or directory: ", not_run)
        self.assertNotIn("standard error", not_run)
        self.assertNotIn("standard output", not_run)
        self.assertNotIn("ghp_made_up_value", kept((1, "ghp_made_up_value\nsomething else\n"))[1])
        old = (1, M.Out("", "unknown flag: --hostname\n\nUsage:  gh auth <command> [flags]\n"))  # R204: as gh 2.4.0 says it
        h = linux_host(runs={("gh", "auth", "token"): old})
        path = os.path.join(h.gh_env()["GH_CONFIG_DIR"], "hosts.yml")
        h.files[path] = "github.com:\n    oauth_token: made-up-login-value\n    user: example-person\n"
        self.assertEqual(M.login_kept(h), ("yes", ""))
        h.files[path] = "github.com:\n    user: example-person\n    git_protocol: https\n"
        self.assertEqual(M.login_kept(h)[0], "no")
        del h.files[path]
        self.assertEqual(M.login_kept(h)[0], "no")  # R204: no file is no login (gh 2.4.0 leaves none before a login)
        h.files[path], h.unreadable = "github.com:\n    oauth_token: made-up-login-value\n", {path}
        self.assertEqual(M.login_kept(h)[0], "unread")
        # could not be read: a stop that says so, nothing made, never a login step
        host = linux_host(runs={("gh", "auth", "token"): (1, "error connecting to the keyring\n")})
        lines = look(StandIn([]), host)[1]
        stop = next(x for x in lines if x.startswith("stop "))
        self.assertTrue(stop.startswith("stop gh could not be asked whether it has a login for github.com"), stop)
        self.assertIn("This tool made nothing", stop)
        self.assertNotIn("insecure-storage", stop)

    def test_G1_23_a_gh_with_no_auth_token_is_read_from_its_file_and_a_stop_carries_only_its_error_words(self):
        # R204, seen 2026-11-26 on a stock Ubuntu 22.04 virtual machine with the distribution's gh 2.4.0, each form of
        # the call with its exit and both streams (only gh's error words are planted, nothing else of that machine).
        usage = ("\nUsage:  gh auth <command> [flags]\n\nAvailable commands:\n  login\n  logout\n  refresh\n"
                 "  setup-git\n  status\n")
        flag = (1, M.Out("", "unknown flag: --hostname\n" + usage + "\n"))  # `gh auth token --hostname github.com`
        bare = (1, M.Out('unknown command "token" for "gh auth"\n' + usage, ""))  # `gh auth token`: not asked
        def host_with(answer, hosts):
            h = linux_host(runs={("gh", "auth", "token"): answer})
            path = os.path.join(h.gh_env()["GH_CONFIG_DIR"], "hosts.yml")
            if hosts is not None:
                h.files[path] = hosts
            return h
        login = "github.com:\n    oauth_token: made-up-login-value\n    user: example-person\n"
        none = "github.com:\n    user: example-person\n    git_protocol: https\n"
        # the answer seen: gh's own file is read, in three answers, as R160 promises for a gh older than 2.17; the
        # phrase is one whole line on standard error, so the suite's warning line before or after it changes nothing
        warn = STREAM_WARNING
        for name, seen in (("as seen", flag), ("a warning line before", (1, M.Out("", warn + flag[1].err))),
                           ("a warning line after", (1, M.Out("", flag[1].err + warn)))):
            with self.subTest(name):
                self.assertEqual(M.login_kept(host_with(seen, login)), ("yes", ""))
                self.assertEqual(M.login_kept(host_with(seen, none))[0], "no")
                # no file is no login: gh 2.4.0 leaves no folder of its own before its first login (seen on the guest)
                kept, why = M.login_kept(host_with(seen, None))
                self.assertEqual(kept, "no", why)
                self.assertIn("is not there", why)
                refused = host_with(seen, login)
                refused.unreadable = {os.path.join(refused.gh_env()["GH_CONFIG_DIR"], "hosts.yml")}
                kept, why = M.login_kept(refused)
                self.assertEqual(kept, "unread", why)
                self.assertIn("Permission denied", why)
                self.assertNotIn("made-up-login-value", why)
        # a newcomer who has not logged in: look says the login step, never "running it again changes nothing", and
        # what was read: gh's file is not there (never "could not be read")
        newcomer = host_with(flag, None)
        code, lines = look(StandIn([]), newcomer)
        self.assertTrue(any("gh auth login" in x for x in lines), lines)
        self.assertFalse(any("Running it again changes nothing" in x for x in lines), lines)
        stop = next(x for x in lines if x.startswith("stop "))
        hosts_path = os.path.join(newcomer.gh_env()["GH_CONFIG_DIR"], "hosts.yml")
        self.assertIn(f"when nobody is logged in: {hosts_path} is not there, so gh has no login saved here yet. This "
                      "tool made nothing", stop)
        self.assertNotIn("could not be read", stop)
        # a file there with no login: gh names no place, and the stop says only what is true of it
        code, lines = look(StandIn([]), host_with(flag, none))
        stop = next(x for x in lines if x.startswith("stop "))
        self.assertIn("gh names no place where it keeps one here, and it may have none saved yet", stop)
        self.assertNotIn("could not be read", stop)
        # the real reader tells the three apart: a file not there, a folder not there, a folder it may not enter
        if os.geteuid() != 0:
            with tempfile.TemporaryDirectory() as d:
                self.assertEqual(M.Host().read_checked(os.path.join(d, "hosts.yml")), (None, None))
                self.assertEqual(M.Host().read_checked(os.path.join(d, "gh", "hosts.yml")), (None, None))
                os.mkdir(os.path.join(d, "gh"))
                open(os.path.join(d, "gh", "hosts.yml"), "w").close()
                os.chmod(os.path.join(d, "gh"), 0)
                try:
                    text, why = M.Host().read_checked(os.path.join(d, "gh", "hosts.yml"))
                    self.assertIsNone(text)
                    self.assertIn("Permission denied", why or "")
                finally:
                    os.chmod(os.path.join(d, "gh"), 0o700)
        # any answer not seen for this call is could not be read, never the older road, even with a login in the file
        for name, answer in (("the bare form's words, never asked", bare),
                             ("those words on standard error", (1, M.Out("", bare[1]))),
                             ("the seen words with another exit", (2, flag[1])),
                             ("the seen words on standard output", (1, M.Out(str(flag[1].err), ""))),
                             ("the seen words with a line on standard output", (1, M.Out("a line\n", flag[1].err)))):
            with self.subTest(name):
                self.assertEqual(M.login_kept(host_with(answer, login))[0], "unread")
        # a stop carries gh's first line on standard error, quoted as read; of standard output only its length
        kept, why = M.login_kept(host_with((1, M.Out("", "error connecting to /run/user/1000/bus\n")), login))
        self.assertEqual(kept, "unread")
        self.assertIn("on standard error ", why)
        self.assertIn("'error connecting to /run/user/1000/bus'", why)
        self.assertIn("its standard output, 0 character(s), is not shown", why)
        # the first two lines of standard error, each quoted: a warning line first does not hide gh's own words
        pre = "" if os.environ.get("MINIONS_TEST_STREAMS") == "1" else warn  # the warning world puts it there itself
        kept, why = M.login_kept(host_with((1, M.Out("", pre + "error connecting to /run/user/1000/bus\nthird\n")), login))
        self.assertIn(f"{M.as_read(warn.strip())}, then 'error connecting to /run/user/1000/bus'", why)
        self.assertNotIn("third", why)
        empty = M.login_kept(host_with((1, M.Out("", "")), login))[1]
        if os.environ.get("MINIONS_TEST_STREAMS") == "1":  # the warning world: the warning is the only line there
            self.assertIn(f"on standard error {M.as_read(warn.strip())}; its standard output", empty)
        else:
            self.assertIn("no words on standard error", empty)
        # a value shaped like a token on standard output, exit 1: neither the stop nor any file written holds it
        made_up = "gho_madeUpTokenR204value000"
        host = host_with((1, M.Out(made_up + "\nsecond line\n", "some refusal\n")), login)
        code, lines = look(StandIn([]), host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        stop = next(x for x in lines if x.startswith("stop "))
        self.assertIn("its standard output, 40 character(s), is not shown", stop)
        self.assertIn("'some refusal'", stop)
        self.assertEqual([x for x in lines if made_up in x or "second line" in x], [])
        self.assertEqual([p for p, v in host.files.items() if isinstance(v, (str, bytes)) and
                          (made_up in (v if isinstance(v, str) else v.decode("utf-8", "replace")))], [])

    def test_G1_20_a_login_github_refuses_makes_no_record_on_a_fresh_home(self):
        # R160, owner 11-24: the need to log in comes after the login was read as the watch reads it, and before
        # gh's place is kept; on a home that never held the tool's record, it leaves none, for each of its answers
        for answer in ((4, NO_LOGIN), (1, "HTTP/2.0 401 Unauthorized\n\n{\"message\": \"Bad credentials\"}")):
            host = linux_host(runs={("gh", "api", "-i", "user"): answer})
            code, lines = look(StandIn([]), host)
            self.assertEqual(code, M.EXIT_NEED, lines)
            self.assertTrue(lines[0].startswith("need you: log in to GitHub: run `gh auth login`"), lines)
            self.assertEqual(os.listdir(host.state_dir()), [], f"the need to log in made the tool's record: {answer}")

    def test_G5_27_the_watch_unit_carries_its_values_whole_or_refuses(self):
        # R190: every value in the watch's unit is one value in systemd's grammar, or
        # setup refuses before its first write. systemd reads an Environment= line by expanding its specifiers
        # (a percent sign and a letter; "%%" is one percent sign), then taking one quoted word with C escapes.
        def systemd_reads(line):
            v = line[len("Environment="):]
            out, i = [], 0
            while i < len(v):  # the specifiers: here only "%%" may stand; any other % names something else
                if v[i] == "%":
                    if v[i + 1:i + 2] != "%":
                        return None
                    out.append("%"); i += 2
                else:
                    out.append(v[i]); i += 1
            v = "".join(out)
            if len(v) < 2 or v[0] != '"' or v[-1] != '"':
                return None
            inner, word, i = v[1:-1], [], 0
            while i < len(inner):
                c = inner[i]
                if c == '"':
                    return None  # the word ends before the line does: not one value
                if c == "\\":
                    nxt = inner[i + 1:i + 2]
                    if nxt not in ("\\", '"'):
                        return None  # another C escape would turn into another character
                    word.append(nxt); i += 2
                else:
                    word.append(c); i += 1
            return "".join(word)
        for folders in ([], ["s bin"], ["bin 100%b"], ["p%hbin"], ["d$HOMEbin"], ['q"bin'], ["b\\x41bin"],
                        ["s bin", "p%hbin", "d$HOMEbin", 'q"bin', "b\\x41bin"]):
            path = ":".join([f"/home/you/{f}" for f in folders] + ["/usr/bin"])
            svc, _ = M.watch_units("person", 300, path)
            env = [x for x in svc.splitlines() if x.startswith("Environment=")]
            self.assertEqual(len(env), 1, svc)
            self.assertEqual(systemd_reads(env[0]), "PATH=" + path, f"not carried whole: {env[0]!r}")
            self.assertIn("\nUser=person\n", svc)
        # a folder with a line feed, a login name that is not the account's, a name systemd's User= does not take:
        # setup stops before its first write, in one line that names the value and the next step
        for label, change in (("a line feed in a PATH folder", "lf"), ("LOGNAME is another account", "logname"),
                              ("a login name with a space", "space")):
            host, sha = setup_host()
            if change == "lf":
                host.which = lambda n: "/home/you/b\nin/gh" if n == "gh" else f"/usr/bin/{n}"
            elif change == "logname":
                host._account = "someone-else"
            else:
                host.user = lambda: "per son"
                host._account = "per son"
            fresh_look(host)
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual(code, M.EXIT_STOP, (label, lines))
            stop = [x for x in lines if x.startswith("stop")]
            self.assertTrue(stop and ("Nothing was changed" in stop[-1]), (label, lines))
            self.assertTrue(G8Words.NEXT.search(stop[-1]), (label, stop[-1]))
            self.assertEqual([a for a in host.ran if a[:1] == ("sudo",)], [], f"{label}: an admin step ran")
        # The runner's unit and its drop-in carry the repository's name inside a path (ExecStart, WorkingDirectory,
        # and a shell line in ExecStopPost): setup refuses a name outside GitHub's own letters before any write.
        for bad in ("o/r b", "o/r%b", "o/r$b", 'o/r"b', "o/r\\b", "o b/r", "o/r\nb", "o/r'b", "o/r;b"):
            host, sha = setup_host()
            out = Out()
            code = M.setup(bad, host=host, gh=setup_gh(sha), out=out, interactive=True)
            lines = out.getvalue().splitlines()
            self.assertEqual(code, M.EXIT_STOP, (bad, lines))
            self.assertTrue(lines and lines[0].startswith("stop not a repository name"), (bad, lines))
            self.assertEqual([a for a in host.ran if a[:1] == ("sudo",)], [], f"{bad!r}: an admin step ran")
            self.assertEqual(os.listdir(host.state_dir()), [], f"{bad!r}: setup wrote its record")
        # THE READ-BACK: setup and status compare what systemd parsed, word for word, from its
        # own answer over its bus, and say the watch is set up only when it equals what was meant
        host, sha = setup_host()
        host.which = lambda n: "/home/you/bin 100%b/gh" if n == "gh" else f"/usr/bin/{n}"
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertTrue([x for x in lines if x.startswith("ok the watch runs every") and "read back word for word" in x], lines)
        code, st = run_cmd(M.status, REPO, host=host, gh=setup_gh(sha))
        self.assertIn("ok the units of this tool read back word for word as systemd holds them (2)", st)
        for label, bus in (
                ("the watch: one word more", lambda real: lambda argv, input=None: (
                    (lambda rc, out: (rc, out.replace('"-I",', '"-I", "x",')))(*real(argv, input)))),
                ("the runner: one word more", lambda real: lambda argv, input=None: (
                    (lambda rc, out: (rc, out.replace('runsvc.sh"]', 'runsvc.sh", "x"]')))(*real(argv, input)))),
                ("no bus", lambda real: lambda argv, input=None: (1, "Failed to connect to bus: No such file or directory"))):
            host, sha = setup_host()
            host.runs[M.BUS] = bus(host.runs[M.BUS])
            fresh_look(host)
            code, lines = run_setup(setup_gh(sha), host)
            said_ok = [x for x in lines if "read back word for word" in x]
            self.assertEqual(said_ok, [], (label, lines))
            if label.endswith("one word more"):
                which = "watch" if "watch" in label else "runner"
                self.assertEqual(code, M.EXIT_STOP, lines)
                self.assertTrue([x for x in lines if x.startswith(f"stop the {which}'s unit, as systemd read it, is not what this tool wrote")], lines)
                turned_on = [a for a in host.ran if "enable" in a and (("--now" in a) if which == "watch" else ("--now" not in a))]
                self.assertEqual(turned_on, [], f"{label}: its unit was turned on: {lines}")
                # the step for when the same stop comes back, and nothing of the person's in it
                stop = [x for x in lines if x.startswith(f"stop the {which}'s unit")][0]
                self.assertIn("if this line comes back the same, run", stop)
                self.assertIn("uninstall --all", stop)
            else:
                self.assertTrue([x for x in lines if x.startswith("warn what systemd made of the runner's unit could not be read")], lines)
                self.assertTrue([x for x in lines if x.startswith("warn what systemd made of the watch's unit could not be read")], lines)
                code, st = run_cmd(M.status, REPO, host=host, gh=setup_gh(sha))
                self.assertTrue([x for x in st if x.startswith("warn what systemd made of") and "could not be read" in x], st)
        # a setting of the person's own in the watch's unit (a drop-in with a proxy address holding a
        # password, made up here): named by name only, a warn, never a stop; no line holds its value. A person's
        # drop-in that sets PATH again: the form seen, where a manager lists the variable ONCE with the value written
        # last (the person's), is "its PATH entry is not the value this tool wrote", a stop that prints no value.
        # A SECOND entry for the PATH this tool set is the stand-in's own form, not one a manager was seen to list:
        # it stays a stop, said by its count only, and still no value is printed.
        secret = "made-up-secret-7f3a"
        for label, extra, kind in (("the person's own variable", f"HTTPS_PROXY=http://me:{secret}@proxy:3128", "warn"),
                                   ("the person's PATH, set again", f"PATH=/home/you/{secret}:/usr/bin", "changed"),
                                   ("a second PATH (the stand-in's own form)", f"PATH=/home/you/{secret}:/usr/bin",
                                    "stop")):
            host, sha = setup_host()
            put = ((lambda out, extra: re.sub(r'"PATH=(?:[^"\\]|\\.)*"', lambda m: json.dumps(extra), out, count=1))
                   if kind == "changed" else
                   (lambda out, extra: out.replace('"data": ["PATH=', '"data": [' + json.dumps(extra) + ', "PATH=', 1)))
            host.runs[M.BUS] = (lambda real, extra, put: lambda argv, input=None: (
                (lambda rc, out: (rc, put(out, extra)
                                  if argv[-1] == "Environment" and "minions_2dwatch" not in out else out)
                 if argv[-1] == "Environment" and "minions-watch" in " ".join(argv) else (rc, out))(*real(argv, input))))(
                host.runs[M.BUS], extra, put)
            fresh_look(host)
            code, lines = run_setup(setup_gh(sha), host)
            self.assertEqual([x for x in lines if secret in x], [], f"{label}: a value the tool did not write is printed")
            if kind == "warn":
                self.assertEqual(code, M.EXIT_OK, (label, lines))
                self.assertTrue([x for x in lines if x.startswith("warn systemd holds in minions-watch.service, beside "
                                                                  "what this tool wrote, setting(s) of your own for "
                                                                  "HTTPS_PROXY")], lines)
                code, st = run_cmd(M.status, REPO, host=host, gh=setup_gh(sha))
                self.assertEqual([x for x in st if secret in x], [], st)
                self.assertIn("ok the units of this tool read back word for word as systemd holds them (2)", st)
                self.assertTrue([x for x in st if x.startswith("warn systemd holds in minions-watch.service") and
                                 "HTTPS_PROXY" in x], st)
            elif kind == "changed":
                self.assertEqual(code, M.EXIT_STOP, (label, lines))
                self.assertTrue([x for x in lines if x.startswith("stop the watch's unit, as systemd read it, is not "
                                                                  "what this tool wrote: its PATH entry is not the value "
                                                                  "this tool wrote")], lines)
            else:
                self.assertEqual(code, M.EXIT_STOP, (label, lines))
                self.assertTrue([x for x in lines if x.startswith("stop the watch's unit, as systemd read it, is not "
                                                                  "what this tool wrote: it holds 2 entries for PATH where this tool wrote one")], lines)
        # the account's name that could not be read from its number: refused before the first write
        host, sha = setup_host()
        host.account = lambda: None
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertTrue([x for x in lines if x.startswith("stop the name of this account (number")], lines)
        self.assertEqual([a for a in host.ran if a[:1] == ("sudo",)], [], lines)
        # status compares with what setup wrote into User=, never with this terminal's LOGNAME
        host, sha = setup_host()
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        host.user = lambda: "someone-else"
        code, st = run_cmd(M.status, REPO, host=host, gh=setup_gh(sha))
        self.assertIn("ok the units of this tool read back word for word as systemd holds them (2)", st)
        # no name in the record and none for the number: the watch's unit "could not be read", whatever LOGNAME says
        data = M.load_state(host)
        self.assertEqual(data["watch"].get("user"), "person", "the name setup wrote is kept by the watch's passes")
        data["watch"].pop("user")
        M.save_state(host, data)
        host.account = lambda: None
        code, st = run_cmd(M.status, REPO, host=host, gh=setup_gh(sha))
        self.assertTrue([x for x in st if x.startswith("warn what systemd made of minions-watch.service (the account "
                                                       "name setup wrote is not in its record")], st)
        self.assertEqual([x for x in st if "read back word for word" in x or "is not what this tool wrote" in x], [], st)
        # a bus that does not answer within its time limit: "could not be read", and every bus call had the limit
        host, limits = FakeHost(), []
        real_run = host.run
        def timed_run(argv, timeout=20):
            if tuple(argv[:3]) == M.BUS:
                limits.append(timeout)
                return 127, M.Out("", f"Command {argv!r} timed out after {timeout} seconds")
            return real_run(argv, timeout)
        host.run = timed_run
        kind, why, own = M.unit_as_meant(host, "minions-watch.service", M.watch_meant("you", "/usr/bin"))
        self.assertEqual(kind, "unread", why)
        self.assertIn("the manager's bus gave no unit object for minions-watch.service", why)
        self.assertTrue(limits and all(t == M.BUS_WAIT_S for t in limits), limits)
        # LoadUnit answers, a property call does not within its limit: "could not be read" again, and that call too
        # had the limit
        host, calls = FakeHost(), []
        real_run = host.run
        def late_property(argv, timeout=20):
            if tuple(argv[:3]) == M.BUS:
                calls.append((argv[3], timeout))
                if argv[3] == "call":
                    return 0, json.dumps({"type": "o", "data": ["/org/freedesktop/systemd1/unit/minions_2dwatch_2eservice"]})
                return 127, M.Out("", f"Command {argv!r} timed out after {timeout} seconds")
            return real_run(argv, timeout)
        host.run = late_property
        kind, why, own = M.unit_as_meant(host, "minions-watch.service", M.watch_meant("you", "/usr/bin"))
        self.assertEqual(kind, "unread", why)
        self.assertIn("the manager's bus gave no ExecStart for minions-watch.service", why)
        self.assertEqual(calls, [("call", M.BUS_WAIT_S), ("get-property", M.BUS_WAIT_S)], calls)
        # a systemd older than the one whose busctl gives JSON: "could not be read", and the line says why
        host = FakeHost()
        host.runs[M.BUS] = lambda argv, input=None: (1, "busctl: unrecognized option '--json=short'")
        host.runs[tuple(M.systemctl("--version"))] = (0, "systemd 239 (239)\n+PAM +AUDIT\n")
        kind, why, own = M.unit_as_meant(host, "minions-watch.service", M.watch_meant("you", "/usr/bin"))
        self.assertEqual((kind, own), ("unread", []), why)
        self.assertIn("this computer's systemd is 239, and its busctl gives no JSON form, which this tool reads from "
                      "systemd 240 on", why)
        host.runs[tuple(M.systemctl("--version"))] = (0, "systemd 255 (255.4-1ubuntu8)\n")
        self.assertNotIn("JSON", M.unit_as_meant(host, "minions-watch.service", M.watch_meant("you", "/usr/bin"))[1])
        # what does reach the runner's unit is one word in its grammar: no space, quote, backslash, % or $
        for good in ("o/r", "Some-Org/my.repo_2", "o/.."):
            folder = M.runner_folder(good)
            unit = M.unit_name(folder, "minions-box")
            text = M.unit_text(folder, unit) + M.dropin_text(folder)
            self.assertNotRegex(folder + unit, r"[\s\"'\\%$;]", good)
            self.assertIn(f"\nExecStart={folder}/runsvc.sh\n", text)
            self.assertEqual(os.path.dirname(folder), "/home/minions-runner/runners", good)

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
        self.assertIn("if these same lines come back, running it again changes nothing: the road named", lines[0])
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

    def test_G9_12_where_the_suite_starts_does_not_change_what_it_finds(self):
        # Row R183 (the maintainer's trial 2026-11-25, on 6cc829a): started from a folder inside the home, the suite
        # failed 3 tests and left 19 folders in /tmp: the guard joined every relative name to the current folder, and
        # shutil.rmtree opens each folder inside its tree by its bare name from its parent's handle, so the suite's own
        # cleanup was taken for a reach into the home. In a child with a made home (the guard's REAL_HOME, nothing
        # allowed around it), started from a folder inside it: the cleanup of a tree outside it reaches nothing, and
        # the guard's own folder is gone at exit; the guard is not loosened: a plain relative open there is still
        # counted, and so is a folder made through a handle that points into it.
        tests = os.path.dirname(os.path.abspath(__file__))
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            script = os.path.join(d, "child.py")
            with open(script, "w") as f:
                f.write(f"""import json, os, shutil, sys
sys.path.insert(0, {tests!r})
import machine_guard as g
home = os.path.realpath({d!r} + "/made-home")
os.makedirs(home + "/sub")
tree = os.path.realpath({d!r} + "/outside/tree")
os.makedirs(tree + "/a/b")
open(tree + "/a/b/f", "w").close()
hfd = os.open(home, os.O_RDONLY)  # a handle into the made home, opened before it is the home the guard watches
g.REAL_HOME, g.ALLOWED = home, ()
os.chdir(home + "/sub")
out = {{"guard_tmp": g._TMP}}
try:
    shutil.rmtree(tree)
    out["cleanup"] = "reached nothing"
except g.MachineReached as e:
    out["cleanup"] = str(e)
out["tree_gone"] = not os.path.exists(tree)
for name, act in (("through a handle", lambda: os.mkdir("y", dir_fd=hfd)),):
    with g.expect() as seen:
        try:
            os.chdir("/")
            act()
            out[name] = "not counted"
        except g.MachineReached:
            out[name] = "counted"
        finally:
            os.chdir(home + "/sub")
out["seen"] = g.seen()
print(json.dumps(out))
""")
            r = subprocess.run([sys.executable, "-I", script], capture_output=True, text=True, timeout=120,
                               cwd=d, env=dict(os.environ))
            out = json.loads(r.stdout.strip().splitlines()[-1])
            self.assertEqual(out["cleanup"], "reached nothing", (out, r.stderr[-2000:]))
            self.assertTrue(out["tree_gone"], out)
            self.assertEqual(out["seen"], [], out)
            self.assertEqual(out["through a handle"], "counted", out)  # made through a handle into the made home
            self.assertFalse(os.path.exists(out["guard_tmp"]), (out, r.stderr[-2000:]))  # its own folder, at exit
        # THE SUITE'S OWN ROAD (the maintainer's trial on a98393f: started from another folder of the home the suite
        # failed 10 and errored 10, every reach inside tempfile.TemporaryDirectory's cleanup, which keeps the rmtree
        # tempfile took from shutil when first imported): a real test file, run by unittest from a folder inside a
        # made home, cleans up as the suite does; it reads Ran, OK, and nothing left in its own temporary folder.
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            home = os.path.realpath(os.path.join(d, "made-home"))
            for sub in ("made-home/sub", "child", "tmp"):
                os.makedirs(os.path.join(d, sub))
            with open(os.path.join(d, "child", "test_child.py"), "w") as f:
                f.write(f"""import os, shutil, sys, tempfile, threading, unittest
sys.path.insert(0, {tests!r})
import machine_guard as g
g.REAL_HOME, g.ALLOWED = {home!r}, ()


def tree(d):
    os.makedirs(os.path.join(d, "a", "b"))
    open(os.path.join(d, "a", "b", "f"), "w").close()


class Cleanups(unittest.TestCase):
    def test_with(self):
        with tempfile.TemporaryDirectory() as d:
            tree(d)
        self.assertFalse(os.path.exists(d))

    def test_cleanup(self):
        t = tempfile.TemporaryDirectory()
        tree(t.name)
        t.cleanup()
        self.assertFalse(os.path.exists(t.name))

    def test_rmtree(self):
        d = tempfile.mkdtemp()
        tree(d)
        shutil.rmtree(d)
        self.assertFalse(os.path.exists(d))

    def test_a_second_thread_is_judged_by_the_folder(self):
        got, real = {{}}, g._real_rmtree
        def other():
            with g.expect():
                try:
                    open("x", "w")
                    got["open"] = "not counted"
                except g.MachineReached:
                    got["open"] = "counted"
        def during(path, *a, **k):  # while this thread deletes a tree, another opens a plain name in the made home
            t = threading.Thread(target=other)
            t.start()
            t.join()
            return real(path, *a, **k)
        g._real_rmtree = during
        try:
            d = tempfile.mkdtemp()
            tree(d)
            shutil.rmtree(d)
        finally:
            g._real_rmtree = real
        self.assertEqual(got["open"], "counted")

    def test_a_road_the_guard_does_not_follow_is_counted(self):
        d = tempfile.mkdtemp()
        tree(d)
        with g.expect() as seen:
            with self.assertRaises(g.MachineReached):
                g._real_rmtree(d)  # shutil's own rmtree, kept by a name the guard did not wrap
        self.assertIn("a road the guard does not follow", seen[0])
        shutil.rmtree(d)
""")
            r = subprocess.run([sys.executable, "-I", "-B", "-m", "unittest", "discover", "-s",
                                os.path.join(d, "child"), "-p", "test_child.py"], capture_output=True, text=True,
                               timeout=120, cwd=os.path.join(home, "sub"), env=dict(os.environ, TMPDIR=os.path.join(d, "tmp")))
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            self.assertIn("Ran 5 tests", r.stderr)
            self.assertEqual(r.stderr.rstrip().splitlines()[-1], "OK", r.stderr[-3000:])
            self.assertEqual(os.listdir(os.path.join(d, "tmp")), [], r.stderr[-3000:])  # the guard's folder too
        # a plain relative open from a folder inside the made home is still counted (the guard is not loosened)
        with tempfile.TemporaryDirectory(dir=TMP) as d:
            home = os.path.realpath(os.path.join(d, "made-home"))
            os.makedirs(os.path.join(home, "sub"))
            here, old = os.getcwd(), (machine_guard.REAL_HOME, machine_guard.ALLOWED)
            try:
                machine_guard.REAL_HOME, machine_guard.ALLOWED = home, ()
                os.chdir(os.path.join(home, "sub"))
                with machine_guard.expect() as seen:
                    with self.assertRaises(machine_guard.MachineReached):
                        open("x", "w")
                self.assertIn("in this account's real home", seen[0])
            finally:
                os.chdir(here)
                machine_guard.REAL_HOME, machine_guard.ALLOWED = old

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



# Rule 31 (iv): values this tool did not write, chosen by whoever can push to the repository: a workflow file's name,
# a job's display name and a runs-on label, each with a backtick pair and a dollar-parenthesis.
ODD_A = ".github/workflows/odd `x` $(id).yml"
ODD_B = ".github/workflows/rel `y` $(id).yml"
ODD_A_YML = ("name: odd\non: [push]\njobs:\n  build:\n    name: say `hi` $(id)\n    runs-on: ubuntu-latest\n"
             "    steps:\n      - run: echo\n  theirs:\n    name: t `a` $(b)\n    runs-on: [self-hosted, 'x`y`$(z)']\n"
             "    steps:\n      - run: x\n")
ODD_B_YML = ("name: rel\non:\n  push:\n    tags:\n      - 'v*'\njobs:\n  publish:\n    name: ship `it` $(now)\n"
             "    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n")


# Rule 31 (a), 2026-11-24: workflow names that hold a line break, a carriage return, and a
# terminal's escape (erase the line, cursor up), each a publish job, so look tries to give a command that names it.
ODD_NL = ".github/workflows/odd\nsudo id #.yml"
ODD_CR = ".github/workflows/odd\rsudo id #.yml"
ODD_ESC = ".github/workflows/odd\x1b[2K\x1b[1Arel.yml"
# The test's own reading of the class, by Unicode category, not the tool's: control characters (C0, C1, DEL, tab),
# format characters (bidi overrides and the like), lone surrogates, and the line and paragraph separators.
ODD_CATEGORIES = ("Cc", "Cf", "Cs", "Zl", "Zp")


def odd_in(text):
    return [hex(ord(c)) for c in text if unicodedata.category(c) in ODD_CATEGORIES]


def outside_quotes(line):
    """A command line with its single-quoted parts taken out: what the shell would read as code."""
    return re.sub(r"'[^']*'", "", line)


def every_command():
    """Rule 31 (vi): each of the 11 commands, as (name, make the world, run it); the bent values' and the line
    ends' trials both run every one."""
    yield "look", lambda: (linux_host(), StandIn(load("look-recent-runs.jsonl"))), \
        lambda h, g, out: M.look(REPO, False, host=h, gh=g, out=out)
    def setup_world():
        host, sha = setup_host()
        fresh_look(host)
        return host, setup_gh(sha)
    yield "setup", setup_world, lambda h, g, out: M.setup(REPO, host=h, gh=g, out=out, interactive=True)
    yield "doctor", lambda: (lambda hs: (hs[0], setup_gh(hs[1])))(doctor_world()), \
        lambda h, g, out: M.doctor(REPO, host=h, gh=g, out=out, interactive=False)
    yield "move", lambda: (move_host(), MoveGH(G3.FILES)), lambda h, g, out: M.move(REPO, host=h, gh=g, out=out)
    yield "move --verify", lambda: (move_host(), MoveGH(G3.FILES)), \
        lambda h, g, out: M.move(REPO, verify=True, host=h, gh=g, out=out, wait_s=0, poll_s=0)
    yield "status", lambda: (g5_host()[0], StatusGH()), lambda h, g, out: M.status(REPO, host=h, gh=g, out=out)
    yield "watch", lambda: (g5_host()[0], StatusGH()), lambda h, g, out: M.watch(host=h, gh=g, out=out)
    yield "drain", lambda: (g5_host()[0], DrainGH()), \
        lambda h, g, out: M.drain(REPO, host=h, gh=g, out=out, wait_s=0, poll_s=0)
    yield "resume", lambda: (g5_host()[0], DrainGH()), \
        lambda h, g, out: M.resume(REPO, host=h, gh=g, out=out, interactive=True)
    yield "uninstall", lambda: (lambda w: (w[2], w[0]))(remove_setup()), \
        lambda h, g, out: M.uninstall(REPO, host=h, gh=g, out=out, interactive=True, wait_s=0, poll_s=0)
    yield "uninstall --all", lambda: (lambda w: (w[2], w[0]))(remove_setup()), \
        lambda h, g, out: M.uninstall(None, everything=True, host=h, gh=g, out=out, interactive=True,
                                      wait_s=0, poll_s=0)


class Rule31(unittest.TestCase):
    def test_rule_31_iv_names_others_chose_are_shown_changed_and_said_so_in_look_and_move(self):
        files = {ODD_A: ODD_A_YML, ODD_B: ODD_B_YML}

        class OddLook(StandIn):
            def api(self, method, path, body=None):
                base = f"repos/{REPO}/contents/.github/workflows"
                if method == "GET" and path.split("?")[0] == base:
                    self.calls.append((method, path))
                    return 200, [{"name": p.rsplit("/", 1)[1], "path": p} for p in sorted(files)]
                if method == "GET" and path.startswith(f"repos/{REPO}/contents/"):
                    self.calls.append((method, path))
                    p = path[len(f"repos/{REPO}/contents/"):].split("?")[0]
                    return 200, {"content": M.base64.b64encode(files[p].encode()).decode()}
                return super().api(method, path, body)

        out = Out()
        M.look(REPO, False, host=linux_host(), gh=OddLook(load("look-recent-runs.jsonl")), out=out)
        looked = out.printed()
        gh = MoveGH(files)
        out2 = Out()
        M.move(REPO, host=move_host(), gh=gh, out=out2)
        moved = out2.printed()
        for road, lines in (("look", looked), ("move", moved)):
            named = [x for x in lines if "odd 'x' $ (id).yml" in x or "rel 'y' $ (id).yml" in x]
            self.assertTrue(named, (road, lines))
            for x in named:
                self.assertTrue(x.endswith(M.SHOWN_CHANGED), (road, x))
            for sentence, commands in unfold(lines):
                self.assertNotIn("`", sentence, (road, sentence))
                self.assertNotIn("$(", sentence, (road, sentence))
                for c in commands:
                    self.assertNotIn("`", c, (road, c))  # R200: quoted or not
                    self.assertNotIn("$(", c, (road, c))
        # R200 (the maintainer's ruling (A), 2026-11-25): no command carries such a name, quoted or not. Look's word
        # for the job that publishes gives no step: its line says to rename the file on GitHub, then look again.
        self.assertFalse([c for _, commands in unfold(looked) for c in commands if "--include-job" in c], looked)
        said = [x for x in looked if "rel 'y' $ (id).yml:publish" in x]
        self.assertTrue(said and "rename the file on GitHub to a name without it" in said[0], looked)
        # The page: each such name stands in its own code span, shown changed by the terminal's own function, and the
        # line carries the terminal's note; no backtick of the name is left to end its span.
        body = pr_body_of(gh)
        lines = [x for x in body.splitlines() if "odd 'x' $ (id).yml" in x or "rel 'y' $ (id).yml" in x]
        self.assertEqual(len(lines), 3, body)  # moved, left as not ours, publishes
        for x in lines:
            self.assertTrue(x.endswith(M.SHOWN_CHANGED), x)
            head = x.split(": ", 1)[0]  # the file and the job: two spans, whole (the words after are row R201's)
            self.assertEqual(head.count("`"), 4, head)
            self.assertNotIn("$(", head)


    def test_rule_31_v_a_text_one_run_keeps_and_a_later_run_prints_keeps_its_command_on_its_own_line(self):
        # 2026-11-24: a text written to the record and shown again by another road. The watch keeps
        # its login step in minions.json; status, a later process with marks of its own, prints it.
        host, clock = g5_host()
        for _ in range(3):
            run_cmd(M.watch, host=host, gh=BlindGH(why="GitHub gave no answer: To get started with GitHub CLI, "
                                                       "please run:  gh auth login"))
            clock.sleep(300)
        with open(M.state_path(host), encoding="utf-8") as f:
            kept = f.read()
        self.assertNotIn(M._MARK, kept)  # a run's own mark is never written down
        later = "f" * 12
        c0, c1 = f"\ue001{later}\ue002", f"\ue003{later}\ue004"
        with mock.patch.multiple(M, C0=c0, C1=c1, _COMMAND=re.compile(re.escape(c0) + "(.*?)" + re.escape(c1), re.S)):
            out = Out()
            M.status(REPO, host=host, gh=StatusGH(), out=out)
        printed = out.printed()
        i = next(k for k, x in enumerate(printed) if x.startswith("stop the watch could not read"))
        self.assertIn("in your own terminal, the first command below (run the second command below first", printed[i])
        self.assertEqual(printed[i + 1:i + 3], ["gh auth login --insecure-storage", f"look {REPO}"])
        # A mark character in a value this tool did not write is taken out when it is kept.
        data = M.load_state(host)
        data["odd"] = "a \ue001" + "0" * 12 + "\ue002x\ue003" + "0" * 12 + "\ue004 b"
        M.save_state(host, data)
        self.assertEqual(M.load_state(host)["odd"], "a " + "0" * 12 + "x" + "0" * 12 + " b")

    def test_rule_31_a_names_with_a_line_break_or_a_terminal_escape_never_make_a_line_or_a_command(self):
        # 2026-11-24: three publish jobs whose workflow files hold a line feed, a
        # carriage return and an escape, and a job that moves from a file with a line feed (for the pull request).
        build = ".github/workflows/b\nsudo id #.yml"
        files = {ODD_NL: ODD_B_YML, ODD_CR: ODD_B_YML, ODD_ESC: ODD_B_YML, build: CI_YML}

        class OddLook(StandIn):
            def api(self, method, path, body=None):
                base = f"repos/{REPO}/contents/.github/workflows"
                if method == "GET" and path.split("?")[0] == base:
                    self.calls.append((method, path))
                    return 200, [{"name": p.rsplit("/", 1)[1], "path": p} for p in sorted(files)]
                if method == "GET" and path.startswith(f"repos/{REPO}/contents/"):
                    self.calls.append((method, path))
                    p = path[len(f"repos/{REPO}/contents/"):].split("?")[0]
                    return 200, {"content": M.base64.b64encode(files[p].encode()).decode()}
                return super().api(method, path, body)

        out = Out()
        M.look(REPO, False, host=linux_host(), gh=OddLook(load("look-recent-runs.jsonl")), out=out)
        looked = out.printed()
        gh = MoveGH(files)
        out2 = Out()
        M.move(REPO, host=move_host(), gh=gh, out=out2)
        moved = out2.printed()
        for road, lines in (("look", looked), ("move", moved)):
            for x in lines:
                self.assertEqual(odd_in(x), [], (road, x))
                self.assertFalse(x.startswith("sudo id") or x.startswith("rel.yml"), (road, x))
            named = [x for x in lines if "odd?sudo id #.yml" in x or "odd?[2K?[1Arel.yml" in x]
            self.assertEqual(len({n for x in named for n in ("odd?sudo id #.yml", "odd?[2K?[1Arel.yml") if n in x}),
                             2, (road, lines))
            for x in named:
                self.assertTrue(x.endswith(M.SHOWN_CHANGED), (road, x))
            for sentence, commands in unfold(lines):
                for c in commands:
                    self.assertNotIn("--include-job", c, (road, c))  # (b): no command whose value was changed
        # Look's own step for such a job: the name cannot be typed on one line; it is renamed on GitHub.
        said = [x for x in looked if "odd?sudo id #.yml" in x and "publishes or deploys" in x]
        self.assertEqual(len(said), 2, looked)  # the line feed's and the carriage return's
        for x in said:
            self.assertIn("cannot be typed on one line", x)
            self.assertIn("rename the file on GitHub", x)
        # The pull request's body (2026-11-24): the name stays inside its list line.
        body = pr_body_of(gh)
        for x in body.split("\n"):
            self.assertEqual(odd_in(x), [], x)
            self.assertFalse(x.startswith("sudo id"), x)
        self.assertIn("b?sudo id #.yml", body)

    def test_rule_31_a_render_changes_every_character_of_the_class_and_never_prints_a_command_holding_one(self):
        # (d): every code point of the class, from the test's own reading of Unicode's categories, so a character
        # cannot slip between a list and the rule.
        seen = 0
        for i in range(0x110000):
            ch = chr(i)
            if unicodedata.category(ch) not in ODD_CATEGORIES:
                continue
            seen += 1
            lines = M.render(f"ok a{ch}b")
            self.assertEqual(len(lines), 1, hex(i))
            self.assertEqual(odd_in(lines[0]), [], hex(i))
            self.assertTrue(lines[0].endswith(M.SHOWN_CHANGED), hex(i))
            lines = M.render("ok run " + M.cmd(f"look a{ch}b") + " again, then " + M.cmd("status x/y"))
            self.assertEqual([odd_in(x) for x in lines], [[]] * len(lines), hex(i))
            self.assertEqual(len(lines), 2, (hex(i), lines))  # only the command with nothing changed is printed
            self.assertEqual(lines[1], "status x/y", hex(i))
            self.assertIn("cannot be typed on one line", lines[0], hex(i))
            self.assertEqual(len(REF.findall(lines[0])), 1, (hex(i), lines[0]))
        self.assertGreater(seen, 2000)  # Cc 65, Cf 170 or so, Cs 2048, Zl 1, Zp 1
        # 2026-11-24: Cf stays, and the fixed words are true for it: a joiner, a zero-width
        # space and a direction isolate are neither a line break nor a terminal's act, but they cannot be seen.
        for ch in ("\u200d", "\u200b", "\u2066"):
            self.assertIn("or that cannot be seen", M.render(f"ok a{ch}b")[0], hex(ord(ch)))
            self.assertIn("or that cannot be seen", M.render("ok run " + M.cmd(f"look a{ch}b"))[0], hex(ord(ch)))
        self.assertIn("or that cannot be seen", M.MD_CHANGED)

    def test_rule_31_vi_an_answer_that_is_not_text_is_read_and_shown_changed_never_a_traceback(self):
        # 2026-11-24, "not text": a file's name whose bytes are not UTF-8 (rm names one a job left in
        # the runner's home; find prints one). Another program's answer is read with each such byte as a lone
        # surrogate (class Cs), so it reaches plain(), which shows it changed; it never ends the tool in a traceback.
        prog = [sys.executable, "-I", "-c", "import sys; sys.stdout.buffer.write(b'kept /home/you/a\\xffb\\n'); "
                "sys.stderr.buffer.write(b'rm: cannot remove \\xfe\\n'); sys.exit(1)"]
        host = M.Host()
        for road in (host.run, host.run_own, lambda argv: host.run_in(argv, "")):
            rc, out = road(prog)
            self.assertEqual(rc, 1)
            self.assertIn("\udcff", out)
            self.assertIn("\udcfe", out.err if hasattr(out, "err") else str(out))
            line = M.render("stop could not remove: " + M.tail(out))[0]
            self.assertEqual(odd_in(line), [], line)
            self.assertTrue(line.endswith(M.SHOWN_CHANGED), line)
            line.encode("utf-8")  # printable as it is

    def test_rule_31_vi_bent_values_through_every_command(self):
        # 2026-11-24: a road is found by running it, not by reading the code. Every command of the
        # tool, each in four worlds: GitHub's names bent (every name, path, title, branch, label and message GitHub
        # answers with), other programs' answers bent (each answer's last line), every folder listing holding a bent
        # name, all three at once, other programs' answers bent but gh's login read, and GitHub's names bent but our
        # runner's listing (in those two, so the roads after that read run). A bent value holds a line feed, the words "sudo id", a carriage return, an
        # escape, a backspace, DEL, NEL, U+2028, a bidi override, a lone surrogate (a byte that is not UTF-8) and NUL.
        # Every line printed must hold none of the class, none may start with the planted words, and no command
        # ends the tool in a traceback.
        bent = "b\nsudo id #\r\x1b[2K\x08\x7f\x85\u2028\u202e\udcff\x00z"
        keys = {"name", "path", "display_title", "head_branch", "workflow_name", "title", "message", "html_url",
                "body", "login", "full_name"}

        def bent_in(v):  # inside the name, after a path's last slash, so its ending (".yml") and folder still hold
            i = v.rfind("/") + 1
            return v[:i] + "a" + bent + v[i:]

        def bend(x):
            if isinstance(x, dict):
                return {k: (bent_in(v) if k in keys and isinstance(v, str) and v else bend(v)) for k, v in x.items()}
            if isinstance(x, list):
                return [bend(v) for v in x]
            return x

        def bent_gh(gh, runners_too=True):
            real = gh.api

            def api(method, path, body=None):
                r = real(method, path.replace("a" + bent, ""), body)
                if not runners_too and "/actions/runners" in path:
                    return r  # the sixth world: our runner as GitHub lists it, so the roads after it are reached
                return r[0], bend(copy.deepcopy(r[1]))
            gh.api = api
            return gh

        def bent_host(host, programs, files, login_too=True):
            if programs:
                real = host._answer

                def answer(argv, input=None):
                    r = real(argv, input)
                    if not login_too and list(argv[:2]) == ["gh", "auth"]:
                        return r  # the fifth world: gh's login read as it is, so the roads after it are reached
                    if isinstance(r, tuple) and len(r) == 2 and isinstance(r[1], str) and r[1].strip():
                        return r[0], r[1].rstrip("\n") + "a" + bent + "\n"
                    return r
                host._answer = answer
            if files:
                for name in ("listdir", "listdir_or_none"):
                    real_ls = getattr(host, name, None)
                    if real_ls is not None:
                        setattr(host, name, (lambda f: lambda path, *a: (
                            (lambda r: r if r is None else list(r) + ["n" + bent])(f(path, *a))))(real_ls))
            return host

        ran, shown = 0, set()
        for name, make, run in every_command():
            for mode in ("github", "programs", "files", "all", "programs after the login", "github after our runner"):
                with self.subTest(command=name, bent=mode):
                    host, gh = make()
                    if mode in ("github", "all", "github after our runner"):
                        bent_gh(gh, runners_too=mode != "github after our runner")
                    bent_host(host, mode in ("programs", "all", "programs after the login"), mode in ("files", "all"),
                              login_too=mode != "programs after the login")
                    out = Out()
                    code = run(host, gh, out)
                    self.assertIsInstance(code, int)
                    ran += 1
                    for x in out.printed():
                        if "sudo id" in x:
                            shown.add((name, mode))  # a bent value reached a printed line, shown changed
                        self.assertEqual(odd_in(x), [], (name, mode, x[:200]))
                        self.assertFalse(x.lstrip().startswith(("sudo id", "z")), (name, mode, x[:200]))
                        x.encode("utf-8")
        self.assertEqual(ran, 66)
        # The worlds reach the roads that print such a value (11 runs, of look, doctor, move, status and uninstall,
        # when this was written); fewer means a world stopped before them and reads nothing. Setup was in this set
        # until R204 (2026-11-26) only through its stop on `gh auth token`, which printed that call's standard output
        # (in these worlds the made-up login value with the bent value after it): that output is a token when the
        # call works, so it is never printed now, only its length. Setup is in it again with R210 (2026-11-26): a
        # bent last line of list-unit-files is no longer systemd's count line, so that read is said not answered,
        # with systemctl's last line, shown changed (with a warning written after it, test_G6_28's run, that last
        # line is the warning, so setup is not in the set there).
        self.assertGreaterEqual(len(shown), 11, sorted(shown))
        base = {"look", "doctor", "move", "status", "uninstall", "uninstall --all"}
        self.assertEqual({n for n, _ in shown},
                         base if os.environ.get("MINIONS_TEST_STREAMS") == "1" else base | {"setup"})

    # Rule 31 (vi), "a line ends at a line feed" (2026-11-24). Two markers of one length, each
    # shown changed the same way (plain() makes every slot ?): LE0 holds U+0001 in its 8 slots, where no reader
    # breaks a line; LE1 holds, in the same slots, the characters str.splitlines() and str.split() break at and a
    # line feed does not: VT, FF, FS, GS, RS, NEL, U+2028, U+2029.
    LE0 = "aPQ\x01R\x01S\x01T\x01U\x01W\x01X\x01Y\x01z"
    LE1 = "aPQ\x0bR\x0cS\x1cT\x1dU\x1eW\x85X\u2028Y\u2029z"
    LE_WHERE = ("github", "programs", "files", "file contents", "all", "programs after the login",
                "github after our runner")

    def test_rule_31_vi_a_line_ends_at_a_line_feed_every_command_reads_one_item_as_one(self):
        # Every command, in seven worlds: the marker planted in GitHub's names (and as a comment line in each
        # workflow file's content), in other programs' answers, in folder listings, in the content of every file
        # the tool reads that is not JSON, all of them, and the two "after" worlds of the bent values' test. Each
        # command runs twice on the same computer and GitHub: the second reads what the first left (the record,
        # the journal, a branch). With every value read to a line feed, LE1's run prints the same lines as LE0's,
        # sends GitHub the same writes, and writes the same files; an item read as two (a ghost group, a cut
        # answer) makes them differ. A second LE0 run is the control: the runs are steady.
        V0, V1 = self.LE0, self.LE1
        self.assertEqual(M.plain(V0)[0], M.plain(V1)[0])
        self.assertEqual((len(V0.split("\n")), len(V1.split("\n")), len(V1.splitlines()), len(V1.split())), (1, 1, 9, 9))
        keys = {"name", "path", "display_title", "head_branch", "workflow_name", "title", "message", "html_url",
                "body", "login", "full_name"}

        def in_yaml(V):
            return V.replace("\x85", "\x01").replace("\u2028", "\x01").replace("\u2029", "\x01")

        def bend(x, V):
            if isinstance(x, dict):
                out = {}
                for k, v in x.items():
                    if k == "content" and isinstance(v, str):  # a workflow file: one comment line more
                        # NEL, U+2028 and U+2029 in a workflow file's text make it a file not read (its own test,
                        # the not-read test's third kind), so the comment line holds the five others and U+0001
                        out[k] = M.base64.b64encode(("# " + in_yaml(V) + "\n").encode() + M.base64.b64decode(v)).decode()
                    elif k in keys and isinstance(v, str) and v:
                        i = v.rfind("/") + 1
                        out[k] = v[:i] + V + v[i:]
                    else:
                        out[k] = bend(v, V)
                return out
            return [bend(v, V) for v in x] if isinstance(x, list) else x

        def plant(host, gh, V, where):
            if "github" in where:
                real = gh.api

                def api(method, path, body=None):
                    r = real(method, path.replace(V, ""), body)
                    if where == "github after our runner" and "/actions/runners" in path:
                        return r
                    return r[0], bend(copy.deepcopy(r[1]), V)
                gh.api = api
            if where in ("programs", "programs after the login", "all"):
                real_a = host._answer

                def answer(argv, input=None):
                    r = real_a(argv, input)
                    if where == "programs after the login" and list(argv[:2]) == ["gh", "auth"]:
                        return r
                    if isinstance(r, tuple) and len(r) == 2 and isinstance(r[1], str) and r[1].strip():
                        return r[0], r[1].rstrip("\n") + V + "\n"
                    return r
                host._answer = answer
            if where in ("files", "all"):
                for name in ("listdir", "listdir_or_none"):
                    setattr(host, name, (lambda f: lambda path, *a: (
                        (lambda r: r if r is None else list(r) + ["n" + V])(f(path, *a))))(getattr(host, name)))
            if where in ("file contents", "all"):
                real_r = host.read

                def read(path):
                    t = real_r(path)
                    if not isinstance(t, str) or not t.strip():
                        return t
                    try:
                        json.loads(t)
                        return t
                    except ValueError:
                        return t.rstrip("\n") + V + ("\n" if t.endswith("\n") else "")
                host.read = read

        def steady(v):  # names a run makes at random (a branch's, a temporary folder's), and the clock
            if not isinstance(v, str):
                return v
            v = re.sub(r"/tmp/[\w.-]+", "/tmp/T", v)
            v = re.sub(r"\btmp[a-z0-9_]{8}\b", "tmpX", v)
            v = re.sub(r"(?<!\d)\d\d:\d\d(:\d\d)?(Z| UTC)", r"HH:MM\2", v)  # the clock, where a run reads it
            v = re.sub(r"\b1[6-9]\d{8}\b", "EPOCH", v)  # the clock as seconds (the watch's seen mark)
            return re.sub(r"minions-([a-z-]+)-[0-9a-f]{8}", r"minions-\1-X", v)

        def same(text, V):  # V's own forms (and its form in a workflow file), raw or as json.dumps writes them, made V0's
            for W in (V, in_yaml(V)):
                for a, b in ((W, V0), (json.dumps(W)[1:-1], json.dumps(V0)[1:-1]),
                             (json.dumps(W, ensure_ascii=False)[1:-1], json.dumps(V0, ensure_ascii=False)[1:-1])):
                    text = text.replace(a, b)
            return text

        def one(make, run, V, where):
            host, gh = make()
            plant(host, gh, V, where)
            before, seen = dict(host.files), []
            for n in (1, 2):
                out = Out()
                seen.append(("exit", n, run(host, gh, out)))
                seen += [("line", n, x) for x in out.printed()]
            seen += [("github", m, p, None if b is None else same(json.dumps(b, sort_keys=True, ensure_ascii=False), V))
                     for m, p, b in getattr(gh, "writes", [])]
            for k in sorted(host.files):
                v = host.files[k]
                if before.get(k) != v and isinstance(v, str):
                    seen.append(("file", k, same(v, V)))
                    # (2): a file the tool writes holds none of the three raw
                    self.assertFalse([c for c in "\x85\u2028\u2029" if c in v], (k, where))
            return [tuple(steady(v) for v in x) for x in seen]

        runs, reached = 0, set()
        for name, make, run in every_command():
            for where in self.LE_WHERE:
                with self.subTest(command=name, planted=where):
                    runs += 1
                    a, a2, b = one(make, run, V0, where), one(make, run, V0, where), one(make, run, V1, where)
                    self.assertEqual(a, a2, "the control: two runs with LE0 are the same")
                    if any(M.plain(V0)[0] in str(x) for x in a):
                        reached.add(name)
                    self.assertEqual([x for x in b if x not in a][:6], [], (name, where, "only with LE1"))
                    self.assertEqual([x for x in a if x not in b][:6], [], (name, where, "only with LE0"))
                    self.assertEqual(len(a), len(b))
        self.assertEqual(runs, 77)
        # the marker reached what is compared in these commands when this was written; fewer means a world
        # stopped before the roads that read it. Setup reaches it through R210's read of list-unit-files: a marker
        # after systemd's count line makes that read not answered, said with systemctl's last line; with a warning
        # written after it (test_G6_28's run) that last line is the warning, so setup is not in the set there.
        every = {"look", "doctor", "move", "status", "uninstall", "uninstall --all"}
        self.assertEqual(reached, every if os.environ.get("MINIONS_TEST_STREAMS") == "1" else every | {"setup"})

    def test_rule_31_vi_a_line_ends_at_a_line_feed_on_the_way_in(self):
        # 2026-11-24: Python's text mode turns a lone carriage return into a line feed before any
        # split (subprocess with text=True, open() by default). A real program's answer and a real file, each
        # holding a CR and the rest of the class, reach the tool as written: read as bytes, then decoded.
        raw = b"a\rb\x0bc\x0cd\x1ce\x1df\x1eg\xc2\x85h\xe2\x80\xa8i\xe2\x80\xa9j\xffk\r\n"
        want = raw.decode("utf-8", "surrogateescape")
        prog = [sys.executable, "-I", "-c",
                f"import sys; sys.stdout.buffer.write({raw!r}); sys.stderr.buffer.write({raw!r})"]
        host = M.Host()
        for road in ("run", "run_own", "run_in"):
            rc, out = getattr(host, road)(prog, *([""] if road == "run_in" else []))
            self.assertEqual((rc, str(out), out.err), (0, want, want), road)
            self.assertEqual(M.lines_of(out), [want[:-1]], road)
            self.assertEqual(len(M.tail(out, 9).split(" / ")), 2, road)  # one line on each stream
        with tempfile.NamedTemporaryFile("wb", dir=TMP, delete=False) as f:
            f.write(raw)
        self.assertEqual(host.read(f.name), raw.decode("utf-8", "replace"))
        self.assertEqual(M.lines_of(host.read(f.name)), [raw.decode("utf-8", "replace")[:-1]])
        # the words of a line: at a space, a tab or a line feed only
        self.assertEqual(M.words_of(" g1 " + self.LE1 + "\tg2\n"), ["g1", self.LE1, "g2"])
        self.assertEqual(M.words_of("600 r " + self.LE1 + " x", 2), ["600", "r", self.LE1 + " x"])
        self.assertEqual(M.last_line("x\n" + self.LE1 + "\n"), self.LE1)
        self.assertEqual(M.lines_of("a\n\nb\n"), ["a", "", "b"])
        self.assertEqual(M.lines_of(""), [])

    NOT_TEXT_CHILD = r"""
import base64, os, sys, tempfile
sys.path.insert(0, sys.argv[1])
import test_minions as T
M = T.M
real_host = M.Host()
tmp = tempfile.mkdtemp(dir=T.TMP)
FF = b"\xff"

def through_a_real_program(text):
    # the stand-in's words, with one byte that is not UTF-8, written by a real program and read by the tool's own run
    data = text.rstrip("\n").encode("utf-8", "surrogateescape") + FF + b"\n"
    rc, out = real_host.run([sys.executable, "-I", "-c", "import sys; sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]))",
                             data.hex()])
    return str(out)

def through_a_real_file(text, n=[0]):
    n[0] += 1
    path = os.path.join(tmp, "f%d" % n[0])
    with open(path, "wb") as f:
        f.write(text.encode("utf-8", "surrogateescape") + FF + b"\n")
    return real_host.read(path)

def planted(host, gh):
    real_answer, real_read = host._answer, host.read
    def answer(argv, input=None):
        r = real_answer(argv, input)
        if list(argv[:2]) == ["gh", "auth"]:
            return r  # gh's login read as it is, so the roads after it run
        if isinstance(r, tuple) and len(r) == 2 and isinstance(r[1], str) and r[1].strip():
            return r[0], through_a_real_program(r[1])
        return r
    host._answer = answer
    host.read = lambda path: (lambda r: r if not r else through_a_real_file(r))(real_read(path))
    real_api = gh.api
    def api(method, path, body=None):
        if method == "GET" and path.split("?")[0].endswith("/contents/.github/workflows/ff.yml"):
            gh.calls.append((method, path))
            return 200, {"content": base64.b64encode(b"name: ff" + FF + b"\non: [push]\njobs:\n  b:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo\n").decode()}
        if method == "GET" and path.split("?")[0].endswith("/contents/.github/workflows"):
            st, d = real_api(method, path, body)
            if st == 200 and isinstance(d, list):
                d = d + [{"name": "ff.yml", "path": ".github/workflows/ff.yml"}]
            return st, d
        return real_api(method, path, body)
    gh.api = api
    return host, gh

class LookGH(T.StandIn):
    def api(self, method, path, body=None):
        if method == "GET" and path.split("?")[0] == "repos/%s/contents/.github/workflows" % T.REPO:
            self.calls.append((method, path))
            return 200, [{"name": "ci.yml", "path": ".github/workflows/ci.yml"}]
        if method == "GET" and path.startswith("repos/%s/contents/.github/workflows/ci.yml" % T.REPO):
            self.calls.append((method, path))
            return 200, {"content": base64.b64encode(T.CI_YML.encode()).decode()}
        return super().api(method, path, body)

runs = [
    ("look", lambda: (T.linux_host(), LookGH(T.load("look-recent-runs.jsonl"))),
     lambda h, g: M.look(T.REPO, False, host=h, gh=g, out=sys.stdout)),
    ("move", lambda: (T.move_host(), T.MoveGH(T.G3.FILES)), lambda h, g: M.move(T.REPO, host=h, gh=g, out=sys.stdout)),
    ("status", lambda: (T.g5_host()[0], T.StatusGH()), lambda h, g: M.status(T.REPO, host=h, gh=g, out=sys.stdout)),
    ("uninstall --all", lambda: (lambda w: (w[2], w[0]))(T.remove_setup()),
     lambda h, g: M.uninstall(None, everything=True, host=h, gh=g, out=sys.stdout, interactive=True, wait_s=0, poll_s=0)),
]
for name, make, run in runs:
    host, gh = planted(*make())
    code = run(host, gh)
    print("DONE %s %s" % (name, code), flush=True)
"""

    def test_rule_31_vi_not_text_on_the_way_in_with_and_without_a_language(self):
        # 2026-11-24, "not text, on the way in", as a planted trial: in look, move, status and
        # uninstall --all, every other program's answer (gh's login read aside) carries the byte 0xFF, written by a
        # real program and read by the tool's own run; every file the tool reads is a real file holding 0xFF; one
        # workflow file's content holds it. The lines go to a real standard output, once with LANG=en_US.UTF-8 and
        # once with no language at all. No command ends the run; nothing printed holds the raw byte or a Python name.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("LANG", "LC_", "PYTHONIOENCODING", "PYTHONUTF8"))}
        for lang in ({"LANG": "en_US.UTF-8"}, {}):
            with self.subTest(lang=lang or "none"):
                p = subprocess.run([sys.executable, "-I", "-c", self.NOT_TEXT_CHILD, os.path.dirname(__file__)],
                                   env=dict(env, **lang), capture_output=True, timeout=300)
                said = p.stdout.decode("utf-8")  # strict: a raw 0xFF in a printed line fails here
                self.assertEqual(p.returncode, 0, (lang, p.stderr.decode("utf-8", "replace")[-1500:]))
                self.assertEqual(re.findall(r"^DONE (.+) -?\d+$", said, re.M),
                                 ["look", "move", "status", "uninstall --all"], said[-800:])
                for x in said.split("\n"):
                    self.assertEqual(odd_in(x), [], (lang, x[:200]))
                    self.assertNotRegex(x, r"Traceback|Unicode(De|En)codeError|ValueError|surrogate", (lang, x[:200]))
                # the byte was read, and said: the workflow file named by look and by move, others' words changed
                self.assertEqual(said.count(".github/workflows/ff.yml: " + M.NOT_TEXT), 2, said[-800:])
                # uninstall --all: the byte after systemd's count line makes R210's read of the unit files not
                # answered, and it is said so, before any change; the words shown with it are systemctl's last line,
                # the byte's line changed, or (test_G6_28's run) the warning written after it
                self.assertRegex(said, r"(?m)^stop could not read whether this computer holds .* Not read: systemd's "
                                       r"unit files \(systemctl answered code 0: .*\n(?=DONE uninstall --all 2$)")
                streams = os.environ.get("MINIONS_TEST_STREAMS") == "1"
                self.assertGreaterEqual(said.count(M.SHOWN_CHANGED), 1 if streams else 2, said[-800:])

    def test_rule_31_vi_a_workflow_file_that_is_not_text_is_named_and_the_others_go_on(self):
        # 2026-11-24, seen in the planted trial: one workflow file whose content is not UTF-8 stopped
        # move for every file, with a step that could not help ("run the same command again"), and uninstall's
        # put-back skipped such a moved file and then said no workflow still asks for our runner. A file that is not
        # text is named in plain words, never taken for nothing there, and the work goes on with the others.
        # 2026-11-24, planted: a file whose content GitHub's answer does not carry (a file over
        # 1 MB: "encoding": "none", "content": "") was "no job read" in look, unnamed in move and verify, and the
        # put-back said no workflow still asks for our runner (rule 6). Rule 31 (vi), 2026-11-24: a file whose text
        # holds NEL, U+2028 or U+2029 is a third kind, since readers of YAML do not agree where its lines end. All
        # three kinds go every road the same way, each with its own step.
        kinds = (({"content": M.base64.b64encode(b"name: x\xff\non: [push]\n").decode()}, M.NOT_TEXT_IS,
                  "Make the file UTF-8 again on GitHub"),
                 ({"size": 2_000_000, "encoding": "none", "content": ""}, M.NOT_GIVEN_IS,
                  "Make the file smaller than 1 MB on GitHub"),
                 ({"content": M.base64.b64encode("# a comment\u2028name: hidden\non: [push]\n".encode()).decode()},
                  M.NOT_ONE_READING_IS.format("U+2028"), "Take U+2028 out of the file on GitHub"))
        for answer, said_is, step in kinds:
            with self.subTest(said_is):
                self.workflow_file_unread(answer, said_is, step)
        # a file GitHub says is empty is never one whose content GitHub did not give; since R202 (no workflow file is
        # passed over in silence) it is named as a file with no jobs key, as every file without one is
        self.assertEqual(M.workflow_text({"size": 0, "encoding": "base64", "content": ""}), (None, M.NOT_JOBS_KEY_IS))
        # the third kind names each of the three it holds by its code; a CR and a CRLF end a line in every YAML,
        # so a file with them is read
        b64 = lambda t: {"content": M.base64.b64encode(t.encode()).decode()}
        self.assertEqual(M.workflow_text(b64("a\x85b\u2029c\u2028d")),
                         (None, M.NOT_ONE_READING_IS.format("U+0085, U+2028 and U+2029")))
        self.assertEqual(M.unread_step(M.NOT_ONE_READING_IS.format("U+0085 and U+2029")),
                         "Take U+0085 and U+2029 out of the file on GitHub")
        self.assertEqual(M.workflow_text(b64("on: [push]\rjobs:\r\n  a:\r    runs-on: x\n")),
                         ("on: [push]\rjobs:\r\n  a:\r    runs-on: x\n", ""))
        # R202: a job given whole on its key's line ("a: {}") is not a plain job key: the file is not read, named
        self.assertEqual(M.workflow_text(b64("on: [push]\rjobs:\r\n  a: {}\n"))[1], M.NOT_JOBS_READ_IS.format(
            3, "not a job's plain name and a colon (letters, digits, _ and -)"))

    def workflow_file_unread(self, answer, said_is, step):
        def not_text(gh, name):
            real = gh.api

            def api(method, path, body=None):
                if method == "GET" and path.split("?")[0].endswith(f"/contents/.github/workflows/{name}"):
                    gh.calls.append((method, path))
                    return 200, dict(answer)
                return real(method, path, body)
            gh.api = api
            return gh
        said = f"{said_is}, so this tool neither reads nor changes it"
        # look: named, with why
        gh = not_text(MoveGH({".github/workflows/ci.yml": CI_YML, ".github/workflows/ff.yml": ""}), "ff.yml")
        out = Out()
        M.look(REPO, False, host=linux_host(), gh=gh, out=out)
        self.assertIn(f"warn .github/workflows/ff.yml: {said}. {step}, then run look again to have it read",
                      out.printed())
        self.assertFalse([x for x in out.printed() if "ff.yml: no job read" in x], out.printed())
        # move: named, and the other file still moves
        host = move_host()
        gh = not_text(MoveGH({".github/workflows/ci.yml": CI_YML, ".github/workflows/ff.yml": ""}), "ff.yml")
        code, lines = run_move(gh, host)
        self.assertEqual(code, M.EXIT_NEED, lines)
        self.assertIn(f"warn .github/workflows/ff.yml: {said}; it stays as it is. {step}, then run look and move again "
                      f"to move its jobs", lines)
        self.assertFalse([x for x in lines if x.startswith("stop ")], lines)
        self.assertEqual(len(gh.blobs()), 1, "ci.yml still moves")
        # the page names it too, under its own heading, in the terminal's words (R202, accepted for every kind)
        unread = pr_body_of(gh).split("## Not read (left as they are)\n")[1].split("\n## ")[0]
        self.assertIn(f"- `.github/workflows/ff.yml`: {said}; it stays as it is. {step}, then run `look {REPO}` and "
                      f"`move {REPO}` again to move its jobs", unread.splitlines())
        # move --verify: a moved file that is not text now is named, never called moved
        merged = gh.blobs()[0]
        h = move_host()
        d = M.load_state(host)
        M.save_state(h, d)
        g = not_text(MoveGH({".github/workflows/ci.yml": merged}, pr_state="closed", merged_at="2026-11-22T23:00:00Z"),
                     "ci.yml")
        code, lines = run_move(g, h, verify=True)
        self.assertEqual(code, M.EXIT_STOP, lines)
        x = next(x for x in lines if ".github/workflows/ci.yml" in x and "now " + said_is in x)
        self.assertIn(step, x)
        self.assertFalse([x for x in lines if x.startswith("ok moved")], lines)
        # uninstall's put-back: the moved file that is not text now is named, with the step left, never "no workflow
        # still asks for our runner"
        gh, world, host = remove_setup()
        not_text(gh, "ci.yml")
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertFalse([x for x in lines if "still asks for our runner" in x], lines)
        x = next(x for x in lines if ".github/workflows/ci.yml" in x and "now " + said_is in x)
        self.assertIn("put its runs-on line back by hand", x)
        self.assertIn("runs-on: ubuntu-latest", x)
        self.assertEqual(gh.blobs(), [])
        self.assertFalse([x for x in lines if "no job read" in x], lines)

    def test_G3_11_a_file_whose_jobs_cannot_be_told_apart_is_not_read_and_the_others_go_on(self):
        # Row R202 (the maintainer's sighting, 2026-11-25): a job's key in another valid spelling started no job, and
        # its lines were read as part of the job above it: the page named a job moved that was not, gave a job a
        # reason that was another's, left a job out of every list, and the line written was another job's. Now a file
        # in which a line at the jobs' indent is not a plain job key is not read at all, on every road: one line
        # for it on the terminal and on the page, no job of it named, nothing written to it, and the other files
        # move as before. Each spelling is planted as the first job and as a later one, beside a job that moves
        # and beside one that stays.
        spellings = (('"second":', "double quotes"), ("'second':", "single quotes"), ("second: &s", "an anchor"),
                     ("second :", "a space before the colon"), ("sec.ond:", "an id with a dot"),
                     ("second: *base", "a job given whole as an alias"))
        base = "x-base: &base\n  runs-on: ubuntu-latest\n  steps:\n    - run: b\n"
        odd_body = "\n    runs-on: ubuntu-latest\n    steps:\n      - run: b\n"
        path = ".github/workflows/two.yml"
        for key, what in spellings:
            for neighbour in ("ubuntu-latest", "macos-latest"):
                for first in (True, False):
                    with self.subTest(what=what, neighbour=neighbour, odd_first=first):
                        plain_job = f"  first:\n    runs-on: {neighbour}\n    steps:\n      - run: a\n"
                        odd_job = "  " + key + ("\n" if key.endswith("*base") else odd_body)
                        text = ("name: two\non: [push]\n" + (base if "*base" in key else "") + "jobs:\n"
                                + (odd_job + plain_job if first else plain_job + odd_job))
                        gh = MoveGH({".github/workflows/ci.yml": CI_YML, path: text})
                        code, lines = run_move(gh, move_host())
                        # the heart: nothing written to the file, no job of it named, the one not-read line
                        written = [e["path"] for m, p, b in gh.writes if p.endswith("/git/trees") for e in b["tree"]]
                        self.assertEqual(written, [".github/workflows/ci.yml"], lines)  # the other file moves
                        self.assertFalse([x for x in lines if f"{path} job " in x], lines)
                        said = [x for x in lines if x.startswith(f"warn {path}: its line ")]
                        self.assertEqual(len(said), 1, lines)
                        self.assertIn("so this tool neither reads nor changes it; it stays as it is", said[0])
                        body = pr_body_of(gh)
                        self.assertFalse([x for x in body.splitlines() if f"`{path}` job" in x], body)
                        unread = body.split("## Not read (left as they are)\n")[1].split("\n## ")[0]
                        (page,) = [x for x in unread.splitlines() if x.startswith(f"- `{path}`: its line ")]
                        self.assertIn("it stays as it is", page)
                        self.assertEqual(code, M.EXIT_NEED, lines)
                        # look alike
                        out = Out()
                        M.look(REPO, False, host=linux_host(), gh=MoveGH({".github/workflows/ci.yml": CI_YML,
                                                                         path: text}), out=out)
                        looked = out.printed()
                        self.assertFalse([x for x in looked if f"{path} job " in x], looked)
                        self.assertEqual(len([x for x in looked if x.startswith(f"warn {path}: its line ")]), 1, looked)
                        # the words: which line, why, and the step
                        n = text.splitlines().index("  " + key) + 1
                        why = M.NOT_JOBS_READ_IS.format(n, "not a job's plain name and a colon (letters, digits, _ and -)")
                        step = "Write each job under jobs: as its plain name and a colon, all at one indent, on GitHub"
                        self.assertEqual(said[0], f"warn {path}: {why}, so this tool neither reads nor changes it; it "
                                                  f"stays as it is. {step}, then run look and move again to move its jobs")
                        self.assertIn(f"warn {path}: {why}, so this tool neither reads nor changes it. {step}, then run "
                                      f"look again to have it read", looked)
                        self.assertEqual(page, f"- `{path}`: {why}, so this tool neither reads nor changes it; it stays "
                                               f"as it is. {step}, then run `look {REPO}` and `move {REPO}` again to move "
                                               "its jobs")
        # every road: move --verify and uninstall's put-back name such a file too, as they name a file not read
        b64 = lambda t: {"content": M.base64.b64encode(t.encode()).decode()}
        text = "name: two\non: [push]\njobs:\n  first:\n    runs-on: ubuntu-latest\n  \"second\":\n    runs-on: x\n"
        self.workflow_file_unread(b64(text), M.NOT_JOBS_READ_IS.format(
            6, "not a job's plain name and a colon (letters, digits, _ and -)"),
            "Write each job under jobs: as its plain name and a colon, all at one indent, on GitHub")
        # a line less indented than the jobs is not read either; comments, blank lines and deeper lines never set a
        # file aside
        self.assertEqual(M.workflow_text(b64("on: [push]\njobs:\n    a:\n      runs-on: x\n  b:\n"))[1],
                         M.NOT_JOBS_READ_IS.format(5, "less indented than the jobs above it"))
        fine = ("on: [push]\njobs:\n# a comment\n  a:   # its comment\n\n    runs-on: x\n      # deeper\n"
                "    steps:\n      - run: |\n          echo 'b:' \"c\" :\n  b:\n    runs-on: y\n")
        self.assertEqual(M.workflow_text(b64(fine)), (fine, ""))
        self.assertEqual([j["id"] for j in M.read_workflow(fine)], ["a", "b"])
        # The jobs key itself (the maintainer's ruling, 2026-11-25): a file with no plain jobs key at the left margin
        # read as no jobs and was named nowhere, on the terminal or the page, and look said only "no job read". Now it
        # is not read and named on every road, beside a file that still moves.
        job = "    runs-on: ubuntu-latest\n    steps:\n      - run: a\n"
        head = "name: four\non: [push]\n"
        no_key = {"the key in double quotes": head + '"jobs":\n  build:\n' + job,
                  "the key in single quotes": head + "'jobs':\n  build:\n" + job,
                  "a space before the colon": head + "jobs :\n  build:\n" + job,
                  "every top-level key indented": "".join("  " + x + "\n" for x in (head + "jobs:\n  build:\n" + job)
                                                          .splitlines()),
                  "the jobs given whole on the key's line": head + "jobs: {build: {runs-on: ubuntu-latest, steps: "
                                                                   "[{run: a}]}}\n",
                  "no jobs key at all": head + "env:\n  A: b\n"}
        path = ".github/workflows/four.yml"
        step = "Write the jobs key plain at the left margin, jobs: on a line of its own, on GitHub"
        for what, text in no_key.items():
            with self.subTest(jobs_key=what):
                gh = MoveGH({".github/workflows/ci.yml": CI_YML, path: text})
                code, lines = run_move(gh, move_host())
                written = [e["path"] for m, p, b in gh.writes if p.endswith("/git/trees") for e in b["tree"]]
                self.assertEqual(written, [".github/workflows/ci.yml"], lines)
                self.assertFalse([x for x in lines if f"{path} job " in x], lines)
                said = [x for x in lines if x.startswith(f"warn {path}: ")]
                self.assertEqual(len(said), 1, lines)  # named, once: never passed over in silence
                self.assertIn("## Not read (left as they are)\n- `.github/workflows/four.yml`: ", pr_body_of(gh))
                self.assertEqual(said, [f"warn {path}: {M.NOT_JOBS_KEY_IS}, so this tool neither reads nor changes "
                                        f"it; it stays as it is. {step}, then run look and move again to move its jobs"],
                                 lines)
                self.assertEqual(code, M.EXIT_NEED, lines)
                body = pr_body_of(gh)
                self.assertIn(f"- `{path}`: {M.NOT_JOBS_KEY_IS}", body)
                self.assertFalse([x for x in body.splitlines() if f"`{path}` job" in x], body)
                # look, verify and uninstall's put-back, and the page's line, through the helper every kind walks
                self.workflow_file_unread(b64(text), M.NOT_JOBS_KEY_IS, step)
        # controls, read as before: a comment after the key, and a document start line first
        for text in (head + "jobs: # the jobs\n  build:\n" + job, "---\n" + head + "jobs:\n  build:\n" + job):
            self.assertEqual(M.workflow_text(b64(text)), (text, ""))
            self.assertEqual([j["id"] for j in M.read_workflow(text)], ["build"])

    def test_rule_31_save_state_is_the_one_writer_of_the_record(self):
        # 2026-11-24: the fixed stored marks are safe only because save_state is the one writer of
        # minions.json. Every json.dump of the tool is inside save_state; the record's name is written in one place,
        # state_path; and state_path is called only where the record is read, written by save_state, or looked for.
        with open(M.__file__, encoding="utf-8") as f:
            src = f.read()
        tree = ast.parse(src)
        where = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call):
                        f = node.func
                        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
                        if name in ("dump", "state_path"):
                            where.setdefault(name, set()).add(fn.name)
        self.assertEqual(where.get("dump"), {"save_state"})
        # R136 (item 4, 2026-11-25): names_unread reads the record to tell "could not be read" from "not there", and
        # known_folders names it as the source of a name it did not take; neither writes it
        # R205: the guard reads it first, and restore puts the copy in its place through save_state
        self.assertEqual(where.get("state_path"), {"load_state", "save_state", "record_look", "names_unread",
                                                   "known_folders", "record_guard", "guarded", "restore",
                                                   "last_whole_pass", "watch_no_record", "lost_words"})
        # R208: the watch's words, read only; (s): watch_no_record names the record that is not there and writes nothing
        docs = {id(d.body[0].value) for d in ast.walk(tree)
                if isinstance(d, (ast.Module, ast.ClassDef, ast.FunctionDef)) and d.body
                and isinstance(d.body[0], ast.Expr) and isinstance(d.body[0].value, ast.Constant)}
        names = {id(n) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and "minions.json" in n.value and id(n) not in docs}
        defs = {fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                and any(id(n) in names for n in ast.walk(fn))}
        self.assertEqual(defs, {"state_path"}, defs)
        self.assertIn("the one writer of minions.json", src[src.index("def save_state"):][:600])


def journals():
    """Every journal.log the suite's computers wrote, under both temporary homes."""
    for top in (TMP, machine_guard._TMP):
        for d, _, names in os.walk(top):
            if "journal.log" in names:
                yield os.path.join(d, "journal.log")


class ZRule31(unittest.TestCase):
    """Rule 31 (B, iv, v), 2026-11-24. Its name sorts last, and it is the
    file's last class, so it runs after every road the suite runs (unittest sorts classes by name; pytest keeps the
    file's order). It reads every line each road printed, as printed: a command stands alone on the line after the
    sentence that names it, as many as it names, and no line carries a backtick, a dollar-parenthesis or a tab where
    a shell would read it."""

    STARTS = ("ok ", "warn ", "stop ", "need you: ", "waiting ", "tell the person ", "----- ")
    # The tests that plant a value with a backtick or a dollar-parenthesis; anywhere else a "shown changed" line
    # means the tool's own words held one (its text not yet rendered), not another's value.
    CHANGED_ON_PURPOSE = ("test_rule_31_iv_names_others_chose_are_shown_changed_and_said_so_in_look_and_move",
                          "test_G6_17_with_the_user_there_its_crontab_and_at_jobs_go_before_userdel_or_the_leave_stops",
                          "test_rule_31_a_names_with_a_line_break_or_a_terminal_escape_never_make_a_line_or_a_command",
                          "test_rule_31_vi_bent_values_through_every_command",
                          "test_rule_31_vi_not_text_on_the_way_in_with_and_without_a_language",
                          "test_rule_31_vi_an_answer_that_is_not_text_is_read_and_shown_changed_never_a_traceback",
                          "test_rule_31_vi_a_line_ends_at_a_line_feed_every_command_reads_one_item_as_one",
                          "test_G6_19_a_name_with_a_line_feed_is_one_name",
                          "test_G8_23_the_move_pages_step_moves_that_one_job",  # R193: a file name planted with U+200B
                          "test_G3_8_an_include_job_word_that_matches_no_job_stops_before_any_write",  # R197: a backtick
                          "test_G8_24_a_step_never_carries_a_name_no_command_may_and_every_step_runs_as_printed")  # R200

    def test_rule_31_every_line_every_road_printed(self):
        bad, odd_starts, commands_seen = [], set(), 0
        kept = [(test, out.printed()) for test, out in PRINTED if not out.closed]
        if len(kept) < 300:
            self.skipTest(f"reads what the whole suite printed, and {len(kept)} outputs were kept: run it with the "
                          "suite")
        for test, lines in kept:
            for sentence, commands in unfold(lines):
                refs = len(REF.findall(sentence))
                if re.search("[-]", sentence):
                    bad.append((test, "a mark", sentence[:120]))
                if odd_in(sentence):  # rule 31 (a): no control, format or separator character, as printed
                    bad.append((test, f"{odd_in(sentence)} in a sentence", sentence[:120]))
                for ch in ("`", "$(", "\t"):
                    if ch in sentence:
                        bad.append((test, f"{ch!r} in a sentence", sentence[:120]))
                if M.SHOWN_CHANGED in sentence and not test.endswith(self.CHANGED_ON_PURPOSE):
                    bad.append((test, "shown changed where no test planted a value", sentence[:120]))
                if len(commands) != refs:
                    bad.append((test, f"names {refs} command(s), {len(commands)} follow", sentence[:120]))
                if not sentence.startswith(self.STARTS) and not (lines and lines[0].startswith("usage: ")):
                    # argparse's own usage text and the typed-wrong line after it (G8.3) are the one other output
                    odd_starts.add(sentence.split(" ", 1)[0][:40])
                for c in commands:
                    commands_seen += 1
                    if c.startswith(self.STARTS) or not c.strip() or c != c.strip():
                        bad.append((test, "a command line that is not a command alone", c[:120]))
                    if re.search("[-]", c) or odd_in(c):
                        bad.append((test, "a mark or a control character in a command", c[:120]))
                    for ch in ("`", "$("):  # R200: none on a command line, quoted or not (rule 31 (iv) as written)
                        if ch in c:
                            bad.append((test, f"{ch!r} on a command line", c[:120]))
                    try:
                        shlex.split(c)
                    except ValueError as e:
                        bad.append((test, f"a command the shell cannot read ({e})", c[:120]))
        hook_lines = 0
        for test, lines in HOOKS:  # the job's log on GitHub: lines of the hooks, run for real
            for line in lines:
                hook_lines += 1
                if re.search("[`\ue001-\ue004]", line) or "$(" in line or odd_in(line):
                    bad.append((test, "a hook's line in the job's log", line[:120]))
        self.assertGreater(hook_lines, 10)
        for path in journals():
            with open(path, encoding="utf-8", errors="replace", newline="") as f:  # a carriage return kept in its line
                for line in f.read().split("\n"):
                    if re.search("[`-]", line) or "$(" in line or odd_in(line):
                        bad.append((path, "a journal line", line[:120]))
        self.assertEqual(bad, [])
        self.assertGreater(commands_seen, 100)
        self.assertEqual(sorted(odd_starts), [])

def tearDownModule():
    machine_guard.check()  # rule 17 (R142): a test that reached this machine fails the run, even if it swallowed it


def damaged_records(real: bytes):
    """R205 (an error is never an absence): a record that is there and could not be read, each way."""
    yield "0 bytes", b""
    yield "cut short", real[: len(real) // 2]
    yield "not the format", b"not json\n"
    yield "not an object", b"[]\n"
    yield "runners not an object", b'{"runners": []}\n'  # setup ended in a TypeError and wrote over it
    yield "not UTF-8", b'{"runners": {"\xff": {}}}\n'
    yield "cannot be opened", None


def plant_record(path: str, real: bytes, data: bytes | None) -> bytes:
    """Writes the damage; returns the bytes the file must still hold afterwards."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(real if data is None else data)
    if data is None:
        os.chmod(path, 0)
    return real if data is None else data


def file_bytes(path: str) -> bytes | None:
    if not os.path.lexists(path):
        return None
    mode = os.stat(path).st_mode
    os.chmod(path, 0o600)
    try:
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.chmod(path, mode)


def odd_home(host, label: str) -> None:
    """Homes 1 and 5 of the walk (a space, a backslash), as the stand-in's own folders: the record moves there."""
    data = M.load_state(host)
    home = tempfile.mkdtemp(dir=TMP, prefix={"plain": "w0", "a space": "w1 x", "a backslash": "w5\\x"}[label])
    host._state = os.path.join(home, ".minions")
    host.home = lambda: home
    M.save_state(host, data)


def printed_command(lines: list[str], line: str) -> str:
    """The command printed after `line`, as the person reads it."""
    for sentence, commands in unfold(lines):
        if sentence == line:
            assert len(commands) == 1, (line, commands)
            return commands[0]
    raise AssertionError((line, lines))


class G6Record(unittest.TestCase):
    """R205, codon G6.35."""

    def test_G6_35_a_record_that_could_not_be_read_is_never_written_over_and_every_command_names_it(self):
        # Seen 2026-11-26 in a stand-in trial on 35515f9: on a damaged record, watch and look wrote a new one over it
        # without the runners, uninstall then said "nothing to uninstall" with the runner still there, status and
        # the others said "no runner of ours" without naming the file, setup ended in a TypeError.
        for name, make, run in every_command():
            probe, _ = make()
            p = M.state_path(probe)
            real = file_bytes(p) if os.path.exists(p) else b'{"looks": {}}\n'
            for dname, data in damaged_records(real):
                with self.subTest(command=name, damage=dname):
                    host, gh = make()
                    p = M.state_path(host)
                    want = plant_record(p, real, data)
                    copy_before = file_bytes(M.copy_path(host))
                    ino = os.stat(p).st_ino
                    out = Out()
                    code = run(host, gh, out)
                    lines = out.printed()
                    self.assertEqual((os.stat(p).st_ino, file_bytes(p)), (ino, want), (name, dname, lines))
                    self.assertEqual(file_bytes(M.copy_path(host)), copy_before, (name, dname))
                    self.assertEqual(code, M.EXIT_STOP, (name, dname, lines))
                    stop = [x for x in lines if x.startswith(f"stop could not read {p} (")]
                    self.assertEqual(len(stop), 1, (name, dname, lines))
                    self.assertIn("so nothing is deleted on its word", stop[0])
                    self.assertTrue(G8Words.NEXT.search(stop[0]), stop[0])
                    self.assertIn(M.watch_unread(), stop[0])  # What the watch does not do
                    self.assertNotIn("Traceback", out.getvalue())
                    if dname == "not UTF-8":
                        self.assertNotIn("\\xff", stop[0])  # where the read stopped, never a value of the file

    def test_G6_35_the_way_out_is_restore_as_printed_then_status_then_uninstall_to_nothing_of_ours(self):
        # One command of the tool's own, taken as printed, in a plain home and in homes 1
        # and 5 of the walk; then status reads the record; then uninstall leaves nothing of ours and 0 runners.
        for label in ("plain", "a space", "a backslash"):
            with self.subTest(home=label):
                gh, world, host = remove_setup(moved=False)
                odd_home(host, label)
                p = M.state_path(host)
                good = file_bytes(p)
                plant_record(p, good, b'{"runners": {"example-org/exa')
                out = Out()
                self.assertEqual(M.status(REPO, host=host, gh=StatusGH(), out=out), M.EXIT_STOP)
                lines = out.printed()
                stop = next(x for x in lines if x.startswith("stop could not read "))
                self.assertIn("(cut short at line 1 column ", stop)
                self.assertIn(f"its second copy, {M.copy_path(host)}, reads whole; it was written at ", stop)
                self.assertIn("anything this tool made here after that time is not in it", stop)
                command = printed_command(lines, stop)
                argv = shlex.split(command)
                self.assertEqual(argv, ["restore"], command)  # the tool's own command, as its steps print them
                out = Out()
                self.assertEqual(M.main(argv, host=host, out=out), M.EXIT_OK, out.printed())
                said = out.printed()
                self.assertTrue(said[0].startswith(f"ok {p} could not be read (cut short"), said)
                self.assertIn("anything this tool made here after that time is not in it", said[0])  # before it acts
                aside = M.aside_files(host)
                self.assertEqual(len(aside), 1, os.listdir(host.state_dir()))
                self.assertEqual(file_bytes(os.path.join(host.state_dir(), aside[0])), b'{"runners": {"example-org/exa')
                self.assertEqual(file_bytes(p), good)
                self.assertIn(f"kept as {os.path.join(host.state_dir(), aside[0])}", said[-1])
                out = Out()
                M.status(REPO, host=host, gh=StatusGH(), out=out)
                self.assertFalse([x for x in out.printed() if "could not read" in x], out.printed())
                self.assertTrue([x for x in out.printed() if x.startswith(f"ok {OURS} for {REPO}: ")], out.printed())
                code, lines = run_uninstall(gh, host, repo=None, everything=True)
                self.assertEqual(code, M.EXIT_OK, lines)
                self.assertIsNone(gh.runner)  # 0 runners on GitHub
                o = world.order
                for step in (f"stop {UNIT}", "userdel"):
                    self.assertIn(step, o)
                self.assertIn(f"ok kept in {host.state_dir()}: {aside[0]}: {M.ASIDE_WHY}", lines)
                self.assertEqual(sorted(os.listdir(host.state_dir())), aside)  # nothing else of ours
                self.assertTrue([x for x in lines if x.startswith("ok this tool is off this computer")], lines)

    def test_G6_35_uninstall_after_the_watch_on_a_damaged_record_still_finds_the_runner(self):
        gh, world, host = remove_setup(moved=False)
        open(M.state_path(host), "wb").close()
        M.watch(host=host, gh=StatusGH(), out=Out())
        code, lines = run_uninstall(gh, host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertFalse([x for x in lines if "nothing to uninstall" in x], lines)
        self.assertNotIn(f"stop {UNIT}", world.order)
        self.assertIsNotNone(gh.runner)

    def test_G6_35_with_the_record_gone_and_its_copy_there_no_command_by_hand_reads_the_home_as_fresh(self):
        # R205, the record gone and its copy there: with the record removed and the tool's
        # own second copy lying in its folder, doctor and uninstall REPO said "nothing to check / nothing to
        # uninstall" (exit 0), status sent the person to setup, setup to look, and uninstall --all took the watch
        # off, all while the runner served. Now every guarded command stops before it changes or asks anything, as
        # the watch does: the copy reads whole, the step is restore; then the same command works.
        def runs(host, gh):
            sgh = StatusGH()
            return [
                ("status", lambda o: M.status(REPO, host=host, gh=sgh, out=o), sgh),
                ("status --rerun", lambda o: M.status(REPO, host=host, gh=sgh, out=o, rerun=9001), sgh),
                ("doctor", lambda o: M.doctor(host=host, gh=sgh, out=o, interactive=True), sgh),
                ("setup", lambda o: M.setup(REPO, host=host, gh=gh, out=o, interactive=True), gh),
                ("look", lambda o: M.look(REPO, False, host=host, gh=gh, out=o), gh),
                ("move", lambda o: M.move(REPO, host=host, gh=gh, out=o), gh),
                ("drain", lambda o: M.drain(REPO, host=host, gh=gh, out=o, interactive=True), gh),
                ("resume", lambda o: M.resume(REPO, host=host, gh=gh, out=o), gh),
                ("uninstall REPO", lambda o: M.uninstall(REPO, host=host, gh=gh, out=o, interactive=True), gh),
                ("uninstall --all", lambda o: M.uninstall(None, everything=True, host=host, gh=gh, out=o,
                                                          interactive=True), gh),
            ]
        fresh = ("no runner of ours", "nothing to uninstall", "nothing to check", "no look of ")
        for i in range(10):
            for copy_kind in ("whole", "0 bytes"):
                gh, world, host = remove_setup(moved=False)
                name, run, asked = runs(host, gh)[i]
                with self.subTest(command=name, copy=copy_kind):
                    p, c = M.state_path(host), M.copy_path(host)
                    os.remove(p)
                    if copy_kind == "0 bytes":
                        open(c, "wb").close()
                    good = file_bytes(c)
                    calls, ran = len(asked.calls), len(host.ran)
                    out = Out()
                    self.assertEqual(run(out), M.EXIT_STOP, out.printed())
                    said = out.printed()
                    stop = [x for x in said if x.startswith("stop ")]
                    self.assertEqual(len(stop), 1, said)
                    self.assertIn(f"this tool's record, {p}, is not there, but its second copy, {c}, is", stop[0])
                    self.assertIn("removed while this tool's parts stay here", stop[0])
                    self.assertFalse([x for x in said if any(f in x for f in fresh)], said)
                    self.assertEqual((len(asked.calls), len(host.ran), world.order), (calls, ran, []),
                                     "nothing changed or asked, here or of GitHub")
                    self.assertEqual(world.active[f"{M.WATCH_UNIT}.timer"], "active")
                    self.assertIsNotNone(gh.runner)
                    self.assertFalse(os.path.lexists(p))
                    self.assertEqual(file_bytes(c), good)
                    if copy_kind == "0 bytes":
                        self.assertIn(f"its second copy, {c}, could not be read either (0 bytes)", stop[0])
                        continue
                    self.assertTrue(stop[0].endswith("the command below; then run the same command again"), stop[0])
                    self.assertTrue(said[said.index(stop[0]) + 1].split()[-1:] == ["restore"], said)
                    out = Out()
                    self.assertEqual(M.main(["restore"], host=host, out=out), M.EXIT_OK, out.printed())
                    out = Out()
                    code = run(out)
                    # restore's own journal line is one more in the journal that uninstall --all keeps and counts
                    said = [re.sub(r"\d+ lines in", "N lines in", x.replace(host.state_dir(), "STATE"))
                            for x in out.printed()]
                    self.assertFalse([x for x in said if "is not there, but its second copy" in x], said)
                    # "Then the same command works": it does what it does on a twin home whose record was never
                    # taken (this stand-in has no look saved, so setup and move ask for one there too).
                    tgh, _tworld, thost = remove_setup(moved=False)
                    _tname, trun, _tasked = runs(thost, tgh)[i]
                    tout = Out()
                    tcode = trun(tout)
                    self.assertEqual((code, said), (tcode, [re.sub(r"\d+ lines in", "N lines in", x.replace(thost.state_dir(), "STATE"))
                                                            for x in tout.printed()]), name)
                    if name not in ("setup", "move"):
                        self.assertFalse([x for x in said if any(f in x for f in fresh)], (name, said))

    def test_G6_35_a_cut_at_each_removal_of_uninstall_all_s_last_stretch_leaves_what_the_next_one_finishes(self):
        # R205: uninstall --all ends by deleting this tool's files in its state folder one by one. A cut (power, a
        # kill) between two of those removals must never leave the second copy there with the record gone, and the
        # same command run again must finish from what the cut left: the folder gone, nothing of ours on GitHub.
        class Cut(BaseException):
            pass

        removed, k = [], 0
        while True:
            gh, world, host = remove_setup(moved=False)
            sd = host.state_dir()
            real_remove, real_rmdir, n = os.remove, os.rmdir, [0]

            def cutting(fn, k=k, sd=sd, n=n):
                def f(p, *a, **kw):
                    if os.path.abspath(p) == sd or os.path.dirname(os.path.abspath(p)) == sd:
                        if n[0] == k:
                            raise Cut("(the folder)" if os.path.abspath(p) == sd else os.path.basename(p))
                        n[0] += 1
                    return fn(p, *a, **kw)
                return f
            os.remove, os.rmdir = cutting(real_remove), cutting(real_rmdir)
            try:
                code, lines = run_uninstall(gh, host, repo=None, everything=True)
            except Cut as e:
                code, lines, at = "cut", [], str(e)
            finally:
                os.remove, os.rmdir = real_remove, real_rmdir
            if code != "cut":
                self.assertEqual(code, M.EXIT_OK, lines)
                break
            removed.append(at)
            with self.subTest(cut_at=at):
                self.assertFalse(os.path.lexists(M.copy_path(host)) and not os.path.lexists(M.state_path(host)),
                                 (at, os.listdir(sd)))
                code, lines = run_uninstall(gh, host, repo=None, everything=True)
                self.assertEqual(code, M.EXIT_OK, lines)
                self.assertFalse([x for x in lines if "is not there, but its second copy" in x], lines)
                self.assertFalse(os.path.lexists(sd), lines)
                self.assertIsNone(gh.runner)
            k += 1
        self.assertEqual(removed[0], M.RECORD_COPY, removed)  # the copy first, the lock last
        self.assertLess(removed.index(M.RECORD_COPY), removed.index("minions.json"), removed)
        self.assertEqual(removed[-2], "minions.lock", removed)
        self.assertEqual(removed[-1], "(the folder)", removed)  # then the folder itself, the last removal

    def test_G6_35_with_no_copy_that_reads_the_stop_says_so_and_names_the_readme_place(self):
        # Both copies unreadable, or no copy: no road in the tool, said plainly, with the
        # README's place that lists what the tool makes and where GitHub lists a repository's runners.
        for cdamage in (None, b"", b"[]"):
            with self.subTest(copy=cdamage):
                gh, world, host = remove_setup(moved=False)
                p, c = M.state_path(host), M.copy_path(host)
                plant_record(p, b"", b"")
                if cdamage is None:
                    os.remove(c)
                else:
                    plant_record(c, b"", cdamage)
                before = {n: file_bytes(os.path.join(host.state_dir(), n)) for n in os.listdir(host.state_dir())}
                for run in (lambda o: M.status(REPO, host=host, gh=StatusGH(), out=o),
                            lambda o: M.main(["restore"], host=host, out=o)):
                    out = Out()
                    self.assertEqual(run(out), M.EXIT_STOP, out.printed())
                    stop = next(x for x in out.printed() if x.startswith(f"stop could not read {p} (0 bytes)"))
                    self.assertIn("is not there" if cdamage is None else "could not be read either (", stop)
                    self.assertIn("it has no road of its own back", stop)
                    self.assertIn(f"\"{M.README_MAKES_HEAD}\" ({M.README_MAKES})", stop)
                    self.assertIn("GitHub lists a repository's runners in its Settings, under Actions, then Runners", stop)
                    self.assertTrue(G8Words.NEXT.search(stop), stop)
                    self.assertFalse([x for x in out.printed() if x.endswith(" restore")], out.printed())
                self.assertEqual({n: file_bytes(os.path.join(host.state_dir(), n))
                                  for n in os.listdir(host.state_dir())}, before)
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        self.assertIn(f"\n## {M.README_MAKES_HEAD}\n", readme)
        self.assertEqual(M.README_MAKES.split("#")[1], M.README_MAKES_HEAD.lower().replace(" ", "-"))
        part = readme.split(f"## {M.README_MAKES_HEAD}\n", 1)[1].split("\n## ", 1)[0]
        for place in (M.RUNNER_USER, M.RUNNERS_HOME, M.SLICE, f"{M.WATCH_UNIT}.timer", M.HOOKS_ROOT, M.RECORDS_ROOT,
                      "~/.minions", f"~/{M.RECORD_NAME}", "actions.runner.", "Settings", "Runners"):
            self.assertIn(place, part, place)

    def test_G6_35_the_watch_says_its_line_when_the_state_begins_and_changes_never_each_pass(self):
        # Said when the state begins and when it changes; status says it while it stands.
        gh, world, host = remove_setup(moved=False)
        p = M.state_path(host)
        jpath = os.path.join(host.state_dir(), "journal.log")

        def a_pass():
            out = Out()
            code = M.watch(host=host, gh=StatusGH(), out=out)
            with open(jpath, encoding="utf-8") as f:
                return code, out.printed(), [x for x in f.read().splitlines() if x.split(" ", 2)[1:2] == ["watch"]]

        plant_record(p, b"", b"")
        code, lines, said = a_pass()
        self.assertEqual(code, M.EXIT_STOP)
        self.assertTrue(lines[0].startswith(f"stop could not read {p} (0 bytes)"), lines)
        self.assertTrue(said[-1].endswith(f"watch its record could not be read: {p} (0 bytes)"), said)
        for _ in range(3):  # the state stands: nothing said, nothing written
            self.assertEqual(a_pass(), (M.EXIT_OK, [], said))
        out = Out()
        M.status(REPO, host=host, gh=StatusGH(), out=out)
        self.assertIn(M.watch_unread(), out.printed()[0])
        plant_record(p, b"", b"not json\n")  # it changes: said again
        code, lines, said2 = a_pass()
        self.assertEqual((code, len(said2)), (M.EXIT_STOP, len(said) + 1), lines)
        self.assertIn("(not the format at line 1 column 1)", lines[0])
        self.assertEqual(M.main(["restore"], host=host, out=Out()), M.EXIT_OK)
        code, lines, said3 = a_pass()  # the record reads again: the watch's own pass, a line of its own
        self.assertEqual(len(said3), len(said2) + 1)
        self.assertNotIn("could not be read", said3[-1])
        plant_record(p, b"", b"")
        os.chmod(jpath, 0)  # a journal that could not be read: the line is said at each pass, never kept back
        try:
            self.assertEqual(M.watch(host=host, gh=StatusGH(), out=Out()), M.EXIT_STOP)
            self.assertEqual(M.watch(host=host, gh=StatusGH(), out=Out()), M.EXIT_STOP)
        finally:
            os.chmod(jpath, 0o600)

    def test_G6_35_save_state_never_writes_over_a_record_that_could_not_be_read_and_sets_a_bad_copy_aside(self):
        host = linux_host()
        Clock(host)
        M.save_state(host, {"runners": {}})
        p, c = M.state_path(host), M.copy_path(host)
        self.assertEqual(file_bytes(p), file_bytes(c))
        plant_record(p, b"", b"[1")
        with self.assertRaises(M.RecordUnread) as e:
            M.save_state(host, {"runners": {"x/y": {}}})
        self.assertEqual((e.exception.path, e.exception.why), (p, "cut short at line 1 column 3"))
        self.assertEqual(file_bytes(p), b"[1")
        with self.assertRaises(M.RecordUnread):
            M.load_state(host)
        os.remove(p)
        self.assertEqual(M.load_state(host), {})  # not there: "no", as before
        plant_record(c, b"", b"")
        M.save_state(host, {"runners": {}})
        aside = M.aside_files(host)
        self.assertEqual([x.split(".unread-")[0] for x in aside], ["minions.copy.json"])
        self.assertEqual(file_bytes(os.path.join(host.state_dir(), aside[0])), b"")
        self.assertEqual(file_bytes(p), file_bytes(c))
        out = Out()
        self.assertEqual(M.main(["restore"], host=host, out=out), M.EXIT_OK)
        self.assertEqual(out.printed(), [f"ok {p} reads whole: nothing to restore, and nothing was changed"])


ENV_KEY = ("sudo", "-n", "-u", "minions-runner", "--", "sh", "-c", getattr(M, "ENV_SH", "R206: not in this code"),  # getattr: red, not a collection stop, on the code before R206
           "sh", f"{FOLDER}/.env")
CAT_ENV = ("sudo", "-n", "-u", "minions-runner", "--", "cat", f"{FOLDER}/.env")
PERSONS_ENV = "LANG=C.UTF-8\nJAVA_HOME=/opt/java\nMADE_BY_THE_PERSON=made-value-r206\n"


# R208 (G6.36): one lock per home. The writers here are real processes on real files in
# a made home, each one of this tool's commands by its name, through the tool's own guard.
TOOLS_ENV = ("LANG=C.UTF-8\nJAVA_HOME=/opt/java\nACTIONS_RUNNER_HOOK_JOB_STARTED=/x/job-started.sh\n"
             "ACTIONS_RUNNER_HOOK_JOB_COMPLETED=/x/job-completed.sh\n")  # as setup's install_hooks wrote it
ADDED_ENV = TOOLS_ENV + "MADE_BY_THE_PERSON=made-value-r209\n"  # the usual way to give jobs a variable: a line added


def env_world(env, env_sum=None, aside=None):
    gh, world, host = remove_setup()
    world.files[f"{FOLDER}/.env"] = env
    world.paths.add(f"{FOLDER}/.env")
    d = M.load_state(host)
    if env_sum is not None:
        d["runners"][REPO]["env_sum"] = env_sum
    if aside is not None:
        d["runners"][REPO]["env_aside"] = aside
    M.save_state(host, d)
    return gh, world, host


def kept_of(host):
    return {k: v for k, v in host.files.items() if k.startswith(host.record_dir() + "/" + M.KEPT_ENV)}


class G6Env(unittest.TestCase):
    """R209: no road of the tool deletes a runner's .env that is not shown to
    be as the tool left it; such a file is set aside as the person's, named by path with its lines by count."""

    def test_G6_37_a_line_the_person_added_to_an_installed_runner_s_env_is_kept_by_uninstall_never_deleted(self):
        # THE ORDINARY WORLD (red on 35515f9): an installed runner, a line added to its .env, then uninstall
        gh, world, host = env_world(ADDED_ENV, M.env_sum(TOOLS_ENV))
        code, lines = run_uninstall(gh, host)
        self.assertEqual(code, M.EXIT_OK, lines)
        kept = kept_of(host)
        self.assertEqual(list(kept.values()), [ADDED_ENV], "kept byte for byte")
        (path,) = kept
        self.assertEqual(host.modes[path], 0o600)
        self.assertRegex(os.path.basename(path), r"^kept-env-example-org_example-repo-\.env-\d{8}T\d{6}Z$")
        line = [x for x in lines if f"{FOLDER}/.env is a settings file of the runner's" in x]
        self.assertEqual(len(line), 1, lines)
        self.assertIn(f"its lines are not the ones this tool wrote there): it is kept as yours in {path} (yours "
                      "alone, mode 600; 5 lines, naming ACTIONS_RUNNER_HOOK_JOB_COMPLETED, ACTIONS_RUNNER_HOOK_JOB_STARTED, "
                      "JAVA_HOME, LANG, MADE_BY_THE_PERSON), never deleted; the folder goes", line[0])
        # nothing of it printed, logged or journaled: names and counts only
        self.assertNotIn("made-value-r209", "\n".join(lines))
        self.assertNotIn("made-value-r209", host.files.get(os.path.join(host.state_dir(), "journal.log"), "")
                         + "".join(v for k, v in host.files.items() if k.endswith("journal.log")))
        self.assertIn(("sudo", "-n", "-u", "minions-runner", "--", "cat", f"{FOLDER}/.env"), host.ran, "as the runner user")
        self.assertNotIn(("sudo", "-n", "cat", f"{FOLDER}/.env"), host.ran, "never as root")

    def test_G6_37_a_env_as_the_tool_left_it_goes_with_the_folder_and_one_with_no_sum_on_record_is_kept(self):
        # (a) three answers: yes (goes, as today); no; could not be read
        gh, world, host = env_world(TOOLS_ENV, M.env_sum(TOOLS_ENV))
        code, lines = run_uninstall(gh, host)
        self.assertEqual((code, kept_of(host)), (M.EXIT_OK, {}), lines)
        gh, world, host = env_world(TOOLS_ENV)  # a setup cut before it kept a sum: the person's, kept
        code, lines = run_uninstall(gh, host)
        self.assertEqual((code, list(kept_of(host).values())), (M.EXIT_OK, [TOOLS_ENV]), lines)
        self.assertTrue(any("this tool kept no sum of it" in x for x in lines), lines)
        # could not be read (the runner user cannot read it): never deleted; the folder stays, named; uninstall
        # goes to its end saying so
        gh, world, host = env_world(ADDED_ENV, M.env_sum(TOOLS_ENV))
        world.unreadable = {f"{FOLDER}/.env"}
        code, lines = run_uninstall(gh, host)
        self.assertEqual(kept_of(host), {})
        self.assertNotIn(f"empty {FOLDER}", world.order, "nothing in that folder deleted")
        self.assertTrue(any(f"{FOLDER}/.env could not be read as minions-runner (the user minions-runner cannot read "
                            "it), so it is not known to be as this tool left it (so the folder stays with it)" in x
                            for x in lines), lines)
        # its kind is read first: a link or a folder is never opened (clear_folder rules on it, as before)
        for kind in ("link", "folder"):
            gh, world, host = env_world("")
            world.files.pop(f"{FOLDER}/.env")
            (world.links if kind == "link" else world.dirs).add(f"{FOLDER}/.env")
            code, lines = run_uninstall(gh, host)
            self.assertNotIn(("sudo", "-n", "-u", "minions-runner", "--", "cat", f"{FOLDER}/.env"), host.ran, kind)
            self.assertEqual(kept_of(host), {})

    def test_G6_37_a_kept_copy_that_cannot_be_made_or_does_not_read_back_leaves_the_env_and_its_folder(self):
        # (c): the copy cannot be made (the record cannot be written): the .env is not deleted, the folder stays
        gh, world, host = env_world(ADDED_ENV, M.env_sum(TOOLS_ENV))
        host.record_unwritable = True
        code, lines = run_uninstall(gh, host)
        self.assertNotIn(f"empty {FOLDER}", world.order)
        self.assertTrue(any("its copy could not be made in your record" in x and "so it is not deleted" in x
                            for x in lines), lines)
        # it does not read back the same as the original: not deleted
        gh, world, host = env_world(ADDED_ENV, M.env_sum(TOOLS_ENV))
        host.read_private = lambda path: b"other bytes"
        code, lines = run_uninstall(gh, host)
        self.assertNotIn(f"empty {FOLDER}", world.order)
        self.assertTrue(any("does not read back the same as the original, so the original is not deleted" in x
                            for x in lines), lines)
        # a size bound before the read
        gh, world, host = env_world("A=" + "x" * (M.ENV_MAX + 1) + "\n", M.env_sum(TOOLS_ENV))
        code, lines = run_uninstall(gh, host)
        self.assertNotIn(("sudo", "-n", "-u", "minions-runner", "--", "cat", f"{FOLDER}/.env"), host.ran)
        self.assertTrue(any(f"is larger than {M.ENV_MAX} bytes, so it is not read" in x for x in lines), lines)
        # two events: two names, never one over the other
        gh, world, host = env_world(ADDED_ENV, M.env_sum(TOOLS_ENV))
        first = M.keep_copy(host, REPO, f"{FOLDER}/.env", ADDED_ENV, M.env_sum(ADDED_ENV))
        again = M.keep_copy(host, REPO, f"{FOLDER}/.env", ADDED_ENV, M.env_sum(ADDED_ENV))
        self.assertEqual((first[1], again[1]), ("", ""))
        self.assertNotEqual(first[0], again[0])

    def test_G6_37_setup_again_over_a_cut_setup_keeps_the_env_before_any_delete_after_r206_s_step(self):
        # (f), (g): in the SAME stand-in computer, with setting_up in the record (no fresh look over it): R206's stop,
        # its step's effect (the file readable to the runner user), setup again: the .env kept before any delete
        # (a finished setup after it is walk (b), on the guest)
        host, sha = setup_host()
        host.runs[ENV_KEY] = (0, "minions-env: not readable\n")
        fresh_look(host)
        listing = ("GET", f"repos/{REPO}/actions/runners?per_page=100")
        none = (200, {"total_count": 0, "runners": []})
        code, lines = run_setup(setup_gh(sha, {listing: none}), host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        chown = (f"`sudo chown -h minions-runner {FOLDER}/.env && sudo -u minions-runner chmod u+rw {FOLDER}/.env`")
        self.assertTrue(any(x.startswith("stop could not read") and x.endswith(chown) for x in lines), lines)
        self.assertIn(REPO, M.load_state(host).get("setting_up") or {})
        host.runs[ENV_KEY] = (0, "minions-env: readable\n")  # the step's effect
        host.runs[CAT_ENV] = (0, PERSONS_ENV)
        host.runs[("sudo", "-n", "-u", "minions-runner", "--", "stat", "-c", "%s", f"{FOLDER}/.env")] = (0, "70\n")
        host.runs[("sudo", "-n", "-u", "minions-runner", "--", "sha256sum", f"{FOLDER}/.env")] = (
            0, f"{M.env_sum(PERSONS_ENV)}  {FOLDER}/.env\n")
        token = ("POST", f"repos/{REPO}/actions/runners/registration-token")
        gh = setup_gh(sha)
        gh.overrides[listing] = lambda body: runners_answer() if token in gh.calls else none
        host.runs[R_TEST + (FOLDER,)] = (0, "")  # the half-made folder is there
        ras = ("sudo", "-n", "-u", "minions-runner", "--")
        host.runs[ras + ("find", FOLDER, "-maxdepth", "0", "-printf", "%D %y\n")] = (0, "2049 d\n")
        host.runs[ras + ("find", FOLDER, "-xdev", "-mindepth", "1", "!", "-user", "minions-runner", "-prune", "-printf",
                         "F\t%D\t%u\t%y\t%P\\0", "-o", "-printf", "O\t%D\t%u\t%y\t%P\\0")] = (0, "")  # emptied
        host.ran.clear()
        code, lines = run_setup(gh, host)
        # the finished setup after it is walk (b) on the guest (this stand-in does not model the folder's delete)
        self.assertFalse([x for x in lines if x.startswith("stop") and ".env" in x], lines)
        self.assertEqual(list(kept_of(host).values()), [PERSONS_ENV], "the .env's bytes in the kept place")
        cat = host.ran.index(CAT_ENV)
        dels = [i for i, a in enumerate(host.ran) if "-delete" in a and FOLDER in a]
        self.assertTrue(dels and cat < min(dels), "kept and compared before anything in that folder is deleted")
        # Unreadable at setup again: setup stops before any delete in that folder, with one step that can be taken
        host, sha = setup_host()
        host.runs[ENV_KEY] = (0, "minions-env: not readable\n")
        fresh_look(host)
        run_setup(setup_gh(sha, {listing: none}), host)
        host.ran.clear()
        gh = setup_gh(sha)
        gh.overrides[listing] = lambda body: runners_answer() if token in gh.calls else none
        host.runs[R_TEST + (FOLDER,)] = (0, "")
        code, lines = run_setup(gh, host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertFalse([a for a in host.ran if "-delete" in a and FOLDER in a])
        self.assertTrue(any(x.startswith("stop ") and chown in x for x in lines), lines)

    def test_G6_37_setup_again_over_a_cut_setup_names_a_step_that_ends_its_stop_for_each_kind(self):
        # R209: setup again over a cut setup printed R206's chown line for
        # every kind of .env that stays, so for a file over the bound, a size not read, a copy that could not be made
        # or does not read back, and for the name R206's step moved it to, that step changed nothing and the same
        # stop came again without end. Each kind now names a step that ends it, taken here: where no command fits,
        # words, and for the person's own file the word is move, never remove.
        listing = ("GET", f"repos/{REPO}/actions/runners?per_page=100")
        none = (200, {"total_count": 0, "runners": []})
        token = ("POST", f"repos/{REPO}/actions/runners/registration-token")
        ras = ("sudo", "-n", "-u", "minions-runner", "--")
        aside = f"{FOLDER}/.env.kept-20261126T120000Z"

        def env_key(path):
            return ENV_KEY[:-1] + (path,)

        def readable(host, path, text=PERSONS_ENV, size=None, sum_=None):
            host.runs[env_key(path)] = (0, "minions-env: readable\n")
            host.runs[ras + ("stat", "-c", "%s", path)] = size or (0, f"{len(text)}\n")
            host.runs[ras + ("cat", path)] = (0, text)
            host.runs[ras + ("sha256sum", path)] = (0, f"{sum_ or M.env_sum(text)}  {path}\n")

        def gone(host, path):  # the person moved it out of the folder: nothing stands at that name
            host.runs[env_key(path)] = (0, "minions-env: not there\n")

        def cut_setup():
            host, sha = setup_host()
            host.runs[ENV_KEY] = (0, "minions-env: not readable\n")
            fresh_look(host)
            run_setup(setup_gh(sha, {listing: none}), host)
            host.runs[R_TEST + (FOLDER,)] = (0, "")
            host.runs[ras + ("find", FOLDER, "-maxdepth", "0", "-printf", "%D %y\n")] = (0, "2049 d\n")
            host.runs[ras + ("find", FOLDER, "-xdev", "-mindepth", "1", "!", "-user", "minions-runner", "-prune",
                             "-printf", "F\t%D\t%u\t%y\t%P\\0", "-o", "-printf", "O\t%D\t%u\t%y\t%P\\0")] = (0, "")
            return host, sha

        def again(host, sha):
            gh = setup_gh(sha)
            gh.overrides[listing] = lambda body: runners_answer() if token in gh.calls else none
            host.ran.clear()
            return run_setup(gh, host)

        def no_privacy(host):
            def refuse(name, data):
                raise PermissionError(13, "Permission denied")
            host.keep_private = refuse

        kinds = [  # (what stands, the step's words, what taking it as printed does)
            ("over the bound", lambda h: readable(h, f"{FOLDER}/.env", size=(0, f"{M.ENV_MAX + 1}\n")),
             "moves ", lambda h: gone(h, f"{FOLDER}/.env")),
            ("a size not read", lambda h: readable(h, f"{FOLDER}/.env", size=(1, "")),
             "moves ", lambda h: gone(h, f"{FOLDER}/.env")),
            ("a copy that cannot be made", lambda h: (readable(h, f"{FOLDER}/.env"), no_privacy(h)),
             "makes room in ", lambda h: h.__dict__.pop("keep_private")),
            ("a copy that does not read back", lambda h: readable(h, f"{FOLDER}/.env", sum_="0" * 64),
             "moves ", lambda h: gone(h, f"{FOLDER}/.env")),
            ("the name R206's step moved it to", lambda h: (gone(h, f"{FOLDER}/.env"),
                                                             h.runs.__setitem__(env_key(aside),
                                                                                (0, "minions-env: not readable\n")),
                                                             M.note_setting(h, REPO, "env_aside", aside)),
             "makes it readable to the runner user, its lines unchanged, and setup then keeps it for you: "
             f"`sudo chown -h minions-runner {aside} && sudo -u minions-runner chmod u+rw {aside}`",
             lambda h: readable(h, aside)),
        ]
        for label, plant, step, take in kinds:
            with self.subTest(kind=label):
                host, sha = cut_setup()
                plant(host)
                code, lines = again(host, sha)
                self.assertEqual(code, M.EXIT_STOP, lines)
                stop = [x for x in lines if x.startswith("stop ") and ".env" in x]
                self.assertEqual(len(stop), 1, lines)
                self.assertIn("Your next step " + step, stop[0])
                self.assertTrue(stop[0].endswith(" Then run the same setup again"), stop[0])
                if step.startswith("moves "):
                    self.assertIn("(it is your file: move it, do not delete it)", stop[0])
                self.assertNotIn(f"chmod u+rw {FOLDER}/.env`", stop[0], "R206's line only where it can end the stop")
                self.assertFalse([a for a in host.ran if "-delete" in a and FOLDER in a], "nothing deleted")
                take(host)  # the step as printed, its effect on this computer
                code, lines = again(host, sha)
                self.assertFalse([x for x in lines if x.startswith("stop") and ".env" in x], (label, lines))

    def test_G6_37_setup_keeps_the_sum_of_what_it_wrote_and_the_name_r206_s_step_moves_it_to_is_read_too(self):
        host, sha = setup_host()
        fresh_look(host)
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_OK, lines)
        tee = [a for a in host.ran if a[-2:] == ("tee", f"{FOLDER}/.env")]
        self.assertEqual(len(tee), 1)
        self.assertRegex(M.load_state(host)["runners"][REPO]["env_sum"], r"^[0-9a-f]{64}$")
        self.assertIn("note_setting(host, repo, \"env_sum\", env_sum(text))", inspect_source(M.install_hooks))
        # R206's mv step moves it to a dated name inside the folder: that name is on record and kept the same way
        aside = f"{FOLDER}/.env.kept-20261126T120000Z"
        gh, world, host = env_world(TOOLS_ENV, M.env_sum(TOOLS_ENV), aside=aside)
        world.files[aside] = PERSONS_ENV
        code, lines = run_uninstall(gh, host)
        self.assertEqual(list(kept_of(host).values()), [PERSONS_ENV], lines)

    def test_G6_37_delete_record_names_the_kept_settings_files_before_the_word_is_typed(self):
        # (e): the prompt says how many kept settings files the record holds, by name; the word takes them with it
        host = linux_host()
        host.tty = True
        host.files[f"{host.record_dir()}/journal.log"] = "x\n"
        host.files[f"{host.record_dir()}/kept-env-example-org_example-repo-.env-20261126T120000Z"] = "A=1\n"
        rep = M.Report(Out())
        host.answer = "delete"
        M.delete_record_if_typed(host, rep)
        self.assertIn("It holds 1 settings file of a runner's kept for you, which go with it: "
                      "kept-env-example-org_example-repo-.env-20261126T120000Z.", host.asked)
        self.assertTrue(re.fullmatch(M.RECORD_NAMES, "kept-env-example-org_example-repo-.env-20261126T120000Z"))


LOCK_HELPER = r'''
import io, json, os, random, sys, time
tree, kind, home, pairs, gap, seed = sys.argv[1:7]
pairs, gap = int(pairs), float(gap) / 1000
extra = sys.argv[7:]
os.environ["HOME"] = home
sys.path.insert(0, tree)
import minions as M
random.seed(int(seed))
guard = getattr(M, "record_guard", lambda fn: fn)  # 35515f9 has no guard: its commands write as they always did
held = {"n": 0}
if hasattr(M, "lock_try"):
    real_try = M.lock_try
    def counted(fd):
        a = real_try(fd)
        held["n"] += a[0] == "held"
        return a
    M.lock_try = counted
ran = {"whole": 0, "i": 0}

@guard
def watch(host=None, out=None):
    d = M.load_state(host)
    time.sleep(random.uniform(0, gap))  # the GitHub calls' time
    d = M.load_state(host)  # the watch reads again before its own save, as watch() does
    w = d.get("watch") or {}
    d["watch"] = {**w, "pass": int(w.get("pass") or 0) + 1, "time": M.stamp(host.now())}
    M.save_state(host, d)
    M.journal(host, "watch", "a whole pass")
    ran["whole"] += 1
    return 0

@guard
def look(host=None, out=None):
    d = M.load_state(host)
    time.sleep(random.uniform(0, gap))  # its own work's time
    d.setdefault("looks", {})[f"o/r{ran['i']}"] = {"t": ran["i"]}
    M.save_state(host, d)
    ran["whole"] += 1
    return 0

@guard
def move(host=None, out=None):  # a command that works for longer than several passes beside the watch
    d = M.load_state(host)
    open(extra[0], "w").close()  # it holds the lock now
    time.sleep(float(extra[1]))
    d.setdefault("moves", {})["o/long"] = {"t": 1}
    M.save_state(host, d)
    ran["whole"] += 1
    return 0

@guard
def setup(host=None, out=None):  # holds the lock until told, then ends; or starts a long child and waits on it
    open(extra[0], "w").close()
    if extra[1] == "child":
        M.Host().run(["sleep", "60"], timeout=120)
    end = time.monotonic() + 120  # bounded: a test that fails before its go file never leaves this behind
    while not os.path.exists(extra[1]) and time.monotonic() < end:
        time.sleep(0.02)
    d = M.load_state(host)
    d.setdefault("looks", {})["o/held"] = {"t": 1}
    M.save_state(host, d)
    return 0

@guard
def uninstall(host=None, out=None):  # holds the lock, then takes this tool's folder away as uninstall --all does
    open(extra[0], "w").close()
    end = time.monotonic() + 120  # bounded: a test that fails before its go file never leaves this behind
    while not os.path.exists(extra[1]) and time.monotonic() < end:
        time.sleep(0.02)
    sd = host.state_dir()
    for n in sorted(os.listdir(sd)):
        os.unlink(os.path.join(sd, n))
    os.rmdir(sd)
    return 0

def _slow(host=None, out=None):  # a watch pass that says it holds (lent or its own), then works a while
    if getattr(M, "_LOCK", {}).get("fd") is None:
        return 9  # it did not hold this tool's lock: it never writes
    open(extra[0], "w").close()
    time.sleep(float(extra[1]))
    d = M.load_state(host)
    w = d.get("watch") or {}
    d["watch"] = {**w, "pass": int(w.get("pass") or 0) + 1, "time": M.stamp(host.now())}
    M.save_state(host, d)
    M.journal(host, "watch", "a whole pass")
    ran["whole"] += 1
    return 0


_slow.__name__ = "watch"  # the tool takes it as its watch: it asks for a lend, never waits
slowwatch = guard(_slow)

fn = {"watch": watch, "look": look, "move": move, "setup": setup, "uninstall": uninstall, "slowwatch": slowwatch}[kind]
codes, errors, outs = {}, {}, []
for i in range(pairs):
    ran["i"] = i
    out = io.StringIO()

    try:
        c = fn(host=M.Host(), out=out)
        codes[str(c)] = codes.get(str(c), 0) + 1
    except Exception as e:  # noqa: BLE001 - counted by kind
        errors[type(e).__name__] = errors.get(type(e).__name__, 0) + 1
    outs.append(out.getvalue())
    if kind in ("watch", "look"):
        time.sleep(random.uniform(0, gap / 4))
print(json.dumps({"kind": kind, "whole": ran["whole"], "held": held["n"], "codes": codes, "errors": errors,
                  "outs": outs[-3:]}))
'''


def lock_helper_path():
    path = os.path.join(TMP, "lock_helper.py")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(LOCK_HELPER)
    return path


def lock_writer(kind, home, pairs=1, gap_ms=0, seed=1, *extra, tree=None):
    return subprocess.Popen([sys.executable, lock_helper_path(), tree or os.path.join(ROOT, "scripts"), kind, home,
                             str(pairs), str(gap_ms), str(seed), *extra], stdout=subprocess.PIPE, text=True)


def lock_result(p, timeout=300):
    out, _ = p.communicate(timeout=timeout)
    return json.loads(out.strip().splitlines()[-1])


def wait_file(path, timeout=30):
    end = time.monotonic() + timeout
    while not os.path.exists(path):
        if time.monotonic() > end:
            raise AssertionError(f"{path} never appeared")
        time.sleep(0.01)


def made_home():
    home = tempfile.mkdtemp(dir=TMP, prefix="lock-home-")
    with mock.patch.dict(os.environ, {"HOME": home}):
        M.save_state(M.Host(), {"runners": {"o/r": {"name": "box", "unit": "u", "folder": "/f"}}})
    return home


def helper_is_watch(pid):
    """In this suite the pass is the helper's watch, not a process of the watch's own service: the lender's check
    is set to that (the real check, lend_peer_ok, is read in its own test)."""
    return helper_is_watch_kind("watch")(pid)


def helper_is_watch_kind(kind):
    def check(pid):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = f.read().split(b"\0")
        except OSError:
            return False
        return len(argv) > 4 and argv[1].endswith(b"lock_helper.py") and argv[3] == kind.encode()
    return check


def home_host(home):
    h = FakeHost()
    h._state = os.path.join(home, ".minions")
    return h


def read_whole(path):
    """The test's own reader of a record (data, None), not there (None, None), or not whole (None, why): never the
    tool's, so the same test reads the record of a tree before R205 and R208 (its red lap on 35515f9)."""
    try:
        with open(path, "rb") as f:
            data = json.loads(f.read().decode("utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as e:
        return None, str(e)
    return (data, None) if isinstance(data, dict) else (None, f"a {type(data).__name__}, not an object")


class G6Lock(unittest.TestCase):
    def test_G6_36_two_real_processes_lose_nothing_and_leave_the_record_and_its_copy_whole(self):
        # A watch-shaped writer (read, GitHub's time, read again, its pass, save) beside a
        # command-shaped one (read, its work, one new look, save), 300 pairs at each of three gaps. Before R208 this
        # lost the runner's entry and hundreds of looks (the stand-in trial, 2026-11-25).
        for gap in (0, 5, 50):
            with self.subTest(gap_ms=gap):
                home = made_home()
                ps = [lock_writer("watch", home, 300, gap, 1), lock_writer("look", home, 300, gap, 2)]
                watch, look = (lock_result(p) for p in ps)
                sd = os.path.join(home, ".minions")
                rec, why = read_whole(os.path.join(sd, "minions.json"))
                cpy, cwhy = read_whole(os.path.join(sd, getattr(M, "RECORD_COPY", "minions.copy.json")))
                self.assertIsNone(why, "the record reads whole")
                lost = {f"o/r{i}" for i in range(300)} - set(rec.get("looks") or {})
                self.assertEqual(("o/r" in (rec.get("runners") or {}), len(lost), (rec.get("watch") or {}).get("pass")),
                                 (True, 0, watch["whole"]), "the runner's entry, every look, every watch pass")
                self.assertEqual((watch["errors"], look["errors"]), ({}, {}), "no error, only held answers")
                self.assertEqual((cwhy, rec), (None, cpy), "its copy reads whole, the same")
                self.assertEqual(look["whole"], 300, "a command by hand waits and runs; it is never skipped")
                with open(os.path.join(sd, "journal.log"), encoding="utf-8") as f:
                    words = [x.split(" ", 2)[1] for x in f]
                self.assertEqual(words.count("watch-held") + watch["whole"], 300)
                self.assertEqual(watch["held"], words.count("watch-held"), "the watch never waits: held is skipped")
                self.assertEqual(sorted(n for n in os.listdir(sd) if ".tmp" in n), [], "no temporary file left")
                print(f"R208 gap {gap} ms: watch whole {watch['whole']}, skipped {words.count('watch-held')}; "
                      f"look whole {look['whole']}, held answers {look['held']}")

    def test_G6_36_a_command_longer_than_a_pass_the_watch_skips_says_so_ends_failed_then_reads_again(self):
        # The watch never waits behind a command; each pass skipped is
        # journaled; past SEEN_PASSES in a row it ends failed and says why, naming the holder; after the command
        # ends, the next pass is whole, the skipped count is 0 again, and both writers' changes are in the record.
        home = made_home()
        sd = os.path.join(home, ".minions")
        holding = os.path.join(TMP, f"holding-{os.getpid()}-{time.monotonic_ns()}")
        mv = lock_writer("move", home, 1, 0, 1, holding, "1.5")
        wait_file(holding)
        watch = lock_result(lock_writer("watch", home, M.SEEN_PASSES + 2, 0, 3))
        self.assertEqual(watch["whole"], 0)
        self.assertEqual(watch["codes"], {str(M.EXIT_OK): M.SEEN_PASSES - 1, str(M.EXIT_STOP): 3}, watch)
        last = watch["outs"][-1]
        self.assertIn(f"the watch has had no whole pass for {M.SEEN_PASSES + 2} passes in a row", last)
        self.assertIn(f"this tool's move, process {mv.pid}", last)
        self.assertIn("Your way out: let that command end", last)
        self.assertEqual(M.held_passes(home_host(home)), M.SEEN_PASSES + 2)
        self.assertEqual(lock_result(mv)["whole"], 1)
        after = lock_result(lock_writer("watch", home, 1, 0, 4))
        self.assertEqual((after["whole"], after["codes"]), (1, {"0": 1}))
        self.assertEqual(M.held_passes(home_host(home)), 0)
        rec, why = read_whole(os.path.join(sd, "minions.json"))
        self.assertIsNone(why)
        self.assertIn("o/long", rec["moves"])
        self.assertEqual(rec["watch"]["pass"], 1)

    def test_G6_36_a_command_by_hand_waits_says_what_for_then_stops_naming_the_holder_and_the_step_is_taken(self):
        # Bounded, said after a moment, a stop that changed nothing with the holder's
        # process id and command name (no arguments), and the step taken: the holder ends, the same command again.
        home = made_home()
        sd = os.path.join(home, ".minions")
        holding = os.path.join(TMP, f"holding-{os.getpid()}-{time.monotonic_ns()}")
        go = holding + ".go"
        holder = lock_writer("setup", home, 1, 0, 1, holding, go)
        wait_file(holding)
        before = file_bytes(os.path.join(sd, "minions.json"))
        ran = []

        @M.record_guard
        def drain(host=None, out=None):
            d = M.load_state(host)
            d.setdefault("looks", {})["o/drain"] = {"t": 1}
            M.save_state(host, d)
            ran.append(1)
            return M.EXIT_OK

        out = Out()
        with mock.patch.object(M, "LOCK_WAIT_S", 1), mock.patch.object(M, "LOCK_SAY_AFTER_S", 0.25):
            code = drain(host=home_host(home), out=out)
        self.assertEqual((code, ran), (M.EXIT_STOP, []))
        text = out.getvalue()
        self.assertIn(f"waiting this tool's record is held by this tool's setup, process {holder.pid}: this command "
                      "waits up to 1 seconds", text)
        self.assertIn(f"stop this tool's record is held by this tool's setup, process {holder.pid}, which did not end "
                      "within 1 seconds: nothing was changed here. Your next step: let that command end", text)
        self.assertNotIn(lock_helper_path(), text, "the command's name, never its arguments")
        self.assertEqual(file_bytes(os.path.join(sd, "minions.json")), before)
        open(go, "w").close()  # the step: that command ends ...
        lock_result(holder)
        out = Out()
        self.assertEqual(drain(host=home_host(home), out=out), M.EXIT_OK)  # ... then the same command again
        self.assertEqual(ran, [1])
        rec, _ = read_whole(os.path.join(sd, "minions.json"))
        self.assertEqual(set(rec["looks"]), {"o/held", "o/drain"})

    def test_G6_36_no_program_the_tool_starts_holds_the_lock(self):
        # A long child (sleep, through the tool's own Host.run), the tool ended by its
        # exact process id, and the lock free while the child still runs; the child holds no handle on the file.
        home = made_home()
        holding = os.path.join(TMP, f"holding-{os.getpid()}-{time.monotonic_ns()}")
        p = lock_writer("setup", home, 1, 0, 1, holding, "child")
        wait_file(holding)
        child = None
        end = time.monotonic() + 10
        while child is None and time.monotonic() < end:
            with open(f"/proc/{p.pid}/task/{p.pid}/children", encoding="ascii") as f:
                kids = f.read().split()
            child = int(kids[0]) if kids else None
            time.sleep(0.02)
        self.assertIsNotNone(child, "the long child started")
        try:
            p.kill()
            p.wait(10)
            close_pipes(p)
            lock = os.path.join(home, ".minions", M.LOCK_NAME)
            fd = os.open(lock, os.O_RDWR)
            try:
                self.assertEqual(M.lock_try(fd), ("taken", ""), "the lock is free once the tool ended")
            finally:
                os.close(fd)
            self.assertTrue(os.path.exists(f"/proc/{child}"), "the child still runs")
            fds = [os.readlink(f"/proc/{child}/fd/{n}") for n in os.listdir(f"/proc/{child}/fd")]
            self.assertNotIn(os.path.realpath(lock), fds)
        finally:
            os.kill(child, 9)
        p.stdout.close()

    def test_G6_36_a_command_that_waited_while_uninstall_took_the_folder_makes_nothing(self):
        # The file it locked is no longer the file at that path; it stops, and makes nothing.
        home = made_home()
        sd = os.path.join(home, ".minions")
        holding = os.path.join(TMP, f"holding-{os.getpid()}-{time.monotonic_ns()}")
        go = holding + ".go"
        holder = lock_writer("uninstall", home, 1, 0, 1, holding, go)
        wait_file(holding)
        ran = []

        @M.record_guard
        def setup(host=None, out=None):
            ran.append(1)
            M.save_state(host, {"runners": {"o/new": {}}})
            return M.EXIT_OK

        out = Out()
        h = home_host(home)
        h.sleep = time.sleep  # the waiting is real here: the holder ends half a second in
        threading.Timer(0.5, lambda: open(go, "w").close()).start()
        code = setup(host=h, out=out)
        lock_result(holder)
        self.assertEqual((code, ran), (M.EXIT_STOP, []))
        self.assertIn(f"stop this tool's folder, {sd}, was removed while this command waited for it (uninstall removes "
                      "it): nothing was made or changed here", out.getvalue())
        self.assertFalse(os.path.lexists(sd), "nothing made again")

    def test_G6_36_the_watch_pass_a_command_starts_and_waits_for_runs_under_its_lock(self):
        # Setup starts the watch's first pass and waits for it (install_watch); that pass
        # asks for the lend, is handed setup's own lock and is whole; only one pass can take it; without the lend
        # the same pass is skipped. And install_watch starts once more when the timer's pass was skipped.
        home = made_home()
        got = {}

        @M.record_guard
        def setup(host=None, out=None):
            self.assertEqual(M.lend_pass(host), "")
            got["mode"] = stat.S_IMODE(os.lstat(os.path.join(home, ".minions", M.LEND_NAME)).st_mode)
            got["lent"] = lock_result(lock_writer("watch", home, 1, 0, 1))
            got["took"] = M.lend_back(host)
            got["again"] = lock_result(lock_writer("watch", home, 1, 0, 2))  # a second pass: no lend left
            return M.EXIT_OK

        with mock.patch.object(M, "lend_peer_ok", helper_is_watch):
            self.assertEqual(setup(host=home_host(home), out=Out()), M.EXIT_OK)
        self.assertEqual((got["lent"]["whole"], got["took"], got["mode"]), (1, True, 0o600))
        self.assertEqual(got["again"]["whole"], 0)
        self.assertFalse(os.path.lexists(os.path.join(home, ".minions", M.LEND_NAME)))
        rec, _ = read_whole(os.path.join(home, ".minions", "minions.json"))
        self.assertEqual(rec["watch"]["pass"], 1)
        # install_watch: the pass the timer started as it was turned on found the lock held; the start runs again
        src = inspect_source(M.install_watch)
        self.assertIn("lend_pass(host)", src)
        self.assertIn('held_after(host, "the watch\'s first pass")', src)
        h = home_host(home)
        M.journal(h, "the watch's first pass")
        self.assertFalse(M.held_after(h, "the watch's first pass"))
        M.journal(h, "watch-held", "this pass skipped")
        self.assertTrue(M.held_after(h, "the watch's first pass"))

    def test_G6_36_the_lend_goes_only_to_the_watch_s_own_pass_of_this_account_and_a_stranger_is_journaled(self):
        # The lender checks who asks (the same account, a process of the watch's own service);
        # any other who asks gets nothing, and the journal says so. The real check: this suite's own process is not
        # in the watch's service, so a pass of the helper is refused by it, and skipped.
        home = made_home()
        got = {}
        self.assertFalse(M.lend_peer_ok(os.getpid()))

        @M.record_guard
        def setup(host=None, out=None):
            M.lend_pass(host)
            got["pass"] = lock_result(lock_writer("watch", home, 1, 0, 1))
            got["took"] = M.lend_back(host)
            return M.EXIT_OK

        setup(host=home_host(home), out=Out())
        self.assertEqual((got["pass"]["whole"], got["took"]), (0, False))
        with open(os.path.join(home, ".minions", "journal.log"), encoding="utf-8") as f:
            text = f.read()
        self.assertRegex(text, r" lend-refused process \d+ of user id \d+ asked for this tool's lock, and it is not the "
                               r"watch's pass this command started: it got nothing\n")
        self.assertIn(" watch-held this pass skipped", text)

    def test_G6_36_a_home_whose_path_is_longer_than_a_socket_s_bound_still_lends_and_the_pass_is_whole(self):
        # A socket's path is bound by 107 bytes; the lend is bound through the folder opened
        # by itself, so a long home binds, and its first pass is whole.
        home = tempfile.mkdtemp(dir=TMP, prefix="lock-home-" + "a" * 60 + "-" + "b" * 60 + "-")
        with mock.patch.dict(os.environ, {"HOME": home}):
            M.save_state(home_host(home), {"looks": {}})
        self.assertGreater(len(os.path.join(home, ".minions", M.LEND_NAME).encode()), 108)
        got = {}

        @M.record_guard
        def setup(host=None, out=None):
            got["why"] = M.lend_pass(host)
            got["pass"] = lock_result(lock_writer("watch", home, 1, 0, 1))
            got["took"] = M.lend_back(host)
            return M.EXIT_OK

        with mock.patch.object(M, "lend_peer_ok", helper_is_watch):
            setup(host=home_host(home), out=Out())
        self.assertEqual((got["why"], got["pass"]["whole"], got["took"]), ("", 1, True))

    def test_G6_36_what_stands_at_the_lend_s_path_is_a_stop_or_a_clean_take_over_never_followed(self):
        # A file, a folder, a link: a stop naming it, never followed or taken; a socket a dead
        # lender left: taken over (stale: the one who makes a lend holds the lock). The watch asks a non-socket:
        # could not be asked, journaled. The next command that takes the lock takes a dead lend away.
        target = os.path.join(TMP, "lend-target")
        with open(target, "w") as f:
            f.write("not ours\n")
        for kind in ("file", "folder", "link", "socket"):
            with self.subTest(kind=kind):
                home = made_home()
                path = os.path.join(home, ".minions", M.LEND_NAME)
                if kind == "file":
                    with open(path, "w") as f:
                        f.write("x")
                elif kind == "folder":
                    os.mkdir(path)
                elif kind == "link":
                    os.symlink(target, path)
                else:
                    dead = socket.socket(socket.AF_UNIX)
                    dead.bind(path)
                    dead.close()  # its lender is gone: no one listens
                got = {}

                @M.record_guard
                def setup(host=None, out=None):
                    got["why"] = M.lend_pass(host)
                    got["took"] = M.lend_back(host)
                    return M.EXIT_OK

                @M.record_guard
                def look(host=None, out=None):
                    got["watch"] = lock_result(lock_writer("watch", home, 1, 0, 1))
                    return M.EXIT_OK

                if kind != "socket":
                    look(host=home_host(home), out=Out())
                    with open(os.path.join(home, ".minions", "journal.log"), encoding="utf-8") as f:
                        self.assertIn(f"its lend could not be asked ({path} is not a socket of this tool's)", f.read())
                setup(host=home_host(home), out=Out())
                want = {"file": "a file, not a socket of this tool's", "folder": "a folder, not a socket of this tool's",
                        "link": "a link, not a socket of this tool's", "socket": ""}[kind]
                self.assertEqual(got["why"], want)
                with open(target) as f:
                    self.assertEqual(f.read(), "not ours\n")
                self.assertEqual(os.path.lexists(path), kind != "socket")
        # A dead lend found by the next command that takes the lock: taken away (lock_leftovers)
        home = made_home()
        path = os.path.join(home, ".minions", M.LEND_NAME)
        dead = socket.socket(socket.AF_UNIX)
        dead.bind(path)
        dead.close()
        self.assertEqual(lock_result(lock_writer("look", home, 1, 0, 1))["whole"], 1)
        self.assertFalse(os.path.lexists(path))

    def test_G6_36_both_ends_hold_one_lock_and_a_pass_killed_early_leaves_the_lender_to_finish(self):
        # Once handed over, the lock is free only when both have closed it: the lender gone
        # while the pass works, a third command still finds it held; the pass ends, it is free. A pass killed early:
        # lend_back comes back within its bound, and install_watch reads that the pass ended before it writes.
        home = made_home()
        holds = os.path.join(TMP, f"lent-holds-{os.getpid()}-{time.monotonic_ns()}")
        got = {}

        @M.record_guard
        def setup(host=None, out=None):
            M.lend_pass(host)
            got["pass"] = lock_writer("slowwatch", home, 1, 0, 1, holds, "3", )
            wait_file(holds)
            got["took"] = M.lend_back(host)
            return M.EXIT_OK

        with mock.patch.object(M, "lend_peer_ok", helper_is_watch_kind("slowwatch")):
            setup(host=home_host(home), out=Out())  # the lender ends here, its own fd closed
        third = M.lock_open(home_host(home))[0]
        self.assertEqual(M.lock_try(third)[0], "held", "the pass still holds the lock it was handed")
        self.assertEqual(lock_result(got["pass"])["whole"], 1)
        self.assertEqual(M.lock_try(third)[0], "taken", "both closed: free")
        os.close(third)
        # Killed early: the lender's lend_back returns within its bound, nothing hangs
        holds2 = holds + "-2"

        @M.record_guard
        def setup2(host=None, out=None):
            M.lend_pass(host)
            p = lock_writer("slowwatch", home, 1, 0, 1, holds2, "60")
            wait_file(holds2)
            p.kill()
            p.wait(30)
            close_pipes(p)
            t0 = time.monotonic()
            got["took2"] = M.lend_back(host)
            got["back_s"] = time.monotonic() - t0
            return M.EXIT_OK

        with mock.patch.object(M, "lend_peer_ok", helper_is_watch_kind("slowwatch")):
            setup2(host=home_host(home), out=Out())
        self.assertTrue(got["took2"])
        self.assertLess(got["back_s"], M.LEND_ACCEPT_S * 3 + 1)
        # install_watch reads that the pass has ended before it writes again (systemd's own word), else it stops
        src = inspect_source(M.install_watch)
        self.assertIn("state = pass_state(host)", src)
        self.assertIn(f"TimeoutStartSec={M.WATCH_PASS_S}s\n", M.watch_units("person", 300)[0])
        for word, want in (("inactive\n", "ended"), ("failed\n", "ended"), ("activating\n", "systemd says activating"),
                           ("", "could not be read")):
            h = FakeHost(runs={tuple(M.systemctl("show", "-p", "ActiveState", "--value", f"{M.WATCH_UNIT}.service")):
                               (0, word)})
            self.assertEqual(M.pass_state(h), want)

    def test_G6_36_the_lock_that_could_not_be_asked_stops_before_any_change_and_its_step_is_taken(self):
        # The lock's third answer: the lock file is a kept file; nothing stands in it.
        for kind in ("folder", "link"):
            with self.subTest(kind=kind):
                home = made_home()
                sd = os.path.join(home, ".minions")
                lock = os.path.join(sd, M.LOCK_NAME)
                os.unlink(lock)  # made_home's own planting made it
                if kind == "folder":
                    os.mkdir(lock)
                else:
                    os.symlink(os.path.join(home, "elsewhere"), lock)
                before = file_bytes(os.path.join(sd, "minions.json"))
                ran = []

                @M.record_guard
                def look(host=None, out=None):
                    ran.append(1)
                    M.save_state(host, {})
                    return M.EXIT_OK

                out = Out()
                self.assertEqual(look(host=home_host(home), out=out), M.EXIT_STOP)
                self.assertEqual(ran, [])
                self.assertEqual(file_bytes(os.path.join(sd, "minions.json")), before)
                why = "a folder, not a file" if kind == "folder" else "a link, not a file"
                self.assertIn(f"stop could not ask for this tool's lock, {lock} ({why})", out.getvalue())
                step = re.search(r"never deleted: `(mv -n -T -- [^`]+)`", out.getvalue()).group(1)
                self.assertEqual(subprocess.run(shlex.split(step)).returncode, 0)  # the step, as printed
                self.assertEqual(look(host=home_host(home), out=Out()), M.EXIT_OK)
                self.assertEqual(ran, [1])
                kept = [n for n in os.listdir(sd) if n.startswith(M.LOCK_NAME + ".kept-")]
                self.assertEqual(len(kept), 1, "set aside, never deleted")
                self.assertFalse(os.path.exists(os.path.join(home, "elsewhere")), "a link is never followed")
        # Nothing stands in the lock file and nothing is read from it
        self.assertEqual(os.path.getsize(os.path.join(sd, M.LOCK_NAME)), 0)
        src = inspect_source(M.lock_holder) + inspect_source(M.lock_take)
        self.assertNotIn("os.read(", src)

    def test_G6_36_no_wait_for_a_person_is_inside_the_hold_and_after_the_answer_the_record_is_read_afresh(self):
        # A person who leaves a prompt open never holds the watch. At setup's Enter, at sudo's
        # password and at uninstall's typed word the lock is let go: a real watch pass run while the person waits is
        # whole, never skipped. After the answer the command takes the lock again and runs from its first line, so it
        # decides on the record as the watch left it; what it printed before the wait is not printed twice.
        home = made_home()
        seen = {}

        def person_waits(answer):
            def wait(*_):
                seen.setdefault("passes", []).append(lock_result(lock_writer("watch", home, 1, 0, 1)))
                return answer
            return wait

        @M.record_guard
        def setup(host=None, out=None):
            rep = M.Report(out)
            seen.setdefault("rounds", []).append(M.load_state(host).get("watch", {}).get("pass"))
            rep.ok("a line before the wait")
            if not M.persons_own_eyes(host, rep, ["the lines you read"], True, "`setup o/r`"):
                return rep.code()
            if not M.ask_admin_once(host, rep, True):
                return rep.code()
            d = M.load_state(host)
            d.setdefault("looks", {})["o/after"] = {"t": 1}
            M.save_state(host, d)
            rep.ok("done")
            return rep.code()

        h = home_host(home)
        h.tty, h.enter, h.read_enters, h.validated = True, True, 0, 0
        h.read_enter = (lambda real: lambda: (person_waits(True)(), real())[1])(h.read_enter)
        h.runs[("sudo", "-n", "true")] = (1, "a password is required")
        h.sudo_validate = (lambda real: lambda: (person_waits(0)(), real())[1])(h.sudo_validate)
        out = Out()
        self.assertEqual(setup(host=h, out=out), M.EXIT_OK, out.getvalue())
        self.assertEqual([p["whole"] for p in seen["passes"]], [1, 1], "each pass while the person waited was whole")
        self.assertEqual(seen["rounds"], [None, 1, 2], "each round read the record afresh, as the watch left it")
        self.assertEqual((h.read_enters, h.validated), (1, 1), "each asked once")
        text = out.getvalue()
        self.assertEqual(text.count("ok a line before the wait"), 1, text)
        self.assertEqual(text.count("after your answer this command took this tool's lock again and read its record "
                                    "afresh: what you were shown reads the same, so it goes on"), 2, text)
        rec, _ = read_whole(os.path.join(home, ".minions", "minions.json"))
        self.assertEqual((rec["watch"]["pass"], sorted(rec["looks"])), (2, ["o/after"]))
        # What the person was to read changed while they answered: asked again, with the reason
        home = made_home()
        n = {"i": 0}

        @M.record_guard
        def setup(host=None, out=None):
            rep = M.Report(out)
            p = M.load_state(host).get("watch", {}).get("pass")
            if not M.persons_own_eyes(host, rep, [f"passes so far: {p}"], True, "`setup o/r`"):
                return rep.code()
            return rep.code()

        h = home_host(home)
        h.tty, h.enter, h.read_enters = True, True, 0

        def enter_while_a_pass_runs():
            n["i"] += 1
            if n["i"] == 1:
                person_waits(True)()
            h.read_enters += 1
            return True

        h.read_enter = enter_while_a_pass_runs
        out = Out()
        self.assertEqual(setup(host=h, out=out), M.EXIT_OK)
        self.assertEqual(h.read_enters, 2)
        self.assertIn("warn after your answer this command read this tool's record afresh, and what you are to read has "
                      "changed meanwhile (another of its commands ran): read it again below", out.getvalue())
        # uninstall's typed word (after its last change to the record): the lock let go first; a save after it is a
        # stop naming the command, never a traceback
        home = made_home()

        @M.record_guard
        def uninstall(host=None, out=None):
            rep = M.Report(out)
            M.last_wait("Type delete: ", person_waits("delete"))
            M.save_state(host, {"looks": {}})
            return rep.code()

        out = Out()
        self.assertEqual(uninstall(host=home_host(home), out=out), M.EXIT_STOP)
        self.assertEqual(seen["passes"][-1]["whole"], 1)
        self.assertIn("stop this tool's uninstall was about to write its record without holding its lock, which it "
                      "never does, so it wrote nothing more and stopped here", out.getvalue())

    def test_G6_44_a_wait_with_no_answer_read_never_says_an_answer_came(self):
        # Row R216, seen on a stock Ubuntu 22.04 guest: setup's Enter with its input closed under a terminal. The wait
        # lets the lock go and the command runs again from its first line, as G6.36 says; when nothing was read, no
        # line says an answer came: the stop says none was read and that nothing was changed.
        home = made_home()

        @M.record_guard
        def setup(host=None, out=None):
            rep = M.Report(out)
            rep.ok("a line before the wait")
            if not M.persons_own_eyes(host, rep, ["the lines you read"], True, "`setup o/r`"):
                return rep.code()
            d = M.load_state(host)
            d.setdefault("looks", {})["o/after"] = {"t": 1}
            M.save_state(host, d)
            rep.ok("done")
            return rep.code()

        h = home_host(home)
        h.tty, h.enter, h.read_enters = True, False, 0  # a terminal whose input is closed: Enter reads nothing
        out = Out()
        code = setup(host=h, out=out)
        text = out.getvalue()
        self.assertNotEqual(code, M.EXIT_OK, text)
        self.assertEqual(h.read_enters, 1, "asked once")
        self.assertNotIn("after your answer", text)
        self.assertNotIn("you read the lines and pressed Enter", text)
        self.assertEqual(text.count("no Enter was read: nothing was changed. Run the same command again in your own "
                                    "terminal"), 1, text)
        self.assertEqual(text.count("ok a line before the wait"), 1, "what was printed before the wait, once")
        rec, _ = read_whole(os.path.join(home, ".minions", "minions.json"))
        self.assertNotIn("o/after", (rec or {}).get("looks", {}), "nothing was changed")
        # An answer that came still says so, as G6.36 holds
        home = made_home()
        h = home_host(home)
        h.tty, h.enter, h.read_enters = True, True, 0
        out = Out()
        self.assertEqual(setup(host=h, out=out), M.EXIT_OK, out.getvalue())
        self.assertEqual(out.getvalue().count("after your answer this command took this tool's lock again"), 1)

    def test_G6_36_every_wait_for_a_person_and_every_long_wait_runs_with_the_lock_let_go(self):
        # Every wait for a person in the tool is one of these three, each inside
        # outside_hold or last_wait: the Enter (persons_own_eyes: setup, uninstall), sudo's password (ask_admin_once:
        # setup, uninstall, drain --stop, resume, doctor), the typed word (uninstall --delete-record). The wait for
        # a running job (wait_idle: drain, uninstall, setup over a cut one) lets the lock go before its first sleep.
        src = inspect_source(M)
        self.assertEqual(src.count("host.read_enter()"), 0)
        self.assertEqual(src.count("host.sudo_validate()"), 0)
        self.assertEqual(len(re.findall(r"host\.ask\(", src)), 0)
        self.assertIn('outside_hold(("enter", command, tuple(lines)), host.read_enter, none_read=',  # R216
                      inspect_source(M.persons_own_eyes))
        self.assertIn('outside_hold(("sudo", cmd), host.sudo_validate)', inspect_source(M.ask_admin_once))
        self.assertIn("last_wait(", inspect_source(M.delete_record_if_typed))
        self.assertIn("input(prompt)", inspect_source(M.Host.ask))
        self.assertEqual(len(re.findall(r"\binput\(", src)), 1)
        # wait_idle: a real watch pass while it waits for a job to end is whole; then the command runs again afresh
        home = made_home()
        answers = iter([{"busy": True, "status": "online"}, {"busy": False, "status": "online"},
                        {"busy": False, "status": "online"}])
        seen = {"rounds": 0}

        @M.record_guard
        def drain(host=None, out=None):
            rep = M.Report(out)
            seen["rounds"] += 1
            got = M.wait_idle(mock.Mock(limited=lambda: False), host, "o/r", {"name": "box", "folder": "/f"}, 60, 0, rep, "drain o/r")
            M.save_state(host, M.load_state(host))
            rep.ok(f"then: {got}")
            return rep.code()

        h = home_host(home)
        h.sleep = lambda s: seen.setdefault("pass", lock_result(lock_writer("watch", home, 1, 0, 1)))
        with mock.patch.object(M, "listed_runner", lambda *a: next(answers)), \
                mock.patch.object(M, "runner_pids", lambda *a: []):
            out = Out()
            self.assertEqual(drain(host=h, out=out), M.EXIT_OK, out.getvalue())
        self.assertEqual((seen["pass"]["whole"], seen["rounds"]), (1, 2))
        self.assertIn("this tool's record is let go while it waits, so its watch goes on", out.getvalue())
        self.assertIn("ok then: idle", out.getvalue())

    def test_G6_36_a_hold_past_the_watch_s_three_passes_heals_at_its_next_pass_with_nothing_for_a_person(self):
        # The REAL watch over a computer with our runner, its folder shared with a real process
        # of this tool's that holds the lock (setup's shape) past SEEN_PASSES passes: each pass skipped, the last
        # failed and says why; no private read is written meanwhile (the job hook's read grows old). The holder ends
        # by itself: the very next pass is whole, writes a fresh private read for the hook, and leaves no mark
        # (no skipped count, no blind entry, status with no skipped line), with no step of a person's between.
        host, clock = g5_host()
        home = tempfile.mkdtemp(dir=TMP, prefix="lock-home-")
        shutil.copytree(host.state_dir(), os.path.join(home, ".minions"))
        host._state = os.path.join(home, ".minions")
        seen = M.seen_path(f"/home/minions-runner/runners/{REPO.replace('/', '-')}")
        holding = os.path.join(TMP, f"holding-{os.getpid()}-{time.monotonic_ns()}")
        go = holding + ".go"
        holder = lock_writer("setup", home, 1, 0, 1, holding, go)
        self.addCleanup(lambda: holder.poll() is None and holder.kill())
        wait_file(holding)
        codes = []
        for _ in range(M.SEEN_PASSES + 1):
            clock.sleep(300)
            out = Out()
            codes.append(M.watch(host=host, gh=StatusGH(), out=out))
        self.assertEqual(codes, [M.EXIT_OK] * (M.SEEN_PASSES - 1) + [M.EXIT_STOP] * 2, out.getvalue())
        self.assertIn(f"the watch has had no whole pass for {M.SEEN_PASSES + 1} passes in a row", out.getvalue())
        self.assertNotIn(seen, host.files, "a skipped pass wrote a private read")
        open(go, "w").close()  # the holder ends by itself: no person's step
        lock_result(holder)
        clock.sleep(300)
        out = Out()
        self.assertEqual(M.watch(host=host, gh=StatusGH(), out=out), M.EXIT_OK, out.getvalue())
        self.assertEqual(host.files[seen].strip(), str(int(clock.t.timestamp())), "the hook's read is fresh again")
        self.assertEqual(M.held_passes(host), 0)
        self.assertFalse((M.load_state(host).get("watch") or {}).get("blind"))
        st = Out()
        M.status(REPO, host=host, gh=StatusGH(), out=st)
        self.assertNotIn("skipped since", st.getvalue())
        self.assertNotIn("no whole pass", st.getvalue())

    def test_G6_36_a_command_whose_own_hold_made_the_watch_skip_says_so_at_its_end(self):
        # The person at the command that held the lock is told, at its end, how many passes its
        # hold made the watch skip, what that meant for jobs past SEEN_PASSES, and that nothing else is theirs to do.
        # Passes another command's hold made skip, before this one began, are not counted for it.
        home = made_home()
        hh = home_host(home)
        M.journal(hh, "watch-held", "this pass skipped: process 1 holds this tool's record")  # not this command's

        def make(n):
            @M.record_guard
            def drain(host=None, out=None):
                if n:
                    lock_result(lock_writer("watch", home, n, 0, 7))
                return M.EXIT_OK
            return drain

        for n, words in ((0, None), (1, "the watch skipped 1 of its passes"),
                         (M.SEEN_PASSES + 1, f"the watch skipped {M.SEEN_PASSES + 1} of its passes")):
            out = Out()
            self.assertEqual(make(n)(host=home_host(home), out=out), M.EXIT_OK)
            text = out.getvalue()
            if words is None:
                self.assertNotIn("the watch skipped", text)
                continue
            self.assertIn(f"warn while this command held this tool's record, {words} (the journal names each)", text)
            self.assertIn("the watch's next pass reads again by itself", text)
            self.assertEqual(f"at {M.SEEN_PASSES} in a row it ended failed" in text, n >= M.SEEN_PASSES, text)

    def test_G6_36_a_watch_pass_with_its_folder_or_record_gone_says_so_makes_nothing_and_the_next_pass_reads(self):
        # The folder this tool keeps its record and lock in is gone, or the record alone is, when the
        # watch fires. The watch never takes that for a fresh home (it runs only after a setup): each pass says so,
        # makes nothing (no folder, no lock file, no record) and ends; where the copy reads, its step is restore, taken
        # as printed, and the next pass is whole. A home this tool cannot write in: the stop's words fit, no mv line.
        host, clock = g5_host()
        home = tempfile.mkdtemp(dir=TMP, prefix="lock-home-")
        kept = os.path.join(TMP, f"kept-{os.getpid()}-{time.monotonic_ns()}")
        shutil.copytree(host.state_dir(), kept)
        host._state = os.path.join(home, ".minions")
        for _ in range(2):  # the folder gone: said at each pass, nothing made
            out = Out()
            self.assertEqual(M.watch(host=host, gh=StatusGH(), out=out), M.EXIT_STOP)
            self.assertIn(f"stop this tool's record, {host._state}/minions.json, is not there, nor its folder "
                          f"{host._state}: the watch runs only after a setup made it", out.getvalue())
            self.assertIn("The watch made nothing and changed nothing", out.getvalue())
            self.assertFalse(os.path.lexists(host._state), "the watch made this tool's folder")
        shutil.copytree(kept, host._state)  # the record alone gone, its copy whole
        os.unlink(os.path.join(host._state, "minions.json"))
        out = Out()
        self.assertEqual(M.watch(host=host, gh=StatusGH(), out=out), M.EXIT_STOP)
        lines = out.printed()
        self.assertIn("reads whole", lines[0])
        self.assertEqual(lines[-1], "restore", lines)
        self.assertFalse(os.path.lexists(os.path.join(host._state, "minions.json")))
        out = Out()
        self.assertEqual(M.main([lines[-1]], host=host, out=out), M.EXIT_OK, out.getvalue())  # taken as printed
        self.assertIn("is not there. Its second copy", out.getvalue())
        self.assertNotIn("kept as", out.getvalue())
        clock.sleep(300)
        out = Out()
        self.assertEqual(M.watch(host=host, gh=StatusGH(), out=out), M.EXIT_OK, out.getvalue())
        self.assertIn(REPO, M.load_state(host)["runners"])
        bare = tempfile.mkdtemp(dir=TMP, prefix="lock-home-")  # a home this tool cannot write in
        os.chmod(bare, 0o500)
        self.addCleanup(os.chmod, bare, 0o700)

        @M.record_guard
        def drain(host=None, out=None):
            return M.EXIT_OK

        hb = FakeHost()
        hb._state = os.path.join(bare, ".minions")
        out = Out()
        self.assertEqual(drain(host=hb, out=out), M.EXIT_STOP)
        self.assertIn(f"(its folder cannot be made: Permission denied)", out.getvalue())
        self.assertIn(f"Your next step: make {bare} a folder you can write to (this tool makes .minions in it, as "
                      "you), then run the same command again", out.getvalue())
        self.assertNotIn("mv -n", out.getvalue())

    def test_G6_36_restore_with_the_record_gone_and_a_copy_there_that_cannot_be_read_stops_as_the_watch_does(self):
        # R205: the record gone and a copy that IS there but
        # could not be read (0 bytes, a list, cannot be opened) was said by restore as "nor a copy of it ... made
        # nothing here yet", exit 0: a failed read said as an absence, by the one command the watch's stop sends a
        # person to. restore now stops there with the words the watch says in the same state, and changes nothing.
        for label, damage in (("0 bytes", b""), ("a list", b"[]"), ("cannot be opened", None)):
            with self.subTest(copy=label):
                gh, world, host = remove_setup(moved=False)
                p, c = M.state_path(host), M.copy_path(host)
                os.remove(p)
                plant_record(c, file_bytes(c), damage)
                before = {n: (os.stat(os.path.join(host.state_dir(), n)).st_mode, file_bytes(os.path.join(
                    host.state_dir(), n)) if damage is not None or n != os.path.basename(c) else None)
                          for n in os.listdir(host.state_dir())}
                out = Out()
                self.assertEqual(M.main(["restore"], host=host, out=out), M.EXIT_STOP, out.printed())
                said = out.printed()
                stop = next(x for x in said if x.startswith("stop "))
                self.assertIn(f"{p} is not there", stop)
                self.assertIn(f"its second copy, {c}, could not be read either (", stop)
                self.assertFalse([x for x in said if "made nothing here yet" in x or "nothing to restore" in x], said)
                self.assertFalse(os.path.lexists(p), "nothing is put in the record's place")
                after = {n: (os.stat(os.path.join(host.state_dir(), n)).st_mode, file_bytes(os.path.join(
                    host.state_dir(), n)) if damage is not None or n != os.path.basename(c) else None)
                         for n in os.listdir(host.state_dir()) if n != M.LOCK_NAME}
                self.assertEqual(after, {n: v for n, v in before.items() if n != M.LOCK_NAME})
                out = Out()
                self.assertEqual(M.watch(host=host, gh=StatusGH(), out=out), M.EXIT_STOP)
                tail = stop.split("could not be read either", 1)[1]
                self.assertIn(tail, next(x for x in out.printed() if x.startswith("stop ")), "the watch's words")
        # with no copy at all and no record, restore stays as it was: a fresh home, nothing to restore
        host = FakeHost()
        out = Out()
        self.assertEqual(M.main(["restore"], host=host, out=out), M.EXIT_OK)
        self.assertIn("nor a copy of it, so this tool made nothing here yet", out.getvalue())

    def test_G6_36_a_process_with_the_lock_file_open_that_does_not_hold_it_is_never_named_its_holder(self):
        # /proc/locks is matched by inode number only (its device is not the
        # one stat gives on btrfs), so a process that has this lock file open WITHOUT holding it (another command
        # waiting at it) and holds a FLOCK on another file with the same inode number elsewhere was named the holder.
        # The holder is now the process whose own descriptor of THIS file carries the FLOCK line (/proc/PID/fdinfo).
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, M.LOCK_NAME)
        open(path, "w").close()
        kids = []
        for take in (True, False):
            code = ("import fcntl, os, sys, time; fd = os.open(sys.argv[1], os.O_RDWR); "
                    + ("fcntl.flock(fd, fcntl.LOCK_EX); " if take else "") + "print('ready', flush=True); time.sleep(60)")
            k = subprocess.Popen([sys.executable, "-I", "-c", code, path], stdout=subprocess.PIPE, text=True)
            self.addCleanup(close_pipes, k)
            self.addCleanup(k.wait)
            self.addCleanup(k.kill)
            self.assertEqual(k.stdout.readline().strip(), "ready")
            kids.append(k)
        holder, waiter = kids
        ino = os.stat(path).st_ino
        with open("/proc/locks", encoding="ascii") as f:
            real = f.read()
        made_up = f"1: FLOCK  ADVISORY  WRITE {waiter.pid} 00:99:{ino} 0 EOF\n"  # its lock on another file system
        real_open = open

        def fake_open(name, *a, **k):
            if name == "/proc/locks":
                return io.StringIO(made_up + real)
            return real_open(name, *a, **k)
        fd = os.open(path, os.O_RDWR)
        self.addCleanup(os.close, fd)
        with mock.patch.object(M, "open", fake_open, create=True):
            pid, _ = M.lock_holder(fd)
        self.assertEqual(pid, holder.pid, "the process whose descriptor of this file holds the lock")
        self.assertNotEqual(pid, waiter.pid)

    def test_G6_36_a_download_that_trickles_ends_within_its_bound_plus_one_read(self):
        # The runner's download is inside the hold, so its bound must hold on a line that trickles. A
        # real server on this computer's loopback sends a long body one byte every 0.3 s: with the whole bound 2 s and
        # one read's wait 1 s, the download ends by the tool's own words within 2 + 1 s and a margin, never waiting on
        # each byte. Red on a read of a fixed size (1 MiB at 1 byte per 0.3 s: days).
        import http.server
        stop = threading.Event()

        class Trickle(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(1 << 20))
                self.end_headers()
                for _ in range(200):
                    if stop.is_set():
                        return
                    try:
                        self.wfile.write(b"x")
                        self.wfile.flush()
                    except OSError:
                        return
                    time.sleep(0.3)

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.addCleanup(stop.set)
        got = {}

        def fetch():
            t0 = time.monotonic()
            try:
                M.Host().fetch_to(f"http://127.0.0.1:{srv.server_address[1]}/runner.tar.gz",
                                  os.path.join(tempfile.mkdtemp(dir=TMP), "runner.tar.gz"), timeout=1, whole_s=2)
                got["end"] = "finished"
            except Exception as e:  # noqa: BLE001
                got["end"] = f"{type(e).__name__}: {e}"
            got["s"] = time.monotonic() - t0

        th = threading.Thread(target=fetch, daemon=True)
        th.start()
        th.join(8)
        self.assertFalse(th.is_alive(), "the download was still running after 8 s on a 2 s bound")
        self.assertLess(got["s"], 4.5, got)
        self.assertIn("TimeoutError: the download took longer than 1 minute, so it was stopped (a slow line)", got["end"])

    def test_G6_36_a_download_that_ends_is_kept_whole_with_its_length_and_without_it(self):
        # A real server on this computer's loopback answers twice: once with a Content-Length (http.client
        # closes the answer and its socket after the last byte, as GitHub's download does), once with none, closed at
        # the end. Both downloads end well and the file is the body byte for byte. Red where the loop sets a timeout
        # on that closed socket: Bad file descriptor after the last byte.
        import http.server
        body = os.urandom(300_000)

        class Whole(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                if self.path == "/with-length":
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                for i in range(0, len(body), 50_000):
                    self.wfile.write(body[i:i + 50_000])
                    self.wfile.flush()

            def log_message(self, *a):
                pass

        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Whole)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        for kind in ("with-length", "without-length"):
            with self.subTest(kind):
                path = os.path.join(tempfile.mkdtemp(dir=TMP), "runner.tar.gz")
                try:
                    M.Host().fetch_to(f"http://127.0.0.1:{srv.server_address[1]}/{kind}", path, timeout=5, whole_s=20)
                    end = "finished"
                except Exception as e:  # noqa: BLE001
                    end = f"{type(e).__name__}: {e}"
                self.assertEqual(end, "finished")
                with open(path, "rb") as f:
                    self.assertEqual(hashlib.sha256(f.read()).hexdigest(), hashlib.sha256(body).hexdigest())

    def test_G6_36_the_mv_step_is_taken_as_printed_from_a_home_with_a_space_and_never_moves_into_a_folder(self):
        # The lock's mv line goes through the helper every printed command does (R200),
        # is taken as printed from a home whose path holds a space; a kept name that stands already (a folder): -T
        # never moves INTO it, -n never over it; a path no printed line may carry gets words, no command.
        home = tempfile.mkdtemp(dir=TMP, prefix="lock home with a space-")
        sd = os.path.join(home, ".minions")
        os.makedirs(sd, mode=0o700)
        lock = os.path.join(sd, M.LOCK_NAME)
        os.mkdir(lock)
        out = Out()
        self.assertEqual(M.lock_take(home_host(home), out, "look"), M.EXIT_STOP)
        step = re.search(r"never deleted: `(mv -n -T -- [^`]+)`", out.getvalue()).group(1)
        self.assertEqual(subprocess.run(["bash", "-c", step]).returncode, 0, step)  # pasted into a shell, as printed
        self.assertFalse(os.path.lexists(lock))
        kept = [n for n in os.listdir(sd) if n.startswith(M.LOCK_NAME + ".kept-")]
        self.assertEqual(len(kept), 1)
        # the kept name stands already, as a folder: the step neither moves into it nor over it
        os.mkdir(lock)
        target = os.path.join(sd, "taken-name")
        os.mkdir(target)
        r = subprocess.run(["mv", "-n", "-T", "--", lock, target], capture_output=True, text=True)
        self.assertEqual(os.listdir(target), [], "nothing moved into the folder at that name")
        self.assertTrue(os.path.isdir(lock), "the lock path stays where it was")
        # a name free when printed: one that stands gets the next free one
        with mock.patch.object(M, "stamp_now", lambda: "20261126T120000Z"):
            os.mkdir(f"{lock}.kept-20261126T120000Z")
            self.assertIn(f"{lock}.kept-20261126T120000Z-2", M.aside_step(lock))
        # a path that holds a backtick: no command, words only
        self.assertNotIn("`mv", M.cmd("x") + M.aside_step("/home/user/a`b/.minions/minions.lock"))
        self.assertIn("set /home/user/a`b/.minions/minions.lock aside by hand", M.aside_step("/home/user/a`b/.minions/minions.lock"))
        self.assertIn("aside_step(path)", inspect_source(M.lock_unasked))

    def test_G6_36_status_never_waits_and_saves_nothing_while_another_holds_the_record(self):
        # Status reads on, says who holds the record, and writes nothing.
        home = made_home()
        sd = os.path.join(home, ".minions")
        holding = os.path.join(TMP, f"holding-{os.getpid()}-{time.monotonic_ns()}")
        go = holding + ".go"
        holder = lock_writer("setup", home, 1, 0, 1, holding, go)
        wait_file(holding)
        before = file_bytes(os.path.join(sd, "minions.json"))
        seen = {}

        @M.record_guard
        def status(host=None, out=None):
            seen["readonly"], seen["by"] = M._LOCK["readonly"], M._LOCK["held_by"]
            M.save_state(host, {"looks": {"o/status": {}}})  # its one save: none
            return M.EXIT_OK

        t0 = time.monotonic()
        self.assertEqual(status(host=home_host(home), out=Out()), M.EXIT_OK)
        self.assertLess(time.monotonic() - t0, 1, "status never waits")
        self.assertEqual((seen["readonly"], seen["by"]), (True, f"this tool's setup, process {holder.pid}"))
        self.assertEqual(file_bytes(os.path.join(sd, "minions.json")), before)
        self.assertFalse(M._LOCK["readonly"], "freed at its end")
        open(go, "w").close()
        lock_result(holder)
        src = inspect_source(M.status)
        self.assertIn("pass(es) skipped", src)
        self.assertIn("status read it as it stands and", src)

    def test_G6_36_every_command_that_changes_the_record_takes_the_lock_and_no_save_goes_without_it(self):
        # Aims 1 and 2: every command of the tool runs through the guard; a save inside a command without the lock
        # is refused, never written.
        for name in M.COMMANDS:
            self.assertTrue(hasattr(getattr(M, name), "__wrapped__"), f"{name} runs through record_guard")
        home = made_home()
        with mock.patch.dict(M._GUARD, {"depth": 1}):
            with self.assertRaises(RuntimeError):
                M.save_state(home_host(home), {})
        rec, _ = read_whole(os.path.join(home, ".minions", "minions.json"))
        self.assertIn("o/r", rec["runners"])
        names = ["minions.lock", "minions.lend", "minions.json.tmp-123-0a1b2c3d", "minions.copy.json.tmp-9-ffffffff"]
        self.assertTrue(all(re.fullmatch(M.STATE_NAMES, n) for n in names), "uninstall --all deletes them")


inspect_source = inspect.getsource


class G2Env(unittest.TestCase):
    """R206, codon G2.29."""

    def test_G2_29_a_runner_env_that_could_not_be_read_is_never_written_over(self):
        # Seen 2026-11-26 in a stand-in trial on 35515f9: a .env the runner user could not open was taken for an
        # empty one and written over with only the tool's three lines; every line of the person's was lost.
        for kind, step in (("not readable", f"sudo chown -h minions-runner {FOLDER}/.env && "
                                            f"sudo -u minions-runner chmod u+rw {FOLDER}/.env"),
                           ("a link", f"sudo -u minions-runner mv -n -T -- {FOLDER}/.env {FOLDER}/.env.kept-"),
                           ("not a file", f"sudo -u minions-runner mv -n -T -- {FOLDER}/.env {FOLDER}/.env.kept-"),
                           ("cat fails", f"sudo -u minions-runner mv -n -T -- {FOLDER}/.env {FOLDER}/.env.kept-"),
                           ("no mark", f"sudo -u minions-runner mv -n -T -- {FOLDER}/.env {FOLDER}/.env.kept-")):
            with self.subTest(kind=kind):
                host, sha = setup_host()
                host.runs[ENV_KEY] = {"cat fails": (0, "minions-env: readable\n"), "no mark": (1, "")}.get(
                    kind, (0, f"minions-env: {kind}\n"))
                host.runs[CAT_ENV] = (1, PERSONS_ENV + "cat: read error\n")  # what a cut read could carry
                fresh_look(host)
                code, lines = run_setup(setup_gh(sha), host)
                self.assertEqual(code, M.EXIT_STOP, lines)
                self.assertFalse([a for a, _ in host.fed if a[-1] == f"{FOLDER}/.env"], "written over")
                stop = next(x for x in lines if x.startswith(f"stop could not read {FOLDER}/.env ("))
                self.assertIn("so it is not written over, and nothing of it was changed", stop)
                command = re.search(r": `([^`]*)`$", stop).group(1)  # run_setup's lines hold each command in place
                self.assertTrue(command.startswith(step), (command, step))
                self.assertNotIn("made-value-r206", "\n".join(lines))  # no line of the file is printed
                for j in journals():
                    with open(j, encoding="utf-8") as f:
                        self.assertNotIn("made-value-r206", f.read())  # nor logged
                if kind != "not readable":
                    continue
                # The step's effect (the file the runner user's own and readable), then setup: the person's lines
                # stay, byte for byte, with the hooks' two lines after them. The stand-in has no GitHub for a setup
                # run again over one that stopped part-way, so this is a fresh world; the step itself, as printed,
                # and the same setup again are walked on a real guest.
                host, sha = setup_host()
                fresh_look(host)
                host.runs[ENV_KEY] = (0, "minions-env: readable\n")
                host.runs[CAT_ENV] = (0, PERSONS_ENV)
                code, lines = run_setup(setup_gh(sha), host)
                self.assertEqual(code, M.EXIT_OK, lines)
                env = next(x for a, x in host.fed if a[-1] == f"{FOLDER}/.env")
                self.assertTrue(env.startswith(PERSONS_ENV), env)
                self.assertEqual(env[len(PERSONS_ENV):].count("ACTIONS_RUNNER_HOOK_JOB_"), 2, env)
        # Not there, 0 bytes, cut short: as before (written new; nothing; the cut line kept as it was).
        for kind, text, want in (("not there", None, ""), ("0 bytes", "", ""), ("cut short", "JAVA_HO", "JAVA_HO\n")):
            with self.subTest(kind=kind):
                host, sha = setup_host()
                host.runs[ENV_KEY] = (0, "minions-env: not there\n" if text is None else "minions-env: readable\n")
                host.runs[CAT_ENV] = (0, text or "")
                fresh_look(host)
                self.assertEqual(run_setup(setup_gh(sha), host)[0], M.EXIT_OK)
                env = next(x for a, x in host.fed if a[-1] == f"{FOLDER}/.env")
                self.assertEqual(env.split("ACTIONS_RUNNER_HOOK_JOB_")[0], "LANG=C.UTF-8\n" + want)



class G2EnvAgain(unittest.TestCase):
    """R221, codon G2.30."""

    def test_G2_30_the_unreadable_env_stop_says_what_the_same_setup_again_does_with_the_file(self):
        # Seen once, steered, on a stock guest at the tag: a file of root's, mode 600, put in place after the
        # runner's configure ended. setup stopped on it as R206 promises and printed its step; the step was taken as
        # printed; the same setup again took the half-made runner away, started fresh and kept the file in the
        # person's record. The stop had said the file is where setup "keeps every other line as it is", and the
        # step "its lines unchanged; then run the same setup again": read as the lines staying in the runner's
        # file, which they did not.
        host, sha = setup_host()
        fresh_look(host)
        unpacks = lambda: sum(1 for a in host.ran if a[:6] == ("sudo", "-n", "-u", "minions-runner", "--", "tar"))
        world = {"step": False}
        fresh = "LANG=C.UTF-8\n"  # what the runner's own env.sh writes in a fresh folder
        def now_text():
            return PERSONS_ENV if world["step"] and unpacks() < 2 else fresh
        host.runs[ENV_KEY] = lambda argv, inp=None: (0, "minions-env: " + (
            "readable" if world["step"] or unpacks() >= 2 else "not readable") + "\n")
        host.runs[CAT_ENV] = lambda argv, inp=None: (0, now_text())
        host.runs[("sudo", "-n", "-u", "minions-runner", "--", "stat", "-c", "%s", f"{FOLDER}/.env")] = (
            lambda argv, inp=None: (0, f"{len(now_text().encode())}\n"))
        host.runs[("sudo", "-n", "-u", "minions-runner", "--", "sha256sum", f"{FOLDER}/.env")] = (
            lambda argv, inp=None: (0, f"{hashlib.sha256(now_text().encode()).hexdigest()}  {FOLDER}/.env\n"))
        deleted = lambda: any(a[-2:] == ("rmdir", FOLDER) for a in host.ran)  # the folder itself, once emptied
        host.runs[("sudo", "-n", "-u", "minions-runner", "--", "test", "-e", FOLDER)] = lambda argv, inp=None: (
            (0, "") if unpacks() == 1 and not deleted() else (1, ""))  # the half-made runner's folder (R128: as its user)
        def find(argv, inp=None):  # the removal's finds in that folder, as the runner user (findutils 4.9.0's forms)
            if list(argv[-3:]) == ["-maxdepth", "0", "-printf"] or "%D %y\n" in argv:
                return (0, "64769 d\n") if not deleted() else (1, f"find: '{FOLDER}': No such file or directory\n")
            return 0, ""  # the chmod walk, the delete, and nothing left after it
        host.runs[("sudo", "-n", "-u", "minions-runner", "--", "find", FOLDER)] = find
        code, lines = run_setup(setup_gh(sha), host)
        self.assertEqual(code, M.EXIT_STOP, lines)
        stop = next(x for x in lines if x.startswith(f"stop could not read {FOLDER}/.env ("))
        # The stop's words: what the same setup again does with the file, and no word that the lines stay in place.
        self.assertNotIn("keeps every other line as it is", stop)
        self.assertIn("so it is not written over, and nothing of it was changed", stop)
        self.assertIn("then run the same setup again: it takes this half-made runner away and starts fresh", stop)
        self.assertIn(f"keeps a copy of this file in your record ({host.record_dir()}), named there", stop)
        self.assertIn("a line of your own in it is yours to add to the new runner's file", stop)
        command = re.search(r": `([^`]*)`$", stop).group(1)  # the command stands alone, last
        self.assertEqual(command, f"sudo chown -h minions-runner {FOLDER}/.env && "
                                  f"sudo -u minions-runner chmod u+rw {FOLDER}/.env")
        # The outcome is what the words say: the step, then the same setup again.
        world["step"] = True
        gh_state = {"labels": ["minions", "minions-medium"], "deleted": False}
        configs = lambda: sum(1 for a in host.ran if M.CONFIG_SCRIPT in a)
        def runners(_body):
            if gh_state["deleted"]:
                return runners_answer() if configs() >= 2 else (200, {"total_count": 0, "runners": []})
            return runners_answer(labels=gh_state["labels"])  # the half-made runner, registered before the stop
        def put_labels(body):
            gh_state["labels"] = list(body["labels"])
            return 200, {"labels": [{"name": x, "type": "custom"} for x in body["labels"]]}
        def delete(_body):
            gh_state["deleted"] = True
            return 204, None
        gh = setup_gh(sha, {("GET", f"repos/{REPO}/actions/runners?per_page=100"): runners,
                            ("PUT", f"repos/{REPO}/actions/runners/7/labels"): put_labels,
                            ("DELETE", f"repos/{REPO}/actions/runners/7"): delete})
        code, lines = run_setup(gh, host)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertTrue([x for x in lines if x.startswith(f"ok a setup or removal of {REPO} here was cut part-way")],
                        lines)
        kept = kept_of(host)
        self.assertEqual(list(kept.values()), [PERSONS_ENV], "the kept copy holds the person's lines")
        (path,) = kept
        self.assertEqual(host.modes[path], 0o600)
        self.assertTrue([x for x in lines if f"it is kept as yours in {path}" in x], lines)
        env = [x for a, x in host.fed if a[-1] == f"{FOLDER}/.env"][-1]  # the new runner's file, as written last
        self.assertTrue(env.startswith(fresh), env)
        self.assertNotIn("MADE_BY_THE_PERSON", env)
        self.assertNotIn("JAVA_HOME", env)
        self.assertEqual(env.count("ACTIONS_RUNNER_HOOK_JOB_"), 2, env)
        self.assertNotIn("made-value-r206", "\n".join(lines))  # no line of the file is printed
        for j in journals():
            with open(j, encoding="utf-8") as f:
                self.assertNotIn("made-value-r206", f.read())  # nor logged



def world_a(user_answer=True):
    """R210, world (a): the suite's installed stand-in (remove_setup: our runner's service running and enabled, its
    slice, the watch, the runner user, its home, both folders, the registration on the GitHub stand-in) with this
    tool's whole state folder gone. gh's own answers (its login) are linux_host's; the runner service's User is
    minions-runner, as the unit the tool writes says, unless `user_answer` is False (a stand-in that does not say)."""
    gh, world, host = remove_setup(moved=False)
    shutil.rmtree(host.state_dir())
    user_q = list(M.systemctl("show", "-p", "User", "--value", UNIT))

    def answer(argv, input=None, wa=world.answer):
        if list(argv[:1]) == ["gh"]:
            return FakeHost._answer(host, argv, input)
        if user_answer and list(argv) == user_q:
            return 0, "minions-runner\n"
        return wa(argv, input)
    host._answer = answer
    return gh, world, host


def look_done(host):
    """A finished look on this home, stamped by the host's own clock (Clock), as a look writes it."""
    when = M.stamp(host.now() - M.dt.timedelta(minutes=5))
    M.save_state(host, {"looks": {REPO: {"time": when, "stopped": False, "survey": {
        "system": "Linux", "machine": "x86_64", "cores": 8, "mem": 16384000 * 1024, "free": 100 * 1024 ** 3,
        "serves": False, "serves_what": ""}}}})


def said_of_ours_as_not_made(lines):
    """Lines that say of what carries this tool's own names that this tool did not make it, or that it is the
    person's (R210: what was read is said, never what a record that does not name it does not say)."""
    names = ("minions-runner", "example-org-example-repo", M.HOOKS_ROOT, M.RECORDS_ROOT)
    return [x for x in lines if any(n in x for n in names)
            and re.search(r"did not make|did not write|it is yours|so it is yours|are yours", x)]


class G6Lost(unittest.TestCase):
    """R210 (G6.38): a computer that holds what carries this tool's own names and its record here does not name."""

    def test_G6_38_road_i_every_command_names_what_was_read_and_none_sends_the_person_to_setup(self):
        # World (a), road (i). Each command that would read the record stops before any change or call to GitHub,
        # naming what was read: the runner user, the runner service (running, enabled), what else of this tool's
        # names is here, the repository the service's name carries. No step deletes the user while its service runs,
        # and no line sends the person to setup, whose stop would be the same.
        runs = {
            "status": lambda gh, host, out: M.status(REPO, host=host, gh=gh, out=out),
            "doctor": lambda gh, host, out: M.doctor(None, host=host, gh=gh, out=out, interactive=True),
            "drain": lambda gh, host, out: M.drain(REPO, host=host, gh=gh, out=out, interactive=True),
            "resume": lambda gh, host, out: M.resume(REPO, host=host, gh=gh, out=out, interactive=True),
            "uninstall REPO": lambda gh, host, out: M.uninstall(REPO, host=host, gh=gh, out=out, interactive=True),
            "setup, after a finished look": None,
        }
        for name, run in runs.items():
            with self.subTest(command=name):
                gh, world, host = world_a()
                out = Out()
                if run is None:
                    look_done(host)
                    code = M.setup(REPO, host=host, gh=gh, out=out, interactive=True)
                else:
                    code = run(gh, host, out)
                lines = out.getvalue().splitlines()
                self.assertFalse([x for x in lines if x == f"setup {REPO}" or "sets one up" in x], lines)
                self.assertNotIn("userdel", out.getvalue())  # its service runs: no step deletes that user
                self.assertEqual(code, M.EXIT_STOP, lines)
                stop = [x for x in lines if x.startswith("stop ")]
                self.assertEqual(len(stop), 1, lines)
                rec = "is not there" if run else "does not name them"
                self.assertTrue(stop[0].startswith(
                    f"stop this computer holds what carries this tool's own names, and this tool's record here, "
                    f"{M.state_path(host)}, {rec}: the user minions-runner; the runner service {UNIT} (it runs now; "
                    "it starts again when this computer restarts, as systemd reads it enabled). Also of this tool's "
                    "names here: the unit minions.slice, the unit minions-watch.timer, the unit minions-watch.service, "
                    f"the folder /home/minions-runner, the folder {M.HOOKS_ROOT}, the folder {M.RECORDS_ROOT}."),
                    stop[0])
                self.assertIn("GitHub may still list a runner for the repository whose owner and name that "
                              "service's name carries (example-org-example-repo)", stop[0])
                self.assertIn("Settings, under Actions, then Runners", stop[0])
                self.assertIn(M.README_MAKES, stop[0])
                self.assertEqual(said_of_ours_as_not_made(lines), [], lines)
                # The end state: nothing changed, on this computer or on GitHub.
                self.assertEqual(world.order, [], name)
                self.assertEqual(world.active.get(UNIT), "active")
                self.assertTrue(world.user and FOLDER in world.paths)
                self.assertEqual((gh.runner or {}).get("name"), OURS)
                self.assertFalse([a for a in host.ran if a[:1] == ("sudo",) and a[2:3] not in (("true",), ("test",))],
                                 "no admin step")
                if run is not None:
                    self.assertFalse(os.path.exists(M.state_path(host)), "no record written")

    def test_G6_38_road_i_a_runner_user_alone_gets_the_userdel_step_only_when_none_of_its_processes_runs(self):
        # The runner user alone (no runner service of this tool's form, no record naming it). status says setup sets
        # one up (setup's stop is the next stop); setup stops before any admin step. Its userdel step is printed only
        # when the user's processes were read and none runs; taken as printed, the same setup goes to its end.
        there = (0, "minions-runner:x:1001:1001::/home/minions-runner:/bin/bash\n")
        for kind, pg in (("none runs", None), ("one runs", (0, "4321\n")),
                         ("not read", (2, "pgrep: could not read /proc\n"))):
            with self.subTest(processes=kind):
                runs = {("getent", "passwd", "minions-runner"): there}
                if pg is not None:
                    runs[("pgrep", "-u", "minions-runner")] = pg
                host, sha = setup_host(runs=runs)
                out = Out()
                M.status(REPO, host=host, gh=StatusGH(), out=out)
                self.assertTrue(f"`setup {REPO}` sets one up" in out.getvalue()
                                or f"setup {REPO}" in out.getvalue().splitlines(), out.getvalue())  # its step: setup
                fresh_look(host)
                code, lines = run_setup(setup_gh(sha), host)
                step = "(sudo userdel -r minions-runner; none of its processes runs now); then run the same command again"
                if kind == "none runs":
                    self.assertIn(step, lines[-1])  # read first: printed only when none of its processes runs
                else:
                    self.assertNotIn("userdel", lines[-1])
                self.assertEqual(code, M.EXIT_STOP, lines)
                self.assertEqual(admin_steps(host), [], "nothing is touched")
                self.assertTrue(lines[-1].startswith("stop this computer holds what carries this tool's own names, "
                                                     f"and this tool's record here, {M.state_path(host)}, does not "
                                                     "name it: the user minions-runner."), lines[-1])
                self.assertEqual(said_of_ours_as_not_made(lines), [], lines)
                if kind == "none runs":
                    # The step taken as printed: the user is gone (and its home with -r), then the same setup.
                    host.runs[("getent", "passwd", "minions-runner")] = (2, "")
                    code, lines = run_setup(setup_gh(sha), host)
                    self.assertEqual(code, M.EXIT_OK, lines)
                    self.assertTrue(M.load_state(host).get("runner_user_made"))
                else:
                    self.assertIn("1 process of the user minions-runner runs now, so no step here deletes that user"
                                  if kind == "one runs" else
                                  "Whether a process of the user minions-runner runs now could not be read", lines[-1])

    def test_G6_38_road_ii_uninstall_all_stops_before_any_change_while_that_service_can_still_run(self):
        # World (a), road (ii): a runner service of this tool's form that no record here names, running and enabled.
        # uninstall --all stops before any change (the watch, the slice and the hooks stay with it), saying what was
        # read, what it would take from that service, one step that switches it off and keeps it off at a restart,
        # and whose it is when another account set this tool up. The step taken as printed, then the same command
        # to its end: the off line, the unit file named (read from systemd's unit files: the unit is no longer
        # loaded), nothing of this tool's names running or starting at a restart, and GitHub's runner still listed.
        gh, world, host = world_a(user_answer=False)
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(world.order, [], "no change, the watch's removal included")
        self.assertEqual(world.active.get(f"{M.WATCH_UNIT}.timer"), "active")
        self.assertIn(SLICE_FILE, world.paths)
        self.assertEqual(code, M.EXIT_STOP, lines)
        self.assertFalse([x for x in lines if x.startswith(("tell the person", "need you"))], "a stop before the tell")
        stop = [x for x in lines if x.startswith("stop ")]
        self.assertEqual(len(stop), 1, lines)
        self.assertIn(f"the runner service {UNIT} (it runs now; it starts again when this computer restarts, as "
                      "systemd reads it enabled)", stop[0])
        self.assertIn("This command would take the limits (the slice), the watch and the job hooks from that service "
                      "and leave it as it is, so it stops here, before any change. Your next step, once GitHub shows "
                      "that runner idle or gone (Settings, under Actions, then Runners): switch that service off and "
                      "keep it off at a restart, as an administrator: ", stop[0])
        self.assertIn("If another account on this computer set this tool up, that service is theirs: the leave is run "
                      "from that account, not from this one", stop[0])
        step = re.search(r"`(sudo systemctl disable --now [^`]+)`|\n(sudo systemctl disable --now .+)\n",
                         out_text := "\n".join(lines) + "\n")
        self.assertTrue(step, out_text)
        argv = shlex.split(step.group(1) or step.group(2))
        self.assertEqual(argv, ["sudo", "systemctl", "disable", "--now", UNIT])
        self.assertEqual(world.answer(["sudo", "-n", *argv[1:], "--no-pager"]), (0, ""))  # the step, as printed
        self.assertNotIn(UNIT, world.answer(list(M.systemctl("list-units", "--all", "--plain", "--no-legend",
                                                             "actions.runner.*", "minions*")))[1],
                         "the stand-in lets a disabled, stopped unit go, as systemd does")
        code, lines = run_uninstall(gh, host, repo=None, everything=True)
        self.assertEqual(code, M.EXIT_OK, lines)
        self.assertEqual(lines[-1], "ok this tool is off this computer: nothing of this tool's names here runs now or "
                         "starts when this computer restarts, as read. What stayed is named above with why each "
                         f"stayed; of it, this tool's record here does not name: the user minions-runner; the runner "
                         f"service {UNIT} (it does not run now; it does not start when this computer restarts, as "
                         "systemd reads it disabled); the folder /home/minions-runner; the folder "
                         f"{M.HOOKS_ROOT}; the folder {M.RECORDS_ROOT}")
        self.assertIn(f"warn census: runner services: {UNIT}", lines)
        self.assertEqual(said_of_ours_as_not_made(lines), [], lines)
        self.assertTrue(world.user, "a user its record does not show it made is never removed")
        # The end state: the file stays, disabled; a restart starts nothing of it; GitHub still lists its runner.
        self.assertIn(f"/etc/systemd/system/{UNIT}", world.paths)
        self.assertNotIn(UNIT, world.enabled)
        world.restart()
        self.assertNotEqual(world.active.get(UNIT), "active")
        self.assertEqual((gh.runner or {}).get("name"), OURS)

    def test_G6_38_road_ii_a_service_stopped_but_enabled_or_not_read_still_stops_before_any_change(self):
        # Only a service that neither runs nor starts at a restart, both read, lets the leave go on.
        enabled_q = list(M.systemctl("is-enabled", UNIT))
        for kind in ("stopped, still enabled", "is-enabled not read", "is-active not read"):
            with self.subTest(kind=kind):
                gh, world, host = world_a(user_answer=False)
                if kind != "is-active not read":
                    world.answer(["sudo", "-n", "systemctl", "stop", UNIT, "--no-pager"])
                real = host._answer
                host._answer = lambda a, i=None, real=real, kind=kind: (
                    (1, "Failed to connect to bus: No such file or directory\n")
                    if (kind == "is-enabled not read" and list(a) == enabled_q)
                    or (kind == "is-active not read" and list(a[:2]) == ["systemctl", "is-active"] and UNIT in a)
                    else real(a, i))
                code, lines = run_uninstall(gh, host, repo=None, everything=True)
                self.assertEqual(world.order, [] if kind == "is-active not read" else [f"stop {UNIT}"], kind)
                self.assertEqual(code, M.EXIT_STOP, lines)
                self.assertIn(f"sudo systemctl disable --now {UNIT}", "\n".join(lines))
                self.assertFalse([x for x in lines if " is off this computer" in x], lines)

    def test_G6_38_the_new_reads_each_not_answered_is_said_and_never_taken_for_none(self):
        # Each of the reads that tell what carries this tool's names (the runner user, systemd's units, its unit
        # files, each folder beside the user), failed in turn on a computer with no record: the read not answered is
        # said, and no line says "off", "nothing to check", "nothing to uninstall" or sends the person to setup.
        bus = (1, "Failed to connect to bus: No such file or directory\n")
        user_there = (0, "minions-runner:x:1001:1001::/home/minions-runner:/bin/bash\n")
        worlds = [
            ("the runner user", {("getent", "passwd", "minions-runner"): (3, "getent: enumeration not supported\n")},
             None, "whether the user minions-runner is here (getent: enumeration not supported)"),
            ("systemd's units", {("systemctl", "list-units"): bus}, None,
             "systemd's units (systemctl answered code 1: Failed to connect to bus"),
            ("systemd's unit files", {("systemctl", "list-unit-files"): bus}, None,
             "systemd's unit files (systemctl answered code 1: Failed to connect to bus"),
        ] + [(f"the folder {f}", {("getent", "passwd", "minions-runner"): user_there}, f, f"whether the folder {f} is here")
             for f in ("/home/minions-runner", M.HOOKS_ROOT, M.RECORDS_ROOT)]
        commands = {
            "status": lambda h, o: M.status(REPO, host=h, gh=StatusGH(), out=o),
            "doctor": lambda h, o: M.doctor(None, host=h, gh=StatusGH(), out=o, interactive=True),
            "uninstall REPO": lambda h, o: M.uninstall(REPO, host=h, gh=StatusGH(), out=o, interactive=True),
            "uninstall --all": lambda h, o: M.uninstall(None, everything=True, host=h, gh=StatusGH(), out=o,
                                                        interactive=True),
        }
        for read, runs, folder, said in worlds:
            for name, run in commands.items():
                with self.subTest(read=read, command=name):
                    host, _sha = setup_host(runs=runs)
                    if folder:
                        there = host.there
                        host.there = lambda p_, there=there, folder=folder: None if p_ == folder else there(p_)
                    out = Out()
                    code = run(host, out)
                    lines = out.getvalue().splitlines()
                    bad = [x for x in lines if re.search(r"is off this computer|nothing to check|nothing to uninstall"
                                                         r"|sets one up", x) or x == f"setup {REPO}"]
                    self.assertEqual(bad, [], lines)
                    # the read by name, then what its program wrote (which may follow a warning line)
                    head, _, inner = said.partition(" (")
                    self.assertIn(head + (" (" if inner else ""), out.getvalue())
                    self.assertIn(inner.split(": ")[-1], out.getvalue())
                    self.assertEqual(code, M.EXIT_STOP, lines)
                    self.assertEqual(admin_steps(host), [], "nothing is touched")

    def test_G6_38_uninstall_all_ends_not_off_with_exit_1_while_a_process_of_the_runner_user_runs(self):
        # The runner user alone stays (its record does not show this tool made it) and a process of it runs: the
        # last line is "not off", and the exit is 1, so an agent that trusts exit codes does not report the tool gone.
        gh, world, host = remove_setup(moved=False)
        d = M.load_state(host)
        d.pop("runner_user_made")
        M.save_state(host, d)
        real = host._answer
        host._answer = lambda a, i=None: (0, "4444\n") if list(a) == ["pgrep", "-u", "1001"] and not world.listener \
            else real(a, i)
        code, lines = run_uninstall(gh, host, everything=True)
        self.assertEqual(code, M.EXIT_FAILED, lines)
        self.assertTrue(lines[-1].startswith("warn this tool is not off this computer: this run left as it is what "
                                             "carries this tool's own names and no record of this tool here names: "
                                             "the user minions-runner. 1 process of the user minions-runner runs now"),
                        lines[-1])

    def test_G6_38_the_unit_files_read_is_systemd_s_count_line_at_exit_0_and_at_exit_1(self):
        # systemd's count line is the answer whichever of exit 0 and exit 1 it ends with (systemd 249, 255 and 259
        # end 1 for none; an older one may end 0): "0 unit files listed." is none, "N unit files listed." the names.
        # Any other exit, or no count line, is not read. Both exits for none give every command the same lines.
        one = ("UNIT FILE STATE VENDOR PRESET\nactions.runner.example-org-example-repo.minions-box-1.service "
               "disabled enabled\n\n1 unit files listed.\n")
        unit = "actions.runner.example-org-example-repo.minions-box-1.service"
        cases = [((1, LUF_NONE), []), ((0, LUF_NONE), []), ((0, one), [unit]), ((1, one), [unit]),
                 ((2, LUF_NONE), None), ((0, ""), None), ((1, ""), None),
                 ((1, "Failed to connect to bus: No such file or directory\n"), None),
                 ((0, "UNIT FILE STATE VENDOR PRESET\n" + unit + " disabled enabled\n"), None)]
        for answer, names in cases:
            with self.subTest(answer=answer):
                host, _sha = setup_host(runs={("systemctl", "list-unit-files"): answer})
                got, why = M.own_unit_files(host)
                self.assertEqual(got, names, why)
                self.assertEqual(why == "", names is not None, why)
        commands = {
            "status": lambda h, o: M.status(REPO, host=h, gh=StatusGH(), out=o),
            "doctor": lambda h, o: M.doctor(None, host=h, gh=StatusGH(), out=o, interactive=True),
            "uninstall REPO": lambda h, o: M.uninstall(REPO, host=h, gh=StatusGH(), out=o, interactive=True),
            "uninstall --all": lambda h, o: M.uninstall(None, everything=True, host=h, gh=StatusGH(), out=o,
                                                        interactive=True),
        }
        for name, run in commands.items():
            with self.subTest(command=name):
                seen = []
                for rc in (1, 0):
                    host, _sha = setup_host(runs={("systemctl", "list-unit-files"): (rc, LUF_NONE)})
                    out = Out()
                    seen.append((run(host, out), out.getvalue()))
                self.assertNotIn("Not read", seen[1][1])
                self.assertEqual(seen[1], seen[0])

    def test_G6_38_a_unit_s_two_reads_have_three_answers_each(self):
        # Whether it runs now (is-active) and whether it starts at a restart (is-enabled): yes, no, or not read,
        # with what systemctl answered; an answer that is not one of the known words is never taken for no, and
        # enabled-runtime, which does not outlast a restart, is said as read, never as a yes. The third value: it can
        # still run (runs, starts, or either not read).
        cases = [
            ((0, "active\n"), (0, "enabled\n"), "it runs now",
             "it starts again when this computer restarts, as systemd reads it enabled", True),
            ((3, "inactive\n"), (1, "disabled\n"), "it does not run now",
             "it does not start when this computer restarts, as systemd reads it disabled", False),
            ((3, "failed\n"), (1, "masked\n"), "it does not run now",
             "it does not start when this computer restarts, as systemd reads it masked", False),
            ((3, "inactive\n"), (0, "enabled-runtime\n"), "it does not run now",
             "systemd reads it enabled-runtime: enabled until this computer restarts, and not after it", False),
            ((3, "inactive\n"), (0, "enabled\n"), "it does not run now",
             "it starts again when this computer restarts, as systemd reads it enabled", True),
            ((4, ""), (1, f"Failed to get unit file state for {UNIT}: No such file or directory\n"),
             "whether it runs now could not be read (systemctl answered code 4",
             "whether it starts when this computer restarts could not be read (systemctl answered code 1", True),
            ((1, "Failed to connect to bus\n"), (0, "static\n"),
             "whether it runs now could not be read (systemctl answered code 1",
             "whether it starts when this computer restarts could not be read (systemctl answered code 0", True),
        ]
        for active, enabled, run_w, boot_w, live_w in cases:
            with self.subTest(active=active, enabled=enabled):
                host = linux_host(runs={tuple(M.systemctl("is-active", UNIT)): active,
                                        tuple(M.systemctl("is-enabled", UNIT)): enabled})
                run, boot, live = M.unit_now(host, UNIT)
                self.assertTrue(run.startswith(run_w), run)
                self.assertTrue(boot.startswith(boot_w), boot)
                self.assertEqual(live, live_w)

if __name__ == "__main__":
    unittest.main()
