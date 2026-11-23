# Windstorm Repo Minions · DNA Strand Master Plan

**Plan version 2.0 · 2026-11-22 · The Windstorm**

Every date in this plan and in PROMISES.json is the calendar day in New York (Eastern time), so one night's
findings carry one date.

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
  G6 Uninstall · G7 Words to the person · G8 The skill and its commands · G9 Tests, CI and the promise gate ·
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
6. **Uninstall** (G6): one command takes it all away, on GitHub and on the computer, and shows that nothing is left.

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
 merges           status / hold / uninstall ─────► both sides read, holds by label ─────────► jobs, queue
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
| **Hold** | The state in which a runner takes no new job. A running job finishes. Made by removing our labels, never by stopping a job. A repository that is no longer private goes further: its runner is taken off it on GitHub's side once no job runs on it (G5.4). |
| **Drain** | Hold, then wait until no job is running. |
| **Both sides** | GitHub's view of a runner (online, busy) and the minion's view (service running, a job process present). |
| **Start test** | One job sent to the repository, on a temporary branch, that runs on no machine, to see whether GitHub starts jobs for it at all; it leaves nothing behind (G1.4). |

---

## Part 3. Invariants that are never broken

| Codon | Claim | Test |
|---|---|---|
| INV1 | No step runs here of a job whose event came after its repository stopped being private, with one exception: a scheduled run, whose record names no repository, in the minutes before the watch reads the change (at most one interval while the watch can reach GitHub, at most three while it cannot); that is the repository's own workflow on its own branch, never a stranger's. Our runner is taken off a public repository as soon as the watch can reach GitHub and no job runs on it; until then it stays and refuses every job but that exception, as follows. `setup` refuses a repository that is not private. On the minion, the job-start hook refuses every job before its first step from the moment GitHub's own record of the job's event says the repository is not private, the watch reads it public, or the watch has not read it private for three passes (G5.15). When the watch reads a repository public and can reach GitHub, it removes our labels and takes our runner off the repository on GitHub's side, with the person's own rights, as soon as no job runs on it; each pass tries again until it is done, and `status` says it first (G5.4). A job that began before the turn finishes (INV4). The watch needs no admin rights and kills no job. The guard lives in the tool, never only in a workflow file, because a fork's pull request runs its own copy of the workflow. (Until plan 2.0 this said the watch stops the runner's service; a real machine showed it cannot.) | `test_INV1_never_a_runner_on_a_public_repository` |
| INV2 | No secret on a command line, in a log, on the screen or in a file another user can read. Registration and removal tokens are read from standard input or a 0600 file, used, and dropped. | planned: test_INV2_no_secret_on_argv_or_in_output |
| INV3 | Nothing that costs money: the tool never changes a plan, a spending limit, a payment method or a billed setting, and never adds a GitHub-hosted job without the person choosing it by name (G5.9). | planned: test_INV3_nothing_that_costs_money |
| INV4 | Never kill a running job. Every stop, restart, update and removal drains first and waits (G5.6). One exception, said before it happens: the person's own explicit `uninstall --now`. (Until plan 2.0 a repository no longer private was a second exception, its service stopped after a short drain; now the guard refuses every later job and the runner is taken off the repository on GitHub's side once idle, so a job already running finishes, G5.4.) | `test_INV4_never_kill_a_running_job` |
| INV5 | Say only what was seen. Every status line names the side it came from and the time it was read. "Online" is never written as "working"; a cost sentence always carries the date its source was read. | planned: test_INV5_say_only_what_was_seen |
| INV6 | Removal leaves nothing: after `uninstall`, a census of runners on GitHub, services, folders, the runner user, labels in workflows and scheduled checks shows none of ours (G6). | planned: test_INV6_remove_leaves_nothing |
| INV7 | The person's steps stay theirs: logging in to GitHub, typing their computer's admin password once, approving access, merging the pull request. The agent never presses Agree or Accept on a terms or consent screen. | planned: test_INV7_the_persons_steps_stay_theirs |
| INV8 | A step that is interrupted leaves the machine as it was or as it would be after the step: every step writes what it is about to do first, and `doctor` finishes or undoes a half-done step. | planned: test_INV8_an_interrupted_step_is_finished_or_undone |

---

## Part 4. Architecture

