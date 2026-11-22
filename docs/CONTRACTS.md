# Platform contracts, as seen

Part 7 of the plan lists what GitHub, its runner and each OS must do for the code to be right. Each entry here says
what was seen, on what, and when. Nothing here is assumed. An entry that has not been seen says so.

Where: a private repository of a free GitHub organisation, made for these checks, with no runner registered, read
through the GitHub CLI's REST calls. Date: 2026-11-22, between 17:51Z and 17:57Z.

## C1 Registration and removal tokens (repository)

- `POST /repos/{owner}/{repo}/actions/runners/registration-token` answered with two fields, `token` and
  `expires_at`. The token expired one hour after it was made.
- `POST /repos/{owner}/{repo}/actions/runners/remove-token` answered the same way, also one hour.
- Both were called with the repository owner's login. The token values were never printed. Only `expires_at` was read.
- Not seen yet: the organisation endpoints; who else may call them.

## C2 The runners list

- `GET /repos/{owner}/{repo}/actions/runners` answered `total_count: 0` with no runner registered.
- Not seen yet: `status` and `busy` while a job starts, runs and ends, and after the service stops. That needs a
  runner on a test machine.

## C3 Labels: our labels only, and the hold (seen 2026-11-22, 18:16Z to 18:26Z)

Where:
- One GitHub runner, 2.338.2, on a Linux test account of a shared server.
- Run as a user-scope process with CPU and memory limits, not as a boot service.
- Registered to a throwaway private repository with `--no-default-labels --labels minions-c3`.
- Jobs started by workflow-dispatch.

Registration:
- GitHub then listed the runner with one label, `minions-c3` (type `custom`), and no `self-hosted`, OS or
  architecture labels.
- The token went from the API through standard input into the environment variable `ACTIONS_RUNNER_INPUT_TOKEN`.
  The runner reads any `ACTIONS_RUNNER_INPUT_<NAME>` variable as its `--<name>` input
  (`src/Runner.Listener/CommandSettings.cs`). The token was never on a command line. Removal used the same way.
- **The runner wrote `.credentials` and `.runner` with mode 664** (`.credentials_rsaparams` 600). G2.8 sets them to
  600 and reads them back.

With the runner online and idle:

| `runs-on` | After 75 s |
|---|---|
| `self-hosted` | waited, `queued`, no runner |
| `[linux]` | waited, `queued`, no runner |
| `[self-hosted, minions-c3]` | waited, `queued`, no runner |
| `[minions-c3]` | ran on our runner: started 4 s after dispatch, succeeded in 2 s, nothing billable |

So G2.11 holds for this runner version, and moved workflows must ask for `[minions]` alone (G3.1). A workflow
that adds `self-hosted` to our label would never run.

The hold:
- **Hold.** `PUT /repos/{owner}/{repo}/actions/runners/{id}/labels` with `{"labels":["minions-held"]}` replaced
  the label. A job asking for `[minions-c3]` then waited with the runner online and idle.
- **Restart.** After the runner was stopped and started again, GitHub still listed `minions-held`: the label set
  through the API survived the runner's restart. The job still waited.
- **Lift.** The same call with `{"labels":["minions-c3"]}` lifted the hold. The waiting job started 48 s later and
  succeeded.

## C7 Linux, first part (same run)

- **Stopping.** A runner started in the background by a non-interactive shell ignores SIGINT. A shell does that
  for background jobs.
  - The first stop, by SIGINT, left the runner running.
  - A second start then gave "A session for this runner already exists" and "Conflict": two copies of one runner.
  - SIGTERM stopped both copies, and their helper scripts, in 2 s.
  - So every stop is SIGTERM to the listener, followed by a read-back that no process of the runner is left, before
    anything else starts it. GitHub's own service file also stops with SIGTERM.
- **Idle cost.** One idle runner: 3 processes, about 126 MB resident (about 99 MB proportional), 1 s of CPU in 8
  minutes, 683 MB of disk for its unpacked copy. G12.2 counts both memory and disk.
- **Not seen yet.** The system unit the runner's own service script writes; its stop while a job runs; slice
  limits; a reboot.

## C7 Linux, as a boot service, on a stock virtual machine (seen 2026-11-22, 22:22Z to 22:26Z)

