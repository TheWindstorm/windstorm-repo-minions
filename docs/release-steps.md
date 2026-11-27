# Release steps

The road a version takes from its release head to its release page, in order. The maintainer runs each step by hand
and reads each step's own output before the next one. Nothing in this list runs by itself.

1. **The suite with the tag at its head** (row R219), before the tag exists anywhere else:

       python3 scripts/tag_head_suite.py vX.Y.Z COMMIT

   It makes a fresh clone of this repository with none of its tags, makes the annotated tag vX.Y.Z on COMMIT in
   that clone only, and checks the tag out. It then runs every test file to its end, each in its own process, and
   then the gate (`tests/test_strand.py`). Go on only when its last line starts with `ok: the whole suite passed`.
   A line `STOP:` names what failed and the clone it kept to read. Nothing was tagged or pushed.
2. **The annotated tag, by hand, pushed alone:**

       git tag -a vX.Y.Z -m "vX.Y.Z" COMMIT
       git push origin vX.Y.Z

   Read it back: `git ls-remote origin refs/tags/vX.Y.Z`, and the tag peels to COMMIT.
3. **The Agent Skills check:** `gh skill publish --dry-run` is the gate. `gh skill publish --tag vX.Y.Z` is not
   used: with gh 2.105.0 it refused a tag that already stood and exited 1 (seen 2026-11-26).
4. **The release page,** made against the standing tag. FILE is this version's section of `CHANGELOG.md`, cut
   whole by a script and checked as one unbroken slice of it:

       gh release create vX.Y.Z --repo TheWindstorm/windstorm-repo-minions --verify-tag --target FULL_COMMIT_SHA \
         --title "vX.Y.Z" --notes-file FILE

   It is not a draft and has no generated notes. Read it back: its tag, its target commit, draft and prerelease both
   false, and the body's sha256 against FILE's.
5. **Main's pin:** `git checkout vX.Y.Z -- README.md CHANGELOG.md` on a branch whose name does not start with
   `release-`, into main by pull request. The pull request's release-place check then reads that every clone line
   names a tag that stands.
