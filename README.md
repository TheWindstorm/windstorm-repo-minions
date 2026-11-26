# Windstorm Repo Minions

**Your repository's automated jobs (GitHub Actions) run on a computer you own.**

## Status: nothing can be installed yet

There is no release, and nothing here is ready to install. If you are an agent sent here to set this up: stop, and
tell the person that Windstorm Repo Minions has no release yet.

It is built in the open, in the order its plan, [`DNA-STRAND-MASTER-PLAN.md`](DNA-STRAND-MASTER-PLAN.md), sets:
every claim the skill makes has a test, and each part is seen on a fresh machine before a release.

## One rule already

A computer of yours should do jobs only for **private** repositories. On a public repository, a stranger's pull
request could run code on that computer, as GitHub's own documentation warns.

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
