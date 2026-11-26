# Windstorm Repo Minions

**Your repository's automated jobs (GitHub Actions) run on a computer you own.**

## Status: not released

Nothing here has a release yet. It is built in the open, in the order its plan,
[`DNA-STRAND-MASTER-PLAN.md`](DNA-STRAND-MASTER-PLAN.md), sets: every claim the skill makes has a test, and each
part is seen on a fresh machine before a release. What a release has and has not been seen to do is written in
[`CHANGELOG.md`](CHANGELOG.md).

## Install

Tell your coding agent:

> Go to https://github.com/TheWindstorm/windstorm-repo-minions and set it up for my repository OWNER/NAME.

**If you are the agent that was sent here:**
1. Clone this repository into the person's home folder: `cd ~ && git clone --branch release-0.1.0 https://github.com/TheWindstorm/windstorm-repo-minions`.
   If `git` is not installed (a stock Debian has none), installing it is the person's step: ask them to run
   `sudo apt update && sudo apt install git` (Debian, Ubuntu) in their own terminal.
2. Read `skills/repo-minions/SKILL.md` and follow it, in its order.
3. Stop and ask the person at each step the skill marks as theirs.
4. Do not invent steps. If the skill does not say what to do, ask the person. A step that is the person's is never
   done another way: if you cannot do it as the tool's line says, tell the person what it printed and ask.

The tool the skill runs is `scripts/minions.py`. It uses only Python's standard library and the GitHub CLI.

**What you need**
- A computer you own, running Linux with systemd. macOS comes in a later version.
- Python 3.10 or newer, `git`, and the GitHub CLI (`gh`). Ubuntu 22.04 or newer and Debian 12 or newer have a
  Python that is new enough; with an older one the tool stops on its first command, before any step.
- A **private** repository. This version serves one repository per computer.
- The tools your jobs use, installed on this computer. GitHub's own computers come with many tools already
  installed; this computer has only what you installed, so a job that stops with "command not found" needs that
  tool put on this computer.

**Your steps.** Only these are yours, and your agent stops and asks for each one:
- installing `gh`, `git` or Python if your computer lacks them (your agent names the one command);
- logging in to GitHub with `gh auth login`, if you are not, as an account that is an admin of the repository;
- running `setup`, and later `uninstall`, yourself in your own terminal (your agent gives you the exact line):
  each first shows you what you must read and goes on only when you press Enter;
- your computer's admin password, typed by you, once (again later for `resume`, `drain --stop`, `doctor` and
  `uninstall`, each in your own terminal);
- a yes in words if your computer already serves something live (a website, a database, a container), or another
  computer;
- a yes before the tool sends one test job that runs on no machine and is deleted at once;
- merging the pull request that moves your jobs (the tool never merges);
- a yes for each job that publishes or deploys, if you want it moved too.

Or do it by hand: clone the repository into your home folder as in step 1 above and run
`python3 -I windstorm-repo-minions/scripts/minions.py look OWNER/NAME`, then `setup`, then `move`, as `SKILL.md` describes.

**Why private only.** On a public repository, a stranger's pull request could run code on your computer, as GitHub's
own documentation warns. The tool refuses a public repository. If one is made public later, your computer refuses its
jobs before their first step, and the check takes the runner off that repository on GitHub's side.

**What it puts on your computer**
- **GitHub's own runner program.** It is downloaded from GitHub, and its checksum is checked against the two places
  GitHub publishes it. It lives in its own folder in the home of a new user, `minions-runner`. The runner keeps
  its own logs there for 30 days (as its own source reads at the version `setup` installs), so that folder grows
  with how busy your repository is.
