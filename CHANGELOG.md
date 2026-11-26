# Changelog

## 0.1.0 (2026-11-26)

The first release. Linux with systemd; one private repository per computer. Every row due in 0.1.0 in
`PROMISES.json` has its test, and each row says whether, where and how it was seen on a real machine. They were
seen on stock virtual machines (Ubuntu 24.04.5, Ubuntu 22.04.5, Debian 12) against a private repository, with
Claude Code 2.1.330 following the skill from a cold start, last on 2026-11-23; the passages added to the README
since were not walked by an agent. Not seen yet, by name: a laptop (battery, lid, its own
sleep, a change of network); an account whose GitHub Actions start no jobs; GitHub's own removal of a runner after
14 days offline; a repository really turned public with a runner on it; GitHub's API limit running out while the
tool works; other Linux distributions, macOS and Windows. The four rows built and tested for a laptop or such an
account are due in 0.1.1, to be seen there (rows R1, R19, R45, R74).

Since those cold starts one default changed: a job that needs more memory than its cap now fails when it
reaches the cap, by name, instead of running slowly; `setup <owner/repo> --limits <processors>,<memory %>` raises
the cap (row R94).

- Root runs only what root owns (family rule 28). A job is code from anyone who can push, and nothing a job can
  write is ever run, sourced or followed by root: the service unit is the tool's own writing (GitHub's `svc.sh` no
  longer runs as root); the runner's dependency script runs from root's own checked copy of the download; every
  act in the runner user's home runs as that user; only a runner user this tool made is used or removed. Every root
  step is listed, with the mode and owner of what it creates, in `docs/root-steps.md`, and a test holds the list
  to the code (rows R127, R128, R129, R135).
- Nothing the tool makes is writable by another account, even when it is started with umask 000 (row R134).
- When the last runner goes, its user goes with everything its jobs left running or scheduled: processes, crontab,
  at jobs, linger, files in `/tmp`, `/var/tmp` and `/dev/shm`; what could not be removed is named (row R130). A
  stop ends what a job left running in the service (`KillMode=mixed`, row R131). A failed unit is not left loaded
  after uninstall (row R132).
- No line says the job hook refuses a job without saying a job that already ran here can switch that hook off; the
  watch, which runs as you, is the gate (row R133).
- A file of yours in the tool's folders, or a `minions-runner` the tool's record does not show it made, no longer
  stops `uninstall`: it stays, named in the census with why, and uninstall finishes (row R136).
- Setup says which system packages were added while the runner's own dependency script ran (read from the
  system's package list before and after it), and `uninstall --all` names them as kept: they are installed for the
  whole system, and another program may use them by then (row R139).
- No test reaches the computer it runs on: one guard, imported first by every test file, gives the tests and their
  children a temporary home and loud stand-ins for the admin, service, package, scheduling, user and login
  commands, and fails the run on any test that reaches the real ones (row R142).
- What your shell carries no longer moves the tool. Its own GitHub calls go to github.com as the login gh saved,
  for `setup` and the watch alike: a `GH_TOKEN`, `GITHUB_TOKEN` or `GH_HOST` in your shell is named and not used,
  `look` and `setup` say which account they act as, and where gh keeps its login (`GH_CONFIG_DIR`,
  `XDG_CONFIG_HOME`) is read once and used by the watch too (row R144). The runner's own configuration runs with an
  environment the tool builds by name, so your locale no longer reaches every job; jobs get `LANG=C.UTF-8`
  (row R143). Before a tag, one round runs on a machine set up as a person's is, listed in `tests/lived-in.md`
  (row R145).
- A network blip no longer reads as "log in again": whether gh's login works is read in three answers (logged in;
  no saved login, or GitHub refuses it; could not be read, which says to run the same command again), from GitHub's
  own answer rather than `gh auth status`'s exit, which differs between gh versions (row R146). The watch starts the
  tool with `python3 -I`, so nothing in your own Python site-packages runs in it (row R147).
- `setup` again after either form of `uninstall` is walked by a test: it decides by what is there now, ends on its own
  check of both sides, and the commands after it work (row R149).
