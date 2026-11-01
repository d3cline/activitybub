# Third-party software

Activity Bub's application source and original sunglasses artwork are MIT
licensed. Portable builds also contain independently licensed dependencies.

| Component | License / source |
| --- | --- |
| Python | PSF License; https://www.python.org/downloads/source/ |
| PySide6 Essentials / Qt | LGPLv3/GPL/commercial options; https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://download.qt.io/official_releases/qt/ |
| Shiboken6 | LGPLv3/GPL/commercial options; same Qt for Python source tree |
| psutil | BSD 3-Clause; https://github.com/giampaolo/psutil |
| PyInstaller bootloader | GPLv2 or later with the bootloader exception; https://github.com/pyinstaller/pyinstaller |
| OpenSSL | Apache License 2.0; https://github.com/openssl/openssl |

Portable builds copy installed dependency and runtime license files into
`LICENSES/` and record exact
versions in `BUILD-INFO.json`. Qt/PySide libraries remain dynamically linked and
replaceable in the adjacent `_internal` directory. No restriction on modifying
or reverse engineering those libraries is imposed by the Activity Bub license.

Source archives do not bundle the dependencies. Developer-only tools such as
Pillow, pytest, and build are used to generate/test artifacts; their code is not
intentionally included in the portable app.