Where:
- A fresh Ubuntu 24.04.5 server cloud image, checksum and signature checked, 4 vCPU, 8 GiB.
- Nothing on it but a login key for the image's own default user.
- Runner 2.338.2 under its own user `minions-runner`, made with `useradd --create-home` (no extra groups).
- Registered with no default labels to a throwaway private repository.
- Installed with the runner's own `svc.sh install minions-runner`.

- **The user's home is 750 on this release.** The person's own account cannot enter it. That is good isolation,
  but the install must therefore run inside `sudo`.
- **The credential files are 664 again** (`.credentials`, `.runner`), as on the first Linux machine. They are set
  to 600.
- **The unit `svc.sh` writes**, `actions.runner.<owner>-<repo>.<name>.service` in `/etc/systemd/system`:

  | Setting | Value |
  |---|---|
  | `After` | `network-online.target` (with no `Wants=`, so it may not wait for the network) |
  | `ExecStart` | `runsvc.sh` |
  | `User` | the runner user |
  | `KillMode` | `process` |
  | `KillSignal` | `SIGTERM` |
  | `TimeoutStopSec` | `5min` |
  | `Restart` | **`no`**: a runner that crashes stays down until the next boot |
  | `Nice` | 0 |
  | `CPUWeight` | not set |
  | `Slice` | `system.slice` |
  | `WantedBy` | `multi-user.target` |

- **Our drop-in and slice, read back after `daemon-reload` and a restart.** The drop-in is
  `50-minions.conf`: `Wants=network-online.target`, `Slice=minions.slice`, `Nice=10`, `CPUWeight=50`,
  `Restart=on-failure`, `RestartSec=30`. The slice `minions.slice` has `CPUQuota=300%`, `MemoryHigh=5G` and
  `MemoryMax=6G`. The runner connected and listened again within 3 s.
- **A stop during a job does NOT wait for the job.** `systemctl stop` returned after 11 s. The runner's script
  stopped its listener ("Sending SIGKILL to runner listener"), and the job ended "Canceled" on the machine. No
  process of the runner user was left. On GitHub, the job and the run concluded **`failure`**, the running step
  `cancelled`, with the annotation "The operation was canceled." `TimeoutStopSec` does not protect a job: only a
  drain before the stop does.
- **A reboot during a job.** Rebooted 25 s into a 2-minute job. The machine was back 20 s later, and the runner
  was "Listening for Jobs" and online on GitHub 24 s after the reboot was asked. The service is enabled, so nobody
  had to start it. The job concluded exactly as for a stop: `failure`, step `cancelled`, "The operation was canceled."
- **A job cut this way therefore looks like a failed job.** The signs: a step `cancelled`, that annotation, the run
  not cancelled by a person, and the cut falling in a stop or a boot of the machine.
- **Idle as a service:** 3 processes, about 145 MB resident (about 129 MB proportional); the slice counted 148 MB.
  Disk 683 MB.

## C7 Linux, the install by `setup` on a stock virtual machine (seen 2026-11-22, 22:37Z to 22:46Z)

Where:
- A fresh disk on Ubuntu 24.04.5's cloud image, 4 cores, 8 GB, with only the distribution's `gh` added.
- The image's passwordless sudo was removed first, and the default user given a password, so sudo asked as it does
  on a person's computer.
- The GitHub login step was done for the agent, as a person would do it themselves.
- Runner 2.338.2, registered by `setup` to a throwaway private repository.

Seen:
- **sudo asks once per terminal.** One `need you` line and one password prompt covered every admin step of
  `setup`. A second terminal was asked again: sudo's permission belongs to the terminal it was given in (G2.12).
- **The runner's dependency script** (`bin/installdependencies.sh`) finished in about 8 s on the stock image.
- **The runners list says `os: "unknown"`** for a runner registered with `--no-default-labels`. The OS has to come
  from the computer itself (G5.1).
- **Inside a job:** the job ran as the runner user with no other groups, in `minions.slice` at nice 10; `sudo` was
  refused, and the default user's home could not be opened.
- **Idle under the service:** three processes, about 150 MB resident together. At stop, systemd reported a memory peak
  of 51.5 MB for the unit.
