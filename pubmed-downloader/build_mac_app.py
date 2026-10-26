#!/usr/bin/env python3
"""Builds PubMed-Downloader-Mac.zip, the double-clickable Mac app.

The app is a standard .app bundle: a small launcher script (mac/launcher.sh)
that finds Python and starts pubmed_app.py, with the Python files inside it.
Building needs nothing Mac-specific, so this runs anywhere:

    python3 build_mac_app.py

Rebuild whenever pubmed_dl.py, pubmed_app.py, pubmed_app.html or mac/ change
(tests/test_mac_app.py fails until you do).
"""

import stat
import sys
import zipfile
from pathlib import Path

import pubmed_dl

HERE = Path(__file__).resolve().parent
ZIP_NAME = "PubMed-Downloader-Mac.zip"
APP = "PubMed Downloader.app"
RESOURCES = ("pubmed_dl.py", "pubmed_app.py", "pubmed_app.html")
FIXED_TIME = (2025, 1, 1, 0, 0, 0)  # same input, same zip


def entries():
    """(path in zip, contents, unix mode) for every file in the app."""
    mac = HERE / "mac"
    plist = (mac / "Info.plist").read_text(encoding="utf-8").replace("{version}", pubmed_dl.__version__)
    yield "READ ME FIRST.txt", (mac / "READ ME FIRST.txt").read_bytes(), 0o644
    yield f"{APP}/Contents/Info.plist", plist.encode("utf-8"), 0o644
    yield f"{APP}/Contents/PkgInfo", b"APPL????", 0o644
    yield f"{APP}/Contents/MacOS/PubMedDownloader", (mac / "launcher.sh").read_bytes(), 0o755
    yield f"{APP}/Contents/Resources/AppIcon.icns", (mac / "AppIcon.icns").read_bytes(), 0o644
    for name in RESOURCES:
        yield f"{APP}/Contents/Resources/{name}", (HERE / name).read_bytes(), 0o644


def build(path):
    files = list(entries())
    folders = sorted({str(Path(name).parent) for name, _, _ in files} - {"."})
    folders = sorted({str(p) for f in folders for p in [Path(f), *Path(f).parents]} - {"."})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for folder in folders:
            info = zipfile.ZipInfo(folder + "/", FIXED_TIME)
            info.create_system = 3  # unix, so the modes below are honoured
            info.external_attr = (stat.S_IFDIR | 0o755) << 16 | 0x10
            zf.writestr(info, b"")
        for name, data, mode in files:
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return path


if __name__ == "__main__":
    out = build(HERE / ZIP_NAME)
    print(f"Built {out.name} ({out.stat().st_size // 1024} KB)")
    sys.exit(0)
