"""Choose a usable desktop backend before Qt initializes."""
from __future__ import annotations

import os
import subprocess
import sys


def xcb_available(environ):
    """Probe in a disposable process: a missing Qt plugin can abort the process."""
    child_env = dict(environ)
    child_env["QT_QPA_PLATFORM"] = "xcb"
    code = (
        "import resource; resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); "
        "from PySide6.QtWidgets import QApplication; "
        "QApplication.setApplicationName('Activity Bub'); "
        "QApplication.setDesktopFileName('activity-bub'); "
        "app=QApplication([])"
    )
    try:
        command = [sys.executable, "--_probe-xcb"] if getattr(sys,"frozen",False) else [sys.executable,"-c",code]
        result = subprocess.run(command, env=child_env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=6, check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def configure_backend(native_wayland=False, environ=None, platform=None, probe=None):
    env = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if not platform.startswith("linux") or env.get("QT_QPA_PLATFORM") or native_wayland:
        return ""
    wayland = env.get("WAYLAND_DISPLAY") or env.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    if wayland and env.get("DISPLAY") and (probe or xcb_available)(env):
        env["QT_QPA_PLATFORM"] = "xcb"
        return "XWayland compatibility"
    return ""
