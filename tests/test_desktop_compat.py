import os
import subprocess
from types import SimpleNamespace

import pytest

from activity_bub.desktop import configure_backend, xcb_available


def test_wayland_uses_xcb_only_after_successful_probe():
    env = {"WAYLAND_DISPLAY":"wayland-0", "DISPLAY":":0"}
    calls = []
    assert configure_backend(environ=env, platform="linux", probe=lambda e: calls.append(dict(e)) or True)
    assert env["QT_QPA_PLATFORM"] == "xcb" and len(calls) == 1
    assert "QT_QPA_PLATFORM" not in calls[0]


@pytest.mark.parametrize("env,native,platform", [
    ({"QT_QPA_PLATFORM":"wayland", "DISPLAY":":0", "WAYLAND_DISPLAY":"w"},False,"linux"),
    ({"DISPLAY":":0", "WAYLAND_DISPLAY":"w"},True,"linux"),
    ({"WAYLAND_DISPLAY":"w"},False,"linux"),
    ({"DISPLAY":":0"},False,"linux"),
    ({"DISPLAY":":0", "WAYLAND_DISPLAY":"w"},False,"win32"),
])
def test_explicit_backend_native_option_and_other_sessions_unchanged(env,native,platform):
    before = dict(env)
    def no_probe(_): raise AssertionError("Unexpected XWayland probe")
    assert not configure_backend(native_wayland=native,environ=env,platform=platform,probe=no_probe)
    assert env == before


def test_failed_xcb_probe_preserves_working_wayland_selection():
    env = {"DISPLAY":":0", "XDG_SESSION_TYPE":"wayland"}
    assert not configure_backend(environ=env,platform="linux",probe=lambda e:False)
    assert "QT_QPA_PLATFORM" not in env


def test_xcb_probe_is_isolated_and_does_not_mutate_parent(monkeypatch):
    from activity_bub import desktop
    captured = {}
    def run(args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(desktop.subprocess,"run",run)
    env = {"DISPLAY":":0"}
    assert xcb_available(env)
    assert env == {"DISPLAY":":0"}
    assert captured["env"]["QT_QPA_PLATFORM"] == "xcb"
    assert captured["timeout"] <= 6


def test_xcb_probe_timeout_and_missing_libraries_are_recoverable(monkeypatch):
    from activity_bub import desktop
    monkeypatch.setattr(desktop.subprocess,"run",lambda *a,**kw:SimpleNamespace(returncode=-6))
    assert not xcb_available({})
    def timeout(*args,**kwargs): raise subprocess.TimeoutExpired("probe",6)
    monkeypatch.setattr(desktop.subprocess,"run",timeout)
    assert not xcb_available({})
