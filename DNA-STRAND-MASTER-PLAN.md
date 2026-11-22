# Windstorm Repo Minions · DNA Strand Master Plan

**Plan version 1.1 · 2026-11-22 · The Windstorm**

> A minion, in infrastructure software, is a worker machine that takes orders from a master. Here the master is
> GitHub, the orders are your repository's chores (tests, builds, checks), and the minions are computers you own.

This document is the complete blueprint for Windstorm Repo Minions. Everything that gets built is built from here.
If you are an agent building a part of this, read **Part 0** first and follow it exactly. If you are a person, read
**Part 1**. The plan is modeled on how life builds: one strand of instructions, each instruction (a codon) small
enough to check, each one checked by a test that carries its number.

Nothing in this plan is built yet. Every codon below says the name its test will have; the name is written
plainly until the test exists, and in `code` form once it does (Part 0, rule 2).

---

## Contents

- Part 0. Rules for builders
- Part 1. The organism on one page
- Part 2. Words used in this plan
- Part 3. Invariants that are never broken
- Part 4. Architecture
- Part 5. The genes: G1 Look first · G2 Install · G3 Move the chores · G4 Keep it ready · G5 Watch and hold ·
  G6 Remove · G7 Words to the person · G8 The skill and its commands · G9 Tests, CI and the promise gate ·
  G10 Self-update · G11 Laptops · G12 Many repositories on one computer
- Part 6. Repository layout
- Part 7. Platform contracts to check before coding
- Part 8. Milestones and acceptance
- Part 9. How we know it works
- Part 10. Decisions already made, and why
- Part 11. Open questions
- Part 12. What already exists, and what this adds
- Appendix A. A worked example
- Appendix B. Changing this plan

---

## Part 0. Rules for builders

1. **The plan wins.** If the code and this plan disagree, the code is wrong. If the plan is wrong, change the plan
   first, in its own pull request (Appendix B), and only then the code.
2. **Every claim has an ID** like `G2.4` (gene 2, codon 4) or `INV3`. Every codon gets at least one test whose name
   carries the ID: `test_G2_4_...`. Until the test exists the plan writes its name plainly after "planned:"; the
   pull request that adds the test writes it in `code` form. `tests/test_strand.py` refuses a test the plan names
   in `code` form that does not exist, and a codon test the plan does not name.
3. **Build genes in the order of Part 8.** A gene starts only when the genes it depends on pass.
4. **Python 3.10 or newer, standard library only,** for the tool. Tests may use the standard `unittest`. The skill
   itself is Markdown. No package from the internet is installed by the product except GitHub's own runner
   release, checked against its published checksum (G2.3).
5. **Never invent a field, command, flag or path** that is not in this plan. Need one: change the plan first.
6. **Dumb tool, smart agent.** The tool reads, writes and reports facts in plain lines; the agent, guided by the
   skill, explains them to the person and decides with them. The tool never guesses on the person's behalf.
7. **Never print, log or pass a secret on a command line.** Tokens move through standard input or a file only the
   tool's user can read, and are gone when the step ends. Tests build fake secrets from pieces at run time.
8. **Check Part 7 before writing the code that leans on it,** and write what was seen in `docs/CONTRACTS.md` with
   the date, the version of the runner or service, and the machine kind.
9. **One gene per pull request,** titled with the gene ID. Every check reads pass before a merge.
10. **Do not grow the product.** Repo Minions puts a repository's jobs on the person's own computers, keeps them
    ready, says plainly what is happening, and takes itself away cleanly. It is not a CI service, a cloud, a
    dashboard of builds, or a way around a bill. Ideas go to Part 11 or `TODO.md`.

---

## Part 1. The organism on one page

### The problem

GitHub runs a repository's automated chores (GitHub Actions jobs) on its own machines and counts them against the
account's monthly included usage; past it, use is billed or blocked. GitHub also lets the chores run on computers
the person owns (self-hosted runners). Doing that well takes more than the four commands in GitHub's guide. Months of
running such computers every day showed what goes wrong: a runner that does not come back after a restart, a job
that waits a day and is cancelled because no computer carries the label it asks for, a disk filled by old work
folders, a machine slowed until its jobs time out, two runners sharing a cache and breaking each other, a job that
reaches files or services it should never see, and a public repository where a stranger's pull request can run code
on the person's computer.

### What GitHub says about cost (read 2026-11-22; re-read before every release)

GitHub's billing documentation says jobs on self-hosted runners do not reduce the included usage and are not
charged; the person pays for their own machines. In December 2025 GitHub announced a per-minute charge for
self-hosted runner use on private repositories, then postponed it with no new date. Storage for artifacts and
caches is counted the same way whichever machine ran the job. So this skill never says "free" or "no bill": it says
what GitHub's own page says on the day it is read, and tells the person when that page changes (G5.10).

### The cure

1. **Look first** (G1). Before anything is installed: is the repository private, what can the person's GitHub
   account and token do, does GitHub start jobs for this repository at all, what secrets can a job reach, and what
   kind of computer is this (cores, memory, disk, what already lives on it).
2. **Install** (G2) GitHub's own runner, checked, as a boot service under its own user with no admin rights, one
   runner per repository, one job at a time.
3. **Move the chores** (G3): a pull request that changes each workflow's `runs-on` to the person's computer, names
   every job that cannot move, and never moves a publishing job silently. The person merges it.
4. **Keep it ready** (G4): limits on CPU, memory and disk, no sleep in the middle of a job, cleanup after jobs.
5. **Watch and hold** (G5): one status line that tells the truth from both sides, and holds (no new jobs, nothing
   killed) when the repository turns public, the disk runs low or the machine is busy.