- A restart lifts only the hold `drain --restart` itself set: a runner you held before it stays held, `resume` or
  `uninstall` of a repository ends its restart hold, and `drain REPO` makes the hold yours (row R154).
- `setup` reads whether systemd runs this computer before it makes anything: in a container, or in Linux inside
  Windows without systemd, it stops having changed nothing here or on GitHub, and `look` says so first. A setup that
  stops after its first step names what it left and the next step (`setup` again finishes it, `uninstall` takes it
  away), and `status` says such a setup as cut part-way (row R155).
- A GitHub login that gh keeps only in a keyring (as on a desktop) cannot be read by the check every 5 minutes, which
  runs when nobody is logged in. `look` and `setup` read the login as that check will, before anything is made, and
  stop with where it is kept, the one step (`gh auth login --insecure-storage`) and what that step means for the
  login. Every GitHub call of the tool is read that way; no answer to a call sent without a login is read as
  anything, and the watch and `status` say each reason by its kind: no login, a repository not there for this
  login, or no answer (row R160).
- A stop before setup's first write makes nothing: on a computer that has never held the tool's record, a `look`
  that stops, or that ends at a step of the person's, keeps no record of itself, and where gh keeps its login is
  written down only once the login reads have passed. Those stops say "this tool made nothing": gh writes files of
  its own in a home where it never ran, and the tool does not claim them (row R160).
