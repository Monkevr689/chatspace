"""Tests for the point-and-click app (pubmed_app.py), against the fake NCBI."""

import csv
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import pubmed_app  # noqa: E402
import pubmed_dl  # noqa: E402
from fake_eutils import FakePubMed, make_articles  # noqa: E402


class AppTestCase(unittest.TestCase):
    articles = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.fake = FakePubMed(dict(self.articles or make_articles(50)))
        self.fake.__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        for patch in (mock.patch.dict(os.environ, {"PUBMED_EUTILS_URL": self.fake.url}),
                      mock.patch.object(pubmed_dl.EUtils, "_throttle", lambda self: None)):
            patch.start()
            self.addCleanup(patch.stop)
        self.app = pubmed_app.App(home=self.home)
        thread = threading.Thread(target=self.app.serve, kwargs={"open_browser": False}, daemon=True)
        with mock.patch("builtins.print"):
            thread.start()
            for _ in range(200):
                if self.app.server and self.app.instance_file.exists():
                    break
                time.sleep(0.01)
        self.addCleanup(lambda: (self.app.server.shutdown(), thread.join(5)))
        self.base = f"http://127.0.0.1:{self.app.port}"

    def call(self, path, body=None, token=None, host=None):
        headers = {"X-Token": self.app.token if token is None else token}
        if host:
            headers["Host"] = host
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if path.startswith("/api") else raw)
        except urllib.error.HTTPError as err:
            return err.code, json.loads(err.read() or b"{}")

    def wait_for_job(self, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = self.call("/api/state")[1]["job"]
            if job and job["state"] != "running":
                return job
            time.sleep(0.05)
        self.fail("job did not finish")

    def download(self, **body):
        body = {"query": "tumor", "detail": "standard", **body}
        status, job = self.call("/api/download", body)
        self.assertEqual(status, 200, job)
        return self.wait_for_job()


class SecurityTests(AppTestCase):
    def test_page_is_served_without_token(self):
        status, html = self.call("/", token="")
        self.assertEqual(status, 200)
        self.assertIn(b"PubMed Downloader", html)

    def test_api_needs_token_and_local_host(self):
        self.assertEqual(self.call("/api/state", token="")[0], 403)
        self.assertEqual(self.call("/api/state", token="wrong")[0], 403)
        self.assertEqual(self.call("/api/delete", {"folder": "x"}, token="")[0], 403)
        self.assertEqual(self.call("/api/state", host="evil.example:80")[0], 403)
        self.assertEqual(self.call("/", host=f"evil.example:{self.app.port}")[0], 403)
        self.assertEqual(self.call("/api/state")[0], 200)

    def test_paths_stay_inside_the_library(self):
        self.download(name="Safe")
        for name in ("../Safe", "..", "", ".hidden", "Safe/../Safe", "/etc"):
            status, reply = self.call("/api/delete", {"folder": name})
            self.assertEqual(status, 400, name)
        self.assertTrue((self.app.library / "Safe").is_dir())
        status, _ = self.call("/api/reveal", {"folder": "Safe", "export": "../manifest.json"})
        self.assertEqual(status, 400)

    def test_settings_file_is_private(self):
        status, _ = self.call("/api/settings", {"api_key": "abcdef0123456789abcdef0123", "email": "a@b.c"})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("/api/settings")[1]["api_key"], "abcdef0123456789abcdef0123")
        self.assertEqual(self.app.settings_path.stat().st_mode & 0o777, 0o600)
        status, reply = self.call("/api/settings", {"api_key": "not a key!"})
        self.assertEqual(status, 400)


