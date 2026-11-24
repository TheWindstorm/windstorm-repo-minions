"""Family rule 17, A TEST THAT PASSES HERE AND FAILS ON A STOCK RUNNER LEANS ON THIS MACHINE (row R142, G9.12).

Every test file imports this module before anything else. At import, for the whole test process and every child it
starts:

1. A temporary home, before anything imports the tool. HOME is a fresh temporary folder (the tool's own folders,
   which it puts under "~", land there); XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_CACHE_HOME and XDG_STATE_HOME are put
   under it; GIT_CONFIG_GLOBAL is an empty file there and GIT_CONFIG_NOSYSTEM=1 (git older than 2.32 does not know
   GIT_CONFIG_GLOBAL; the home is the base); this account's lines to its session (XDG_RUNTIME_DIR,
   DBUS_SESSION_BUS_ADDRESS, TMUX, TMUX_PANE) and to GitHub (GH_TOKEN, GITHUB_TOKEN, GH_CONFIG_DIR) are taken out. A
   child inherits all of it.
2. Loud stand-ins, first on PATH, for every name in FORBIDDEN: each exits 97 and writes one line into a file in the
   temporary folder, so a bash child that starts sudo is counted though this hook is not in it.
3. A Python audit hook in this process: starting a FORBIDDEN name found outside a temporary folder, or this
   module's own stand-in; starting a child whose environment names this account's real home as HOME, or no HOME,
   or a PATH that leaves out the stand-ins; and opening, listing or changing anything in the real home outside the
   checkout, the temporary folder and Python's libraries. Each is stopped at that call (MachineReached) and counted.
   A test may bring its own stand-in, in a temporary folder of its own.

4. The count fails the test in its own result, also when the test swallowed the error; and check(), called by each
   test file's tearDownModule, fails the run on any count from 2 or 3.

What this cannot see: a read that only stats a path (os.stat, os.path.exists: Python raises no audit event for it),
and what a child does by itself; parts 1 and 2 are for that.

Left off FORBIDDEN, with why: LEFT_OFF below, one reason per name; a test holds every program the tool and its
hooks list to one of the two.

MINIONS_GUARD_OFF=1 is the gate test's control: the guard taken out (no hook, no count), the home and stand-ins
kept so nothing real runs. MINIONS_GUARD_MEASURE=1, for taking a count only and never in CI: no temporary home and no
raise, everything recorded.
"""
import atexit
import contextlib
import os
import pwd
import shutil
import site
import sys
import tempfile
import unittest

FORBIDDEN = frozenset({
    # admin rights
    "sudo", "su", "pkexec", "doas",
    # services, timers, sessions, the init system (Linux and macOS)
    "systemctl", "journalctl", "loginctl", "systemd-run", "systemd-inhibit", "launchctl", "pmset", "diskutil",
    # packages
    "dpkg", "dpkg-query", "apt", "apt-get", "rpm", "dnf", "yum", "zypper", "snap", "brew",
    # scheduling
    "crontab", "at", "atq", "atrm", "batch",
    # users and groups
    "useradd", "userdel", "usermod", "groupadd", "groupdel", "passwd",
    # this account's logins and sessions
    "gh", "docker", "podman", "tmux", "claude"})
_FILES = "a file tool: acts only on the path a test hands it, and the default home is the temporary one"
_READS = "reads only"
_ON_PURPOSE = "the tests run it on purpose, inside the temporary home"
LEFT_OFF = {
    **{n: _FILES for n in ("install", "cp", "rm", "rmdir", "chmod", "chown", "tee", "tar", "mktemp", "find", "mkdir",
                           "realpath", "dirname")},
    **{n: "the hook tests use it on processes they started" for n in ("kill", "pgrep", "sleep", "setsid")},
    **{n: _READS for n in ("cat", "stat", "id", "test", "sha256sum", "systemd-detect-virt", "sysctl", "sw_vers",
                           "netstat", "date", "awk", "seq", "df", "tail", "tr")},
    **{n: _ON_PURPOSE for n in ("bash", "env", "python3", "git")},
}
ROOT = os.path.realpath(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REAL_HOME = os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir)
MEASURE = os.environ.get("MINIONS_GUARD_MEASURE") == "1"
OFF = os.environ.get("MINIONS_GUARD_OFF") == "1"


