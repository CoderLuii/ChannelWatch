# ChannelWatch Agent Instructions

These instructions apply to the ChannelWatch repository.

## Public Text

- Public repository text must read as maintainer-authored.
- Do not include tool attribution, tool labels, or hidden attribution in branch names, PR titles, PR bodies, commit messages, release notes, changelogs, or docs.
- Use neutral branch names such as `dependency-refresh` or `release-v0.8.1`.

## Release Work

- Follow `RELEASE.md` whenever a change is intended to ship.
- Release work is complete only when the code/docs update, the single release commit, the pushed `main` branch, the git tag, and the GitHub Release all point to the same release.
- Land release changes as exactly one commit on `main`.
- The release commit subject must be exactly the release tag, for example `v0.8.1`.
- Do not add trailers, prefixes, suffixes, or tool labels to release commit messages.
- If a pull request is used, squash merge it with the subject set to the release tag and delete the release branch after merge.
- Update README version history and GitHub Release notes in the same release flow.
