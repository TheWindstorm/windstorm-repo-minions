# Every step this tool runs as root

A job is code from anyone who can push to the repository. It runs as the runner user (`minions-runner`), so
anything that user can write, a job can write. The rule this list checks: **nothing a job can write is ever run,
sourced or followed by root.**

Each row below is one place in `scripts/minions.py` where the tool runs a command through `sudo` as root. Steps
it runs as the runner user (`sudo -u minions-runner`) are not root steps and are not listed. A test
(`test_G2_15_every_root_step_is_on_the_list`) reads the tool's source and fails on a root step that is not on this
list, or on a row here that the tool no longer has.

Each row answers three questions:

1. **Runs a job's file?** Does root execute or source a file a job could have written? (no / yes, fixed how)
2. **Job's path?** Does root read, write, chown, chmod, mkdir, copy or delete at a path with a part the runner user
   can write or re-point? (no / yes, fixed how)
3. **Creates (mode, owner)?** For a step that makes a file or folder: the mode and owner it sets by name, never left
   to the umask the command ran under. The tool also raises its own umask to at least 022 when it starts (R134).

The question about the job hooks has one answer for the whole tool. It is at the end.

Folders, and who can write them:

- `/etc/systemd/system`, `/usr/local/lib/windstorm-repo-minions` and `/var/lib/windstorm-repo-minions`: root.
  Inside the last one, each repository's records folder belongs to the runner user (its jobs' hooks write there),
  and `seen` belongs to you.
- `/tmp/minions-download.XXXXXXXX`: root's own folder, made fresh with `mktemp` (0700, then 0711 so the runner user
  can read the one file it is told the name of). `/tmp` is sticky, so no one else can rename or replace it.
- `/home/minions-runner` and everything under it: the runner user. Every step there runs as the runner user.

