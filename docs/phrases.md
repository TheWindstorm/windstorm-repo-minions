# Words this tool reads from other programs

This tool asks every program in one language. On Linux, each program it starts gets `LC_ALL=C.UTF-8`, and
`LANGUAGE` is removed (`c_language`). It does not use plain `C`. Nothing the tool starts for you or for a job gets this
environment: the runner's own `.env` is written apart from it. On a Mac, the environment is passed on unchanged, because
macOS 13 has no `C.UTF-8`. So wherever an exit code or a field can be read instead of words, the tool reads that.

Below is every phrase the tool matches in another program's answer: the program, how it is asked, the phrase exactly
as the code matches it, the stream it comes on, the version it was seen in, and whether it was seen translated. A test
(`test_G6_29_every_phrase_matched_is_on_the_list`) fails when the code matches a phrase this page does not list, or
when this page lists one the code no longer matches.

That test's limit: it finds a phrase only where the code matches it against an answer held in a variable named `out`,
`head`, `mode`, `words` or `entry`, or passed through `said()`. A phrase matched against an answer under any other name
is not found by it. Row R191, due 0.1.1, turns the test around: it starts from every answer the tool reads from another
program, whatever its name, and finds each phrase matched against it.

"Seen" means seen on a real computer. "German guest" is a stock Ubuntu 24.04.5 computer with the German language
pack, `LANG=de_DE.UTF-8` and `LANGUAGE=de_DE:de`, on 2026-11-24.

## Read by exit code or field (no words)

- `getent passwd minions-runner`: exit 0 with seven fields (the name, then the number in field 3) means the user is
  there; exit 2 with nothing written means it is not; any other answer is "not read". glibc 2.39.
- `pgrep -u <number>`: the user is asked by its number, read from getent, never by name. Exit 0 lists processes;
  exit 1 lists none. procps 4.0.4.
- `stat -c`, `systemctl show -p`, `systemctl is-enabled`, `systemctl is-active`: fields and state words made for
  programs (`masked`, `enabled`, `active`). systemd 255.
- Files of the kernel: `/proc/self/mountinfo` (field 5, `\040` read as a space), `/proc/net/tcp`, `/proc/net/route`,
  `/proc/stat` (`btime`), the job slice's `memory.events` (`oom_kill`, `high`) and `memory.pressure` (`full avg10=`),
  and `/proc/meminfo`.

## Read by words

- program: sudo
  - asked: `sudo -n ...` (never waits for a password)
  - phrase: `a password is required`
  - stream: standard error
  - seen: sudo 1.9.15p5. On the German guest, a call that carried the shell's language answered
    `sudo: Ein Passwort ist notwendig`; the same call with `C.UTF-8` and no `LANGUAGE` answered in English.
  - translated: yes, so it is asked in `C.UTF-8`.
- program: id (on a Mac only; Linux asks getent)
  - asked: `id -u minions-runner`
  - phrase: `no such user`
  - stream: standard error
  - seen: coreutils 9.4 on the German guest: `Einen solchen Benutzer gibt es nicht` in the shell's language, English
    with `C.UTF-8`. Not run on a Mac by this tool's tests.
  - translated: yes on Linux, so Linux asks getent's exit code instead.
- program: crontab
  - asked: `crontab -l -u minions-runner`
  - phrase: `no crontab`
  - stream: standard error
  - seen: cron 3.0pl1 (Ubuntu 24.04). On the German guest it stayed English.
  - translated: not seen translated.
- program: gh
  - asked: `gh auth token --hostname github.com`
  - phrase: `no oauth token found`
  - stream: standard error
  - seen: gh 2.45.0 (`no oauth token found for github.com`, exit 1).
  - translated: gh has no translations; not seen in another language.
- program: gh (older than 2.17, which has no `auth token`)
  - asked: `gh auth token --hostname github.com`
  - phrase: `unknown flag: --hostname` (one whole line on standard error, any line of it, with exit 1 and nothing on
    standard output; then gh's own file is read, and a file not there is no login)
  - stream: standard error, exit 1, nothing on standard output.
  - seen: gh 2.4.0 (the distribution's gh on a stock Ubuntu 22.04 virtual machine), 2026-11-26.
  - the other form, not asked by this tool: `gh auth token` with no flag gets `unknown command "token" for "gh auth"`
    on standard output, exit 1, nothing on standard error (the same gh, the same day). Before 2026-11-26 this page
    said that phrase was seen on standard error for the call with the flag; it was not, and the code no longer
    matches it.
  - before its first login the same gh, in a home of its own, left no folder at all (asked `auth token`,
    `auth status` and `api user`; the same machine, the same day).
  - translated: not seen in another language.
- program: gh
  - asked: `gh auth status --hostname github.com`
  - phrase: `Token scopes:\s*(.+)`
  - phrase: `(keyring)`
  - phrase: `\((GH_TOKEN|GITHUB_TOKEN|GH_ENTERPRISE_TOKEN|GITHUB_ENTERPRISE_TOKEN)\)`
  - stream: standard error in gh 2.4.0 and 2.23.0, standard output in newer gh; both are read.
  - seen: gh 2.4.0, 2.23.0, 2.45.0.
  - translated: not seen in another language.
- program: gh
  - asked: `gh auth login --hostname github.com --help`
  - phrase: `--insecure-storage` (a flag's name)
  - stream: either; both are read.
  - seen: gh 2.45.0 has it.
  - translated: a flag's name is not translated.
- program: GitHub's API (through `gh api -i`)
  - phrase: `(?im)^x-ratelimit-remaining:\s*(\d+)`
  - phrase: `(?im)^x-ratelimit-reset:\s*(\d+)`
  - phrase: `(?im)^retry-after:\s*(\d+)`
  - phrase: `^X-Oauth-Scopes:[ \t]*(.*?)\r?$`
  - phrase: `rate limit` (in the answer's `message` field)
  - stream: the answer's headers and body
  - translated: GitHub answers in English; headers are fields.
- program: journalctl
  - asked: `journalctl -u <unit> -o short-unix --since -3d`
  - phrase: `^\d+(\.\d+)?\s` (the time field each line starts with)
  - phrase: `Stopping |Stopped |Failed with result|Deactivated`
  - stream: standard output (the words were written by systemd when the unit stopped)
  - seen: systemd 255.
  - translated: not seen in another language.
- program: busctl (logind's sleep delay)
  - phrase: `t (\d+)` (a typed field)
  - translated: a field.
- program: pmset (Mac)
  - asked: `pmset -g batt`; `pmset -g`
  - phrase: `InternalBattery`
  - phrase: `^\s*sleep\s+(\d+)`
  - translated: not seen in another language; not run on a Mac by this tool's tests.
- program: diskutil (Mac)
  - asked: `diskutil info /`
  - phrase: `Solid State:\s*(\w+)`
  - translated: not seen in another language; not run on a Mac by this tool's tests.
- program: sysctl (Mac)
  - asked: `sysctl -n vm.swapusage`
  - phrase: `used = ([\d.]+)M`
  - translated: a field.

## Read from standard error on purpose

These programs' answers are read from standard error on purpose; every other answer is read from standard output only:
sudo (its refusal), id (Mac), crontab, gh (`auth token`, `auth status`, `auth login --help`, and its error words shown
to you), and stat (its refusal, shown to you, never matched).
