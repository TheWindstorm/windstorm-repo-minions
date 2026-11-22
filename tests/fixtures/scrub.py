"""Turn a MINIONS_RECORD file (one JSON line per GitHub call, see scripts/minions.py GitHub.api) into a fixture.

    python3 tests/fixtures/scrub.py RECORDED.jsonl OWNER/REPO > tests/fixtures/NAME.jsonl

Every string is scrubbed: the owner and repository become example-org/example-repo, every e-mail address becomes
person@example.invalid, every person's name and login found in author, committer, actor and owner fields becomes
Example Person / example-person, and the run aborts if anything token-shaped is left. Review the result before
committing it.
"""
import json
import re
import sys

TOKENISH = re.compile(r"(gh[opsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def people(obj, found):
    """Collect names and logins of people from the fields GitHub uses for them."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("author", "committer", "actor", "triggering_actor", "sender") and isinstance(v, dict):
                for f in ("name", "login"):
                    if isinstance(v.get(f), str):
                        found.add(v[f])
            people(v, found)
    elif isinstance(obj, list):
        for v in obj:
            people(v, found)


def scrub(obj, swaps):
    if isinstance(obj, dict):
        return {k: scrub(v, swaps) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v, swaps) for v in obj]
    if isinstance(obj, str):
        s = EMAIL.sub("person@example.invalid", obj)
        for a, b in swaps:
            s = re.sub(re.escape(a), b, s, flags=re.I)
        return s
    return obj


def main():
    path, repo = sys.argv[1], sys.argv[2]
    owner, name = repo.split("/")
    rows = [json.loads(x) for x in open(path, encoding="utf-8")]
    found = set()
    for r in rows:
        people(r, found)
    swaps = [(repo, "example-org/example-repo"), (name, "example-repo"), (owner, "example-org")]
    swaps += sorted(((p, "example-person" if " " not in p else "Example Person") for p in found
                     if p and p.lower() not in (owner.lower(), "example-person")), key=lambda x: -len(x[0]))
    for r in rows:
        line = json.dumps(scrub(r, swaps), sort_keys=True)
        if TOKENISH.search(line):
            sys.exit("token-shaped text left; not writing the fixture")
        print(line)


if __name__ == "__main__":
    main()
