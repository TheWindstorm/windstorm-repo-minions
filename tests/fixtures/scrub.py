"""Turn a MINIONS_RECORD file (one JSON line per GitHub call, see scripts/minions.py GitHub.api) into a fixture.

    python3 tests/fixtures/scrub.py RECORDED.jsonl OWNER/REPO > tests/fixtures/NAME.jsonl

What is scrubbed:
- the owner and repository become example-org/example-repo;
- every e-mail address becomes person@example.invalid;
- a person's name or login found in the fields GitHub uses for people (author, committer, actor,
  triggering_actor, sender) becomes Example Person / example-person;
- every account object (one with a `login`: a person, an organisation, an owner) loses its number: `id` becomes
  EXAMPLE_ACCOUNT_ID, `node_id` EXAMPLE_NODE_ID, `avatar_url` a numberless one, `gravatar_id` empty. A login is
  scrubbed, but the number GitHub gives an account names it just as surely (it leads back to the login).

Where (G9.5): a name only where it is the whole value of a name field; a login only where it is the whole value of a
login field or a whole segment of a URL path. Never inside other text, and never inside a file path: once a committer
named like a product turned `.github/workflows` into a made-up path. GitHub's own accounts (the merge committer
"GitHub", "web-flow", bots) are not people and stay.

The run aborts if anything token-shaped is left. Review the result before committing it.
"""
import json
import re
import sys

TOKENISH = re.compile(r"(gh[opsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PEOPLE_FIELDS = ("author", "committer", "actor", "triggering_actor", "sender")
NOT_PEOPLE = {"GitHub", "web-flow", "github-actions", "dependabot"}


def is_person(value: str) -> bool:
    return bool(value) and value not in NOT_PEOPLE and not value.endswith("[bot]") and value != "example-person" \
        and value != "Example Person"


def people(obj, names: set, logins: set) -> None:
    """Collect the names and logins of people from the fields GitHub uses for them."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in PEOPLE_FIELDS and isinstance(v, dict):
                if isinstance(v.get("name"), str) and is_person(v["name"]):
                    names.add(v["name"])
                if isinstance(v.get("login"), str) and is_person(v["login"]):
                    logins.add(v["login"])
            people(v, names, logins)
    elif isinstance(obj, list):
        for v in obj:
            people(v, names, logins)


def scrub(obj, repo: str, names: set, logins: set, key: str = ""):
    owner, name = repo.split("/")
    if isinstance(obj, dict):
        return {k: scrub(v, repo, names, logins, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v, repo, names, logins, key) for v in obj]
    if not isinstance(obj, str):
        return obj
    s = EMAIL.sub("person@example.invalid", obj)
    if key == "name" and s in names:
        return "Example Person"
    if key == "login" and s in logins:
        return "example-person"
    # The repository and its owner, as whole URL or name segments (case as GitHub writes them).
    s = re.sub(rf"(?<![A-Za-z0-9_.-]){re.escape(repo)}(?![A-Za-z0-9_-])", "example-org/example-repo", s)
    s = re.sub(rf"(?<=/){re.escape(name)}(?![A-Za-z0-9_-])", "example-repo", s)
    s = re.sub(rf"(?<![A-Za-z0-9_.-]){re.escape(owner)}(?![A-Za-z0-9_-])", "example-org", s)
    if s == name:
        s = "example-repo"
    # A login inside a URL: only a whole path segment.
    for login in logins:
        s = re.sub(rf"(?<=/){re.escape(login)}(?=$|[/?#])", "example-person", s)
    return s


EXAMPLE_ACCOUNT_ID = 1
EXAMPLE_AVATAR = "https://avatars.githubusercontent.com/u/0?v=4"
AVATAR_ID = re.compile(r"avatars\.githubusercontent\.com/u/(\d+)")


def accounts(obj):
    """Every account object loses the number that names it (see the top of this file)."""
    if isinstance(obj, dict):
        out = {k: accounts(v) for k, v in obj.items()}
        if isinstance(obj.get("login"), str):
            for k, v in (("id", EXAMPLE_ACCOUNT_ID), ("node_id", "EXAMPLE_NODE_ID"), ("avatar_url", EXAMPLE_AVATAR),
                         ("gravatar_id", "")):
                if k in out:
                    out[k] = v
        return out
    if isinstance(obj, list):
        return [accounts(v) for v in obj]
    if isinstance(obj, str):
        return AVATAR_ID.sub("avatars.githubusercontent.com/u/0", obj)
    return obj


def scrub_rows(rows: list, repo: str) -> list:
    names, logins = set(), set()
    for r in rows:
        people(r, names, logins)
    return [accounts(scrub(r, repo, names, logins)) for r in rows]


def main():
    path, repo = sys.argv[1], sys.argv[2]
    rows = [json.loads(x) for x in open(path, encoding="utf-8")]
    for r in scrub_rows(rows, repo):
        line = json.dumps(r, sort_keys=True)
        if TOKENISH.search(line):
            sys.exit("token-shaped text left; not writing the fixture")
        print(line)


if __name__ == "__main__":
    main()