- **That user.** It has no admin rights, and it cannot open your home folder.
- **A boot service** that runs the runner as that user. It is capped by default at half your processors and 60 % of
  your memory; on a computer with one processor that is all of it, and `look` says so plainly. `setup` tells you the real numbers before it installs, and `setup OWNER/NAME --limits PROCESSORS,MEMORY%` changes
  them, also on a runner already set up. A job that needs more memory than its cap fails when it reaches the cap,
  by name, instead of running slowly: on GitHub the run shows a failed step ("Process completed with exit code
  137"), and `status` on this computer says the memory cap stopped it, not your code, with the command that raises
  the cap. Jobs are kept out of your swap where this computer holds that: `setup` says whether it does, and
  `doctor` tells you if it ever stops.
- **Two small scripts the runner calls around each job.** They empty the job's folder afterwards and keep the
  computer from falling asleep on its own during the job. A sleep you ask for, or closing the lid, still happens.
- **A check every 5 minutes.** It keeps the runner ready, holds it when something is wrong, and tells `status`.
- **A folder of notes**, `~/.minions`.

In your own home folder it uses only these: the clone you made in step 1 (`~/windstorm-repo-minions`), the GitHub
CLI's own login (`~/.config/gh`, written when you run `gh auth login`), `~/.minions`, and, after leaving, your
record. It changes no other file of yours. Installing the plugin is Claude Code's own act: it writes only Claude
Code's plugin places. The skill itself makes nothing until you ask it to set runners up.

The admin password is used to make the user, install the service, its limits, the two scripts and the check. It is
typed into your computer's own password prompt, and the tool never sees or keeps it.

**Leaving.** After `uninstall --all`, nothing of this tool is left installed or running; your record of what it did
is kept in one folder you own, and uninstall tells you where (`~/repo-minions-record`: every step it took and each
job's start and end, plain text, nothing secret). `uninstall --all --delete-record` deletes that record too, once you
type delete at your own terminal. If you remove the skill with Claude Code's own `plugin uninstall`, only the skill
leaves Claude Code: the runner, its service, the check and the limits stay and go on taking your repository's jobs.
The one command that takes them off, in your own terminal:
`python3 -I /usr/local/lib/windstorm-repo-minions/minions.py uninstall --all`.

**What to expect**
- While your computer is off, asleep or held, your checks wait.
- GitHub cancels a check that has waited 24 hours.
- A job cut by a restart or a sleep shows on GitHub as failed. `status` names it as cut, never as your test failing.
- If nothing runs: `status` shows whether the runner is connected and waiting, and when GitHub last handed it a
  job. A job GitHub has not handed over is on GitHub's side: a schedule there can be late, or not come.

**Seen to work** (each on a stock virtual machine, with the versions it installs, against a private repository,
2026-11-22 and 2026-11-23):
- Ubuntu 24.04.5 LTS: Python 3.12.3, GitHub CLI 2.45.0, systemd 255.
- Ubuntu 22.04.5 LTS: Python 3.10.12, GitHub CLI 2.4.0, systemd 249.
- Debian 12: Python 3.11.2, GitHub CLI 2.23.0 (git installed by the person first).
- Claude Code 2.1.330 as the agent following the skill from a cold start. That is the only version tried.

**Seen to stop before any step** (stock virtual machine, 2026-11-23): Ubuntu 20.04.6 LTS, whose Python is 3.8.10
(and whose archive has no `gh`). The tool says the version it found and where this version runs.

**Not seen yet:**
- A real laptop on battery: the runner held until mains power, and the laptop named before setup, are tested and
  not seen, because a virtual machine has no battery.
- A real laptop's closed lid, low battery or a sleep you ask for, none of which the tool holds off during a job:
  tested, and not seen, because a virtual machine has no lid or battery.
- A real laptop's own sleep and a change of its network: in a virtual machine the runner was seen coming back by
  itself after a network cut and after a suspend, never yet on a real laptop.
- An account whose GitHub Actions start no jobs: `look`'s check for it is tested, and not seen on such an account.
- GitHub's own removal of a runner after 14 days offline. Seen only with the removal made by hand.
- A repository really turned public with a runner on it. Seen only with the privacy read made to answer "public";
  turning a repository public with a runner on it is not done, by rule.
- GitHub's API limit running out while the tool works.
- Other Linux distributions, macOS, Windows, other agents.

## What GitHub charges

GitHub does not charge for jobs that run on your own computers (its docs, read 2026-11-22). It announced a charge for
them in December 2025 and postponed it. `look` prints the sentence it finds on GitHub's billing page, with the date it
read it, or says it could not read the page.

Storage for artifacts and caches is GitHub's, and it counts the same whichever computer ran the job. Your computer's
electricity and upkeep are yours.

This is not a way around a GitHub bill. GitHub's page on locked accounts
(https://docs.github.com/en/billing/how-tos/troubleshooting/locked-account) says a lock can block GitHub Actions. It
does not say that jobs on your own computers are spared. Before anything is installed, `look` checks whether GitHub
starts jobs for your repository.

A job's code runs as the runner's own user, and it could leave a cron line or an at job behind. Uninstall and setup
read for both and say what they find.

Uninstall deletes the files the runner's user owns in /tmp, /var/tmp and /dev/shm, as that user and never as root.
Anything a job wrote anywhere else outside the runner's own folders is not looked for and stays.

If you later give the runner's user more rights (a container engine's group, a sudo rule), its jobs can leave files
that user does not own. Uninstall does not delete those. It names them.

## What it makes on a computer

If this tool's record of what it made (`~/.minions/minions.json`) and its second copy both cannot be read, the tool
cannot tell what it made, and it changes nothing. If neither is there while what carries the tool's names is on the
computer (left by an earlier setup whose record was lost, or made from another account on this computer), the tool
names what it reads and takes none of it on; `uninstall --all` goes on only once no runner service of its naming form
runs or starts at a restart, and then leaves that service's file, the user and its home as they are. These are the
places it makes, so you can look by hand:

- the user `minions-runner`, and its home, with one folder per repository under `/home/minions-runner/runners/`;
- one service per runner, named `actions.runner.` then the repository and the runner's name, `.service`, with a
  drop-in folder beside it in `/etc/systemd/system/`;
- the slice `minions.slice`, which holds the limits for jobs, in `/etc/systemd/system/`;
- the watch, `minions-watch.timer` and `minions-watch.service`, in `/etc/systemd/system/`;
- the job hooks and the copy of this tool the watch runs, in `/usr/local/lib/windstorm-repo-minions/`;
- each job's start and end, and when the watch last read the repository private, in `/var/lib/windstorm-repo-minions/`;
- in your own home: `~/.minions/` (the record, its copy, the journal and the lock its commands take turns by) and
  `~/repo-minions-record/` (your record of what it did, which is yours and stays).

A runner's settings file (`.env` in its folder) that holds lines you added is not deleted with the runner: it is
kept in `~/repo-minions-record/`, readable by you only, and the command names it.

The lock keeps this tool's own commands on one computer from changing the record at the same time. A home
folder shared by two computers is not held apart by it: set this tool up from one of them only.

On GitHub, a repository's runners are listed in its Settings, under Actions, then Runners.

## Family

Part of the Windstorm skills: [Pit Stop](https://github.com/TheWindstorm/windstorm-pit-stop),
[Pit Board](https://github.com/TheWindstorm/windstorm-pit-board),
[Tardigrade](https://github.com/TheWindstorm/windstorm-tardigrade).

This project is not affiliated with, endorsed by or sponsored by GitHub. GitHub is a trademark of GitHub, Inc.
