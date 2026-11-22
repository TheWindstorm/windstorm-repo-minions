# Changelog

## Unreleased

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
