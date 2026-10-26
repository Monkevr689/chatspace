"""Checks the Mac app zip: up to date with the code, launcher runnable."""

import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import build_mac_app  # noqa: E402


def contents(path):
    with zipfile.ZipFile(path) as zf:
        return {i.filename: (zf.read(i), i.external_attr >> 16) for i in zf.infolist()}


class MacAppTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fresh = contents(build_mac_app.build(Path(tmp.name) / "app.zip"))

    def test_committed_zip_is_up_to_date(self):
        committed = contents(ROOT / build_mac_app.ZIP_NAME)
        self.assertEqual(sorted(committed), sorted(self.fresh))
        stale = [name for name in self.fresh if committed[name] != self.fresh[name]]
        self.assertEqual(stale, [], "run: python3 build_mac_app.py")

    def test_bundle_layout(self):
        app = build_mac_app.APP
        plist = plistlib.loads(self.fresh[f"{app}/Contents/Info.plist"][0])
        executable = f"{app}/Contents/MacOS/{plist['CFBundleExecutable']}"
        self.assertEqual(self.fresh[executable][1] & 0o777, 0o755)
        self.assertTrue(self.fresh[f"{app}/Contents/Resources/{plist['CFBundleIconFile']}.icns"][0]
                        .startswith(b"icns"))
        for name in build_mac_app.RESOURCES:
            self.assertIn(f"{app}/Contents/Resources/{name}", self.fresh)

    @unittest.skipUnless(shutil.which("bash"), "needs bash")
    def test_launcher_syntax(self):
        subprocess.run(["bash", "-n", str(ROOT / "mac" / "launcher.sh")], check=True)


if __name__ == "__main__":
    unittest.main()