class FlowTests(AppTestCase):
    def test_count(self):
        status, reply = self.call("/api/count", {"query": "tumor"})
        self.assertEqual((status, reply["count"]), (200, 50))
        status, reply = self.call("/api/count", {"query": "tumor", "from": "2016", "to": "2017"})
        self.assertLess(reply["count"], 50)
        self.assertEqual(self.call("/api/count", {"query": " "})[0], 400)
        self.assertEqual(self.call("/api/count", {"query": "tumor", "from": "20x6"})[0], 400)

    def test_download_browse_and_library(self):
        job = self.download(name="Tumor/growth: study", max=30, detail="minimal")
        self.assertEqual(job["state"], "done", job)
        self.assertEqual(job["result"]["records"], 30)
        items = self.call("/api/library")[1]["items"]
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["folder"], "Tumor growth study")
        self.assertEqual((item["records"], item["complete"], item["query"]), (30, True, "tumor"))
        self.assertEqual(item["fields"], pubmed_dl.FIELD_PRESETS["minimal"])

        found = self.call("/api/papers?folder=Tumor+growth+study&q=%22Study+49+%22")[1]
        self.assertEqual([p["title"] for p in found["papers"]], ["Study 49 of tumor growth"])
        everything = self.call("/api/papers?folder=Tumor+growth+study&limit=10")[1]
        self.assertEqual((len(everything["papers"]), everything["more"]), (10, True))

        # Same name again gets its own folder.
        self.download(name="Tumor/growth: study", max=5)
        names = sorted(i["folder"] for i in self.call("/api/library")[1]["items"])
        self.assertEqual(names, ["Tumor growth study", "Tumor growth study 2"])

    def test_exports(self):
        self.download(name="T")
        status, _ = self.call("/api/export", {"folder": "T", "kind": "ai", "size": "small"})
        self.assertEqual(status, 200)
        job = self.wait_for_job()
        self.assertEqual(job["state"], "done", job)
        result = job["result"]
        self.assertEqual(result["papers"], 50)
        exports = self.app.library / "T" / "exports"
        md = (exports / result["export"] / result["files"][0]).read_text(encoding="utf-8")
        self.assertTrue(md.startswith("# T (part 1)"))
        self.assertIn("PubMed search: tumor", md)
        self.assertIn("\n## Study ", md)
        with zipfile.ZipFile(exports / result["zip"]) as zf:
            self.assertEqual(len(zf.namelist()), len(result["files"]))

        self.call("/api/export", {"folder": "T", "kind": "csv", "filter": '"Study 1"'})
        job = self.wait_for_job()
        self.assertEqual(job["state"], "done", job)
        raw = (exports / job["result"]["export"]).read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))  # so Excel reads it as UTF-8
        rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
        self.assertEqual(len(rows), job["result"]["papers"])
        self.assertTrue(all(r["title"].startswith("Study 1") for r in rows))

        self.call("/api/export", {"folder": "T", "kind": "ai", "filter": "zebrafish"})
        job = self.wait_for_job()
        self.assertEqual((job["state"], job["message"]), ("failed", "No papers matched those words."))
        # Same-second exports get their own names; the failed one leaves nothing.
        self.assertEqual(len(self.call("/api/library")[1]["items"][0]["exports"]), 3)
        self.call("/api/export", {"folder": "T", "kind": "ai"})
        self.assertEqual(self.wait_for_job()["state"], "done")
        self.assertEqual(len(self.call("/api/library")[1]["items"][0]["exports"]), 5)

    def test_no_results_leaves_nothing_behind(self):
        job = self.download(query="zebrafish", name="Fish")
        self.assertEqual(job["state"], "failed")
        self.assertIn("No papers matched", job["message"])
        self.assertFalse((self.app.library / "Fish").exists())

    def test_delete_and_reveal(self):
        self.download(name="Gone")
        with mock.patch("pubmed_app.reveal", return_value=None) as reveal:
            self.assertEqual(self.call("/api/reveal", {"folder": "Gone"})[0], 200)
            reveal.assert_called_once_with(self.app.library / "Gone")
            self.call("/api/reveal", {})
            reveal.assert_called_with(self.app.library)
        self.assertEqual(self.call("/api/delete", {"folder": "Gone"})[0], 200)
        self.assertEqual(self.call("/api/library")[1]["items"], [])

    def test_finds_running_instance(self):
        self.assertEqual(pubmed_app.find_running(pubmed_app.App(home=self.home)),
                         f"{self.base}/#token={self.app.token}")
        self.assertIsNone(pubmed_app.find_running(pubmed_app.App(home=self.home / "other")))


class StopResumeTests(AppTestCase):
    articles = make_articles(2500)

    def test_stop_then_resume(self):
        real = pubmed_dl.EUtils.efetch
        gate, reached = threading.Event(), threading.Event()
        calls = []

        def slow(client, pmids):
            calls.append(pmids)
            if len(calls) == 6:  # first file (5 x 200) is saved by now
                reached.set()
                gate.wait(10)
            return real(client, pmids)

        with mock.patch.object(pubmed_dl.EUtils, "efetch", slow):
            self.call("/api/download", {"query": "tumor", "name": "Big", "detail": "minimal"})
            self.assertTrue(reached.wait(10))
            status, reply = self.call("/api/download", {"query": "tumor", "name": "Other"})
            self.assertEqual(status, 400)
            self.assertIn("already running", reply["error"])
            self.call("/api/cancel", {})
            gate.set()
            job = self.wait_for_job()
        self.assertEqual(job["state"], "cancelled")
        item = self.call("/api/library")[1]["items"][0]
        self.assertEqual((item["complete"], item["fetched"], item["pmids"]), (False, 1000, 2500))
        self.assertFalse(any(p.name.endswith(".tmp") for p in (self.app.library / "Big").iterdir()))
        self.assertFalse((self.app.library / "Other").exists())

        self.call("/api/resume", {"folder": "Big"})
        job = self.wait_for_job()
        self.assertEqual(job["state"], "done", job)
        item = self.call("/api/library")[1]["items"][0]
        self.assertEqual((item["complete"], item["records"]), (True, 2500))
        self.assertEqual(len(self.fake.fetched), 6 + 8)  # only the missing 1,500 again


if __name__ == "__main__":
    unittest.main()