6. **Remove** (G6): one command takes it all away, on GitHub and on the computer, and shows that nothing is left.

### What it is not

- Not a service run by us: everything runs on the person's computers and their GitHub account.
- Not for public repositories, ever (INV1).
- Not a way around a GitHub bill or a locked account (INV5, G1.4).

### The shape

```
 PERSON           AGENT (skill)                    TOOL (minions.py)                        GITHUB
 "set up      ─►  reads SKILL.md ─► look ────────► reads account, repo, workflows, machine ◄─► REST API
  Repo Minions"   explains, asks  ◄── plain lines                                               │
 logs in,         setup ─────────────────────────► runner user + checked runner + service ───► registered runner
 approves,        move ──────────────────────────► branch + pull request with runs-on ───────► PR (person merges)
 merges           status / hold / remove ────────► both sides read, holds by label ─────────► jobs, queue
```

---

## Part 2. Words used in this plan

| Word | Meaning |
|---|---|
| **Person** | The owner of the repository and the computer. The only one who logs in, approves and merges. |
| **Agent** | The coding agent the person talks to, guided by this skill. |
| **Tool** | `scripts/minions.py`, the one program the agent runs. |
| **Repository** | One GitHub repository the person chose. Always private (INV1). |
| **Runner** | One copy of GitHub's runner program, registered to one repository, on one computer. |
| **Minion** | A computer that carries one or more runners. |
| **Runner user** | The account on the minion that the runners run as. It is not the person's own account and has no admin rights. |
| **Label** | A word a runner carries; a job's `runs-on` asks for labels. Our runners are registered with no default labels (no `self-hosted`, no OS, no architecture) and carry only ours: `minions` and `minions-<size>` (G2.7, G2.11). |
| **Hold** | The state in which a runner takes no new job. A running job finishes. Made by removing our labels, never by stopping a job, with one exception: a repository that is no longer private (G5.4). |
| **Drain** | Hold, then wait until no job is running. |
| **Both sides** | GitHub's view of a runner (online, busy) and the minion's view (service running, a job process present). |
| **Start test** | One job sent to the repository, on a temporary branch, that runs on no machine, to see whether GitHub starts jobs for it at all; it leaves nothing behind (G1.4). |

---

## Part 3. Invariants that are never broken

| Codon | Claim | Test |
|---|---|---|
| INV1 | Never a runner on a public repository, ours or the person's. `setup` refuses a repository that is not private; `watch` stops every runner of a repository within its interval when that repository is no longer private, and says so (G5.4). The guard lives in the tool, never only in a workflow file, because a fork's pull request runs its own copy of the workflow. | planned: test_INV1_never_a_runner_on_a_public_repository |
| INV2 | No secret on a command line, in a log, on the screen or in a file another user can read. Registration and removal tokens are read from standard input or a 0600 file, used, and dropped. | planned: test_INV2_no_secret_on_argv_or_in_output |
| INV3 | Nothing that costs money: the tool never changes a plan, a spending limit, a payment method or a billed setting, and never adds a GitHub-hosted job without the person choosing it by name (G5.9). | planned: test_INV3_nothing_that_costs_money |
| INV4 | Never kill a running job. Every stop, restart, update and removal drains first and waits (G5.6). Two exceptions, each said before it happens: the person's own explicit `remove --now`, and a repository that is no longer private (G5.4), where the service is stopped after a short drain even if a job still runs. | planned: test_INV4_never_kill_a_running_job |
| INV5 | Say only what was seen. Every status line names the side it came from and the time it was read. "Online" is never written as "working"; a cost sentence always carries the date its source was read. | planned: test_INV5_say_only_what_was_seen |
| INV6 | Removal leaves nothing: after `remove`, a census of runners on GitHub, services, folders, the runner user, labels in workflows and scheduled checks shows none of ours (G6). | planned: test_INV6_remove_leaves_nothing |
| INV7 | The person's steps stay theirs: logging in to GitHub, typing their computer's admin password once, approving access, merging the pull request. The agent never presses Agree or Accept on a terms or consent screen. | planned: test_INV7_the_persons_steps_stay_theirs |
| INV8 | A step that is interrupted leaves the machine as it was or as it would be after the step: every step writes what it is about to do first, and `doctor` finishes or undoes a half-done step. | planned: test_INV8_an_interrupted_step_is_finished_or_undone |

---

## Part 4. Architecture

- **One skill, one tool.** `skills/repo-minions/SKILL.md` tells the agent what to run, in what order, what to say,
  and which steps are the person's. `scripts/minions.py` does the work and prints short, stable lines
  (`ok ...`, `warn ...`, `stop ...`, `need you ...`) that the agent reads and explains.
- **State** lives in one folder per minion owned by the person's account: `~/.minions/` (0700), holding
  `minions.json` (which repositories, which runners, which labels, the last reads), `journal.log` (each step written
  before it runs, INV8) and nothing secret. The runners themselves live in the runner user's home.
- **GitHub is read and written only through the REST API** with the person's own GitHub CLI login (`gh api`), so
  the tool never holds the person's token. The scopes needed are checked first (G1.3).
