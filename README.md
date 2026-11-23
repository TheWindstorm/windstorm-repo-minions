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
1. Clone this repository: `git clone https://github.com/TheWindstorm/windstorm-repo-minions`.
2. Read `skills/repo-minions/SKILL.md` and follow it, in its order.
3. Stop and ask the person at each step the skill marks as theirs.
4. Do not invent steps. If the skill does not say what to do, ask the person. A step that is the person's is never
   done another way: if you cannot do it as the tool's line says, tell the person what it printed and ask.

The tool the skill runs is `scripts/minions.py`. It uses only Python's standard library and the GitHub CLI.

**What you need**
- A computer you own, running Linux with systemd. macOS comes in a later version.
- Python 3.10 or newer, `git`, and the GitHub CLI (`gh`).
- A **private** repository. This version serves one repository per computer.

**Your steps.** Only these are yours, and your agent stops and asks for each one:
- installing `gh`, `git` or Python if your computer lacks them (your agent names the one command);
- logging in to GitHub with `gh auth login`, if you are not, as an account that is an admin of the repository;
- your computer's admin password, typed by you, once;
- a yes before the tool sends one test job that runs on no machine and is deleted at once;
- merging the pull request that moves your jobs (the tool never merges);
- a yes for each job that publishes or deploys, if you want it moved too.

Or do it by hand: clone the repository and run `python3 -I windstorm-repo-minions/scripts/minions.py look
OWNER/NAME`, then `setup`, then `move`, as `SKILL.md` describes.

**Why private only.** On a public repository, a stranger's pull request could run code on your computer, as GitHub's
own documentation warns. The tool refuses a public repository. If one is made public later, it holds and stops the
runner within one check.

**What it puts on your computer**
- **GitHub's own runner program.** It is downloaded from GitHub, and its checksum is checked against the two places
  GitHub publishes it. It lives in its own folder in the home of a new user, `minions-runner`.
- **That user.** It has no admin rights, and it cannot open your home folder.
- **A boot service** that runs the runner as that user. It is capped by default at half your processors and 60 % of
  your memory. `setup` tells you the real numbers before it installs, and `setup OWNER/NAME --limits PROCESSORS,MEMORY%` changes
  them.
- **Two small scripts the runner calls around each job.** They empty the job's folder afterwards and keep the
  computer from falling asleep on its own during the job. A sleep you ask for, or closing the lid, still happens.
- **A check every 5 minutes.** It keeps the runner ready, holds it when something is wrong, and tells `status`.
- **A folder of notes**, `~/.minions`.

The admin password is used to make the user, install the service, its limits, the two scripts and the check. It is
typed into your computer's own password prompt, and the tool never sees or keeps it. `uninstall --all` takes
everything away and shows that nothing is left.

**What to expect**
- While your computer is off, asleep or held, your checks wait.
- GitHub cancels a check that has waited 24 hours.
- A job cut by a restart or a sleep shows on GitHub as failed. `status` names it as cut, never as your test failing.

**Seen to work:** Ubuntu 24.04.5 LTS, Python 3.12.3 and GitHub CLI 2.45.0 (the versions a stock Ubuntu 24.04 machine
installs). Anything else is not seen yet.

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

## Family

Part of the Windstorm skills: [Pit Stop](https://github.com/TheWindstorm/windstorm-pit-stop),
[Pit Board](https://github.com/TheWindstorm/windstorm-pit-board),
[Tardigrade](https://github.com/TheWindstorm/windstorm-tardigrade).

This project is not affiliated with, endorsed by or sponsored by GitHub. GitHub is a trademark of GitHub, Inc.
