# Changelog

## Unreleased

- Memory: a job that needs more than its cap now fails when it reaches the cap, by name, instead of running
  slowly; `setup <owner/repo> --limits <processors>,<memory %>` raises the cap. Jobs get no high mark and are kept
  out of swap, where this computer holds that: `setup` reads it back from the running slice and says kept, not
  kept, or could not be read, and `doctor` tells you if it stops being held (G4.2, row R94).
- A job killed by our memory limit is said with the limit it was killed under. After a limit change, `status` said
  the new limit for a job killed under the old one (G5.14, row R93).
- `setup <owner/repo> --limits` changes the limits of a runner already set up here, in place: the slice is
  rewritten, read back from systemd and recorded, and nothing else is touched. It stopped with "exists already",
  though the line naming a job killed by the memory limit called it the one way to change the limit; that line now
  names the repository too (G7.11, row R92).
- `uninstall --all` ends a job's sleep hold left behind by a service stopped mid-job, by exact PID, and finishes.
  It stopped on "processes of minions-runner still run" with no way out, though those processes were its own.
  Any other process of the runner user is named with its name, never ended by the tool, and the line says how
  to finish (G6.8, row R91).
- The watch reads a finished job from GitHub once. It read every job in this computer's records again each
  pass (16 calls a pass with 13 jobs, about 190 an hour, against the 24 it said). The words now say the true
  spend: about 24 calls an hour, and at most 2 more for each job that ends here (G8.14, row R90).
- A kill by our memory limit ends the job's process only: the runner's service now goes on (`OOMPolicy=continue`),
  where systemd's default stopped the whole service and the runner went offline (G2.9, row R88).
- The idle-sleep hold of a job ends when the runner's service stops, or when the next job starts, as well as when the
  job ends: a service stopped mid-job left it for up to 6 hours (G4.4, row R89).
- A job held at our memory limit is named. With the default limits a job that outgrows them is not killed: the
  kernel holds it at the high mark and it barely moves, perhaps until GitHub's own time limit. The watch now reads
  the slice's own files each pass, and after two passes `status` names the job first, with the two ways out:
  cancel it, or give jobs more memory with `setup --limits` (G5.16, row R87).
- The line naming a runner GitHub shows offline says what was read: offline at every watch pass since a time,
  and now. It said GitHub had shown it offline the whole span, though the watch reads GitHub once a pass and the
  runner may have been online between (G11.4, row R86).
- The time GitHub has shown our runner offline is counted in waking time: just after a long sleep, `status` no
  longer says a runner that is still reconnecting "has not come back". setup's and resume's both-sides check ends
  an offline record written before the runner came online (G11.4, row R85).
- `status` says "busy" only when GitHub shows the runner online and busy and a job runs here. GitHub showing it
  offline while a job runs here is said as the two sides disagreeing, as a `warn` (G5.2, row R84).
- `status` judges the watch's heartbeat by waking time. The watch's timer counts only time the computer is awake,
  so after a sleep of more than about 10 minutes `status` began with a false stop ("the watch is not running").
  Each pass now records how long the computer has slept, and `status` says it slept and that the next pass comes
  within one interval of waking time (G5.3, row R83).
- The start test's read-back asks GitHub up to three times. It says a run or branch is left only when GitHub
  still answers with it, and says GitHub gave no clear answer when it never did. Before, any answer but "not
  found" (a 504, or a deletion still finishing) was called left (G1.4, row R82).
- On a stock Debian 12: `look` reads the login's scopes also from an older GitHub CLI that prints them without
  quotes (before, it said `workflow` was missing; row R79); the computer's own local name resolution port, 5355,
  counts as upkeep, not as something live (row R80); and the skill says that installing `git` on a computer
  without it is the person's step, with its command (row R81).
- Tests: a new recorded fixture, `tests/fixtures/path-word-author.jsonl`, holds GitHub's real answer for a commit
  whose author was named like a path word ("workflows"), scrubbed: the name is replaced and the path keeps its word
  (G9.5, row R43).
- `uninstall`'s census now counts a runner service that runs as neither this tool's runner user nor you, without
  naming it, as `look` does. Before, it printed such a service's name. It also says the slice and the watch are
  kept for this tool's other runners only when runners of ours are left (G1.12, row R78).
- The `need you` lines for a missing or logged-out GitHub CLI now say, in the line itself, that this skill
  reaches GitHub only through the GitHub CLI and its own login, that no SSH key and no other saved login on the
  computer is used or looked for, and that the step is the person's, in their own terminal (G1.10, row R77).
- `move --verify`, when its deadline passes while GitHub shows our runner offline, now says so and names
  `resume OWNER/NAME` first, then `move OWNER/NAME --verify` again. Before, it said to verify again after the
  next push, which the waiting run does not need (G3.6, row R76).
- `look` now gives the computer's own upkeep ports (remote login, name, address and time services, local name
  discovery) an `ok` line that names what each one is and says it is not counted as something live. Before, a
  remote-login port alone was a `warn`, and an agent asked the person whether to go on at all. Any other port
  open to the network is still a `warn` (G1.7, row R75).