- **The boot service** per OS: Linux, a systemd system unit per runner (as GitHub's runner installs it) plus a
  drop-in with our limits; macOS, a LaunchDaemon that runs as the runner user (Part 7, C8); Windows, later (Part 8).
- **The watch** is one small scheduled check per minion (a systemd timer, a LaunchDaemon interval), run as the
  person's account with no admin rights, that reads both sides and applies holds (G5).

---

## Part 5. The genes

### G1 Look first

Nothing is installed until `look` has run and the agent has told the person what it found. `look` changes
nothing.

| Codon | Claim | Test |
|---|---|---|
| G1.1 | `look <owner/repo>` reads the repository's visibility and stops with a plain reason if it is not private (INV1). | planned: test_G1_1_look_stops_on_a_repository_that_is_not_private |
| G1.2 | `look` reads whether the owner is a personal account or an organisation and says what follows: a personal account can only have runners per repository; an organisation can use a runner group limited to chosen private repositories. | planned: test_G1_2_look_names_account_kind_and_runner_scope |
| G1.3 | `look` checks that the person's GitHub CLI login has what the next steps need (admin on the repository to register a runner; `workflow` scope to change workflow files) and names any that is missing, with the one command the person runs to add it. | planned: test_G1_3_look_names_missing_token_rights |
| G1.4 | `look` runs one start test, only with the person's yes to that one change: it pushes a temporary branch `minions-start-test-<random>` whose only commit has no parent and holds one file, `.github/workflows/minions-start-test.yml`, whose one job asks for a label no runner carries. Because the commit holds no other file, none of the person's own workflows runs on it; the default branch is never touched, so a protected default branch does not matter. GitHub creates the job and it waits on no machine; `look` reads that one job exists, cancels the run, deletes the run, deletes the branch, and reads back that the branch and the run are gone (Part 7 C9). A run that ends `startup_failure` with zero jobs is reported as "GitHub did not start the run; this is the account's or repository's setting or billing, not your computer", and `setup` stops there: a runner cannot fix it, and the skill is never offered as a way around it. If the branch cannot be pushed (a rule on branch creation, no `workflow` scope), `look` says which and reads the repository's recent runs instead; with none, it says "could not test whether GitHub starts jobs here" and `setup` asks the person to decide. | planned: test_G1_4_the_start_test_leaves_nothing_and_names_a_zero_job_startup_failure |
| G1.5 | `look` counts the Actions secrets a job of this repository can read (repository, environment and, where readable, organisation) and names them, so the person knows what a job on their computer can reach. Names only, never values. | planned: test_G1_5_look_names_the_secrets_a_job_could_read |
| G1.6 | `look` surveys the computer: OS and version, architecture, cores, memory, swap in use, disk kind where it can be read, free space on the disk that will hold the work folders, whether it runs on battery or Wi-Fi only, its sleep setting. | planned: test_G1_6_look_surveys_the_computer |
| G1.7 | `look` lists what already lives on the computer that a job could disturb or be disturbed by: running services that listen on ports, container projects, other runners. A computer that serves something live is named as such, and `setup` asks the person to choose another computer or say yes in words. | planned: test_G1_7_look_names_what_already_lives_on_the_computer |
| G1.8 | `look` reads every workflow of the repository and lists, per job: its `runs-on`, the tools it asks for (setup actions, `sudo`, containers, browsers), and whether this computer has them. | planned: test_G1_8_look_lists_each_jobs_needs_against_the_computer |
| G1.9 | `look` reads GitHub's billing page for Actions, finds the sentence on self-hosted runners, and prints it with the date read; if the page cannot be read or the sentence is not found, it says that, never a guess (G7.1). | planned: test_G1_9_look_reads_the_current_cost_rule_with_its_date |
| G1.10 | The first thing `look` checks: the GitHub CLI is installed and logged in. If not: `need you: install the GitHub CLI` with the one install command for this OS, or `need you: log in to GitHub` with `gh auth login`, and nothing else runs. Logging in is the person's step (INV7). | planned: test_G1_10_no_github_cli_or_login_is_the_first_need_you |
| G1.11 | `look` checks that the person can become admin on this computer (to make the runner user and the boot service). If not: `need you: this needs an admin of this computer` and `stop`, before anything is touched. | planned: test_G1_11_no_admin_rights_on_the_computer_stops_before_anything |

### G2 Install

| Codon | Claim | Test |
|---|---|---|
| G2.1 | `setup` runs only after a `look` of the same repository on the same computer less than an hour old with no `stop` line. | planned: test_G2_1_setup_needs_a_fresh_clean_look |
| G2.2 | `setup` makes the runner user (no admin rights, not in any group that gives root, such as `docker` or `sudo`/`admin`/`wheel`), with a home the person's own files are not reachable from. Making a user needs the computer's admin password once: the person's step. | planned: test_G2_2_the_runner_user_has_no_admin_rights |
| G2.3 | `setup` downloads GitHub's runner release for this OS and architecture and checks it against the checksum GitHub publishes with that release; a mismatch stops everything. The version is the newest release on the day, written down. | planned: test_G2_3_the_runner_release_is_checked_before_use |
| G2.4 | Each runner is a fresh unpacked copy in its own folder, with its own work folder; never a copy of another runner's folder, never a shared cache. | planned: test_G2_4_each_runner_has_its_own_folders |
| G2.5 | The registration token is asked of GitHub through the API by the tool, passed to the runner's configuration on standard input, and never written to disk or shown (INV2). | planned: test_G2_5_the_registration_token_goes_by_stdin_only |
| G2.6 | Each runner is registered to exactly one repository (personal accounts) or to a runner group limited to the chosen private repositories (organisations); never to a whole organisation's every repository. | planned: test_G2_6_a_runner_serves_only_chosen_private_repositories |
| G2.7 | Each runner is registered with the runner's option for no default labels and carries only `minions` and one of `minions-small`, `minions-medium`, `minions-large` (from G1.6's survey), so heavy jobs can ask for a strong computer. | planned: test_G2_7_runners_carry_only_our_labels |
| G2.8 | The runner's credential files are readable by the runner user only (0600), read back after install. | planned: test_G2_8_credential_files_are_the_runner_users_only |
| G2.9 | The runner runs as a boot service that starts again by itself after a restart, ordered after the network and after our limits are in place. | planned: test_G2_9_the_runner_is_a_boot_service_started_after_its_limits |
| G2.10 | `setup` ends with both sides read: GitHub lists the runner online and idle, and the service is running. Until both are seen, the line is `waiting`, never `ok`. | planned: test_G2_10_setup_ends_with_both_sides_read |
| G2.11 | A job that asks for `self-hosted` alone, or for an OS or architecture label alone, never lands on our runner: it waits for another runner. Seen on a real runner (Part 7 C3) before any G5 code is written, because every hold rests on it. | planned: test_G2_11_a_job_asking_only_for_self_hosted_never_lands_on_ours |

