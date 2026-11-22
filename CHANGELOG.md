# Changelog

## Unreleased

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
