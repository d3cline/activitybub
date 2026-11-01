import importlib.util
import sys
from pathlib import Path

import yaml


ROOT=Path(__file__).resolve().parents[1]


def test_frozen_backend_probe_uses_app_flag_not_python_c(monkeypatch):
    from activity_bub import desktop
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(sys,"frozen",True,raising=False)
    monkeypatch.setattr(desktop.subprocess,"run",lambda command,**kwargs:calls.append(command) or SimpleNamespace(returncode=0))
    assert desktop.xcb_available({"DISPLAY":":0"})
    assert calls[0]==[sys.executable,"--_probe-xcb"]


def test_source_package_includes_workflows_icons_and_no_local_data():
    spec=importlib.util.spec_from_file_location("build_release",ROOT/"tools/build_release.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    paths={str(relative).replace('\\','/') for _,relative in module.source_files()}
    assert {".github/workflows/release.yml","activity_bub/assets/activity-bub.ico","LICENSE","SECURITY.md"}<=paths
    assert not any(".venv/" in p or ".sqlite3" in p or p.startswith("build/") for p in paths)


def test_workflows_are_valid_and_publishing_is_draft_only():
    for path in (ROOT/".github/workflows").glob("*.yml"):
        data=yaml.safe_load(path.read_text())
        assert data["permissions"]["contents"]=="read"
        assert data["jobs"]
    release=(ROOT/".github/workflows/release.yml").read_text()
    assert "--draft" in release and "Refusing to overwrite a published release" in release