- **One skill, one tool.** `skills/repo-minions/SKILL.md` tells the agent what to run, in what order, what to say,
  and which steps are the person's. `scripts/minions.py` does the work and prints short, stable lines
  (`ok ...`, `warn ...`, `stop ...`, `need you ...`, `waiting ...`, `tell the person ...`) that the agent reads and explains; the exit codes
  are G8.3's table.
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
| G1.1 | `look <owner/repo>` reads the repository's visibility and stops with a plain reason if it is not private (INV1). | `test_G1_1_look_stops_on_a_repository_that_is_not_private` |
| G1.2 | `look` reads whether the owner is a personal account or an organisation and says what follows: a personal account can only have runners per repository; an organisation can use a runner group limited to chosen private repositories. | `test_G1_2_look_names_account_kind_and_runner_scope` |
| G1.3 | `look` checks that the person's GitHub CLI login has what the next steps need (admin on the repository to register a runner; `workflow` scope to change workflow files) and names any that is missing, with the one command the person runs to add it. | `test_G1_3_look_names_missing_token_rights` |
| G1.4 | `look` runs one start test, only with the person's yes to that one change: it pushes a temporary branch `minions-start-test-<random>` whose only commit has no parent and holds one file, `.github/workflows/minions-start-test.yml`, whose one job asks for a label no runner carries. Because the commit holds no other file, none of the person's own workflows runs on it; the default branch is never touched, so a protected default branch does not matter. GitHub creates the job and it waits on no machine; `look` reads that one job exists, cancels the run, deletes the run, deletes the branch, and reads back that the branch and the run are gone (Part 7 C9). A run that ends `startup_failure` with zero jobs is reported as "GitHub did not start the run; this is the account's or repository's setting or billing, not your computer", and `setup` stops there: a runner cannot fix it, and the skill is never offered as a way around it. If the branch cannot be pushed (a rule on branch creation, no `workflow` scope), `look` says which and reads the repository's recent runs instead; with none, it says "could not test whether GitHub starts jobs here" and `setup` asks the person to decide. | `test_G1_4_the_start_test_leaves_nothing_and_names_a_zero_job_startup_failure` |
| G1.5 | `look` counts the Actions secrets a job of this repository can read (repository, environment and, where readable, organisation) and names them, so the person knows what a job on their computer can reach. Names only, never values. | `test_G1_5_look_names_the_secrets_a_job_could_read` |
| G1.6 | `look` surveys the computer: OS and version, architecture, cores, memory, swap in use, disk kind where it can be read, free space on the disk that will hold the work folders, whether it runs on battery or Wi-Fi only, its sleep setting. | `test_G1_6_look_surveys_the_computer` |
| G1.7 | `look` lists what already lives on the computer that a job could disturb or be disturbed by: running services that listen on ports, container projects, other runners. A computer that serves something live is named as such, and `setup` asks the person to choose another computer or say yes in words. | `test_G1_7_look_names_what_already_lives_on_the_computer` |
| G1.8 | `look` reads every workflow of the repository and lists, per job: its `runs-on`, the tools it asks for (setup actions, `sudo`, containers, browsers), and whether this computer has them. | `test_G1_8_look_lists_each_jobs_needs_against_the_computer` |
| G1.9 | `look` reads GitHub's billing page for Actions, finds the sentence on self-hosted runners, and prints it with the date read; if the page cannot be read or the sentence is not found, it says that, never a guess (G7.1). | `test_G1_9_look_reads_the_current_cost_rule_with_its_date` |
| G1.10 | The first thing `look` checks: the GitHub CLI is installed and logged in. If not: `need you: install the GitHub CLI` with the one install command for this OS, the one that works on a fresh install of it (Debian and Ubuntu: `sudo apt update && sudo apt install gh`, because a fresh cloud image has no package lists yet and `apt install gh` alone fails there: seen on stock Ubuntu 24.04, 2026-11-22, P1a; the lines for other systems are not seen yet), or `need you: log in to GitHub` with `gh auth login`, and nothing else runs. Logging in is the person's step (INV7). | `test_G1_10_no_github_cli_or_login_is_the_first_need_you` |
| G1.11 | `look` checks that the person can become admin on this computer (to make the runner user and the boot service). If not: `need you: this needs an admin of this computer` and `stop`, before anything is touched. | `test_G1_11_no_admin_rights_on_the_computer_stops_before_anything` |
| G1.12 | On a computer other people also use, what is theirs is counted, never named: containers of a shared container service and runner services that do not run as the person are given as numbers only; only the person's own (rootless containers, services running as them) are named. Port numbers are given; they say nothing of whose they are. | `test_G1_12_other_peoples_things_are_counted_never_named` |
| G1.13 | `look` names a disk kind only when the system says it for real; a virtual disk (which Linux reports as rotating) is named "disk kind not known (virtual)", never "spinning disk". Seen on a stock Ubuntu 24.04 virtual machine, 2026-11-22 (Part 7 C7). | `test_G1_13_a_virtual_disk_is_not_called_spinning` |
| G1.14 | `look` never offers what this version's `setup` does not do: in 0.1.0 an organisation's repository is told it gets one runner for this one repository (G7.6), not a runner group. Seen on a stock Ubuntu 24.04 virtual machine, 2026-11-22 (Part 7 C7). | `test_G1_14_look_offers_only_what_this_setup_does` |
| G1.15 | `look` and `move` judge a job by the same rules and in the same words: a publishing or deploying job (G3.3) is named as such by `look` too, never "can move here"; a job asking for `self-hosted` alone or another runner's custom label is "asks for a runner that is not ours", not "cannot run on this computer". Seen on a stock Ubuntu 24.04 virtual machine, 2026-11-22 (G3's run). | `test_G1_15_look_and_move_judge_jobs_alike` |

### G2 Install

