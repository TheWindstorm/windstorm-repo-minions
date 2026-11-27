#!/usr/bin/env python3
"""The suite with the tag at its head, before the tag is pushed (row R219, codon G9.19; the family's rule of
2026-11-26).

    python3 scripts/tag_head_suite.py vX.Y.Z [COMMIT] [--source REPO]

Makes a fresh clone of REPO (default: the repository this script is in), with none of its tags; makes the
annotated tag vX.Y.Z on COMMIT (default: REPO's HEAD) in that clone only; checks the tag out, so the clone's head is
that tag; then runs every test file to its end, each in its own process as CI does (its own "Ran N tests" and "OK"
lines), and then the gate, tests/test_strand.py, whose own last line must say "gate: passed: ".

Exit 0 and a last line "ok: ..." only when all of it passed: then, and only then, the tag is made by hand where it
is pushed from, and pushed. Exit 1 and a line "STOP: ..." otherwise; the clone is kept and named, to read.
Nothing outside the clone is changed: no tag is made in REPO, nothing is pushed. Standard library only.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
GATE = "test_strand.py"
RAN = re.compile(r"^Ran [0-9]+ tests? in .*$", re.M)
OK = re.compile(r"^OK( \(.*\))?$", re.M)


def git(*args, cwd=None):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise Stop(f"git {' '.join(args)} failed (exit {p.returncode}): {p.stderr.strip()}")
    return p.stdout.strip()


class Stop(Exception):
    pass


def run_file(clone, name):
    """One test file to its end, in its own process; passed only with its own exit 0, Ran and OK lines."""
    p = subprocess.run([sys.executable, os.path.join("tests", name), "-v"], cwd=clone, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True, timeout=3600)
    out = p.stdout
    with open(os.path.join(clone, f".tag-head-{name}.log"), "w", encoding="utf-8") as f:
        f.write(out)
    if p.returncode != 0:
        raise Stop(f"tests/{name} failed (exit {p.returncode}); its output: {clone}/.tag-head-{name}.log")
    if not (RAN.search(out) and OK.search(out)):
        raise Stop(f"tests/{name} ended without its runner's summary: a run that ends early is not a pass")
    return out


def main(argv):
    ap = argparse.ArgumentParser(prog="python3 scripts/tag_head_suite.py")
    ap.add_argument("tag")
    ap.add_argument("commit", nargs="?", default="HEAD")
    ap.add_argument("--source", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args(argv)
    if not TAG.fullmatch(a.tag):
        ap.error(f"{a.tag!r} is not a version tag vX.Y.Z")
    tag, source = a.tag, a.source
    try:
        commit = git("rev-parse", "--verify", a.commit + "^{commit}", cwd=source)
    except Stop as e:
        print(f"STOP: {e}")
        return 1
    clone = tempfile.mkdtemp(prefix=f"tag-head-{tag}-")
    try:
        git("clone", "--quiet", "--no-tags", "--no-hardlinks", source, clone)
        if git("tag", "--list", cwd=clone):
            raise Stop("the fresh clone already holds a tag; the tag must be made in the clone only")
        git("-c", "user.name=tag-head-suite", "-c", "user.email=tag-head-suite@invalid", "tag", "-a", tag, "-m",
            f"{tag} (made in this clone only, to run the suite)", commit, cwd=clone)
        git("-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", tag, cwd=clone)
        if git("cat-file", "-t", tag, cwd=clone) != "tag":
            raise Stop(f"{tag} in the clone is not an annotated tag")
        head = git("rev-parse", "HEAD", cwd=clone)
        if head != commit or git("rev-parse", f"{tag}^{{commit}}", cwd=clone) != head:
            raise Stop(f"the clone's head is not {tag} on {commit}")
        print(f"clone {clone}: head {head}, annotated tag {tag} made there only")
        names = sorted(n for n in os.listdir(os.path.join(clone, "tests"))
                       if n.startswith("test_") and n.endswith(".py"))
        if GATE not in names:
            raise Stop(f"tests/{GATE}, the gate, is not in the tree at {tag}")
        for name in [n for n in names if n != GATE] + [GATE]:
            out = run_file(clone, name)
            print(f"ok tests/{name}: {RAN.search(out).group(0).strip()}")
        last = (out.rstrip("\n").splitlines() or [""])[-1]
        if not last.startswith("gate: passed: "):
            raise Stop("the gate's own last line is not there: it did not run to its end")
        print(f"ok the gate: {last}")
    except (Stop, subprocess.TimeoutExpired) as e:
        print(f"STOP: {e}; nothing was tagged or pushed outside the clone {clone}, which is kept to read")
        return 1
    shutil.rmtree(clone, ignore_errors=True)
    print(f"ok: the whole suite passed with {tag} at its head ({commit}); the tag may now be made and pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