- **A deleted slice stays active.** After `minions.slice`'s file was deleted and systemd reloaded, the slice was
  still listed as active until `systemctl stop minions.slice` (G6.5).
- **`systemctl` waits on a pager** when its output goes to a real terminal. Read through a pipe, it does not. Every
  call of the tool passes `--no-pager` (G8.5).
- **A virtio disk reads as rotating** (`/sys/block/vda/queue/rotational` is 1), so the disk kind of a virtual machine
  cannot be read from it (G1.13).
- After removal: GitHub's runners list showed `total_count: 0`, and no user, unit, slice or runner process was left.

## C9, a move's pull request and its merge (seen 2026-11-22, 22:57Z to 23:00Z)

Where: a fresh disk on Ubuntu 24.04.5's cloud image, with one runner registered by `setup` to a throwaway private
repository, and `move` run there.

- **The pull request runs the moved line.** The pull request that `move` opened started a `pull_request` run before
  any merge. That run used the moved `runs-on: [minions]` line and ran on our runner. So the person sees the change
  work before they merge it.
- **The merge starts a run.** The merge's push run on the default branch started 2 s after the merge. `move --verify`
  found that run and did not have to start one.
- Both runs: `billable: {}`.

## C7 Linux, keeping the runner ready (seen 2026-11-22, 23:21Z to 23:30Z)

Where: the same kind of fresh disk, 4 cores, 8 GB; and a second one with 3 GB for the memory floor.

- **Job hooks.** The runner ran our scripts named by `ACTIONS_RUNNER_HOOK_JOB_STARTED` and
  `ACTIONS_RUNNER_HOOK_JOB_COMPLETED` in its `.env`. The job's log says "A job started hook has been configured by
  the self-hosted runner administrator", and the same for the completed hook. After the job, its work folder was
  empty, although the job had written a file there.
- **A started hook must wait for its locks.** The hook first returned before `systemd-inhibit` held its locks, so
  the job's first step saw none of them. The hook now waits until both locks are held.
- **Sleep locks from the runner user.** Stock polkit allowed the runner user, with no admin rights, to take both of
  them:
  - `systemd-inhibit --what=idle --mode=block`;
  - `systemd-inhibit --what=sleep --mode=delay`.
  `systemd-inhibit --list` showed them during the job, held by the runner user, beside the image's own delay locks.
- **The delay is 30 s on this image.** logind's `InhibitDelayMaxSec` defaults to 5 s, and a drop-in on the image sets
  it to 30 s. A suspend asked during a job waited on our delay lock for 30.2 s. The machine then slept and woke on a
  timer. The job passed: the sleep was delayed, not refused, and a 30 s sleep did not cut that job.
- `LidSwitchIgnoreInhibited` was logind's default, `yes`: the lid switch is not held by sleep locks.
- **The hold through a job.** The label hold was set while a job ran. The job went on to the end and passed. No new job
  landed until the hold was lifted, and the next job landed after it was.
- **A cleanup that enters the runner user's home as the person is refused** (that home is closed, G2.2). The
  runner's own removal must run as root or as that user (G6.6).
- **`gh auth logout` leaves its file.** After logout, `~/.config/gh/hosts.yml` was still there, with the token gone.

## C4 What a run looks like when no job is made

- **A workflow file that is invalid** (a step with both `run` and `uses`), pushed: one run, `conclusion: failure`,
  `name` equal to the file's path, `path` the real file, zero jobs.
- **A run GitHub refuses to start**: not reproduced here. GitHub's documentation says a lock on an account can
  block GitHub Actions. The shape the setup looks for is `conclusion: startup_failure` with zero jobs. The setup
  checks this first, before anything is installed.