| Codon | Claim | Test |
|---|---|---|
| G2.1 | `setup` runs only after a `look` of the same repository on the same computer less than an hour old with no `stop` line. | `test_G2_1_setup_needs_a_fresh_clean_look` |
| G2.2 | `setup` makes the runner user (no admin rights, not in any group that gives root, such as `docker` or `sudo`/`admin`/`wheel`), with a home the person's own files are not reachable from. Making a user needs the computer's admin password once: the person's step. | `test_G2_2_the_runner_user_has_no_admin_rights` |
| G2.3 | `setup` downloads GitHub's runner release for this OS and architecture and checks it against the checksum GitHub publishes with that release; a mismatch stops everything. The version is the newest release on the day, written down. | `test_G2_3_the_runner_release_is_checked_before_use` |
| G2.4 | Each runner is a fresh unpacked copy in its own folder, with its own work folder; never a copy of another runner's folder, never a shared cache. | `test_G2_4_each_runner_has_its_own_folders` |
| G2.5 | The registration token is asked of GitHub through the API by the tool, passed to the runner's configuration on standard input, and never written to disk or shown (INV2). | `test_G2_5_the_registration_token_goes_by_stdin_only` |
| G2.6 | Each runner is registered to exactly one repository (personal accounts) or to a runner group limited to the chosen private repositories (organisations); never to a whole organisation's every repository. | `test_G2_6_a_runner_serves_only_chosen_private_repositories` |
| G2.7 | Each runner is registered with the runner's option for no default labels and carries only `minions` and one of `minions-small`, `minions-medium`, `minions-large` (from G1.6's survey), so heavy jobs can ask for a strong computer. | `test_G2_7_runners_carry_only_our_labels` |
| G2.8 | The runner's credential files are readable by the runner user only (0600), read back after install. | `test_G2_8_credential_files_are_the_runner_users_only` |
| G2.9 | The runner runs as a boot service that starts again by itself after a restart, ordered after the network and after our limits are in place. The unit the runner's own script writes has `Restart=no` and orders after the network without asking for it (seen, Part 7 C7), so our drop-in adds `Wants=network-online.target`, `Restart=on-failure` and our slice. | `test_G2_9_the_runner_is_a_boot_service_started_after_its_limits` |
| G2.10 | `setup` ends with both sides read: GitHub lists the runner online and idle, and the service is running. Until both are seen, the line is `waiting`, never `ok`. | `test_G2_10_setup_ends_with_both_sides_read` |
| G2.11 | A job that asks for `self-hosted` alone, or for an OS or architecture label alone, never lands on our runner: it waits for another runner. Seen on a real runner (Part 7 C3) before any G5 code is written, because every hold rests on it. | `test_G2_11_a_job_asking_only_for_self_hosted_never_lands_on_ours` |
| G2.12 | The admin password is asked once and every admin step runs from that same terminal; sudo's permission belongs to one terminal, and a second one is asked again. A `setup` cut part-way says to run it again from the same terminal. Seen on a stock Ubuntu 24.04 virtual machine, 2026-11-22 (Part 7 C7). | planned: test_G2_12_admin_steps_stay_on_the_terminal_that_was_asked |

### G3 Move the chores

| Codon | Claim | Test |
|---|---|---|
| G3.1 | `move` makes a branch and a pull request in the repository that changes `runs-on` of each job that can move to `[minions]` (plus a size label where the job needs one), never to `self-hosted`. The person merges it; the tool never merges. | `test_G3_1_move_opens_a_pull_request_and_never_merges` |
| G3.2 | Jobs that no owned computer can take (another OS, a GPU, a tool G1.8 found missing) are left as they are and listed in the pull request's description with the reason. | `test_G3_2_jobs_that_cannot_move_are_left_and_named` |
| G3.3 | A job that publishes or deploys (a release, a package upload, a deploy, anything on a tag push or holding a deploy secret) is never moved silently: it is left unchanged and named, and moving it needs the person's word for that job. | `test_G3_3_publishing_jobs_are_never_moved_silently` |
| G3.4 | Each moved workflow gets `concurrency` with `cancel-in-progress` for pull requests, so a newer push replaces the older run instead of queueing behind it. Only where the workflow has none already. | planned: test_G3_4_moved_workflows_cancel_superseded_runs |
| G3.5 | `move` adds a small check the person can keep (`lint`): every `runs-on` in the repository asks for labels some registered runner carries, open pull requests' heads included; a job no runner can take is named before it waits, and so is a job that asks for `self-hosted` alone. | planned: test_G3_5_lint_names_jobs_no_runner_can_take |
| G3.6 | After the merge, `move --verify` sees one real job of each moved workflow finish on the minion and reports its result. "Moved" is said only after that. | `test_G3_6_moved_is_said_only_after_a_real_job_ran` |

### G4 Keep it ready

| Codon | Claim | Test |
|---|---|---|
| G4.1 | One job at a time per runner. More parallel jobs means more runners, each with its own folders, only when G1.6's survey allows. | `test_G4_1_one_job_at_a_time_per_runner` |
| G4.2 | The runner's service runs at low priority and inside a CPU and memory limit set from the survey (Linux: nice, CPU weight, CPU quota, memory high and max; macOS: what Part 7 C8 finds possible). Until measured values exist, the limits are fixed defaults: half the processors (never less than one), memory high at 50 % and memory max at 60 % of the computer's memory. This row is not built while the limits are not set from the survey. | planned: test_G4_2_the_runner_runs_low_and_capped |
| G4.3 | A disk budget: work folders are cleaned after each job, with the job's temporary folder and the actions it fetched (GitHub's runner downloads a job's actions before any hook runs, so even a job the start hook refuses leaves them; seen 2026-11-22), and old tool caches pruned on a schedule; below a free-space floor (5 GB for the work folders) the watch holds our runner before the disk fills, and lifts that hold by itself at the floor plus 1 GB; a runner held for another reason is left as it is (P4 found no hold before this, 2026-11-23). | `test_G4_3_the_disk_never_fills_from_jobs`, `test_G4_3_below_the_floor_the_watch_holds_and_lifts_by_itself` |
| G4.4 | The computer does not fall asleep by itself (an idle sleep) while a job runs (Linux: an idle inhibitor while a job process exists, G4.9; macOS: an assertion for the same time). It may sleep when idle; the skill tells the person that a sleeping minion takes no job. | `test_G4_4_no_sleep_during_a_job` |
| G4.5 | The runner keeps GitHub's own self-update on (GitHub refuses runners that are too old); `doctor` reads and reports the runner version. | `test_G4_5_runner_self_update_stays_on_and_is_reported` |
| G4.6 | A stop of the runner's service cancels its running job within seconds, whatever the stop timeout (seen, Part 7 C7). So nothing ever stops, restarts or updates the service, or restarts the computer on the skill's say-so, while a job runs: the drain (G5.6) comes first, every time, and the stop only after both sides read idle. | `test_G4_6_the_service_is_stopped_only_after_a_drain` |
| G4.7 | Every stop of a runner is SIGTERM to its listener (a runner started in the background ignores SIGINT), followed by a read-back that no process of that runner is left, before anything may start it again; two copies of one runner are never left running (Part 7 C7, seen). | `test_G4_7_a_stop_is_sigterm_and_read_back_never_two_copies` |
| G4.8 | The limits have floors: never less than one processor; and when the memory max (the hard limit, where the kernel kills a job; not the high mark) would be under 2 GiB, `look` says before `setup` that a job will likely be killed by the limit, and `setup` stops instead of installing. The stop line names the way out, `setup --limits <processors>,<memory %>` with a share that reaches 2 GiB, and what it costs: while a job runs, that much less memory is left for the person's own work, so the computer may slow down or other programs may be closed by the system. Why 2 GiB: one idle runner takes about 145 MB (Part 7 C7), but a job's build, tests and tools take far more; the number is checked when G4.2 is measured. | `test_G4_8_limits_have_floors_and_too_little_is_said_before_setup` |
| G4.9 | The skill keeps the computer from falling asleep by itself while a job runs, and never takes away a sleep the person or the hardware asks for beyond G11.3's short delay: never the lid switch, a low-battery sleep, or a sleep the person chose. A laptop closed into a bag with a job running can sleep; a job cut that way is named as cut (G5.7). Linux takes exactly two locks while a job runs: `idle` in block mode (stops only the idle sleep) and `sleep` in delay mode (puts off a requested sleep by logind's `InhibitDelayMaxSec`, 5 s by default and 30 s on a stock Ubuntu 24.04, where a drop-in sets it; never refuses it); the words for the person name that delay as read on this computer, or say it could not be read. It never asks for `handle-lid-switch` or any other `handle-*` key, `shutdown`, or `sleep` in block mode, and the skill says which locks are taken and what they do not hold. | `test_G4_9_the_inhibitor_never_holds_a_sleep_the_person_or_hardware_asks_for`, `test_G4_9_the_delay_said_is_the_one_this_computer_gives` |

