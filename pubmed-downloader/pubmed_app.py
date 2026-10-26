#!/usr/bin/env python3
"""PubMed Downloader app: a point-and-click front end for pubmed_dl.

Starts a small private web server on this computer (127.0.0.1 only, guarded
by a random token) and opens it in the default browser. Collections are
saved in ~/PubMed Library. On a Mac, the "PubMed Downloader" app runs this
for you; anywhere else run `python3 pubmed_app.py`.
"""

import argparse
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import webbrowser
from argparse import Namespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pubmed_dl

APP_NAME = "PubMed Downloader"
UI_FILE = Path(__file__).resolve().parent / "pubmed_app.html"
IDLE_EXIT_SECONDS = 20 * 60  # quit when no browser tab has checked in for this long
MAX_BODY = 1 << 20
EXPORT_SIZES = {"small": 20_000, "medium": 100_000, "large": 400_000}


class Cancelled(Exception):
    """The user pressed Stop."""


class AppError(Exception):
    """A problem to show the user as-is."""


class Job:
    """One download or export running in the background."""

    def __init__(self, kind, title, folder):
        self.kind = kind
        self.title = title
        self.folder = folder
        self.state = "running"  # running, done, failed, cancelled
        self.phase = "start"
        self.status = ""
        self.done = 0
        self.total = 0
        self.lines = []
        self.message = ""
        self.result = {}
        self.cancel_requested = False
        self.started = time.time()
        self.finished = None

    def add_line(self, line):
        line = line.strip("\n")
        if line.strip():
            self.lines = (self.lines + [line.strip()])[-200:]

    def snapshot(self):
        return {key: getattr(self, key) for key in (
            "kind", "title", "folder", "state", "phase", "status", "done", "total",
            "lines", "message", "result", "started", "finished")}


class JobConsole(pubmed_dl.Console):
    """Feeds pubmed_dl's progress into a Job, and stops it when asked."""

    def __init__(self, job):
        self.job = job
        self.live = False
        self._width = 0

    def _check(self):
        if self.job.cancel_requested:
            raise Cancelled()

    def log(self, message):
        self._check()
        self.job.add_line(message)

    def status(self, message):
        self._check()
        self.job.status = message.strip()

    def progress(self, done, total, phase="download"):
        self._check()
        self.job.done, self.job.total, self.job.phase = done, total, phase


def safe_name(text):
    name = re.sub(r"[^\w\- ]+", " ", text, flags=re.UNICODE)
    name = re.sub(r"\s+", " ", name).strip(" .-")[:60].strip()
    return name or "My collection"


def parse_year(value, end=False):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        return pubmed_dl.parse_date(value, end=end)
    except pubmed_dl.argparse.ArgumentTypeError as err:
        raise AppError(str(err)) from None


def positive_or_none(value, what):
    if value in (None, ""):
        return None
    try:
        number = int(str(value).replace(",", ""))
    except ValueError:
        number = 0
    if number < 1:
        raise AppError(f"{what} must be a whole number above 0")
    return number


def search_words(text):
    """Words to look for, keeping "quoted phrases" together."""
    return [a or b for a, b in re.findall(r'"([^"]+)"|(\S+)', text or "")]


def unique_path(folder, stem, suffix=""):
    """folder/stem+suffix, or "stem 2", "stem 3"... if that is taken."""
    path, n = folder / f"{stem}{suffix}", 2
    while path.exists() or (not suffix and path.with_name(path.name + ".zip").exists()):
        path, n = folder / f"{stem} {n}{suffix}", n + 1
    return path


def reveal(path):
    """Show a file or folder in Finder (or the system's file manager)."""
    path = Path(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path.is_file() else ["open", str(path)])
    elif os.name == "nt":
        os.startfile(str(path if path.is_dir() else path.parent))  # noqa: B606
    else:
        subprocess.Popen(["xdg-open", str(path if path.is_dir() else path.parent)])


