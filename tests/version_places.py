"""R194: the release gates' reads of a version from a name, in one place: the clone lines of this repository in the
README and the skill, and the README's status heading. test_G9_8 runs them on the real files, and test_G9_16
plants the forms a gate must refuse, so both read the same words."""
import re

# This repository's address, and only it: an optional .git, then no character a repository's name can hold, so
# windstorm-repo-minions-other or windstorm-repo-minions.github.io is another repository, never this one (R194).
OURS = r"https://github\.com/TheWindstorm/windstorm-repo-minions(?:\.git)?(?![A-Za-z0-9_.-])"


def clone_options(text: str) -> list[str]:
    """The options of each clone of THIS repository in a text, every one on a line counted."""
    return [o.strip() for o in re.findall(r"git clone ([^`]*?)" + OURS, text)]


def clones_ours(text: str) -> bool:
    """Whether a text carries a clone of this repository (so its README's status heading must name the version)."""
    return re.search(r"git clone [^`]*" + OURS, text) is not None


def heading_names(heading: str, version: str) -> bool:
    """Whether a status heading names exactly this version, and no other version that starts with it."""
    # R194: no digit, letter, dot, plus or hyphen after it, so 0.1.0-rc1, 0.1.0+local and 0.1.0rc1 are not 0.1.0
    return re.match(rf"{re.escape(version)}(?![0-9A-Za-z.+-])", heading) is not None
