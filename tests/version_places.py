"""R194: the release gates' reads of a version from a name, in one place: the clone lines of this repository in the
README and the skill, and the README's status heading. test_G9_8 runs them on the real files, and test_G9_16
plants the forms a gate must refuse, so both read the same words.

Every `git clone` in a text has one of three answers, never a fourth: this repository whole (its options are read and
checked; the letter case of the host, the owner and the name is ignored, as GitHub ignores it), or an address in
OTHERS (another party's repository a text may name), or NOT READ, and a test fails naming its line. No clone is
skipped because the reader could not read it."""
import re

OWNER_NAME = "thewindstorm/windstorm-repo-minions"
# This repository's addresses, lower case, with no .git and no trailing slash: HTTPS, and the two ssh forms.
OURS = {f"https://github.com/{OWNER_NAME}", f"git@github.com:{OWNER_NAME}", f"ssh://git@github.com/{OWNER_NAME}"}
# Another party's repository a text may name in a clone, in the same form (none today).
OTHERS: set = set()

_CLONE = re.compile(r"git clone\b((?:(?!git clone\b)[^`\n])*)")  # up to a backtick, a line's end or the next clone
_ADDRESS = re.compile(r"(?:[a-z][a-z0-9+.-]*://\S+|[\w.-]+@[\w.-]+:\S+)", re.I)


def _plain(address: str) -> str:
    """An address as compared: a sentence's closing marks taken off its end, then .git and a slash, all lower case."""
    a = address.rstrip(".,;:!?)]'\"").lower()
    a = a[:-4] if a.endswith(".git") else a
    return a.rstrip("/")


def clones(text: str) -> list[tuple[str, str, str]]:
    """Every `git clone` in a text, each as (answer, options, its line): answer "ours", "other" or "not read"."""
    out = []
    for m in _CLONE.finditer(text):
        start = text.rfind("\n", 0, m.start()) + 1
        end = text.find("\n", m.start())
        line = text[start:end if end >= 0 else len(text)].strip()
        words = m.group(1).split()
        at = next((i for i, w in enumerate(words) if _ADDRESS.fullmatch(w)), None)
        if at is None:
            out.append(("not read", "", line))
            continue
        a = _plain(words[at])
        out.append(("ours" if a in OURS else "other" if a in OTHERS else "not read", " ".join(words[:at]), line))
    return out


def clone_options(text: str) -> list[str]:
    """The options of each clone of THIS repository in a text, every one on a line counted."""
    return [o for answer, o, _ in clones(text) if answer == "ours"]


def clones_ours(text: str) -> bool:
    """Whether a text carries a clone of this repository (so its README's status heading must name the version)."""
    return bool(clone_options(text))


def clones_not_read(text: str) -> list[str]:
    """The line of every clone that is neither this repository whole nor in OTHERS: a test fails naming each."""
    return [line for answer, _, line in clones(text) if answer == "not read"]


def heading_names(heading: str, version: str) -> bool:
    """Whether a status heading names exactly this version, and no other version that starts with it."""
    # R194: no digit, letter, dot, plus or hyphen after it, so 0.1.0-rc1, 0.1.0+local and 0.1.0rc1 are not 0.1.0
    return re.match(rf"{re.escape(version)}(?![0-9A-Za-z.+-])", heading) is not None