class MachineReached(RuntimeError):
    pass


_TMP = tempfile.mkdtemp(prefix="minions-guard-")
atexit.register(shutil.rmtree, _TMP, True)
BIN = os.path.join(_TMP, "bin")
LOG = os.path.join(_TMP, "reached.log")
os.makedirs(BIN)
for _name in FORBIDDEN:
    with open(os.path.join(BIN, _name), "w") as _f:
        _f.write("#!/bin/sh\n"
                 f"printf '%s: ran %s %s (a child)\\n' \"${{MINIONS_GUARD_TEST:-outside a test}}\" \"{_name}\" \"$*\""
                 f" >> '{LOG}'\n"
                 f"echo 'machine_guard: {_name} is never run in a test (rule 17)' >&2\n"
                 "exit 97\n")
    os.chmod(os.path.join(BIN, _name), 0o755)
os.environ["PATH"] = BIN + os.pathsep + os.environ.get("PATH", os.defpath)

HOME = os.path.join(_TMP, "home")
if not MEASURE:
    os.makedirs(HOME)
    os.environ["HOME"] = HOME
    for _k, _sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"),
                     ("XDG_CACHE_HOME", ".cache"), ("XDG_STATE_HOME", ".local/state")):
        os.environ[_k] = os.path.join(HOME, _sub)
        os.makedirs(os.environ[_k])
    os.environ["GIT_CONFIG_GLOBAL"] = os.path.join(HOME, ".gitconfig")
    open(os.environ["GIT_CONFIG_GLOBAL"], "w").close()
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"
    for _k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GH_CONFIG_DIR", "XDG_RUNTIME_DIR",
               "DBUS_SESSION_BUS_ADDRESS", "TMUX", "TMUX_PANE"):
        os.environ.pop(_k, None)


def path(rest: str) -> str:
    """A PATH for a test's child that names its own folders: the stand-ins stay first."""
    return BIN + os.pathsep + rest


def _allowed_roots():
    roots = {ROOT, os.path.realpath(tempfile.gettempdir()), os.path.realpath(sys.prefix),
             os.path.realpath(sys.base_prefix), os.path.realpath(sys.exec_prefix)}
    try:
        roots.add(os.path.realpath(site.getusersitepackages()))
    except Exception:
        pass
    for p in sys.path:  # Python's own libraries; never the home itself
        if p and os.path.isabs(p):
            roots.add(os.path.realpath(p))
    roots.discard(REAL_HOME)
    return tuple(sorted(roots))


ALLOWED = _allowed_roots()
reached = []
_sink = None  # expect() collects here instead
_busy = False

_run = unittest.TestCase.run


def _named_run(self, result=None):
    """A child's stand-in line names the test that started it; and the count fails the test in its own result (part
    4), also when the test swallowed the error."""
    os.environ["MINIONS_GUARD_TEST"] = self.id()
    try:
        return _run(self, result)
    finally:
        os.environ.pop("MINIONS_GUARD_TEST", None)
        new = [x for x in seen() if x.startswith(self.id() + ": ")]
        if new and not OFF and result is not None:
            try:
                raise AssertionError("this test reached this machine instead of a stand-in (rule 17):\n"
                                     + "\n".join(new))
            except AssertionError:
                result.addFailure(self, sys.exc_info())


unittest.TestCase.run = _named_run


def _where():
    return os.environ.get("MINIONS_GUARD_TEST") or "outside a test"


