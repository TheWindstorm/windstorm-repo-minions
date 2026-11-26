# The lived-in round

A stock machine is empty where a person's is full: their shell, their git settings and their sudo settings are
there, and a stock virtual machine has none of them. So before a tag, after the stock machine, one round runs
**lived-in**: the same stock virtual machine, with the settings below in the person's shell, and the tool run from
that shell (`look`, `setup`, one real job, the watch's passes, `uninstall --all`). What is read after it: which of
these names reached the runner user's processes during the job, its files, its service and the tool's root folders;
whether a marking hook or module of the person's ran; and every line the tool said about them. Names and paths are
read, never values.

A planted token is a made-up value, never a real one.

| Setting | What it is set to | What it would do if handed on |
|---|---|---|
| `GH_TOKEN`, `GITHUB_TOKEN` | a made-up value | replace the login gh saved, for setup but not for the watch |
| `GH_ENTERPRISE_TOKEN`, `GITHUB_ENTERPRISE_TOKEN` | a made-up value | the same, for another host |
| `GH_HOST` | `ghe.invalid`, in a `look` only | move every gh call off github.com |
| `GH_CONFIG_DIR` | a folder of the person's, where the saved login was moved | move where gh reads the saved login; the watch, run without the shell, would read another place |
| `XDG_CONFIG_HOME` | another folder of the person's (`GH_CONFIG_DIR` wins over it) | the same, by gh's own order |
| `LANG`, `LC_ALL`, `LANGUAGE` | `de_DE.UTF-8`, `de_DE:de` | reach every job through the runner's `.env` |
| `GIT_CONFIG_GLOBAL` | a file with `url.<base>.insteadOf`, `credential.helper` and `core.hooksPath` (a marking hook) | move a fetch, use the person's credentials, run the person's hooks |
| `NODE_OPTIONS` | a small memory limit | change every Node program a job or the runner starts |
| `PYTHONPATH` | a folder with a marking `sitecustomize.py` | run the person's code in the tool (the tool runs `python3 -I`) |
| user site-packages | a marking `usercustomize.py` in `~/.local/lib/python3.X/site-packages`, first seen to run for a plain `python3 -c pass` in that account (the control), then a watch pass read for whether it ran it; not `sitecustomize.py`: on Ubuntu the system's own `sitecustomize.py` is found first, so a person's file of that name in the user site never runs (seen on Python 3.12.3) | run the person's code in every watch pass (the watch's unit starts `python3 -I`) |
| sudoers `env_keep+=` (`GIT_*`, `GH_TOKEN`) | a drop-in, one small extra pass | carry those names through `sudo -u minions-runner` |
| sudoers `timestamp_timeout=0` | a drop-in, one small extra pass | refuse every `sudo -n` after the one password |
| `HTTPS_PROXY` | not tried: the virtual machine has no proxy | the tool hands it on to gh as the person has it, as it does certificate settings |

What the tool promises for each: `docs/root-steps.md` lists the root steps; the runner's own configuration runs
with an environment built by name (`RUNNER_ENV` in `scripts/minions.py`); the tool's gh goes to github.com as the
saved login (`GH_NOT_HANDED`, `GH_PLACE_SETTINGS`). A test holds this list to those names
(`test_G9_13_the_lived_in_list_holds_to_the_code`).
