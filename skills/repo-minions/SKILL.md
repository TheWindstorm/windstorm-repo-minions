---
name: repo-minions
description: 'Windstorm Repo Minions: your own computers do your repo''s chores. It sets up a GitHub Actions runner on a computer the person owns, for one private repository, moves the jobs that computer can take onto it through a pull request the person merges, and keeps it ready, watched and capped so the computer stays theirs. Use when the user says "repo minions", "minions look", "minions setup", "minions status", "minions drain" or "minions resume"; asks to run their GitHub Actions jobs on their own computer, laptop or server; wants a self-hosted runner set up, checked, held or uninstalled for a private repository; or asks why a job on their own runner is waiting, was cut or stopped. Linux with systemd in this version. Never for a public repository.'
license: Apache-2.0
---

# Windstorm Repo Minions

A minion is a computer the person owns that takes jobs for one of their private GitHub repositories. This skill sets
one up, moves the jobs it can take, and keeps it ready, while the computer stays the person's: the jobs run as a
user of their own with no admin rights, inside processor and memory limits, and never on a public repository.

The whole tool is one script: `scripts/minions.py`, two levels above this skill's base directory
(`<base>/../../scripts/minions.py`). It uses only the Python standard library and the GitHub CLI (`gh`). Run it
with `python3 -I`. If the file is not there, this skill was installed without its plugin: get it with
`cd ~ && git clone https://github.com/TheWindstorm/windstorm-repo-minions`, in the person's home folder. If `git`
is not installed (a stock Debian has none), installing it is the person's step, like any `need you`: ask them to
run `sudo apt update && sudo apt install git` (Debian, Ubuntu) in their own terminal. Nothing
that `setup` installs runs from the clone, but `status` and `uninstall` are run from it later, and a clone under
`/tmp` is gone after a restart.

## The order, always the same

Run the steps in this order. Do not skip one, and do not start the next while the last ended on `stop`,
`need you` or `waiting`.

1. **look**: `minions.py look <owner/repo>`, then, with the person's yes, `minions.py look <owner/repo> --start-test`.
2. **tell**: paste look's `tell the person` lines to the person as one block, all of them, as printed (see
   "Before setup" below).
3. **setup**: `minions.py setup <owner/repo>`.
4. **move**: `minions.py move <owner/repo>`.
5. **verify**: after the person merged, `minions.py move <owner/repo> --verify`.
6. **status**: `minions.py status <owner/repo>`, whenever the person asks how it is doing.

To pause it: `minions.py drain <owner/repo>` holds the runner and waits until no job runs (nothing is cut);
`minions.py resume <owner/repo>` lets jobs land again. Before a restart or an update of the computer, drain before anything else.

## What the tool prints

Every line starts with one of six kinds. Read the line, then say it to the person in plain words; quote
the tool's own numbers and names, never your own. When you give the tool's words as its words, give them exactly
as printed: add nothing inside them (no other time zone, no conversion, no comment). Your own explanation is
never presented as the tool's.

| Line | Means | You do |
|---|---|---|
| `ok` | done and read back | go on |
| `warn` | done, but something to know | tell the person, then go on |
| `stop` | nothing more was done | tell the person why and what the line says to do; do not go on |
| `need you` | a step that is the person's | ask the person to do it; wait for them |
| `waiting` | not done yet: the tool is waiting for something, with a deadline | see "Waiting" below |
| `tell the person N of M` | words the person must hear | paste the whole block, 1 of M to M of M, as printed, before the next step |

The exit code is the worst line's:

| Exit | Means |
|---|---|
| 0 | every line `ok`, `warn` or `tell the person` |
| 2 | a `stop` line |
| 3 | a `need you` line and no `stop` (also the start test's yes, which comes before the other lines) |
| 4 | a `waiting` line and neither of those |
| 1 | the tool itself failed; its `stop` line names the error and the last step it wrote down |
| 64 | the command was typed wrong |
| 130 | the person interrupted it; the journal says which step it was on |

## Waiting

`waiting` is not done. Never tell the person it worked while the last line is `waiting`.
Tell them what is being waited for, that nothing is broken, and when the wait ends: the line carries its own
deadline. Then run the command the line names, at that time or when the person asks. For example: "GitHub has not
shown the runner online yet. Nothing is broken; it is waiting until 21:40 UTC. I will check again then."

## The person's steps

These are theirs. Never do them for the person, never type a password, never press Agree or Accept on a
terms or consent screen.

A step that is the person's is never done another way. If a `need you` line names a command, ask the person to
run it. If you cannot do what a line says, or the command fails, tell the person what it printed and ask; never
find another route (a download instead of the named install, another package source, another login, another
folder).

- **Logging in to GitHub.** If look says `need you: log in to GitHub`, ask the person to run `gh auth login` in
  their own terminal.
- **The admin password, once.** Setup needs this computer's admin password one time, to make the runner's own
  user and its boot service. Say: "Setup will ask for this computer's admin password once. Please type it where
  sudo asks, in your own terminal. I never see it." Run setup in that same terminal: the permission belongs to one
  terminal, and another one asks again.
- **A yes beside something live.** If setup says `need you: this computer serves something live`, say what it
  names (ports open to the network, containers) and that jobs here would share its processors, memory and disk.
  Ask whether to use another computer or to run jobs here anyway. Only on their yes in words, run
  `setup <owner/repo> --beside-live-too`.
- **The start test's yes.** Before `--start-test`, say what it does, in look's own words: one temporary branch
  with one workflow file, a job that runs on no machine, then the run and the branch are deleted and read back gone.
  Run it only after the person says yes.
- **Merging the move pull request.** The tool never merges. Say: "I opened pull request #N. Please read it and
  merge it yourself when you are happy with it."
- **A publishing job.** A job that publishes or deploys (a release, a package upload, a deploy) is left where it
  is. Moving one needs the person's word for that one job: `move <owner/repo> --include-job <file>:<job>`.
  Ask them by name of the job, one at a time.

## Before setup

look's last lines are `tell the person` lines, numbered `tell the person 1 of N:` to `N of N:` (9 on most
computers). Paste them to the person as they are, as one block, all of them, numbers included: never shortened,
merged, reworded or left out. Add your own words only after the block, never inside it. Then wait for their go
before setup. These are what they carry; if one is missing, say it in these words:

- **What a job can reach.** Name what look listed: the secrets a job can read (names only) and what already runs
  on this computer. Then say: "Anyone who can push to this repository can run code on this computer."
- **Restarts and sleep.** "A restart of this computer cancels the job running on it. A short sleep only delays it;
  a sleep of more than about 10 minutes fails it on GitHub. A job waits at most 24 hours for a runner before GitHub
  cancels it."
- **Waiting checks.** "While your computer is held, asleep or off, your checks wait. GitHub cancels a check that
  waited 24 hours."
- **The limits.** Read look's limits line to the person word for word. It sounds like "at most half of this
  computer's processors and 60 % of its memory", with the real numbers. The one way to change them is
  `setup <owner/repo> --limits <processors>,<memory %>`. If look says the memory share is too small for a job,
  give the person its way out and what that costs their own work, as the line says. On a computer with one
  processor the line says "1 of 1" and that everything else there will be slow while a job runs: read that too,
  and let the person decide.
- **A job too big for the cap.** "A job that needs more memory than that fails when it reaches the cap, and
  `status` names it, instead of running slowly."
- **A job's tools.** "A job here has only the tools installed on this computer. GitHub's own computers come with
  many tools already installed, so a job that stops with "command not found" needs that tool installed here."

- **Sleep.** "While a job runs, this computer does not fall asleep by itself. A sleep you ask for, the lid, or a
  low battery still puts it to sleep, about N seconds later." Give the delay look's numbered block names for this
  computer, not a number of your own. "A job cut that way is named as cut, not as your test failing." The tool takes two locks during a job: one that holds only the computer's own idle sleep, and one
  that puts off a requested sleep by the short delay the system allows. It never holds the lid switch, the power
  or sleep keys, a shutdown, or a sleep someone asks for.
- **One repository.** "This version serves one repository per computer; more come in the next version."
- **Storage.** "Artifact and cache storage count the same whichever computer ran the job."

## Cost

Say only the cost sentence look printed, with the date it was read, word for word. If look printed "I could not
read GitHub's rule today", say: "I could not read GitHub's page today." Never add a price, an amount, a promise
that something costs nothing, or that the person spends less: the tool did not read that.

## Safety rules

- **Private repositories only.** The tool refuses a repository that is not private, and stops its runner if
  a repository becomes public. Never set up a runner for a public repository by any other way: anyone could
  run code on the person's computer through a pull request.
- **The tool never merges.** It opens pull requests; the person merges them.
- **No token on a command line.** Tokens go from the GitHub CLI to the runner on standard input, never as an
  argument, never on the screen, never in a file another user can read. Do not paste a token into a command.
- **Other people's things.** On a computer other people also use, the tool counts their containers and runners
  and never names them. Say the counts; never go looking.
- **Merge rules.** Never tell the person that required checks or branch protection hold their merges unless look
  printed that it read them.
- **Nothing is cut.** Every stop, restart and removal drains before it acts. If the person wants the computer back now,
  `drain` and wait for its `ok`.

## Problems

| The person says | Do |
|---|---|
| "My job is waiting" | `status`; read its lines; if the runner is held, `resume` |
| "My test failed on my computer" | `status`; it names a job cut by a restart or stopped by this computer's memory limit; otherwise it is the job's own result |
| "I need my computer now" | `drain`, wait for its `ok` |
| "Something is wrong with it", or a command was cut part-way | `doctor <owner/repo>`, in the person's own terminal: it reads back every promise of the install, names each one not kept with its next step, and finishes or undoes a step that was cut |
| "Take it off" | `uninstall <owner/repo>`; `uninstall --all` leaves nothing of this skill on the computer and opens a pull request that puts moved jobs back (theirs to merge); pass on its `tell the person` line about that pull request's own checks |