### G5 Watch and hold

| Codon | Claim | Test |
|---|---|---|
| G5.1 | `status` prints one line per runner: GitHub's side (online/offline, busy/idle) and the minion's side (service running, a job process present), each with the time read; then the repository's queue: jobs waiting and the age of the oldest. The minion's OS and architecture come from its own survey, never from GitHub's runners list, which says `unknown` for a runner with no default labels (seen, Part 7 C7). The first word of every line is the worst true thing: a runner neither idle nor busy is `warn`, and while a job waits on a runner that is offline or held, the queue's line is a `warn`, first, naming what it waits behind and the way on; `status` never begins with `ok` then (P2, 2026-11-23: it began `ok ...: not ready` while a job waited). | `test_G5_1_status_reads_both_sides_and_the_queue`, `test_G5_1_status_never_begins_with_ok_while_a_job_waits_on_a_runner_that_cannot_take_it` |
| G5.2 | "Idle" is said only when both sides agree: GitHub says not busy AND no job process runs on the minion. Otherwise the line says which side says what. | `test_G5_2_idle_only_when_both_sides_agree` |
| G5.3 | `watch` runs on a schedule (default every 5 minutes) and writes one line per run to the journal; a heartbeat older than three intervals is reported by `status` as "the watch is not running". | `test_G5_3_the_watch_runs_and_its_silence_is_noticed` |
| G5.4 | When a repository is no longer private, `watch`, within one interval, writes `public` where the job-start hook reads it, so every later job is refused before its first step (G5.15), and removes our labels when GitHub can be reached. As soon as no job runs on our runner, it takes the runner off the repository on GitHub's side with the person's own rights: GitHub refuses that while a job runs (HTTP 422, and the job finishes; seen 2026-11-22 on a stock Ubuntu machine). Once the runner is removed, GitHub hands it no job: a job asking for our labels stayed queued with no runner for two and a half minutes, 65 seconds of them while the old listener still ran here, and no step of it ran (seen). The old listener stops its own service when it next tries to make a session with GitHub, and how soon is not fixed (seen: at once, after 65 seconds, after about two minutes, and not within 13 minutes); the watch stops nothing, and `status` reads the service and says what it is. Each pass tries again until it is done; `status` says why first, from what it reads at that moment. Coming back is the person's: `resume --private-again` once GitHub reads the repository private, then `setup`, which stops what is left of the old listener and reads that back, registers the runner again in place with its folders and labels, and starts it (seen; without the stop, a listener still running kept the service and the new registration stayed offline, seen). The watch needs no admin rights: a system timer has no sudo ticket (found 2026-11-22: the test's stand-in host had said yes to sudo, G9.7). A label change alone is not the boundary, because a fork's copy of a workflow can ask for any label; the hook is. | `test_G5_4_a_repository_no_longer_private_gets_no_job_here_and_our_runner_leaves_it`, `test_G5_4_what_github_took_is_read_back_never_assumed` |
| G5.5 | Holds for low disk, high load or memory pressure (thresholds from the survey) and quiet hours the person sets are made by changing labels so no job asks for them; running jobs finish; the hold lifts by itself when the reason is gone, except G5.4. | planned: test_G5_5_holds_change_labels_and_never_stop_a_job |
| G5.6 | `drain` holds and then waits until both sides say idle (G5.2), with a time limit; it ends with "online and idle" or says what is still running. Every restart, update and removal goes through it (INV4). | `test_G5_6_drain_ends_with_online_and_idle_read` |
| G5.7 | After a restart of the minion, `status` names any job that was running when it went down and was cancelled, and offers to run it again once. GitHub marks such a job `failure`, not cancelled (seen, Part 7 C7); it is known by a step `cancelled`, the annotation "The operation was canceled.", no person having cancelled the run, and the cut falling in a stop or boot of the minion. It is never counted as the person's test failing. A job is named cut only when both sides agree, GitHub's result and this computer's own record of a stop, boot or sleep during it; a job that came through a short sleep is not cut (seen: a job passed after a 30 s suspend on a stock virtual machine, 2026-11-22). | `test_G5_7_a_job_cut_by_a_restart_is_named_and_offered_again` |
| G5.8 | `status` names a runner idle for 14 days or more, and any runner on GitHub's side that no service on any minion of ours matches, and offers removal (G6). | planned: test_G5_8_idle_and_orphaned_runners_are_named |
| G5.9 | Opt-in, off by default: a fallback for when the minion is offline. Each choice says in plain words that it runs a small job on GitHub's machines, which counts against the included usage (INV3); the person picks it by name. Design in Part 11, Q3. | planned: test_G5_9_the_offline_fallback_is_opt_in_and_says_its_cost |
| G5.10 | `watch` re-reads GitHub's cost rule (G1.9) once a week (from GitHub's documentation site, not its API, so it spends nothing of the person's API limit) and tells the person, first and once, at the next `status`, when the self-hosted sentence changed, with both versions and dates; a page that cannot be read is journaled, never taken for a change. In 0.1.0 by the owner's word (2026-11-23), for R16. | `test_G5_10_a_change_in_the_cost_rule_is_told` |
| G5.11 | Before a planned restart (a system update, a reboot, a shutdown): `drain --restart` holds every runner on the computer, waits for the running job, and ends with "online and idle: safe to restart now" or names what still runs. The hold lifts by itself once the computer has restarted and both sides read the runner online. The skill tells the agent to run it before any restart it asks for, and tells the person to ask for it before theirs: a restart cancels the running job. | `test_G5_11_drain_before_a_planned_restart_lifts_after_the_boot` |
| G5.12 | GitHub removes a runner it has not seen connect for more than 14 days (Part 7 C5). When `watch` or `status` reads that a runner of ours is gone on GitHub's side while its service is there (a laptop away for two weeks, a computer switched off), it reads the service and says so plainly. GitHub hands a removed runner no job, and its listener stops its own service when it next tries to make a session, with no restart loop, at a time that is not fixed (G5.4, seen); the watch, which has no admin rights (G9.7), stops nothing; and `setup` again stops what still runs, then re-registers it with the person's login, keeping its folders and labels. | `test_G5_12_a_runner_github_removed_is_named_and_registered_again` |
| G5.13 | A hold nobody notices looks like a broken project. When a job of the repository has waited longer than a threshold (default 30 minutes) while our runners are held, offline or asleep, `status` prints `warn` first: which job, how long, which hold, and that GitHub cancels it after 24 hours. | `test_G5_13_a_job_waiting_behind_our_hold_is_warned` |
| G5.14 | A job killed by our own memory limit (the kernel stopping a process inside `minions.slice`) shows on GitHub as a failed test. `status` names it as "stopped by this computer's memory limit", with the job and the time, never as the person's code failing, and says the one way to change the limit (G7.8). | `test_G5_14_a_job_killed_by_our_limit_is_named_as_such` |
| G5.15 | A runner takes a job only while this computer's guard lets it. The guard is the job-start hook: it runs as the runner's user before any step of the job and needs no GitHub call and no admin rights. **Before the hook:** GitHub's runner program itself fetches and unpacks the actions the workflow names into its work folder, before the hook runs, for a job the hook then refuses too; nothing of them is run, no container is made (the runner's user may not use the container program: it has no groups), and they are deleted at the job's end (G4.3; seen 2026-11-22 on a stock Ubuntu machine with an action and a service container: the runner's only processes for the job were the unpacking and our two hooks). It refuses the job at once when GitHub's own record of the event that started it (the file the runner gives every job) says the repository is not private; that record named the repository and its privacy for push, pull_request and workflow_dispatch (seen on a real runner, 2026-11-22), and a schedule's record names no repository. When that record cannot be read or names no repository, the watch's record alone decides: that is neither a refusal nor a pass by itself. It also refuses when the watch's record says `public`, or when the watch has not read the repository private in the last three passes (15 minutes at the default interval): then it waits up to 6 minutes for a fresh read (after a boot or a wake the next pass comes within that) and refuses. A read dated in the future is not fresh. The watch writes its record as the person, in a folder the runner's user can read and cannot write; the hook is root's, in a root folder, and the runner's user cannot change it. **The gap it leaves:** GitHub's record is written when the event happens, so a job whose event came before the repository turned public, or a scheduled job, can still start in the time between the turn and the watch's next pass: at most one interval, 5 minutes at the default. **Where the hook is named:** in the runner's own settings file (`.env` in its folder), which the runner's user can write; a job that was allowed to run could change that for later jobs, as it could the runner's own programs. A stranger's job cannot, because the hook refuses it before its first step. **What the watch does not do:** it stops no service and kills no job: it runs as the person, with no admin rights (G5.4). Each blind pass ends `failed`, with the count and the reason; `status` shows it first as a `stop` line with the person's way out (log in again; `setup` again where `gh` works; the network), never as `ok`. A job the guard refused shows on GitHub as failed; `status` names it as refused by this computer's guard when both sides agree (the hook's record and GitHub's failure), beside a cut or a kill, never as the person's test. `setup` runs one real pass and reads back that it read the repository private before it says ok. A job refused or waiting is the lesser harm than a runner serving a repository that went public while the watch was blind. Seen blind on a stock Ubuntu 24.04 machine, 2026-11-22 (P1a): a `gh` only in the person's own folder was not found by the watch's system service, which journaled "GitHub not read (HTTP 0)" each pass while `status` said ok. | `test_G5_15_a_blind_watch_says_so_and_holds_after_three_passes`, `test_G5_15_the_job_hook_refuses_a_job_without_a_fresh_private_read`, `test_G5_15_status_names_a_refused_job_as_refused`, `test_G5_15_setup_reads_back_one_real_watch_pass` |

