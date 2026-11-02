# Activity Bub portable edition

Extract the entire archive. Keep the executable beside its `_internal` directory.
No Python installation or pip download is needed.

- Linux: run `./Activity-Bub` (make it executable with `chmod +x Activity-Bub` if needed).
- Windows: double-click `Activity-Bub.exe`. This release is unsigned; build from
  source if you prefer to inspect and compile the application yourself.
- `--demo` opens offline sample posts. `--smoke-test` opens them briefly and exits.
- Linux Wayland sessions use the checked XWayland compatibility path when
  available. `--native-wayland` skips that selection.

Your database stays in your normal user data folder, outside this application
folder. Closing Activity Bub and replacing the whole extracted application
folder upgrades the app while retaining your bookmarks, themes, and passport.

Read `THIRD_PARTY_NOTICES.md` and `LICENSES/` for bundled dependencies. Shared Qt
libraries are kept as separate files under `_internal`, so compatible replacement
libraries can be used. `BUILD-INFO.json` records the exact build versions.

The GitHub Linux workflow builds on Ubuntu 22.04; glibc 2.35 or newer is required.
Other Linux release files may have a newer minimum; consult BUILD-INFO.json and
the release notes. Windows builds target Windows 10/11 x86-64. Native desktop
testing beyond the automated smoke check remains a release-maintainer task.

The locally built 0.4.0 Linux archive requires glibc 2.39 or newer. If your
desktop reports a missing `libxcb-cursor.so.0`, install your distribution's
xcb-cursor package. Headless smoke tests cannot verify the compositor libraries
provided by your desktop.

Security update: remote previews require the restricted Linux image worker.
Windows and macOS currently browse text with remote previews disabled; no
in-process image fallback is used. Unsupported Linux architectures or blocked
seccomp setup also fail closed. Original/media links open in your external
browser and leave this protection boundary. See the source package's
`docs/SECURITY-AUDIT.md` for the threat model, limits, and remaining risks.

Auto Doom, speed, and Auto CW are in the feed footer. D toggles scrolling;
manual scrolling or opening a post pauses it. Auto CW reveals flagged content
and permits its preview loading; Hide CW still filters such posts out. Speed
and Auto CW persist, but scrolling does not restart automatically on launch.