| function | root runs | runs a job's file? | job's path? | creates (mode, owner) |
|---|---|---|---|---|
| `check_admin` | `true` | no | no | nothing |
| `ask_admin_once` | `true` | no | no | nothing |
| `doctor` | `true` | no | no | nothing |
| `ensure_runner_user` | `useradd` | no | no: the name is read absent first (R129) | the user and its group; its home, set by the next row |
| `ensure_runner_user` | `chmod` | no | no: `/home/minions-runner` itself, an entry in root's `/home` that no job can re-point | `/home/minions-runner` 0750, the runner user's |
| `ensure_runner_user` | `stat` | no | no: the same folder | nothing |
| `unpack_from_roots_copy` | `mktemp` | no | no | `/tmp/minions-download.XXXXXXXX` 0700 root:root (mktemp's own mode) |
| `unpack_from_roots_copy` | `install` | no | no: your checked download, copied into root's own folder | `runner.tgz` 0644 root:root |
| `unpack_from_roots_copy` | `sha256sum` | no | no: root's copy is the one checked (R127) | nothing |
| `unpack_from_roots_copy` | `chmod` | no | no: root's own folder | that folder 0711 root:root |
| `unpack_from_roots_copy` | `rm` | no | no: root's own folder | nothing |
| `run_dependencies` | `install` | no | no: a folder inside root's own | `root` 0700 root:root |
| `run_dependencies` | `tar` | no | no: from root's checked copy, never the runner's folder | `bin/installdependencies.sh` inside the 0700 root folder, deleted with it |
| `run_dependencies` | `bash` | no: the script comes from root's checked copy (R127; it used to run from the runner's folder) | no | what the runner's dependency script installs (system packages) |
| `root_file` | `(argv)` | no | no: only the paths the rows below give it, all in root's folders | `tee`, then `chown root:root`, then `chmod`: 0644 root:root unless the caller names a mode |
| `install_service` | `install` | no | no | the drop-in folder 0755 root:root |
| `install_service` | `(argv)` | no: `systemctl daemon-reload`, `enable`, `start`. systemd starts `runsvc.sh` as the runner user (`User=`), never as root | no | nothing. The unit, drop-in and slice are written by `root_file` (0644 root:root), the unit in this tool's own words (R127; GitHub's `svc.sh` used to run as root from the runner's folder) |
| `change_limits` | `systemctl daemon-reload` | no | no | nothing. The slice is written by `root_file` (0644 root:root) |
| `install_hooks` | `install` | no | no: root's folders. Each repository's records folder is made in root's folder, then handed to the runner user | the hooks folders 0755 root:root; the records root 0755 root:root; the repository's records folder 2750, the runner user's, your group; `seen` 0755, yours, your group |
| `install_hooks` | `stat` | no | no | nothing. The two hooks are written by `root_file` (0755 root:root) |
| `install_watch` | `install` | no | no: the tool's copy, in root's folder | the hooks root 0755 root:root; the tool's copy 0755 root:root |
| `install_watch` | `(argv)` | no: `systemctl daemon-reload`, `enable --now` the timer. The watch runs as you (`User=`), from root's copy | no | nothing. The watch's service and timer are written by `root_file` (0644 root:root) |
| `install_watch` | `systemctl start` | no | no | nothing |
| `start_runner` | `systemctl start` | no | no | nothing |
| `_stop_runner` | `systemctl stop` | no | no | nothing |
| `resume` | `systemctl enable` | no | no | nothing |
| `reregister` | `(argv)` | no: `systemctl enable`, `start` | no | nothing |
| `doctor_one` | `stat` | no | no: the hooks (root's) and the runner user's home entry. The credential files are read as the runner user (R128) | nothing |
| `root_test` | `chosen` | no | no: `sh -c` with the tool's own fixed script, `if test -e "$1"; then echo "minions-test: there"; else echo "minions-test: not there"; fi`, the path given as its argument (never in the script), on root's own paths only; the answer is that printed mark, never sudo's exit or words. A path in the runner user's home is read as the runner user (R128) | nothing |
| `root_test` | `(argv)` | no | no: the same marked `sh -c` test of the runner user's home folder itself (`/home/minions-runner`), read as root only when the runner user reads absent (getent's exit 2; seen: that user removed by hand, so sudo cannot run anything as it, and nothing is then tried as it): a folder in `/home`, which a job cannot replace or re-point; where it is not there, nothing in it is (R167). Where it is there, nothing inside it is read, followed or deleted by root; it is named as what stayed (R173) | nothing |
| `remove_service` | `systemctl disable` | no | no | nothing |
| `remove_service` | `rm` | no | no: the unit and drop-in in `/etc/systemd/system`. The unit name, when read from the runner's `.service` file, is read as the runner user and must match `actions.runner.*.service` with no `/`; with that user gone it is not read there: it is the name this tool gives the unit, used unless that unit's file in `/etc/systemd/system` is read not there (R167) | nothing |
| `remove_service` | `systemctl daemon-reload` | no | no | nothing |
| `remove_service` | `systemctl reset-failed` | no | no | nothing |
| `clear_folder` | `rm` | no | no: `rm -f` by exact name, in a folder only this tool writes (the job hooks, the drop-in, the copy, `seen`'s files of this tool: root's), each name taken from the code's own list. A folder a job can write is emptied as the runner user, never by root (R128, R136) | nothing |
| `clear_folder` | `rmdir` | no | no: a folder of this tool, by exact name, once what this tool or the runner user made in it is gone. `rmdir` follows nothing and removes only an empty folder; a folder that still holds something stays, named (R136) | nothing |
| `empty_then_rmdir` | `rmdir` | no | no: a records folder that the runner user has already emptied. `rmdir` follows nothing and removes only an empty folder | nothing |
| `remove_shared` | `systemctl stop` | no | no | nothing |
| `remove_shared` | `systemctl disable` | no | no | nothing |
| `remove_shared` | `rm` | no | no: root's folders, and `seen` (yours) | nothing |
| `remove_shared` | `systemctl daemon-reload` | no | no | nothing |
| `remove_shared` | `systemctl reset-failed` | no | no | nothing |
| `remove_slice` | `systemctl stop` | no | no | nothing |
| `watch_off` | `systemctl stop` | no | no | nothing |
| `remove_slice` | `rm` | no | no | nothing |
| `remove_slice` | `systemctl daemon-reload` | no | no | nothing |
| `remove_runner_user` | `crontab` | no | no: root's spool | nothing |
| `remove_runner_user` | `atq` | no | no | nothing |
| `left_schedules` | `find` | no | no: root's own at spool folder (`/var/spool/cron/atjobs`, or `/var/spool/at`), which no job can write; `-maxdepth 1 -type f -nouser -printf '%f %U\n'` reads each file's name and owner number only, and follows nothing. Read only when the runner user reads absent: setup before `useradd`, uninstall to name what stayed (change 4) | nothing |
| `remove_runner_user` | `atrm` | no | no | nothing |
| `remove_runner_user` | `loginctl` | no | no | nothing |
| `remove_runner_user` | `userdel` | no | no: the home is emptied first, as the runner user, of what that user made. `userdel` without `-r` (R130, R136) | nothing |
| `remove_runner_user` | `rmdir` | no | no: the runner user's home, once that user is deleted and nothing stayed in it: a folder in `/home`; `rmdir` follows nothing and removes only an empty folder (R130) | nothing |
| `hand_to_root` | `find` | no | no: BEFORE `userdel` (every process of the runner user is ended by then), below the runner user's home, the records root and each folder of this tool that stayed: `-xdev -uid N -print0 -execdir chown -h root:root`, one file system, each link itself and never its target, `chown` handed the name in its own folder (never a whole path through folders another user may own); in `/tmp`, `/var/tmp` and `/dev/shm` only the exact paths listed, `-maxdepth 0 ! -type l`, each read once more as not a link. Each hand read back with the same `find` and no `-exec`; what is still of that number stops the leave before `userdel`, a link aside (named). Nothing else changes owner (R136) | nothing |
| `hand_home_to_root` | `find` | no | no: after `userdel`, the runner user's home itself when it stays: `-maxdepth 0 -nouser -execdir chown -h root:root`, a folder in `/home`, which no job can replace, taken only while its number has no user; read back, and run again by a later run that finds that home (R136) | nothing |

