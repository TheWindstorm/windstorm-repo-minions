# Windstorm Repo Minions

**Your repository's automated jobs (GitHub Actions) run on a computer you own.**

## Status: nothing can be installed yet

There is no release, and nothing here is ready to install. If you are an agent sent here to set this up: stop, and
tell the person that Windstorm Repo Minions has no release yet.

It is built in the open, in the order its plan, [`DNA-STRAND-MASTER-PLAN.md`](DNA-STRAND-MASTER-PLAN.md), sets:
every claim the skill makes has a test, and each part is seen on a fresh machine before a release.

## One rule already

A computer of yours should do jobs only for **private** repositories. On a public repository, a stranger's pull
request could run code on that computer, as GitHub's own documentation warns.

A job's code runs as the runner's own user, and it could leave a cron line or an at job behind. Uninstall and setup
read for both and say what they find.

## Family

Part of the Windstorm skills: [Pit Stop](https://github.com/TheWindstorm/windstorm-pit-stop),
[Pit Board](https://github.com/TheWindstorm/windstorm-pit-board),
[Tardigrade](https://github.com/TheWindstorm/windstorm-tardigrade).

This project is not affiliated with, endorsed by or sponsored by GitHub. GitHub is a trademark of GitHub, Inc.