### G6 Uninstall

| Codon | Claim | Test |
|---|---|---|
| G6.1 | `uninstall <owner/repo>` drains, unregisters the runner on GitHub's side (removal token through standard input), stops and deletes the service, deletes the runner's folders, and records each step in the journal first. An error answer from GitHub is never taken for "not listed": nothing on this computer is deleted until GitHub, read, no longer lists the runner (seen 2026-11-22: an uninstall deleted everything here while GitHub still listed our runner, which its own census then named; GitHub had answered HTTP 502 to runner calls that hour, and the code took an error answer for "not listed"; the uninstall's earlier lines were not kept). `status` and the hold say "could not be read" too. | `test_G6_1_uninstall_takes_a_runner_away_on_both_sides`, `test_G6_1_an_error_answer_from_github_is_never_taken_for_gone` |
| G6.2 | `uninstall --all` also deletes the runner user, the watch, and `~/.minions/`, and opens a pull request that puts each moved `runs-on` back as it was before `move`. | `test_G6_2_uninstall_all_leaves_nothing_and_offers_the_workflows_back` |
| G6.3 | `uninstall` ends with a census (INV6): runners on GitHub for the repository, services, folders, user, scheduled checks; each "none" or named. | `test_G6_3_uninstall_ends_with_a_census` |
| G6.4 | A removal interrupted part-way is finished by running `uninstall` again; it never fails on a step already done. | `test_G6_4_uninstall_again_finishes_an_interrupted_removal` |
| G6.5 | `uninstall` stops `minions.slice` before deleting its file and reads back that it is gone: a slice whose file is deleted stays loaded and active after a reload until it is stopped. Seen on a stock Ubuntu 24.04 virtual machine, 2026-11-22 (Part 7 C7). | `test_G6_5_uninstall_stops_the_slice_before_deleting_it` |
| G6.6 | `uninstall` runs the runner's own removal as root or as the runner user, never by entering the runner user's home as the person (that home is closed to the person, G2.2), and removes the GitHub side before it deletes the runner user. If the runner's own removal cannot run, it deletes the runner through the API and reads back that it is gone. Seen on a stock Ubuntu 24.04 virtual machine, 2026-11-22 (G4's run). | `test_G6_6_uninstall_runs_as_the_runner_and_github_goes_first` |
| G6.7 | Before `uninstall` opens the pull request that puts moved jobs back, it tells the person that the pull request starts its own checks when opened, as any pull request does, so a job put back on GitHub's machines whose workflow starts on pull requests runs there once for it before the merge. Seen 2026-11-22 (P1a): one such job ran on GitHub's machines each time, and nothing had said so. | `test_G6_7_uninstall_says_the_put_back_checks_before_its_pull_request` |

