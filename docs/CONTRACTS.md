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

C2's busy and status changes, C3 (labels as the hold), C5's update deadline,
C7 and C8 (services on Linux and macOS), C10 (organisation runner groups), C12 (laptops). Each needs a runner on a
booked test machine, or a read of the docs, before its code.
