# Changelog

## Unreleased

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