- A GitHub CLI with no `auth token` command (gh 2.4.0, Ubuntu 22.04's own) has its login read from gh's own file,
  and no file there is no login yet, with the login step; before, `look` stopped there on every such computer.
  A person who has never logged in is told that gh has no login saved here yet, or that it may have none, never
  that where gh keeps it could not be read.
  Only the answer seen from that gh is taken for it, and a
  stop on gh's answer about the login says gh's own words on standard error and nothing of what it wrote on
  standard output, where a token comes when the call works; before, such a stop quoted that output's last
  lines, holding back only a line shaped like a GitHub token (row R204).
- A record of what the tool made that could not be read (empty, cut short, not its format, unreadable) is never
  taken for an empty one or written over, by any command or by the watch's timer: every command stops before it
  changes anything, naming the file and why. Before, the watch wrote a new record over it without the runners,
  and `uninstall` then said there was nothing to uninstall, with the runner still there. The record now has a
  second copy beside it; the one step back is `restore`, a new command, which keeps the file that could not be
  read under a dated name and puts the copy in its place; when no copy reads, the stop says so and names the
  README's list of what the tool makes on a computer (row R205).
- With the record removed and its second copy still there, no command run by hand takes the computer for one
  where the tool made nothing: each stops before it changes or asks anything and says the record was removed while
  the tool's parts stay, with `restore` as its step when the copy reads. Before, `doctor` and `uninstall` of a
  repository said there was nothing to check or uninstall, `status` sent the person to `setup`, and `uninstall
  --all` took the watch off, with the runner still serving. `uninstall --all` deletes the copy before the record,
  so a cut between its last removals never leaves that state (row R205).
- `setup` never writes over a runner `.env` it could not read; it stops, names the file, and gives one step that
  keeps the file's lines. Before, such a file was written over with only the tool's own lines (row R206).
- The tool's own commands and its watch take turns with its record: a command that changes it holds one lock
  from its first read to its end. A command run by hand waits up to a minute for another, then stops and names
  it (only the process that holds the lock, never one that only has its file open); the watch never waits, and a pass that finds the record held is skipped and said. Before, two at the
  same time could undo each other's changes, lose an entry, or leave the record unreadable; seen in a stand-in
  trial with two processes writing in a tight loop, not on a real machine. No wait for a person is inside the
  hold: at a prompt, at the admin password and while a command waits for a running job, the lock is let go, and
  after the answer the command takes it again and reads the record afresh; when what the person was shown has
  changed meanwhile, it says so and asks again. The watch pass `setup` starts and waits for is handed `setup`'s
  own lock and is never skipped; a pass ends within 4 minutes. A command whose hold made the watch skip a pass
  says so at its end. The watch never takes its record, or the folder of its record and lock, for a fresh home
  when it is not there: it makes nothing, says so at each pass, and `restore` puts a copy that reads back in its
  place. `status --rerun` asks GitHub nothing while another of the tool's commands holds the record: it names
  that command, says the job was not run again, and gives the same command again (row R208).
- No road of the tool deletes a runner's `.env` that is not as the tool left it. `uninstall`, and `setup` run
  again after a setup that was cut short, read the file before the runner's folder goes; a file with lines
  added after setup wrote it is kept in `~/repo-minions-record/`, readable by its owner only, and named by path
  with its count of lines, never its lines. A line already in the file when setup wrote it is part of what
  setup left and goes with the folder. Where it cannot be kept and compared, the file and its folder stay, named, and
  `setup` gives the step that ends that stop for its kind (for the person's own file: move it, never remove it).
  `uninstall --delete-record` names the kept files before the word is typed. Before, those lines were deleted
  with the folder and nothing was said (row R209).
- On a computer that holds what carries the tool's own names (its runner user, a runner service of its naming
  form, their folders) and no record of the tool here names it, no line says the tool did not make it or that it
  is yours: each says what was read. `status`, `doctor`, `drain`, `resume`, `uninstall` of a repository and `setup`
  stop before any change, naming whether that service runs now and starts at a restart, the repository its name
  carries (GitHub may still list a runner for it), the README's list of what the tool makes on a computer and where
  GitHub lists runners. A read that did not answer is said, never taken for none. No step deletes the runner user
  while such a service is here or a process of that user runs. `uninstall --all` stops before any change while such
  a service runs, starts at a restart, or could not be read, with one step that switches it off and keeps it off,
  and says that it is another account's if another account set the tool up; with it off, the leave goes on and
  names it. It ends saying the tool is off only when nothing of its names can still run; otherwise it says what can,
  and exits 1. Before, `status` sent the person to `setup`, whose step deleted a user whose runner was running, and
  `uninstall --all` took the watch from that runner and ended "off this computer" (row R210).
- Nothing the tool writes names a folder inside Claude Code's plugin cache. Run from the plugin, `setup` keeps the
  path of Claude Code's own list of plugins and the plugin's name, never its version folder, and the tool says the
  skill was removed from Claude Code only when that list was read and names it nowhere: an update of the plugin says
  nothing, and a list that is not there, or that holds the plugin with no row as Claude Code writes one, is said as
  not read. Claude Code's own `plugin uninstall` takes the plugin off that list and leaves its folder; the census
  says so, and the tool touches neither. A test reads everything setup writes for such a path
  (row R161).
- `setup` and `uninstall` are yours to run, in your own terminal: each first shows you what you must read before
  anything changes (setup: eleven lines, twelve on a laptop, one sentence each) and goes on only when you press
  Enter. Run where nobody can press a key, as in an agent's shell, they change nothing and print the exact line for
  you (G7.15, row R112).
- Every block of lines for you that an agent passes on comes between a head and a foot line saying, in the tool's
  own output, that it goes to you whole and word for word (G7.16, row R113).
- The move pull request opens with move's own lines about waiting checks, restarts and sleep, and a job's tools, so
  you read them on the page where you decide to merge (G7.17, row R116).
- `move --verify` counts a moved job that ran on another computer of yours with this tool's runner, and names it; a
  runner it cannot identify is said as such (G3.7, row R114).
- After `uninstall --all` the census names the folder the skill was downloaded into: still there and yours to keep
  or delete, or gone (G6.13, row R117). The skill says an agent never deletes, or offers to delete, what uninstall
  kept (G8.18, row R115), and never looks into your keys or saved logins (G8.17, row R110).
- `status` names a job stopped at the memory cap, cut, or refused by its workflow file and its own name as well
  as the run; when it cannot read the workflow, it says so (G5.18, row R118).
- In your home folder the skill uses only the places it names (the clone, the GitHub CLI's own login, `~/.minions`,
  and after leaving, your record) and changes no other file of yours; before each release a stock machine's whole
  home is listed before and after setup, a real job and uninstall, and any change outside those places stops the
  release (G6.14, row R119).
- Setup tells the watch's first pass from the one before by a count of passes, not by the time, so a pass in the
  same second as the last one is no longer said as "wrote nothing" (G5.19, row R120).
- Installing the plugin is Claude Code's own act: it writes only Claude Code's plugin places, and the skill makes
  nothing until you ask it to set runners up (G6.14, row R121).
- Each line for you is said where it matters: before setup, before the merge, when status names a job the memory
  cap stopped (G7.14, row R111). The cost line points to GitHub's own page and claims nothing (G7.13, row R109).
- Every system command the tool prints for you is one that was run exactly as printed on that kind of system; the
  tool lists them. Off Debian and Ubuntu, the GitHub CLI's install line names no command, only where to read. Lines
  name this computer's values instead of placeholders (G8.16, row R108).
- CI: a newer push to a pull request cancels its older runs.
- After Claude Code's own `plugin uninstall`, `status` says Claude Code's own list no longer names the skill, that the
  runner and its watch go on taking your jobs, and the one command that takes them off; the watch writes it into
  its journal once (G6.12, row R107).
- `look` counts your own containers and other runner services and names them only on `look <owner/repo>
  --show-names`; `uninstall`'s census counts your own runner services. Their names may be your projects, and an
  agent keeps what it reads (G1.16, row R106).
- `setup <owner/repo>` on a runner already set up here puts this version's copy of the tool in place for the watch
  and touches nothing else. It stopped with "exists already", so the step `doctor` and `status` name for an older
  copy did nothing (G4.10, row R101).
- `status` says when GitHub last handed this runner a job, or that it has not yet, and that a job GitHub does not
  hand over (a schedule there can be late, or not come) is on GitHub's side (G5.17, row R105).
- The skill has evals, in the skill-creator layout (`skills/repo-minions/evals/evals.json`): the cold-start runs
  written as cases, each with the sentence the agent is given, what the person must be told and what must not
  happen (G9.9, row R103).
- `uninstall` keeps your record of what the tool did on your computer: the journal of every step and each job's
  start and end move into one plain-text folder you own, `~/repo-minions-record`, and the census names it last.
  Nothing of the tool is left installed or running. `uninstall --all --delete-record` deletes the record too, once
  you type delete at your own terminal. Before, `uninstall --all` deleted that record (G6.10, INV6, row R99).
- The skill says what keeps running after Claude Code's own `plugin uninstall` (the runner, the watch, the
  limits: they do your jobs) and the one command that takes them off, run from the copy setup installed. A test
  runs `uninstall --all` from that copy alone, to the end (G6.11, row R100).