def _in_home(p):
    if isinstance(p, bytes):
        p = os.fsdecode(p)
    if not isinstance(p, str) or not p:
        return None
    p = os.path.realpath(p if os.path.isabs(p) else os.path.join(os.getcwd(), p))
    if p != REAL_HOME and not p.startswith(REAL_HOME + os.sep):
        return None
    if "__pycache__" in p or any(p == r or p.startswith(r + os.sep) for r in ALLOWED):
        return None
    return p


def _name(first):
    if isinstance(first, (list, tuple)):
        first = first[0] if first else ""
    if isinstance(first, bytes):
        first = os.fsdecode(first)
    return os.path.basename(str(first or "").split()[0]) if str(first or "").split() else ""


def _child(exe, argv, env):
    """What is wrong with starting this child, or None."""
    name = _name(exe) or _name(argv if isinstance(argv, (list, tuple)) else str(argv or ""))
    env = os.environ if env is None else env
    def get(k):
        v = env.get(k) if k in env else (env.get(k.encode()) if isinstance(env, dict) else None)
        return os.fsdecode(v) if isinstance(v, bytes) else v
    p = get("PATH")
    if name in FORBIDDEN:
        first = exe or (argv[0] if isinstance(argv, (list, tuple)) and argv else str(argv or "").split()[0])
        first = os.fsdecode(first) if isinstance(first, bytes) else str(first)
        found = first if os.sep in first else shutil.which(first, path=p if p is not None else os.defpath)
        found = os.path.realpath(found) if found else None
        tmp = os.path.realpath(tempfile.gettempdir())
        if not found or not found.startswith(tmp + os.sep) or found.startswith(os.path.realpath(BIN) + os.sep):
            return f"ran {name}"
    home = get("HOME")
    if not home:
        return f"started {name or 'a child'} with no HOME"
    if os.path.realpath(home) == REAL_HOME:
        return f"started {name or 'a child'} with this account's real home as HOME"
    if p is not None and BIN not in p.split(os.pathsep):
        return f"started {name or 'a child'} with a PATH that leaves out the stand-ins ({p})"
    return None


def _hook(event, args):
    global _busy
    if _busy:
        return
    what = None
    _busy = True
    try:
        if event == "subprocess.Popen":  # (executable, args, cwd, env)
            what = _child(args[0], args[1], args[3] if len(args) > 3 else None)
        elif event in ("os.posix_spawn", "os.exec"):  # (path, argv, env)
            what = _child(args[0], args[1], args[2] if len(args) > 2 else None)
        elif event == "os.system":
            what = _child(None, str(args[0]), None)
        elif event in ("open", "os.listdir", "os.scandir", "os.mkdir", "os.remove", "os.rmdir", "os.rename",
                       "os.replace", "os.chmod", "os.chown", "os.symlink", "os.link", "shutil.rmtree", "os.truncate",
                       "os.utime"):
            p = _in_home(args[0]) if args else None
            if p:
                what = f"{event} in this account's real home: {p}"
    except Exception as e:  # the guard's own fault is counted too, never read as "nothing reached"
        what = f"the guard could not read {event}: {e!r}"
    finally:
        _busy = False
    if what:
        seen = f"{_where()}: {what}"
        (reached if _sink is None else _sink).append(seen)
        if not MEASURE:
            raise MachineReached(seen)


@contextlib.contextmanager
def expect():
    """For the guard's own test: what the guard stops inside is collected here, not counted against the run."""
    global _sink
    old, _sink = _sink, []
    try:
        yield _sink
    finally:
        _sink = old


def seen() -> list:
    """Everything counted so far: this process's hook, then the stand-ins' lines from every child."""
    try:
        with open(LOG) as f:
            children = f.read().splitlines()
    except FileNotFoundError:
        children = []
    return reached + children


def check():
    """Called by each test file's tearDownModule: fails on any test that reached this machine."""
    if seen() and not OFF:
        raise AssertionError("tests reached this machine instead of a stand-in (rule 17):\n" + "\n".join(seen()))


if not OFF:
    sys.addaudithook(_hook)