The one place a root step makes something the runner user can write, outside its home, is each repository's records
folder: the job hooks run as the runner user and write that repository's job records there. Nothing in it is ever
run, sourced or followed by root: at uninstall the runner user deletes what it made there, and root removes the folder only once it is empty. What the runner user did not
make stays, named in the census.

Steps run as you, not as root, that read the whole system: `package_list` runs `dpkg-query -W` (or `rpm -qa` where
there is no `dpkg-query`) just before and just after the runner's dependency script, to read which system packages
were added while that script ran (two lists tell what was added in that time, not who added it). It reads the
package list only and changes nothing (R139). The packages it names stay when the tool leaves, named in the census,
because another program may use them by then.

No service of this tool runs as root. The runner's unit runs as the runner user. Its drop-in's `ExecStopPost` has
no `+`, so it runs as the runner user too. The watch runs as you.

## The job hooks

The two hooks are `job-started.sh` and `job-completed.sh`, in `/usr/local/lib/windstorm-repo-minions/<repository>/`.
Root owns them (0755) and the folders above them, so no job can rewrite a hook.

What a job can do is change which hook runs. The runner names its hooks in its own `.env` file, and the runner user
owns that file. A job can blank the start hook there; the runner reads `.env` again when it restarts. Seen on a stock
Ubuntu 24.04 virtual machine on 2026-11-24: after that, the next job ran with no start hook.

So the start hook refuses a stranger's job, before any step, only on a runner where no job has tampered with it. A
stranger's first job never reaches a step, so a stranger cannot switch the hook off. Anyone whose job has already
run here can switch it off, and gains nothing by doing so: they could already run code here.

The gate a job cannot touch is the watch. It runs as you, from root's copy of this tool. When a repository is no
longer private, the watch takes our labels off and deletes the runner on GitHub's side once no job runs. A listener
started by hand, outside the service, stops by itself once GitHub has deleted its runner.

So if someone who could push has switched the hook off, then between a turn to public and the watch's next pass (up
to five minutes, longer while GitHub cannot be reached) nothing on this computer refuses a job, and a job already
running finishes. Seen on a stock Ubuntu 24.04 virtual machine on 2026-11-24, with the privacy read made to answer
"public": the hook blanked, a listener started by hand outside the service; the watch's next pass took the labels
off and deleted the runner, a job sent after it waited with no runner, and the hand-started listener ended.