- G11.4, by ruling, with the reason: a runner whose service runs while GitHub has shown it offline for more than
  two watch intervals is named first in `status`, as a `warn` with its way on (`drain --stop`, then `resume`).
  The codon promised a restart; it is made smaller because the watch has no admin rights and cannot restart
  anything, and the runner was seen coming back by itself after a network cut and two suspends in a virtual
  machine. A listener that never reconnects has not been seen; if it is, the row reopens (row R74).
- Tests: a new recorded fixture, `tests/fixtures/runner-life.jsonl`, holds GitHub's real answers for one runner's
  life (registered, idle, busy, held, resumed, removed; tokens removed, scrubbed). The test stand-in answers every
  recorded call exactly as recorded, and the hand-made runner answers carry only what a recorded one carries (G9.3).
- New command `doctor [OWNER/NAME]` (G8.4, INV8): it reads back every promise of the install on this computer as
  it is now (the runner's user, its folder and credential files, the boot service, the limits, the job hooks, the
  disk floor, the runner's version, one copy only, both sides with our labels, the watch), one line each, and
  changes nothing else. The last line counts the promises not kept, each named above with its next step (exit 2).
  A setup or removal cut part-way is undone or finished, read back. Without admin rights or a terminal to ask,
  what only an admin can read is said as not read.
- On a computer with one processor, the limits sentence in `look` and `setup` says "1 of 1" and, plainly, that
  while a job runs it uses all of it, at low priority, and everything else there will be slow (G7.8).
- `status`'s warning for a job waiting on an offline runner names the command that starts it: `resume OWNER/NAME`.
  "1 core" and "1 processor" are singular now.
- Row R17's second half (load and disk kind beside a slow result in `status`) moves to 0.2.0; 0.1.0 keeps
  `look`'s memory and swap reading and `status`'s naming of a job killed by the memory limit.
- `resume` (when it has to start the runner's service) and `drain --stop` now ask for the admin password
  themselves, once, before their admin step. Before, in a terminal where none was asked, `resume` stopped and sent
  the person back to "the terminal where you typed the password" (G2.12, row R73).
- `setup` now asks before it runs jobs on a computer that serves something live, as `look` said it would: it
  names what look found and changes nothing until the person says yes in words (`setup OWNER/NAME
  --beside-live-too`) or chooses another computer. Any port open to the network counts, except the computer's
  own upkeep; a port on 127.0.0.53 (a stock Ubuntu's own name service) is no longer counted as open to the
  network (G1.7, row R72).
- A setup cut part-way (for example when sudo's permission ran out) is found again: `uninstall` takes the
  half-made runner away, and `setup` run again takes it away first, read back, then sets up fresh. A read as
  root that sudo refused is said as not read in the census, never as none (G2.12, row R71).
- When sudo's permission runs out part-way, or the command runs in another terminal, the stop now says to run
  it again from the terminal where the password was typed, and that nothing is done twice (G2.12, row R32).
- On battery a laptop's runner is held by the watch, by default, and the hold lifts on mains power; the laptop
  sentence said this already, and now it is so. `resume OWNER/NAME --on-battery-too` takes jobs on battery,
  `--on-battery-held` goes back (G11.2, row R19).
- `look` names a computer with a lid as a laptop too, not only one with a battery (G11.1).
- Every wait says its deadline once it really waits: `setup`'s wait for the runner online, the start test and
  `resume` now say it too; every `waiting` line names the command to run next (G8.6, G8.7, rows R39, R40).
- The watch reads GitHub's cost rule again once a week; when its words on self-hosted runners change, the next
  `status` says so first, with both versions and dates (G5.10, for row R16).
- Before setup the person hears that a short sleep only delays a job and a sleep of more than about 10 minutes
  fails it on GitHub, from `look`'s numbered block and the skill (G7.9, row R49).
- The recorded GitHub answers used by the tests no longer carry the numbers of the accounts they were recorded
  from (an account's id, node id and avatar link); the scrubber removes them and a test refuses them (G9.4).
- GitHub's API limit: when GitHub refuses for it, the tool names it with GitHub's reset time, makes no call until
  then, and a drain's or `move --verify`'s wait ends with a `waiting` line instead of polling into it; the
  watch's line says it comes back by itself (G8.14, row R68).
- `status` begins with its worst true line: a runner neither idle nor busy is a `warn`, and jobs waiting on a
  runner that is offline or held are a `warn`, first, with what they wait behind and the way on (G5.1, row R67).
- Below 5 GB free for the work folders, the watch holds our runner before the disk fills, and lifts the hold by
  itself once 6 GB are free; until now only `setup` and `resume` checked the floor (G4.3, row R66).
- `look` reads the delay this computer gives a sleep during a job (logind's own setting, no admin rights) and
  the words for the person name it, or say it could not be read, instead of "a little later" (G4.9, row R48).
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
