# Release a version

1. Update `activity_bub/__init__.py`, `pyproject.toml`, and `CHANGELOG.md` together.
2. Run the tests and `python tools/make_icons.py` if the icon changed.
3. Run `python tools/build_release.py` on each target OS. Linux/macOS cannot
   cross-compile the Windows app; use the Windows GitHub runner for Windows.
4. Check the portable executable on a real desktop, including image loading,
   resizing, themes, the passport, and your compositor/input method.
5. Push a `vX.Y.Z` tag matching the package version. The release workflow tests
   both native builds and creates a **draft** GitHub Release with artifacts.
6. Inspect the draft, checksums, build information, license files, and real-desktop
   results, then publish it when ready.

## Start the GitHub repository

The source archive contains the complete repository tree, including workflows.
Extract it, then use GitHub's normal new-repository flow or the GitHub CLI:

```bash
git init -b main
git add .
git commit -m "Release Activity Bub 0.5.1"
gh repo create activity-bub --public --source . --push
git tag v0.5.1
git push origin v0.5.1
```

These commands publish a new public repository under the signed-in account.
Choose `--private` instead if a private repository is intended. This package
does not execute those commands for you.

Source-only packaging: `python tools/build_release.py --source-only`.
Use `--output release/v0.5.1` to stage a version separately, and `--no-isolation`
only when the required build dependencies are already installed.
Platform builds go in `release/`; do not commit generated binaries or user data.
CI uses read-only repository access. Only the draft-release job receives write
access to releases; pull requests cannot publish a release.