- So `look` (G1.4) can tell "your workflow file is broken" (failure, the file's path) from "GitHub did not start
  the run" (startup_failure). A runner cannot fix the second.

## C5 Offline removal and updates (GitHub's documentation, read 2026-11-22)

- "A self-hosted runner is automatically removed from GitHub if it has not connected to GitHub Actions for more
  than 14 days." An ephemeral runner is removed after a shorter time offline. (docs: manage runners, remove runners)
- Self-hosted runners "receive automatic updates for the self-hosted runner application only", and automatic
  updates may be disabled. (docs: concepts, self-hosted runners)
- Not found on the day: a current rule for how far behind a runner may fall before GitHub refuses it jobs. Until
  it is seen, G4.5 keeps the runner's own updates on and the doctor reports the version; it makes no claim about a
  deadline.

## C6 The runner's releases and checksums

- `GET /repos/actions/runner/releases/latest` on 2026-11-22: `v2.338.2`, published 2026-10-26. It has assets for
  linux-x64, linux-arm64, osx-x64, osx-arm64, win-x64 and win-arm64, among others.
- SHA-256 checksums are published in two places that agreed for linux-x64:
  - the release notes, between markers `<!-- BEGIN SHA linux-x64 -->` and `<!-- END SHA linux-x64 -->`;
  - `GET /repos/{owner}/{repo}/actions/runners/downloads`, field `sha256_checksum` per OS and architecture, with
    `filename` and `download_url`.
- G2.3 reads the downloads API for the file and its checksum. It checks the downloaded file against that checksum
  and also against the release notes' checksum. A disagreement stops everything.

## C9 Dispatching a workflow

- `POST /repos/{owner}/{repo}/actions/workflows/{file}/dispatches` with `ref=main` and `return_run_details=true`
  answered HTTP 200 with `workflow_run_id`, `run_url` and `html_url`. The run is known without guessing.
- A workflow whose one job asks for a label no runner carries (`runs-on: [self-hosted, minions-no-runner-here]`)
  made a run with one job in `queued`. It ran on no machine. Cancelled with
  `POST .../runs/{id}/cancel`, it ended `cancelled`. The run's timing (`GET .../runs/{id}/timing`) showed
  `billable: {}`.
- This is G1.4's dispatch test. Its workflow file must be on the default branch for the dispatch to be accepted.
  Not seen yet: the answer when Actions is turned off for the repository.

### C9, the start test (G1.4), 2026-11-22 18:05Z to 18:12Z

- The plan's start test cannot use workflow-dispatch, because that needs the file on the default branch. A push
  can be used instead.
- With no workflow on the default branch, a push to a temporary branch holding a workflow with `on: push` started a
  run. Its one job asked only for a custom label (`runs-on: [minions-no-runner-here]`, without `self-hosted`), and
  it was created and waited (`queued`).
- With a push workflow of the "person's" on the default branch, a commit with no parent holding only our one file,
  pushed to a temporary branch, started only our workflow: one run, one job, queued. The person's workflow did not
  run, because a pushed commit's workflows are read from that commit's own files.
  - Built with `git hash-object`, `git mktree` and `git commit-tree`, so no working copy of the person's repository
    is changed.
  - Pushed as `<commit>:refs/heads/<branch>`.
- Cleanup, each answered `204 No Content`: cancel (`POST .../runs/{id}/cancel`), then delete the run
  (`DELETE .../runs/{id}`), then delete the branch (`DELETE .../git/refs/heads/<branch>`).
  - Read back afterwards: the run is "Not Found", the branch list has only the default branch, and no workflow of
    ours is listed.
  - The run's timing showed `billable: {}`.
- Not seen yet:
  - a rule that refuses creating a branch;
  - a token without `workflow` scope pushing a workflow file. Expected: refused. Not yet seen.

## C11 GitHub's billing page

- `https://docs.github.com/en/billing/concepts/product-billing/github-actions`, read 2026-11-22 17:57Z. It has a
  list under "The following usage remains free independently and does not reduce the included compute usage:".
  The list has two items, "Standard GitHub-hosted runners in public repositories" and "Self-hosted runners."
- On the same page, about Copilot code review: "You can also configure GitHub-hosted larger runners or self-hosted
  runners via Actions Runner Controller (ARC), which are billed at different rates."
- G1.9 looks for that list and for "Self-hosted runners" inside it. If it cannot find them it prints
  "I could not read GitHub's rule today", never a guess.

## Not seen yet

C2's busy and status changes during a job (busy=true was read within 5 s of a dispatch), C5's update deadline,
C7 and C8 (services on Linux and macOS), C10 (organisation runner groups), C12 (laptops). Each needs a runner on a
booked test machine, or a read of the docs, before its code.