### G7 Words to the person

| Codon | Claim | Test |
|---|---|---|
| G7.1 | The cost sentence is the one read by G1.9 with its date, or "I could not read GitHub's page today". Never "free", "no bill", "saves" or an amount the tool did not read. | `test_G7_1_cost_words_are_read_and_dated_never_promised` |
| G7.2 | The skill tells the person that artifact and cache storage count the same whichever computer ran the job. | `test_G7_2_storage_is_said_to_count_the_same` |
| G7.3 | The skill tells the person, before `setup`, what a job on their computer can reach (G1.5, G1.7) and that anyone who can push to the repository can run code there. | `test_G7_3_what_a_job_can_reach_is_said_before_setup` |
| G7.4 | The skill tells the person that a restart of the minion cancels the job running on it, that a short sleep delays it and a long one fails it (G7.9), and that a job waits at most 24 hours for a runner before GitHub cancels it. | `test_G7_4_restart_and_waiting_limits_are_said` |
| G7.5 | The skill never says required checks or branch protection hold merges unless the tool read that the repository has them. | `test_G7_5_merge_rules_are_said_only_when_read` |
| G7.6 | Version 0.1.0 says, in the skill and at `setup`, that it serves one repository per computer and that more come in the next version (G12), and holds it: while this tool's record names another repository on this computer, `setup` changes nothing, names the one served and the way on (`uninstall` it first). | `test_G7_6_version_0_1_says_one_repository_and_more_next` |
| G7.7 | Before `setup` the person hears: "While your computer is held, asleep or off, your checks wait. GitHub cancels a check that waited 24 hours." | `test_G7_7_waiting_checks_are_said_before_setup` |
| G7.8 | Before `setup` the person hears the limits in plain words ("at most half of this computer's processors and 60 % of its memory", with the real numbers) and the one way to change them: `setup --limits <processors>,<memory %>`. | `test_G7_8_the_limits_are_said_in_plain_words_before_setup` |
| G7.9 | Before `setup` the person hears that a long sleep during a job fails that job on GitHub: the skill's lock only puts a sleep off, and GitHub gave up on a job about 10 minutes after the computer went to sleep (seen on a stock Ubuntu 24.04 virtual machine, 2026-11-22: a job came through about 265 s asleep; one asleep about 865 s was failed by GitHub while the machine still slept). `status` then names it as cut (G5.7). | `test_G7_9_a_long_sleep_fails_the_job_and_is_said_before_setup` |
| G7.10 | `look` itself prints, as its last lines and as `tell the person` lines, what the person must hear before setup: that anyone who can push can run code on this computer; private repositories only; that checks wait while the computer is off, asleep or held and GitHub cancels one after 24 hours; restarts and sleep; the limits word for word; the laptop sentence when there is a battery; one repository per computer; and the cost sentence. They go out as one numbered block, `tell the person 1 of 7:` to `7 of 7:`, so a line dropped or merged in a relay shows to the agent and to the person, and the skill says to paste the block whole and add the agent's own words only after it. A list in the skill file was skipped by a small model (P1a run B, 2026-11-22: the waiting sentence was never said); a line the tool prints is not; and an unnumbered block was shortened, two lines dropped and two reworded under the word "verbatim" (run B again, 2026-11-22). | `test_G7_10_look_prints_what_to_tell_the_person_before_setup` |