### G3 Move the chores

| Codon | Claim | Test |
|---|---|---|
| G3.1 | `move` makes a branch and a pull request in the repository that changes `runs-on` of each job that can move to `[minions]` (plus a size label where the job needs one), never to `self-hosted`. The person merges it; the tool never merges. | planned: test_G3_1_move_opens_a_pull_request_and_never_merges |
| G3.2 | Jobs that no owned computer can take (another OS, a GPU, a tool G1.8 found missing) are left as they are and listed in the pull request's description with the reason. | planned: test_G3_2_jobs_that_cannot_move_are_left_and_named |
| G3.3 | A job that publishes or deploys (a release, a package upload, a deploy, anything on a tag push or holding a deploy secret) is never moved silently: it is left unchanged and named, and moving it needs the person's word for that job. | planned: test_G3_3_publishing_jobs_are_never_moved_silently |
| G3.4 | Each moved workflow gets `concurrency` with `cancel-in-progress` for pull requests, so a newer push replaces the older run instead of queueing behind it. Only where the workflow has none already. | planned: test_G3_4_moved_workflows_cancel_superseded_runs |
| G3.5 | `move` adds a small check the person can keep (`lint`): every `runs-on` in the repository asks for labels some registered runner carries, open pull requests' heads included; a job no runner can take is named before it waits, and so is a job that asks for `self-hosted` alone. | planned: test_G3_5_lint_names_jobs_no_runner_can_take |
| G3.6 | After the merge, `move --verify` sees one real job of each moved workflow finish on the minion and reports its result. "Moved" is said only after that. | planned: test_G3_6_moved_is_said_only_after_a_real_job_ran |

### G4 Keep it ready

