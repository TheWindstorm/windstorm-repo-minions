# Changelog

## Unreleased

- `setup` says this version serves one repository per computer, and holds it: a second repository's `setup`
  changes nothing, names the repository already served and says to uninstall it first (G7.6).
- The skill names where to clone: the person's home folder. Seen once, over a restart: nothing that `setup`
  installs ran from a clone under `/tmp`; the watch, the job hooks and the runner came back, and the clone was gone.
- `look` prints the words for the person as one numbered block (`tell the person 1 of 7:` to `7 of 7:`), and the
  skill says to paste it whole, adding the agent's own words only after it (G7.10). Every `stop` and `need you`
  line names what happened and the one next step (G8.12). A reader that stops reading never cuts a command
  short (G8.13). Rows R63 to R65.
- An error answer from GitHub is never taken for "our runner is not listed": `uninstall` deletes nothing here
  until GitHub, read, no longer lists it; `status` and the hold say "could not be read". `status` says a removal
  is owed when an idle runner of a repository no longer private is still listed (G6.1, G5.4). Dates in the plan
  are the day in New York.
- A removed runner's listener can keep running for a while: `setup` again now stops it before registering
  again (without that, the new registration stayed offline); `status` reads GitHub and the service for a
  repository no longer private instead of repeating the watch's record; a hold GitHub took behind an error answer
  is read back (G5.4, G5.12). G5.15 says what the runner fetches before the hook.
- The job-completed hook also deletes the actions a job fetched: GitHub's runner downloads them before any hook
  runs, so even a refused job left them on disk (G4.3).
- Plan 2.0: INV1, INV4 and G5.4 corrected to the guard a real machine has (the job-start hook refuses every
  later job; labels off, and the runner taken off the repository on GitHub's side once idle, with the person's
  own rights; the watch stops no service; G5.12 reads the service the runner stopped itself); G9.7; rows R60
  to R62.
- From two cold starts: `look` prints what the person must hear before setup as `tell the person` lines;
  every open `need you` is exit 3; the gh install line refreshes apt first; `uninstall` says the put-back pull
  request's checks first; every program a service runs is resolved at setup; this computer's guard (the job-start
  hook) refuses a job of a repository GitHub's own event or the watch says is not private, or the watch has not
  read private for 15 minutes; a blind watch is a `stop` line; `status` names a refused job; the skill says a
  person's step is never done another way and the tool's words are quoted exactly (G1.10, G5.15, G6.7, G7.10,
  G8.3, G8.9 to G8.11; rows R52 to R59).
- Plan 1.14, from two cold starts (an agent with a shell on a fresh Ubuntu machine, given one sentence): G1.10
  (the gh install line that works on a fresh image), G8.3 (six line kinds; every open ask is exit 3), G5.15 (a
  blind watch says so and the runner takes no job until it reads the repository private again), G6.7, G7.10, G8.9
  to G8.11; rows R52 to R59.
- Plan 1.13, from G6's run on a stock virtual machine: G7.9 (a long sleep fails the job, said before setup);
  rows for two systemd 255 changes the code now reads (sleep wording, slice load state); rows R49 to R51.
- Plan 1.12: the command that takes everything away is `uninstall` (was `remove`), the family's word; `remove`
  stays free for taking one repository's runner off a computer that serves several. Codon numbers stay.
- G5 Watch and hold, first part (Linux): `status` (both sides of each runner with the time each was read, "idle"
  only when both agree, the queue, the watch's heartbeat), `watch` (a system timer every 5 minutes, run as you),
  the guard for a repository no longer private (held, a 2-minute drain, stopped and kept stopped until you say it
  is private again), `drain --restart` (lifts by itself after the boot), a runner GitHub removed (named, held from a
  restart loop, registered again in place by `setup`), and a warning first for a job waiting behind our hold.
- The skill file, `skills/repo-minions/SKILL.md`: the fixed order, the person's steps, the five line kinds and
  exit codes, what `waiting` means, the sentences said before setup, the cost rule and the safety rules
  (G7.1 to G7.5, G7.7, G7.8, G8.1, G8.2, G8.8).
- Plan 1.11, from G4's run on a stock virtual machine: G6.6 (remove runs as the runner, GitHub first), G5.7 (a job
  that came through a short sleep is not cut), G4.9 (the real sleep delay, 30 s on Ubuntu 24.04); rows R46 to R48.
- G4 Keep ready (Linux): `drain` (hold, wait until no job runs, then optionally `--stop`) and `resume`; `setup
  --limits <processors>,<memory %>`; the memory floor (a memory max under 2 GiB stops setup, with the way out); each
  job's work folder emptied after it; the runner's version read with its self-update left on. **Sleep, in plain
  words:** while a job runs, the runner takes exactly two locks. One blocks only the computer's own idle sleep. The
  other only delays a sleep you ask for, by logind's delay (5 s by default, 30 s on Ubuntu 24.04); it never refuses
  it. Neither holds the lid switch, the power or suspend keys, a low-battery sleep or a shutdown: closing the lid or
  choosing to sleep still puts the computer to sleep, and a job running then is cut.
- Plan 1.10: G4.9, only the idle sleep is held during a job, never the lid switch, a low-battery sleep or a
  sleep the person chose; G4.4 says "falls asleep by itself" (row R45).
- Plan 1.9: G9.6, the gate counts only tests the runner collects and runs to a pass (row R44).
- Plan 1.8, from G3's run on a stock virtual machine: G1.15 (look and move judge jobs alike), G9.5 (scrubbing
  never changes paths); rows R42 and R43.
- Plan 1.7: G5.7 (a cut job is named as cut) moves into 0.1.0 (rows R11 and R29); G4.8 measures the 2 GiB floor
  against the memory max and its stop line names `setup --limits` and what it costs.
- Plan 1.6: five line kinds (`waiting` joins `ok`, `warn`, `stop`, `need you`) and G8.3's whole exit-code table;
  G8.6 to G8.8 (a wait has a deadline, exits 4 and names what next, and is not done); G4.2's interim fixed limits
  said as such; G4.8 (floors), G5.14 (a job killed by our limit is named), G7.8 (the limits said before setup);
  rows R36 to R41.
- Plan 1.5, from G2's run on a stock virtual machine: G1.13 (a virtual disk is not called spinning), G1.14
  (look offers only what this setup does), G2.12 (admin steps stay on the terminal that was asked), G6.5 (stop
  the slice before deleting it), G8.5 (no pager), G5.1 (the OS comes from the minion, not GitHub's list); rows
  R30 to R35.
- Plan 1.4, from a stock virtual machine: G4.6 (a service stop cancels the job, so drain first), G2.9 (the
  unit's Restart=no and missing Wants=), G5.7 (a cut job is marked failure); rows R27 to R29.
- Plan 1.3: G1.12, other people's things on a shared computer are counted, never named (row R26); G9.4,
  recorded fixtures are scrubbed of tokens, email addresses and real names (row R25).
- Plan 1.2: G12.2 carries the idle numbers seen on a real runner (machine kind, runner version, and what twenty
  runners come to).
- Plan 1.1: G4.7, a stop is SIGTERM and read back, never two copies of one runner (seen on a real runner);
  row R24; every promise row has its tracking issue.
- Plan 1.0: the DNA Strand Master Plan (8 invariants, 12 genes, 12 platform contracts to check, 3 milestones,
  10 real-machine proofs) and 23 promise rows from lessons already learned. Nothing is built yet.