### G8 The skill and its commands

| Codon | Claim | Test |
|---|---|---|
| G8.1 | `skills/repo-minions/SKILL.md` follows the Agent Skills format and passes `gh skill publish --dry-run`. | `test_G8_1_the_skill_file_passes_the_validator` |
| G8.2 | The skill's order is fixed: look, tell, setup, move, verify, status. It names each step that is the person's (INV7) and what to say there. | `test_G8_2_the_skill_names_the_order_and_the_persons_steps` |
| G8.3 | The tool's commands are exactly: `look`, `setup`, `move`, `lint`, `status`, `watch`, `hold`, `resume`, `drain`, `doctor`, `uninstall`, `self-update` (`remove` is kept free for a later command that takes one repository's runner off a computer serving several; `uninstall` takes everything the skill made). Each prints lines of six kinds only: `ok`, `warn`, `stop`, `need you`, `waiting`, and `tell the person` (words the agent passes on exactly as printed; they never change the exit code, G7.10). The exit code is the worst line's, in this order, and these are every way a command can end: **0** every line `ok` or `warn`; **2** a `stop` line; **3** a `need you` line and no `stop`, also one that lets the steps after it run (the start test's yes): an agent that trusts exit codes went on past an ask that ended 0 (P1a, 2026-11-22); the one `need you` that is not an open ask is the admin password sudo asks for at once in the same run; **4** a `waiting` line and neither of those (G8.7); **1** the tool itself failed (an error it did not expect: it prints a `stop` line naming the error and the journal's last step); **64** the command was typed wrong (unknown command or option: never 2, which is `stop`); **130** the person interrupted it (the journal says which step it was on). | `test_G8_3_commands_and_line_kinds_are_exactly_these` |
| G8.4 | `doctor` checks every promise of G2 and G4 on the computer as it is now and finishes or undoes a half-done step (INV8). | planned: test_G8_4_doctor_checks_install_and_finishes_half_done_steps |
| G8.5 | Every `systemctl` and `journalctl` call of the tool passes `--no-pager`, so no call waits on a pager when run from a real terminal. Seen on a stock Ubuntu 24.04 virtual machine, 2026-11-22 (Part 7 C7). | `test_G8_5_no_system_call_waits_on_a_pager` |
| G8.6 | A command never hangs on `waiting`: each wait carries its own deadline, said in the line, and the command ends when it passes. The deadline is said once the command really waits (`setup`'s wait for the runner online, the start test, `resume`, `drain`, `move --verify`), never on a read that answers at once. | `test_G8_6_no_command_hangs_on_waiting` |
| G8.7 | A command that ends on `waiting` exits 4, and its last line says what it waits for and which command to run next. | `test_G8_7_waiting_exits_4_and_names_what_and_what_next` |
| G8.8 | The skill tells the agent that `waiting` is not done, and what to tell the person meanwhile: what is being waited for, and that nothing is broken. | `test_G8_8_the_skill_says_waiting_is_not_done` |
| G8.9 | Every program a service of ours runs by name is listed in the tool with why, and `setup` resolves each with the person's PATH: the watch's unit gets exactly those folders, in the PATH's order; the job hooks get theirs and must find each outside the person's home folder (the runner's user cannot enter it), or setup stops and names it. A test reads the tool's source and the hooks and fails on any program run by name that is not listed. A system service has the system's PATH, not the person's (seen, G5.15). | `test_G8_9_every_program_a_service_runs_by_name_is_listed_and_resolved` |
| G8.10 | The skill says a step that is the person's is never done another way: when an agent cannot do what a `need you` line says, or its command fails, it tells the person what was printed and asks; it never finds another route (a download instead of the named install, another source, another login, another folder). Seen 2026-11-22 (P1a run B): a small model downloaded `gh` into the person's folder instead of asking. | `test_G8_10_a_persons_step_is_never_done_another_way` |
| G8.11 | The skill says the tool's words, given as its words, are given exactly as printed, with nothing added inside them (no other time zone, no conversion, no comment), and that the agent's own explanation is never presented as the tool's. Seen 2026-11-22 (P1a run B): an Eastern time was put inside a line given as `look`'s exact words. | `test_G8_11_the_tools_words_are_quoted_exactly_or_not_called_its_words` |
| G8.12 | Every `stop` and `need you` line names what happened, in GitHub's or the system's own words where there are some (the HTTP answer, the program's last lines), and the one next step: run the same command again, settle what it says, a command to run, or that there is no step to take here. Seen 2026-11-22 (run B again): `move` stopped on "could not read it, so nothing is moved", with no answer and no way out, and a cold agent went into the tool's source. | `test_G8_12_every_stop_and_need_you_line_names_what_happened_and_the_next_step` |
| G8.13 | A reader that stops reading (`| head`, a closed terminal) never cuts a command short: the printing stops, the command finishes its work and its exit code, and no traceback is printed. Seen 2026-11-22: `look ... | head -2` ended in a BrokenPipeError traceback. | `test_G8_13_a_reader_that_stops_reading_never_cuts_a_command_short` |
| G8.14 | GitHub's API limit is the person's own, shared by every program on their login: the tool spends little of it (the watch about 24 calls an hour; `status` 2 plus 1 per queued run; a drain's or `move --verify`'s wait 1 call per 10 s, at most 180 and 60), and when GitHub refuses for its limit (HTTP 403 or 429 with no calls left, or `Retry-After`), the tool names it with GitHub's own reset time, makes no call until then, and a wait ends with a `waiting` line instead of polling into it; the watch's blind line says it comes back by itself. Seen 2026-11-23: the fleet's login spent its 5000 calls an hour (by fast polling outside the tool), and the watch's words said to check the network. | `test_G8_14_a_spent_api_limit_is_named_and_never_polled_into` |

### G9 Tests, CI and the promise gate

| Codon | Claim | Test |
|---|---|---|
| G9.1 | This repository's own CI runs on GitHub's standard runners only. | `test_no_workflow_of_this_public_repository_asks_for_a_self_hosted_runner` |
| G9.2 | The plan, `PROMISES.json` and the tests agree; a version is refused while a promise due in it has no test, and the version after it until the promise was seen on a real machine. | `test_PROMISES_no_version_ships_while_a_promise_due_in_it_is_not_built` |
| G9.3 | Tests that need GitHub use a stand-in of its API built from recorded real answers (in `tests/fixtures/`, recorded per Part 7); a row whose test leans on the stand-in is proven on a real machine and a real private repository before the tag. | planned: test_G9_3_the_github_stand_in_answers_as_recorded |
| G9.4 | Recorded GitHub answers are scrubbed before they are committed: no token, no email address, no real person's name or login (commit authors, run actors), and no account's number (an account object's `id`, `node_id` or avatar link names the account as surely as its login; found in the committed fixtures on 2026-11-23); a test reads every fixture and refuses any of them. | `test_G9_4_fixtures_hold_no_token_no_email_no_real_names` |
| G9.5 | The scrubber replaces a person's name or login only where it is a whole value of a name, login or author field, never inside paths or other text (a committer named like a product once turned `.github/workflows` into a made-up path); a test checks that every fixture's paths survive scrubbing. Seen on a stock Ubuntu 24.04 virtual machine, 2026-11-22 (G3's run). | `test_G9_5_scrubbing_never_changes_paths` |
| G9.6 | The gate of G9.2 counts a test only when the test runner collects it and it runs to a pass; a name found in a file is not a test. Every row marked built and every codon whose test is named is checked at any version, not only when its version is due. No test file defines one test name twice (a later twin hides the real body while its name passes). A skip is listed as "not run here" with its reason, never counted as a pass. The gate's own test plants each way a test can look present and not run (after a helper's `return`, renamed out of collection, under `if False:`, skipped, failing, shadowed by a twin, its text in a string) and must refuse each. Seen 2026-11-22: with G1.4's test moved under a helper after its `return`, the gate passed while the test never ran. | `test_G9_6_the_gate_counts_only_tests_that_ran` |
| G9.7 | A stand-in for this computer grants nothing a real machine refuses: in the tests of what a system timer runs as the person (the watch), `sudo -n` fails, as it does there. Found 2026-11-22: G5.4's test passed on a stand-in that said yes to sudo, while a real watch has no sudo ticket. | `test_G9_7_a_stand_in_grants_nothing_a_real_machine_refuses` |

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
| G12.2 | How many idle runners a computer can carry is computed from one idle runner's memory as measured on that OS (Part 7 C7); `setup` refuses a runner past that number and says how many there are and why. Seen so far (Linux x64, runner 2.338.2, a test account on a shared server, 2026-11-22): one idle runner holds about 126 MB of memory resident (about 99 MB proportional) and 683 MB of disk for its own unpacked copy. These are per runner: twenty idle runners come to about 2.5 GB of memory and about 13.7 GB of disk before any job runs, and each job's work folder comes on top. `setup` counts disk as well as memory. Other OS and versions: not measured yet. | planned: test_G12_2_idle_runners_are_counted_against_measured_memory |
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
| 0.1.0 | Linux, one private repository per computer, personal account: G1, G2, G3.1 to G3.3, G3.6, G4.1 to G4.7, G5.1 to G5.4, G5.6, G5.11, G5.12, G5.7, G5.13, G5.14, G5.10 (moved in by the owner's word, 2026-11-23, for R16), G6, G7, G8, G9, G11 on Linux, all INV. | No G5 code before C3's first part is seen. Every row due in 0.1.0 has its test; Part 9's proofs P1 to P6 and P8 seen on a clean Linux account, P9 and P10 on a Linux laptop. |
| 0.2.0 | macOS (C8 and C12 first), organisations and runner groups (G1.2, G2.6), G3.4, G3.5, G5.5, G5.8, G11 on macOS, G12 (many repositories on one computer). | Rows due in 0.2.0 tested; P1 to P6 and P8 again on a clean Mac account, P9 and P10 on a Mac laptop; 0.1.0's rows proven. |
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
- **P6 Uninstall.** `uninstall --all`: the census is empty on GitHub and the computer, compared with a census taken
  before `setup`.
- **P7 Turned public.** This proof breaks the first rule for a few minutes, so it runs only under all of these
  guards: a repository made for it, empty of code and secrets; fork pull requests needing approval for all outside
  contributors; the watch interval shortened for the test; the runner on a clean test account that holds nothing
  else of worth; public for under two minutes, then private again and deleted right after; G5.4 in force.
  It passes when, within the shortened interval, every job is refused before its first step, our runner is off
  the repository on GitHub's side once idle, and `status` says why.
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