- `doctor` and `status` say when the watch runs a different version of the tool than the one you run, which is
  older, and the one step that puts this version's copy in place. After an update of the skill, the watch went on
  running the old copy with nothing said (G4.10, row R101).
- `look`'s block for the person carries only what the person must hear: no promise about a later version, no
  "(seen)", and the tools line says only what to do. A test holds a bound on the block so it cannot grow back. In a
  cold start the smallest model kept every line in meaning but lost three such clauses (G7.12, row R104).
- `uninstall --all` prints the census last, and the census names this tool's state folder.
- `look`'s block for the person also says what a job too big for the memory cap does, and that a job has only the
  tools installed on this computer. In a cold start these were in the README only and did not reach the person
  (G7.10, row R98).
- `look` reads the login's scopes from GitHub's own answer where the GitHub CLI does not print them (gh 2.4.0,
  Ubuntu 22.04's); it said it could not read them (G1.3, row R97).
- Below Python 3.10 every command stops before any step, saying the version found and that this version runs on
  Ubuntu 22.04 or newer and Debian 12 or newer. On Ubuntu 20.04 (Python 3.8) there was no check: `setup` made and
  started the runner, then failed (G8.15, row R95).
- A runner whose setup was cut before its service was recorded is stopped by the name in its own folder; the
  removal said "the service is Failed to mangle name" (G6.9, row R96).
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
- A job held at our memory limit is named. Under a high mark (this tool's default before row R94, so on runners set
  up before it) a job that outgrows the limits is not killed: the kernel holds it at the high mark and it barely
  moves, perhaps until GitHub's own time limit. The watch now reads
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