| Codon | Claim | Test |
|---|---|---|
| G4.1 | One job at a time per runner. More parallel jobs means more runners, each with its own folders, only when G1.6's survey allows. | planned: test_G4_1_one_job_at_a_time_per_runner |
| G4.2 | The runner's service runs at low priority and inside a CPU and memory limit set from the survey (Linux: nice, CPU weight, CPU quota, memory high and max; macOS: what Part 7 C8 finds possible). | planned: test_G4_2_the_runner_runs_low_and_capped |
| G4.3 | A disk budget: work folders are cleaned after each job and old tool caches pruned on a schedule; below a free-space floor the runners are held (G5.5) before the disk fills. | planned: test_G4_3_the_disk_never_fills_from_jobs |
| G4.4 | The computer does not sleep while a job runs (Linux: an inhibitor while a job process exists; macOS: an assertion for the same time). It may sleep when idle; the skill tells the person that a sleeping minion takes no job. | planned: test_G4_4_no_sleep_during_a_job |
| G4.5 | The runner keeps GitHub's own self-update on (GitHub refuses runners that are too old); `doctor` reads and reports the runner version. | planned: test_G4_5_runner_self_update_stays_on_and_is_reported |
| G4.6 | A stop of the service waits long enough for a running job to finish or be cancelled cleanly (stop timeout at least as long as the runner's own), never the system's short default. | planned: test_G4_6_service_stop_waits_for_the_job |
| G4.7 | Every stop of a runner is SIGTERM to its listener (a runner started in the background ignores SIGINT), followed by a read-back that no process of that runner is left, before anything may start it again; two copies of one runner are never left running (Part 7 C7, seen). | planned: test_G4_7_a_stop_is_sigterm_and_read_back_never_two_copies |

### G5 Watch and hold

| Codon | Claim | Test |
|---|---|---|
| G5.1 | `status` prints one line per runner: GitHub's side (online/offline, busy/idle) and the minion's side (service running, a job process present), each with the time read; then the repository's queue: jobs waiting and the age of the oldest. | planned: test_G5_1_status_reads_both_sides_and_the_queue |
| G5.2 | "Idle" is said only when both sides agree: GitHub says not busy AND no job process runs on the minion. Otherwise the line says which side says what. | planned: test_G5_2_idle_only_when_both_sides_agree |
| G5.3 | `watch` runs on a schedule (default every 5 minutes) and writes one line per run to the journal; a heartbeat older than three intervals is reported by `status` as "the watch is not running". | planned: test_G5_3_the_watch_runs_and_its_silence_is_noticed |
| G5.4 | When a repository is no longer private, `watch`, within one interval, removes our labels from its runners, drains with a short limit (default 2 minutes), then stops their service and keeps it stopped, and `status` says why first. A label change alone is not the boundary here, because a fork's copy of a workflow can ask for any label. `resume` works only once the repository is private again and the person says so. | planned: test_G5_4_a_repository_no_longer_private_is_stopped_after_a_short_drain |
| G5.5 | Holds for low disk, high load or memory pressure (thresholds from the survey) and quiet hours the person sets are made by changing labels so no job asks for them; running jobs finish; the hold lifts by itself when the reason is gone, except G5.4. | planned: test_G5_5_holds_change_labels_and_never_stop_a_job |
| G5.6 | `drain` holds and then waits until both sides say idle (G5.2), with a time limit; it ends with "online and idle" or says what is still running. Every restart, update and removal goes through it (INV4). | planned: test_G5_6_drain_ends_with_online_and_idle_read |
| G5.7 | After a restart of the minion, `status` names any job that was running when it went down and was cancelled, and offers to run it again once. | planned: test_G5_7_a_job_cut_by_a_restart_is_named_and_offered_again |
| G5.8 | `status` names a runner idle for 14 days or more, and any runner on GitHub's side that no service on any minion of ours matches, and offers removal (G6). | planned: test_G5_8_idle_and_orphaned_runners_are_named |
| G5.9 | Opt-in, off by default: a fallback for when the minion is offline. Each choice says in plain words that it runs a small job on GitHub's machines, which counts against the included usage (INV3); the person picks it by name. Design in Part 11, Q3. | planned: test_G5_9_the_offline_fallback_is_opt_in_and_says_its_cost |
| G5.10 | `watch` re-reads GitHub's cost rule (G1.9) once a week and tells the person, at the next `status`, when the self-hosted sentence changed, with both versions and dates. | planned: test_G5_10_a_change_in_the_cost_rule_is_told |
| G5.11 | Before a planned restart (a system update, a reboot, a shutdown): `drain --restart` holds every runner on the computer, waits for the running job, and ends with "online and idle: safe to restart now" or names what still runs. The hold lifts by itself once the computer has restarted and both sides read the runner online. The skill tells the agent to run it before any restart it asks for, and tells the person to ask for it before theirs: a restart cancels the running job. | planned: test_G5_11_drain_before_a_planned_restart_lifts_after_the_boot |
| G5.12 | GitHub removes a runner it has not seen connect for more than 14 days (Part 7 C5). When `watch` or `status` reads that a runner of ours is gone on GitHub's side while its service is there (a laptop away for two weeks, a computer switched off), it holds the service from restarting in a loop, says so plainly, and `setup` again re-registers it with the person's login, keeping its folders and labels. | planned: test_G5_12_a_runner_github_removed_is_named_and_registered_again |
| G5.13 | A hold nobody notices looks like a broken project. When a job of the repository has waited longer than a threshold (default 30 minutes) while our runners are held, offline or asleep, `status` prints `warn` first: which job, how long, which hold, and that GitHub cancels it after 24 hours. | planned: test_G5_13_a_job_waiting_behind_our_hold_is_warned |

### G6 Remove

| Codon | Claim | Test |
|---|---|---|
| G6.1 | `remove <owner/repo>` drains, unregisters the runner on GitHub's side (removal token through standard input), stops and deletes the service, deletes the runner's folders, and records each step in the journal first. | planned: test_G6_1_remove_takes_a_runner_away_on_both_sides |
| G6.2 | `remove --all` also deletes the runner user, the watch, and `~/.minions/`, and opens a pull request that puts each moved `runs-on` back as it was before `move`. | planned: test_G6_2_remove_all_leaves_nothing_and_offers_the_workflows_back |
| G6.3 | `remove` ends with a census (INV6): runners on GitHub for the repository, services, folders, user, scheduled checks; each "none" or named. | planned: test_G6_3_remove_ends_with_a_census |
| G6.4 | A removal interrupted part-way is finished by running `remove` again; it never fails on a step already done. | planned: test_G6_4_remove_again_finishes_an_interrupted_removal |

### G7 Words to the person

| Codon | Claim | Test |
|---|---|---|
| G7.1 | The cost sentence is the one read by G1.9 with its date, or "I could not read GitHub's page today". Never "free", "no bill", "saves" or an amount the tool did not read. | planned: test_G7_1_cost_words_are_read_and_dated_never_promised |
| G7.2 | The skill tells the person that artifact and cache storage count the same whichever computer ran the job. | planned: test_G7_2_storage_is_said_to_count_the_same |
| G7.3 | The skill tells the person, before `setup`, what a job on their computer can reach (G1.5, G1.7) and that anyone who can push to the repository can run code there. | planned: test_G7_3_what_a_job_can_reach_is_said_before_setup |
| G7.4 | The skill tells the person that a restart or sleep of the minion cancels or delays the job running on it, and that a job waits at most 24 hours for a runner before GitHub cancels it. | planned: test_G7_4_restart_and_waiting_limits_are_said |
| G7.5 | The skill never says required checks or branch protection hold merges unless the tool read that the repository has them. | planned: test_G7_5_merge_rules_are_said_only_when_read |
| G7.6 | Version 0.1.0 says, in the skill and at `setup`, that it serves one repository per computer and that more come in the next version (G12). | planned: test_G7_6_version_0_1_says_one_repository_and_more_next |
| G7.7 | Before `setup` the person hears: "While your computer is held, asleep or off, your checks wait. GitHub cancels a check that waited 24 hours." | planned: test_G7_7_waiting_checks_are_said_before_setup |

### G8 The skill and its commands

| Codon | Claim | Test |
|---|---|---|
| G8.1 | `skills/repo-minions/SKILL.md` follows the Agent Skills format and passes `gh skill publish --dry-run`. | planned: test_G8_1_the_skill_file_passes_the_validator |
| G8.2 | The skill's order is fixed: look, tell, setup, move, verify, status. It names each step that is the person's (INV7) and what to say there. | planned: test_G8_2_the_skill_names_the_order_and_the_persons_steps |
| G8.3 | The tool's commands are exactly: `look`, `setup`, `move`, `lint`, `status`, `watch`, `hold`, `resume`, `drain`, `doctor`, `remove`, `self-update`. Each prints `ok`, `warn`, `stop` or `need you` lines and exits non-zero on `stop`. | planned: test_G8_3_commands_and_line_kinds_are_exactly_these |
| G8.4 | `doctor` checks every promise of G2 and G4 on the computer as it is now and finishes or undoes a half-done step (INV8). | planned: test_G8_4_doctor_checks_install_and_finishes_half_done_steps |

### G9 Tests, CI and the promise gate

| Codon | Claim | Test |
|---|---|---|
| G9.1 | This repository's own CI runs on GitHub's standard runners only. | `test_no_workflow_of_this_public_repository_asks_for_a_self_hosted_runner` |
| G9.2 | The plan, `PROMISES.json` and the tests agree; a version is refused while a promise due in it has no test, and the version after it until the promise was seen on a real machine. | `test_PROMISES_no_version_ships_while_a_promise_due_in_it_is_not_built` |
| G9.3 | Tests that need GitHub use a stand-in of its API built from recorded real answers (in `tests/fixtures/`, recorded per Part 7); a row whose test leans on the stand-in is proven on a real machine and a real private repository before the tag. | planned: test_G9_3_the_github_stand_in_answers_as_recorded |

### G10 Self-update

| Codon | Claim | Test |
|---|---|---|
| G10.1 | `self-update` installs the newest released version of the skill and tool, only while no step of the tool is running (the runner itself is updated by GitHub, G4.5), keeps the previous version beside it, and goes back to it if `doctor` fails. | planned: test_G10_1_self_update_steps_back_on_a_failed_doctor |

### G12 Many repositories on one computer

A personal account allows one runner per repository, and the person this skill is for often has many private
repositories. So one computer carrying many runners is the main case, not an edge.

| Codon | Claim | Test |
|---|---|---|
| G12.1 | One overall limit across all our runners on a computer: at most K jobs at once, K from the survey (G1.6). Linux: all our runner services in one slice with the CPU and memory limits of G4.2 applied to the slice as a whole; when K runners are busy, the idle ones are held (G5.5) until one is free. | planned: test_G12_1_at_most_k_jobs_at_once_across_all_our_runners |
| G12.2 | How many idle runners a computer can carry is computed from one idle runner's memory as measured on that OS (Part 7 C7); `setup` refuses a runner past that number and says how many there are and why. | planned: test_G12_2_idle_runners_are_counted_against_measured_memory |
| G12.3 | `setup` takes a list of repositories; `look` runs for each; the admin step is asked once for the whole list; each repository gets its own runner, folders and registration. | planned: test_G12_3_setup_for_a_list_asks_the_admin_step_once |
| G12.4 | `status` sums up first: runners, busy, held and why, jobs waiting and the oldest, then one line per repository. | planned: test_G12_4_status_sums_up_before_the_lines |

### G11 Laptops

The person's own computer is often a laptop: it sleeps, its lid closes, it runs on battery, it changes networks.
These are ordinary cases, not faults. Where the OS lets a sleep or lid close happen anyway, the skill says so
instead of pretending to hold it.

| Codon | Claim | Test |
|---|---|---|
| G11.1 | `look` says the computer is a laptop when it has a battery or a lid, and the skill tells the person before `setup`: a laptop takes jobs only while it is awake, on the network and (by default) on mains power. | planned: test_G11_1_a_laptop_is_named_and_its_limits_said_before_setup |
| G11.2 | On battery the runners are held (G5.5), by default; the hold lifts on mains power. The person can switch this off by name. | planned: test_G11_2_on_battery_the_runners_are_held |
| G11.3 | A sleep asked while a job runs is put off for as long as the OS allows (Linux: a delay lock; macOS: what Part 7 C12 finds), and the skill names what cannot be put off (for example a lid closed on battery). | planned: test_G11_3_sleep_is_put_off_during_a_job_where_the_os_allows |
| G11.4 | After a wake or a network change, `watch` reads both sides within one interval; a runner the service shows running but GitHub shows offline for more than two intervals is restarted, only when no job process runs. | planned: test_G11_4_after_a_wake_or_network_change_the_runner_is_seen_back |
| G11.5 | After a wake, `status` names a job that was cut by the sleep and offers to run it again once (as G5.7). | planned: test_G11_5_a_job_cut_by_sleep_is_named_and_offered_again |

---

## Part 6. Repository layout

```
DNA-STRAND-MASTER-PLAN.md   this plan
PROMISES.json               lessons as gates (G9.2)
README.md  CHANGELOG.md  LICENSE  NOTICE  TODO.md
skills/repo-minions/SKILL.md
scripts/minions.py          the tool
docs/CONTRACTS.md           Part 7, as seen
tests/test_strand.py        plan and promise gate
tests/test_minions.py       one test per codon
tests/fixtures/             recorded GitHub answers, no secrets
.claude-plugin/plugin.json  .claude-plugin/marketplace.json
.github/workflows/tests.yml GitHub's standard runners only
```

---

## Part 7. Platform contracts to check before coding

Each is checked on a real machine and a real private repository, written in `docs/CONTRACTS.md` with date,
version and machine kind, before the code that leans on it. None is assumed.

| ID | Contract to check | Leans on it |
|---|---|---|
| C1 | The runner registration and removal token endpoints for a repository and for an organisation; who may call them; how long a token lives. | G2.5, G6.1 |
| C2 | The runners list endpoint: its `status` and `busy` fields, how soon they change after a job starts and ends, and after the service stops. | G2.10, G5.1, G5.2 |
| C3 | On a real runner registered with no default labels: a job that asks for `self-hosted` alone waits and does not land on it; a job that asks for our labels does. Then: changing its labels through the API while it runs, whether it keeps them across its own restart, and whether a waiting job that asks for a removed label stays queued (the hold). The first part is seen before any G5 code. | G2.7, G2.11, G5.5 |
| C4 | What a run looks like when GitHub does not start it: `startup_failure`, zero jobs, its fields; reproduced on a repository where jobs are not allowed to run. | G1.4 |
| C5 | GitHub's rules for runners that are offline for long (removal after a time) and that are too old (forced update), as written in its docs on the day. | G4.5, G5.8 |
| C6 | The release assets of GitHub's runner and where their checksums are published. | G2.3 |
| C7 | Linux: the unit the runner's own service script writes, its stop behaviour while a job runs, a drop-in for slice, CPU and memory limits, and the memory one idle runner uses (for G12.2). | G2.9, G4.2, G4.6, G12.1, G12.2 |
| C8 | macOS: whether a LaunchDaemon can run the runner as a user with no login session; what the runner needs from a GUI session; how to keep the machine awake during a job; what limits macOS allows. | G2.9, G4.2, G4.4 |
| C9 | Starting a test run without touching the default branch: a push of a one-file orphan commit to a temporary branch, then cancel, delete the run, delete the branch. (Workflow-dispatch needs the file on the default branch, so G1.4 does not use it.) | G1.4 |
| C10 | Organisation runner groups: limiting a group to selected private repositories, and that groups refuse public repositories by default. | G2.6 |
| C11 | GitHub's billing page: where the self-hosted sentence is and how it is worded on the day. | G1.9, G5.10 |
| C12 | Laptops: how long Linux's delay lock can put off a sleep; what a macOS power assertion holds and what it does not (lid closed on battery); how to read battery, mains power and lid state on each OS without admin rights. | G11.1 to G11.3 |

Seen once so far (2026-11-22, a private repository of a free GitHub organisation, no runner registered): a job
that asks for a label no runner carries is created and waits (`queued`, 1 job); cancelled, the run's timing showed
nothing billable. Then (18:10Z) with a push workflow of the "person's" on the default branch: a one-file orphan
commit pushed to a temporary branch started only its own workflow, one job, queued; cancelled, the run deleted,
the branch deleted, nothing billable and nothing left. That is G1.4's start test, written in `docs/CONTRACTS.md`.

---

## Part 8. Milestones and acceptance

| Version | What | Accepted when |
|---|---|---|
| 0.1.0 | Linux, one private repository per computer, personal account: G1, G2, G3.1 to G3.3, G3.6, G4.1 to G4.7, G5.1 to G5.4, G5.6, G5.11, G5.12, G5.13, G6, G7, G8, G9, G11 on Linux, all INV. | No G5 code before C3's first part is seen. Every row due in 0.1.0 has its test; Part 9's proofs P1 to P6 and P8 seen on a clean Linux account, P9 and P10 on a Linux laptop. |
| 0.2.0 | macOS (C8 and C12 first), organisations and runner groups (G1.2, G2.6), G3.4, G3.5, G5.5, G5.7, G5.8, G5.10, G11 on macOS, G12 (many repositories on one computer). | Rows due in 0.2.0 tested; P1 to P6 and P8 again on a clean Mac account, P9 and P10 on a Mac laptop; 0.1.0's rows proven. |
| 0.3.0 | The opt-in offline fallback (G5.9), self-update (G10), Windows if Part 11 Q4 says yes. | As above, on each OS it claims. |

---

## Part 9. How we know it works

Tests prove the code does what the codon says against stand-ins. A stand-in's idea of how GitHub or the runner
behaves is not proof: these are seen on real machines that have none of our set-up, with a throwaway private
repository made for the proof and deleted after, before the tag.

- **P1 The cold start.** A fresh agent in a clean account, given only this repository's address and one sentence
  ("go to GitHub and set up Windstorm Repo Minions for <repo>"), gets a moved job green on the minion. The person's
  steps were only theirs (INV7); count them and name them in the proof.
- **P2 Offline.** The runner service stopped: a push queues; `status` says "offline" from both sides and the oldest
  wait; started again, the job runs.
- **P3 Restart in the middle of a job.** The minion restarted while a job runs: the runner comes back by itself,
  `status` names the cancelled job (G5.7), the next push runs.
- **P4 Disk low.** Free space pushed under the floor with a filler file: runners held, no job started, the hold
  lifts when the filler goes.
- **P5 Two jobs at once.** Two pushes: one runs, one waits, no shared folder touched by both.
- **P6 Remove.** `remove --all`: the census is empty on GitHub and the computer, compared with a census taken
  before `setup`.
- **P7 Turned public.** This proof breaks the first rule for a few minutes, so it runs only under all of these
  guards: a repository made for it, empty of code and secrets; fork pull requests needing approval for all outside
  contributors; the watch interval shortened for the test; the runner on a clean test account that holds nothing
  else of worth; public for under two minutes, then private again and deleted right after; G5.4 (stop after a
  short drain) in force. It passes when the runner is stopped within the shortened interval and `status` says why.
- **P8 A planned restart.** `drain --restart` while a job runs: no new job starts, the running one finishes,
  "safe to restart now" is said only then; after the restart the hold lifts by itself and the next push runs.
- **P9 Sleep and lid** (a laptop): a sleep asked during a job is put off as far as the OS allows; a sleep forced
  anyway is followed, after the wake, by the runner back on both sides and the cut job named.
- **P10 Battery and network** (a laptop): unplugged, the runners are held; plugged in, they take jobs again; after a
  change of network the runner is seen back within two watch intervals.

---

## Part 10. Decisions already made, and why

1. **GitHub's own runner, not a re-implementation and not a container image of someone else's.** It is the program
   GitHub supports and updates; we wrap it and keep it safe.
2. **One runner per repository by default.** A personal account allows nothing else; for organisations it keeps a
   repository's jobs away from another's secrets.
3. **A runner user with no admin rights and no container access by default.** Access to the container service is
   admin access by another name. Jobs that need containers are named by G1.8 and need the person's word (Part 11 Q2).
4. **Hold by label, never by stopping a job.** A stop that kills is itself an incident. One exception, written
   here on purpose: a repository that is no longer private is stopped after a short drain (G5.4), because a label
   is not a boundary against a fork's copy of a workflow.
5. **The tool reads GitHub through the person's GitHub CLI login** and never stores a GitHub token of its own.
6. **Never on a public repository**, checked at the tool, at setup and on a schedule, because GitHub itself
   recommends self-hosted runners for private repositories only and visibility can change after setup.
7. **No cost promise.** The cost sentence is read from GitHub on the day and dated (G1.9, G7.1).

---

## Part 11. Open questions

- **Q1 The name's short line. Settled 2026-11-22:** "your own computers do your repo's chores". The earlier
  second half (that GitHub's meter stops) is gone: storage still counts, and GitHub has announced a charge it may
  bring back.
- **Q2 Jobs that need containers.** Options: rootless containers for the runner user; a virtual machine per job;
  refuse. Decide before 0.2.0 with what C7 and C8 show.
- **Q3 The offline fallback.** Known patterns: a first job on GitHub's machines that asks whether a runner with our
  label is online and picks `runs-on` from the answer (needs a token that can list runners, stored as a secret: weigh
  that against G1.5); or a manual input to choose. Both cost a little hosted time. Decide before 0.3.0.
- **Q4 Windows.** In or out of 0.3.0.
- **Q5 Ephemeral runners** (a fresh runner for each job) for a cleaner start each time: worth the extra start time
  and registration calls? Measure on a real machine before deciding.

---

## Part 12. What already exists, and what this adds

Looked at on 2026-11-22. GitHub's own guide gives the commands. Actions Runner Controller runs runners on
Kubernetes. Several companies run runners on their own or cloud machines. `act` runs workflows locally without
registering a runner. Agent skills found on public skill registries (read from their listing pages; their full
files were not all read):

| Skill (author) | What its page says it does |
|---|---|
| github-runner (alisonaquinas, llm-ci-dev) | Documents downloading, registering and running the runner as a service on Linux, macOS and Windows, and Actions Runner Controller; the person makes the registration token by hand. |
| github-runner-setup (stars-end) | Runner set-up and upkeep with a dedicated runner user and systemd services. |
| setup-org-runners (jleechanorg, MIT) | Organisation-wide runners on a fresh Mac in Docker containers, with launchd services. |
| self-hosted-runner-preflight (jleechanorg, MIT) | A checklist for runner failures (low disk, missing runner) in its author's own fleet, reading the host's containers and not only GitHub's API. |
| manage-gh-runners (radixark, Apache-2.0) | Adds, removes, lists or swaps runners on its author's CI hosts that use one particular container image. |

None of those pages states a rule against public repositories. None says it checks, before installing, that
GitHub starts jobs for the repository, moves the workflows by pull request and names the jobs that must not move,
holds runners without killing a job (before a restart, on battery, when a repository turns public), or shows that
a removal left nothing. That is what Repo Minions adds; the plan above is how.

---

## Appendix A. A worked example

The person: "Go to GitHub and set up Windstorm Repo Minions for my-org/my-app."
1. The agent installs the skill, runs `look my-org/my-app`: `ok private`, `ok organisation: a runner group for
   chosen private repositories`, `ok jobs start (last run 2 hours ago, 4 jobs)`, `warn 2 secrets a job can read:
   DEPLOY_KEY, NPM_TOKEN`, `ok Linux x64, 8 cores, 16 GB, NVMe, 120 GB free`, `warn 1 job needs macOS: build-ios`,
   `warn release.yml publishes on tag push`, and the cost sentence with today's date.
2. The agent tells the person, in plain words, what a job on this computer can reach, which jobs stay on GitHub,
   and that the release job is left alone. The person says go.
3. `setup my-org/my-app`: `need you: type this computer's admin password once`; then `ok runner checked`,
   `ok registered`, `ok service running`, `ok both sides: online and idle`.
4. `move my-org/my-app`: `ok pull request #12: 3 jobs moved, build-ios and release left as they were`. The person
   merges. `move --verify`: `ok test (ubuntu) ran on the minion: success in 3 min 10 s`.

---

## Appendix B. Changing this plan

1. A change to the plan is its own pull request titled `Plan: <what changes>`, touching only this file,
   `PROMISES.json` and `CHANGELOG.md`.
2. Its description says which codons change and why.
3. Raise the plan version: `1.x` for additions and clarifications; `2.0` for a change to an invariant.
4. Never renumber codons. A removed codon stays as `**G5.9 Removed in plan 1.3:** <reason>.`; new codons take the
   next free number in their gene.
5. Code that follows a plan change is a separate pull request.

---

*Windstorm Repo Minions and The Windstorm are names of The Windstorm. This project is not affiliated with, endorsed
by or sponsored by GitHub. GitHub is a trademark of GitHub, Inc.*