class App:
    def __init__(self, home=None):
        home = Path(home or os.environ.get("PUBMED_APP_HOME") or Path.home())
        if sys.platform == "darwin":
            self.support = home / "Library" / "Application Support" / APP_NAME
        else:
            self.support = home / ".config" / "pubmed-downloader"
        self.library = home / "PubMed Library"
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.job = None
        self.last_seen = time.monotonic()
        self.server = None

    # -- settings ------------------------------------------------------------

    @property
    def settings_path(self):
        return self.support / "settings.json"

    def settings(self):
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        return {"api_key": data.get("api_key", ""), "email": data.get("email", "")}

    def save_settings(self, data):
        settings = {"api_key": str(data.get("api_key", "")).strip(),
                    "email": str(data.get("email", "")).strip()}
        if settings["api_key"] and not re.fullmatch(r"[A-Za-z0-9]{20,64}", settings["api_key"]):
            raise AppError("That doesn't look like an NCBI API key (letters and numbers only).")
        self.support.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
        os.chmod(self.settings_path, 0o600)
        return settings

    def client(self, retries=6, log=None):
        s = self.settings()
        return pubmed_dl.EUtils(email=s["email"] or None, api_key=s["api_key"] or None,
                                retries=retries, log=log)

    # -- library ---------------------------------------------------------------

    def collection(self, name):
        """The folder of an existing collection, refusing anything outside the library."""
        name = str(name or "")
        folder = self.library / name
        if (not name or name != Path(name).name or name.startswith(".")
                or not (folder / "manifest.json").is_file()):
            raise AppError("That collection doesn't exist any more.")
        return folder

    def list_library(self):
        items = []
        if not self.library.is_dir():
            return items
        running = self.job.folder if self.job and self.job.state == "running" else None
        for folder in self.library.iterdir():
            manifest_path = folder / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                settings = manifest["settings"]
            except (OSError, ValueError, KeyError):
                continue
            try:
                about = json.loads((folder / "about.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                about = {}
            parts = manifest.get("parts", {}).values()
            exports = folder / "exports"
            items.append({
                "folder": folder.name,
                "name": about.get("name") or folder.name,
                "query": settings.get("query") or f"PMIDs from {settings.get('ids_file')}",
                "from": settings.get("from"),
                "to": settings.get("to"),
                "fields": settings.get("fields"),
                "matches": manifest.get("total_matches"),
                "pmids": manifest.get("pmids", 0),
                "records": sum(p["records"] for p in parts),
                "fetched": sum(p["pmids"] for p in parts),
                "bytes": sum(p["bytes"] for p in parts),
                "raw_bytes": sum(p.get("raw_bytes", 0) for p in parts),
                "complete": bool(manifest.get("complete")),
                "running": folder.name == running,
                "created": manifest.get("created"),
                "exports": sorted((p.name for p in exports.iterdir() if not p.name.startswith(".")),
                                  reverse=True) if exports.is_dir() else [],
            })
        items.sort(key=lambda item: item.get("created") or "", reverse=True)
        return items

    def papers(self, name, text, limit=50):
        folder = self.collection(name)
        found, more = [], False
        for _, record in pubmed_dl.iter_matching([folder], search_words(text)):
            if len(found) == limit:
                more = True
                break
            found.append(record if isinstance(record, dict) else {"text": record})
        return {"papers": found, "more": more}

    def delete(self, name):
        folder = self.collection(name)
        if self.job and self.job.state == "running" and self.job.folder == folder.name:
            raise AppError("Stop the download first.")
        shutil.rmtree(folder)

    def reveal(self, name=None, export=None):
        if not name:
            self.library.mkdir(parents=True, exist_ok=True)
            return reveal(self.library)
        folder = self.collection(name)
        if export:
            target = folder / "exports" / export
            if export != Path(export).name or not target.exists():
                raise AppError("That export doesn't exist any more.")
            return reveal(target)
        return reveal(folder)

    # -- searching -----------------------------------------------------------

    def count(self, data):
        query = str(data.get("query") or "").strip()
        if not query:
            raise AppError("Type at least one keyword.")
        start, end = parse_year(data.get("from")), parse_year(data.get("to"), end=True)
        kw = {}
        if start or end:
            kw = {"datetype": "pdat", "mindate": start or pubmed_dl.EARLIEST,
                  "maxdate": end or pubmed_dl.latest_date()}
        try:
            result = self.client(retries=1).esearch(query, **kw)
        except pubmed_dl.EUtilsError as err:
            raise AppError(f"Couldn't reach PubMed. Are you online? ({err})") from None
        notes = []
        pubmed_dl.report_search_warnings(result, notes.append)
        return {"count": int(result["count"]),
                "translation": result.get("querytranslation") or query,
                "notes": [n.strip() for n in notes]}

    # -- background jobs -------------------------------------------------------

    def _start(self, job, work):
        with self.lock:
            if self.job and self.job.state == "running":
                raise AppError("Something is already running - wait for it or press Stop.")
            self.job = job
        threading.Thread(target=self._run, args=(job, work), daemon=True).start()
        return job.snapshot()

    def _run(self, job, work):
        console = JobConsole(job)
        try:
            job.result = work(console) or {}
            job.state = "done"
        except Cancelled:
            job.state = "cancelled"
            job.message = "Stopped."
        except pubmed_dl.EUtilsError as err:
            job.state, job.message = "failed", str(err)
        except (AppError, SystemExit) as err:
            text = str(err.code if isinstance(err, SystemExit) else err)
            job.state, job.message = "failed", text.replace("Error: ", "", 1)
        except Exception as err:  # keep the app alive, show what happened
            traceback.print_exc()
            job.state, job.message = "failed", f"Unexpected problem: {err}"
        job.finished = time.time()

    def download_args(self, folder, settings):
        s = self.settings()
        return Namespace(
            query=settings["query"],
            ids_file=Path(settings["ids_file"]) if settings.get("ids_file") else None,
            mindate=parse_year(settings.get("from")),
            maxdate=parse_year(settings.get("to"), end=True),
            max=settings.get("max"), sort=settings.get("sort", "newest"),
            fields=",".join(settings["fields"]) if isinstance(settings["fields"], list)
            else settings["fields"],
            format=settings.get("format", "jsonl"), compress=settings.get("compress", "xz"),
            per_file=settings.get("per_file", 1000),
            abstract_chars=settings.get("abstract_chars"),
            max_authors=settings.get("max_authors"),
            batch_size=200, fresh=False, output=str(folder),
            email=s["email"] or None, api_key=s["api_key"] or None)

    def _download_work(self, job, args, new=False):
        folder = Path(args.output)

        def work(console):
            try:
                pubmed_dl.cmd_download(args, console)
            finally:
                if new and not (folder / "manifest.json").exists():
                    shutil.rmtree(folder, ignore_errors=True)  # nothing was saved
            if not folder.exists():
                raise AppError("No papers matched that search.")
            manifest = pubmed_dl.load_manifest(folder / "manifest.json") or {}
            parts = manifest.get("parts", {}).values()
            return {"records": sum(p["records"] for p in parts),
                    "bytes": sum(p["bytes"] for p in parts),
                    "raw_bytes": sum(p.get("raw_bytes", 0) for p in parts),
                    "matches": manifest.get("total_matches", 0)}
        return work

    def start_download(self, data):
        query = str(data.get("query") or "").strip()
        if not query:
            raise AppError("Type at least one keyword.")
        detail = data.get("detail", "standard")
        if detail not in pubmed_dl.FIELD_PRESETS:
            raise AppError("Unknown detail level.")
        settings = {
            "query": query, "ids_file": None,
            "from": str(data.get("from") or "").strip() or None,
            "to": str(data.get("to") or "").strip() or None,
            "max": positive_or_none(data.get("max"), "Number of papers"),
            "sort": "newest", "fields": detail,
            "abstract_chars": positive_or_none(data.get("abstract_chars"), "Abstract length"),
        }
        name = safe_name(data.get("name") or query)
        self.library.mkdir(parents=True, exist_ok=True)
        folder, n = self.library / name, 2
        while folder.exists():
            folder, n = self.library / f"{name} {n}", n + 1
        args = self.download_args(folder, settings)
        folder.mkdir()
        about = {"name": folder.name, "form": data.get("form") or {}}
        (folder / "about.json").write_text(json.dumps(about, indent=2), encoding="utf-8")
        job = Job("download", folder.name, folder.name)
        try:
            return self._start(job, self._download_work(job, args, new=True))
        except AppError:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def resume(self, name):
        folder = self.collection(name)
        manifest = pubmed_dl.load_manifest(folder / "manifest.json")
        args = self.download_args(folder, manifest["settings"])
        job = Job("download", folder.name, folder.name)
        return self._start(job, self._download_work(job, args))

    def start_export(self, data):
        folder = self.collection(data.get("folder"))
        kind = data.get("kind", "ai")
        needles = search_words(data.get("filter"))
        manifest = pubmed_dl.load_manifest(folder / "manifest.json") or {}
        query = (manifest.get("settings") or {}).get("query")
        exports = folder / "exports"
        exports.mkdir(exist_ok=True)
        stamp = pubmed_dl.export_stamp()
        job = Job("export", f"Exporting {folder.name}", folder.name)

        def progress(n):
            job.status = f"{n:,} papers written"
            if job.cancel_requested:
                raise Cancelled()

        if kind == "csv":
            out = unique_path(exports, f"spreadsheet {stamp}", ".csv")

            def work(console):
                try:
                    papers = pubmed_dl.export_csv([folder], out, needles, progress)
                except BaseException:
                    out.unlink(missing_ok=True)
                    raise
                if not papers:
                    out.unlink(missing_ok=True)
                    raise AppError("No papers matched those words.")
                return {"papers": papers, "export": out.name, "files": [out.name]}
        elif kind == "ai":
            words = EXPORT_SIZES.get(data.get("size"), EXPORT_SIZES["medium"])
            out = unique_path(exports, f"for AI {stamp}")

            def work(console):
                try:
                    files, papers, zip_path = pubmed_dl.export_markdown(
                        [folder], out, words, needles, title=folder.name,
                        about=f"PubMed search: {query}" if query else "", progress=progress)
                except BaseException:
                    shutil.rmtree(out, ignore_errors=True)
                    raise
                if not papers:
                    shutil.rmtree(out, ignore_errors=True)
                    zip_path.unlink(missing_ok=True)
                    raise AppError("No papers matched those words.")
                return {"papers": papers, "export": out.name, "zip": zip_path.name,
                        "files": [p.name for p in files],
                        "words": sum(len(p.read_text(encoding="utf-8").split()) for p in files)}
        else:
            raise AppError("Unknown export type.")
        return self._start(job, work)

    def cancel(self):
        if self.job and self.job.state == "running":
            self.job.cancel_requested = True
        return self.state()

    def state(self):
        s = self.settings()
        return {"job": self.job.snapshot() if self.job else None,
                "library": str(self.library),
                "has_api_key": bool(s["api_key"])}

    # -- the web server --------------------------------------------------------

    def serve(self, port=0, open_browser=True):
        handler = make_handler(self)
        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.server.daemon_threads = True
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}/#token={self.token}"
        self.write_instance_file()
        threading.Thread(target=self._watch_idle, daemon=True).start()
        print(f"{APP_NAME} running at {self.url}", flush=True)
        if open_browser:
            webbrowser.open(self.url)
        try:
            self.server.serve_forever()
        finally:
            self.server.server_close()
            self.instance_file.unlink(missing_ok=True)

    def stop(self):
        if self.job and self.job.state == "running":
            self.job.cancel_requested = True
        threading.Thread(target=self.server.shutdown, daemon=True).start()

    @property
    def instance_file(self):
        return self.support / "running.json"

    def write_instance_file(self):
        self.support.mkdir(parents=True, exist_ok=True)
        self.instance_file.write_text(json.dumps(
            {"port": self.port, "token": self.token, "pid": os.getpid()}), encoding="utf-8")
        os.chmod(self.instance_file, 0o600)

    def _watch_idle(self):
        while True:
            time.sleep(30)
            busy = self.job and self.job.state == "running"
            if not busy and time.monotonic() - self.last_seen > IDLE_EXIT_SECONDS:
                print("No browser window for a while - quitting.", flush=True)
                self.server.shutdown()
                return


def find_running(app):
    """URL of an instance that is already running, if any."""
    try:
        info = json.loads(app.instance_file.read_text(encoding="utf-8"))
        url = f"http://127.0.0.1:{int(info['port'])}"
        request = urllib.request.Request(f"{url}/api/ping", headers={"X-Token": info["token"]})
        with urllib.request.urlopen(request, timeout=2) as resp:
            if json.load(resp).get("app") == APP_NAME:
                return f"{url}/#token={info['token']}"
    except Exception:
        return None
    return None


def make_handler(app):
    routes_get = {
        "/api/ping": lambda q: {"app": APP_NAME},
        "/api/state": lambda q: app.state(),
        "/api/library": lambda q: {"items": app.list_library(), "path": str(app.library)},
        "/api/settings": lambda q: app.settings(),
        "/api/papers": lambda q: app.papers(q.get("folder"), q.get("q", ""),
                                            min(int(q.get("limit") or 50), 500)),
    }
    routes_post = {
        "/api/count": app.count,
        "/api/download": app.start_download,
        "/api/resume": lambda d: app.resume(d.get("folder")),
        "/api/cancel": lambda d: app.cancel(),
        "/api/export": app.start_export,
        "/api/delete": lambda d: app.delete(d.get("folder")) or {"ok": True},
        "/api/reveal": lambda d: app.reveal(d.get("folder"), d.get("export")) or {"ok": True},
        "/api/settings": app.save_settings,
        "/api/quit": lambda d: app.stop() or {"ok": True},
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "PubMedDownloader"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def _allowed(self, needs_token):
            # Only pages served by this app may talk to it: check the Host
            # header (DNS rebinding) and the secret token (other web pages).
            host = self.headers.get("Host", "")
            if host not in (f"127.0.0.1:{app.port}", f"localhost:{app.port}"):
                self._send(403, {"error": "forbidden"})
                return False
            if needs_token and not hmac.compare_digest(
                    self.headers.get("X-Token", ""), app.token):
                self._send(403, {"error": "This window is out of date - open the app again."})
                return False
            return True

        def _route(self, routes, arg):
            path = urllib.parse.urlsplit(self.path).path
            if path not in routes:
                return self._send(404, {"error": "not found"})
            app.last_seen = time.monotonic()
            try:
                self._send(200, routes[path](arg))
            except AppError as err:
                self._send(400, {"error": str(err)})
            except (ValueError, TypeError, KeyError) as err:
                self._send(400, {"error": f"Bad request: {err}"})

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path in ("/", "/index.html"):
                if self._allowed(needs_token=False):
                    self._send(200, UI_FILE.read_bytes(), "text/html; charset=utf-8")
                return
            if self._allowed(needs_token=True):
                query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
                self._route(routes_get, query)

        def do_POST(self):
            if not self._allowed(needs_token=True):
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"error": "too large"})
            try:
                data = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {"error": "expected JSON"})
            self._route(routes_post, data)

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=f"Run the {APP_NAME} app.")
    parser.add_argument("--port", type=int, default=0, help="port to use (default: any free one)")
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    args = parser.parse_args(argv)
    app = App()
    existing = find_running(app)
    if existing:
        print(f"{APP_NAME} is already running - opening it.", flush=True)
        if not args.no_browser:
            webbrowser.open(existing)
        return 0
    try:
        app.serve(args.port, open_browser=not args.no_browser)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
