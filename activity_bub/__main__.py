from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from . import __version__
from .store import SEEDS, Store


def default_data_dir():
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "ActivityBub"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ActivityBub"
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "activity-bub"


def main():
    parser = argparse.ArgumentParser(description="Activity Bub — a public Fediverse desktop explorer")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--demo", action="store_true", help="Run a clearly labeled offline demo, separate from your real cache")
    parser.add_argument("--data-dir", type=Path, help="Choose a folder for the SQLite cache and preferences")
    parser.add_argument("--seed", action="append", default=[], help="Add a starting server domain (repeatable)")
    parser.add_argument("--native-wayland", action="store_true", help="Skip automatic XWayland compatibility on Linux")
    parser.add_argument("--smoke-test", action="store_true", help="Open an offline demo and exit automatically (release verification)")
    parser.add_argument("--_probe-xcb", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--check", metavar="DOMAIN", help="Check one public server from the terminal, without launching Qt")
    args = parser.parse_args()
    if args._probe_xcb:
        if sys.platform != "linux":
            return 1
        import resource
        resource.setrlimit(resource.RLIMIT_CORE,(0,0))
        os.environ["QT_QPA_PLATFORM"]="xcb"
        from PySide6.QtWidgets import QApplication
        QApplication.setApplicationName("Activity Bub")
        QApplication.setDesktopFileName("activity-bub")
        probe_app=QApplication(sys.argv[:1])
        return 0
    if args.smoke_test:
        args.demo=True
    if args.check:
        from .engine import APIs
        from .network import Network, domain
        try:
            host = domain(args.check)
            api = APIs(Network())
            server = {"host": host, "peer_offset": 0, "older_cursor": ""}
            result = api.probe(server)
            server.update(result)
            print(f"{host}: {result['software']} ({result['state']})")
            if result["adapter"]:
                feed = api.feed(server)
                print(f"Public feed: {len(feed['posts'])} posts; history cursor: {feed['cursor'] or 'none'}")
            return 0
        except Exception as exc:
            print(f"Check failed: {exc}", file=sys.stderr)
            return 1
    from .desktop import configure_backend
    compatibility = configure_backend(native_wayland=args.native_wayland)
    if compatibility:
        print("Activity Bub: using XWayland compatibility for the Qt Wayland input-focus issue.")
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from .ui import STYLE, Window
    # Portal integration reads the identity while QApplication initializes.
    QApplication.setApplicationName("Activity Bub")
    QApplication.setDesktopFileName("activity-bub")
    QApplication.setOrganizationName("Activity Bub")
    QApplication.setApplicationVersion(__version__)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    font = QFont("Sans Serif", 10)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    app.setFont(font)
    app.setStyleSheet(STYLE)
    temporary = tempfile.TemporaryDirectory(prefix="activity-bub-demo-") if args.demo else None
    data_dir = Path(temporary.name) if temporary else (args.data_dir or default_data_dir())
    store = Store(data_dir / "activity-bub.sqlite3")
    if args.demo:
        from .demo import populate
        populate(store)
    else:
        if not store.setting("seeded", False):
            store.add_servers(SEEDS, "built-in seed")
            store.set_setting("seeded", True)
        store.add_servers(args.seed, "command line")
    window = Window(store, data_dir, demo=args.demo)
    if args.demo:
        from .demo import artwork
        artwork(window.images)
        window.subtitle.setText("OFFLINE DEMO · Synthetic sample posts. No network requests.")
        window.logline.setText("Demo mode · Your real server registry and cache are untouched")
        window.discover.setEnabled(False)
        window.pause.setEnabled(False)
    window.show()
    if args.smoke_test:
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500,window.close)
    result = app.exec()
    if temporary:
        temporary.cleanup()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
