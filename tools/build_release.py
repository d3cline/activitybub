"""Build a portable native app, verified source archive, wheel, and sdist."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import ssl
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from activity_bub import __version__

EXCLUDE={".git",".venv","__pycache__",".pytest_cache","build","dist","release",".mypy_cache",".ruff_cache"}


def source_files():
    for path in sorted(ROOT.rglob("*")):
        relative=path.relative_to(ROOT)
        if not path.is_file() or any(part in EXCLUDE or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.suffix in {".pyc",".pyo",".sqlite3",".sqlite",".db",".bundle",".pem",".key",".spec"} or ".sqlite3-" in path.name or path.name==".env" or path.name.startswith(".env."):
            continue
        yield path,relative


def source_archive(destination):
    with zipfile.ZipFile(destination,"w",zipfile.ZIP_DEFLATED) as archive:
        for path,relative in source_files():
            archive.write(path,"Activity-Bub/"+relative.as_posix())


def collect_licenses(destination):
    folder=destination/"LICENSES"
    folder.mkdir(exist_ok=True)
    for name in ("PySide6_Essentials","shiboken6","psutil","Pillow","pyinstaller"):
        package=importlib.metadata.distribution(name)
        found=False
        for entry in package.files or []:
            if "licenses" in entry.parts or entry.name.lower().startswith(("license","copying")):
                source=Path(package.locate_file(entry))
                if source.is_file():
                    target=folder/name/Path(*entry.parts)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(source,target)
                    found=True
        if not found:
            fallback=ROOT/"third_party"/"licenses"
            if not fallback.is_dir():raise RuntimeError(f"No license files found for {name}")
            shutil.copytree(fallback,folder/name,dirs_exist_ok=True)
    shutil.copytree(ROOT/"third_party"/"licenses",folder/"runtime",dirs_exist_ok=True)
    for name in ("LICENSE","THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT/name,destination/name)


def binary_archive(app_dir,destination):
    if destination.suffix==".zip":
        with zipfile.ZipFile(destination,"w",zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(app_dir.rglob("*")):
                if path.is_file():archive.write(path,Path("Activity-Bub")/path.relative_to(app_dir))
    else:
        with tarfile.open(destination,"w:gz",compresslevel=9) as archive:
            archive.add(app_dir,arcname="Activity-Bub")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--source-only",action="store_true")
    parser.add_argument("--no-python",action="store_true",help="Skip duplicate wheel/sdist on a second build platform")
    parser.add_argument("--no-isolation",action="store_true",help="Use already-installed Python build dependencies")
    parser.add_argument("--output",type=Path,help="Output folder inside release/ (default: release/)")
    args=parser.parse_args()
    release=args.output.resolve() if args.output else ROOT/"release"
    if release!=ROOT/"release" and ROOT/"release" not in release.parents:
        parser.error("--output must be inside the project's release/ folder")
    release.mkdir(parents=True,exist_ok=True)
    version=__version__
    if not args.no_python:
        source_archive(release/f"Activity-Bub-v{version}-source.zip")
        command=[sys.executable,"-m","build","--outdir",str(release)]
        if args.no_isolation:command.append("--no-isolation")
        subprocess.run(command,cwd=ROOT,check=True)
    if not args.source_only:
        if sys.platform not in ("linux","win32"):
            raise SystemExit("Native builds are configured for Linux and Windows only")
        # Fresh staging also avoids stale or partially restored symlinks from
        # earlier portable builds colliding during PyInstaller's COLLECT step.
        (ROOT/"build").mkdir(exist_ok=True)
        staging=Path(tempfile.mkdtemp(prefix="native-",dir=ROOT/"build"))
        command=[sys.executable,"-m","PyInstaller","--noconfirm","--clean","--onedir","--windowed",
                 "--distpath",str(staging/"dist"),"--workpath",str(staging/"work"),"--specpath",str(staging),
                 "--name","Activity-Bub","--collect-data","activity_bub",
                 "--exclude-module","tkinter","--exclude-module","PySide6.QtWebEngineCore",
                 "--exclude-module","PySide6.QtWebEngineWidgets","--exclude-module","PySide6.QtQml",
                 "--exclude-module","PySide6.QtQuick","--exclude-module","PIL.ImageQt",
                 "--exclude-module","PIL.ImageTk","--exclude-module","PIL.ImageShow"]
        # Some Python distributions load companion public-CA files from beside
        # ssl.py. Keep those data files with the frozen module, preserving its
        # existing TLS behavior. Never bundle private keys or alter trust checks.
        for companion in Path(ssl.__file__).parent.glob("*-cas.pem"):
            contents=companion.read_bytes()
            if b"PRIVATE KEY" in contents or not (b"BEGIN CERTIFICATE" in contents or b"BEGIN TRUSTED CERTIFICATE" in contents):
                raise RuntimeError("Unexpected non-certificate SSL companion file")
            command += ["--add-data",str(companion)+os.pathsep+"."]
        if sys.platform=="win32":
            command += ["--icon",str(ROOT/"activity_bub/assets/activity-bub.ico")]
        command.append(str(ROOT/"run_app.py"))
        subprocess.run(command,cwd=ROOT,check=True)
        app_dir=staging/"dist"/"Activity-Bub"
        collect_licenses(app_dir)
        shutil.copy2(ROOT/"docs/PORTABLE.md",app_dir/"READ-ME-FIRST.md")
        build_info={"application":version,"python":platform.python_version(),"platform":platform.platform(),
                    "libc":platform.libc_ver(),
                    "architecture":platform.machine(),"dependencies":{name:importlib.metadata.version(name)
                    for name in ("PySide6_Essentials","shiboken6","psutil","Pillow","pyinstaller")}}
        (app_dir/"BUILD-INFO.json").write_text(json.dumps(build_info,indent=2)+"\n")
        executable=app_dir/("Activity-Bub.exe" if sys.platform=="win32" else "Activity-Bub")
        env=dict(os.environ)
        env["QT_QPA_PLATFORM"]="offscreen"
        subprocess.run([str(executable),"--smoke-test"],env=env,cwd=app_dir,timeout=60,check=True)
        system="windows" if sys.platform=="win32" else "linux"
        arch=platform.machine().lower()
        arch={"amd64":"x86_64","aarch64":"arm64"}.get(arch,arch)
        suffix="zip" if sys.platform=="win32" else "tar.gz"
        destination=release/f"Activity-Bub-v{version}-{system}-{arch}.{suffix}"
        binary_archive(app_dir,destination)
    # Separate manifests allow Linux and Windows artifacts to merge without
    # accidentally overwriting a platform's checksum list.
    suffix="source" if args.source_only else ("windows" if sys.platform=="win32" else "linux")
    rows=[]
    for path in sorted(release.iterdir()):
        if path.is_file() and not path.name.endswith("SHA256SUMS.txt"):
            digest=hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b""):
                    digest.update(chunk)
            rows.append(digest.hexdigest()+"  "+path.name)
    (release/f"{suffix}-SHA256SUMS.txt").write_text("\n".join(rows)+"\n")
    print("Release files:",release)


if __name__=="__main__":
    main()
