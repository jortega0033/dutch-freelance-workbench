# Releasing

A release here is a tagged commit on `main`: this is a local-only application with no package registry, installer or hosted deployment. Only the owner releases; agents never tag, create a GitHub release or push to `main`.

## Steps (owner)

1. Check that `main` has a green CI run (both operating systems, both Python versions).
2. On a clean checkout of `main`, run the full suite from the repository root: `python -m unittest discover -s tests -t .` (baseline: 204 tests, 1 skipped).
3. For a release with user-visible changes, run the browser acceptance run named in `README.md` with external traffic blocked and record the result.
4. In `CHANGELOG.md`, rename `Unreleased` to the version and date, add a new empty `Unreleased` section, and merge that change through a pull request.
5. Tag the merge commit (`git tag -a vX.Y.Z -m "vX.Y.Z"`) and push the tag.
6. Create the GitHub release from the tag, with the changelog section as its notes.

A schema upgrade cannot be undone in a user's database: say so in the release notes.
