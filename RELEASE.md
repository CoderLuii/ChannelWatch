# Release Process

Use this process whenever a change is intended to ship publicly.

## Required Outcome

Every release must finish with all of these aligned:

- `main` contains the release changes.
- The release lands as one commit.
- The release commit subject is exactly the release tag, for example `v0.8.1`.
- The git tag uses the same value as the commit subject.
- The GitHub Release uses the same tag and title.
- README version history is updated.
- Release notes are published on GitHub.

Do not stop after opening or merging a pull request. The release is not complete until the GitHub Release page shows the new version as latest, unless the release is intentionally marked as a prerelease.

## Public Writing Rules

- Write all public text as maintainer-authored copy.
- Do not include tool attribution, tool labels, or hidden attribution.
- Keep branch names neutral and descriptive, such as `dependency-refresh` or `release-v0.8.1`.
- Keep release notes user-facing: what changed, compatibility notes, validation, and upgrade instructions.

## Commit Rule

Release commits use only the release tag as the subject:

```text
v0.8.1
```

Do not use descriptive release commit subjects such as `Update dependencies`, `Release v0.8.1`, or `Keep dependency baselines current`.

## Checklist

1. Update the code and docs needed for the release.
2. Update README version history.
3. Run the relevant validation for the changed areas.
4. Commit all release changes as one commit with the subject set to the release tag.
5. Push `main`.
6. Create the matching git tag and GitHub Release.
7. Mark the release as latest for stable releases.
8. Verify the GitHub Releases page shows the new version.

## Command Pattern

```bash
git switch main
git pull --ff-only

# Make release changes and run validation.

git add .
git commit -m "v0.8.1"
git push origin main

gh release create v0.8.1 \
  --target main \
  --title "v0.8.1" \
  --notes-file release-notes.md \
  --latest
```

If a pull request is required, squash merge it so the resulting `main` commit subject is exactly the release tag, then create the matching GitHub Release immediately after merge.
