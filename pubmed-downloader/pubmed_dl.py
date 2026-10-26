#!/usr/bin/env python3
"""Bulk-download PubMed records and pack them into small compressed files.

Searches PubMed through NCBI's E-utilities, keeps only the details you ask
for and writes them out as numbered, compressed chunks
(part-00001.jsonl.xz, part-00002.jsonl.xz, ...). Interrupted downloads
resume where they stopped. Needs only Python 3.8+ - nothing to install.

    python3 pubmed_dl.py                                  # guided mode
    python3 pubmed_dl.py count "crispr AND 2023[dp]"
    python3 pubmed_dl.py download "crispr AND 2023[dp]" -o crispr2023
    python3 pubmed_dl.py read crispr2023 --grep "off-target" --limit 5

Run any command with -h to see all of its options.
"""

import argparse
import bz2
import calendar
import csv
import gzip
import http.client
import json
import lzma
import os
import re
import shlex
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

__version__ = "1.0.0"

DEFAULT_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL_NAME = "pubmed_dl"
# PubMed's search hands out at most the first 9,999 IDs of any query, so
# bigger result sets are split into date windows that each stay under this.
SEARCH_LIMIT = 9999
EARLIEST = date(1600, 1, 1)
# Split by publication date first; a single day that is still too big is
# split again by the date the record entered PubMed.
DATE_TYPES = ("pdat", "edat")
RETRY_STATUSES = {429, 500, 502, 503, 504}

ALL_FIELDS = (
    "pmid", "title", "abstract", "authors", "affiliations", "journal",
    "journal_abbrev", "year", "pub_date", "volume", "issue", "pages", "doi",
    "pmcid", "pub_types", "mesh", "keywords", "language",
)
FIELD_PRESETS = {
    "minimal": ["pmid", "title", "year", "journal", "doi"],
    "standard": ["pmid", "title", "abstract", "authors", "journal", "year",
                 "doi", "mesh", "keywords"],
    "full": list(ALL_FIELDS),
}
FORMATS = ("jsonl", "csv", "txt")
COMPRESSION_EXT = {"xz": ".xz", "gz": ".gz", "bz2": ".bz2", "none": ""}
PART_RE = re.compile(r"^part-\d{5,}\.(jsonl|csv|txt)(\.(xz|gz|bz2))?(\.tmp)?$")
LIST_SEP = "; "


class EUtilsError(RuntimeError):
    """NCBI refused a request, or kept failing after every retry."""


class _Retry(Exception):
    """A reply worth asking for again (cut off, backend hiccup).

    `limit` caps the attempts for replies that are less likely to be
    transient than a dropped connection.
    """

    def __init__(self, message, limit=None):
        super().__init__(message)
        self.limit = limit


# --------------------------------------------------------------------------
# Talking to NCBI


class EUtils:
    """Minimal E-utilities client: throttled, retrying, POST-only."""

    def __init__(self, email=None, api_key=None, base_url=None, retries=6,
                 timeout=120, log=None):
        self.email = email
        self.api_key = api_key
        self.base_url = (base_url or os.environ.get("PUBMED_EUTILS_URL")
                         or DEFAULT_BASE_URL).rstrip("/")
        self.retries = retries
        self.timeout = timeout
        # NCBI allows 3 requests a second, or 10 with an API key.
        self.min_interval = 0.11 if api_key else 0.34
        self.log = log or (lambda msg: None)
        self._last_request = 0.0

    def _throttle(self):
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def call(self, endpoint, params, parse):
        params = dict(params, tool=TOOL_NAME)
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        body = urllib.parse.urlencode(params).encode()
        url = f"{self.base_url}/{endpoint}"
        headers = {"User-Agent": f"{TOOL_NAME}/{__version__}"}
        for attempt in range(self.retries + 1):
            delay = min(2 ** attempt, 60)
            limit = self.retries
            self._throttle()
            try:
                request = urllib.request.Request(url, data=body, headers=headers)
                with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                    raw = resp.read()
                return parse(raw)
            except urllib.error.HTTPError as err:
                if err.code not in RETRY_STATUSES:
                    raise EUtilsError(f"NCBI answered HTTP {err.code}: {_error_body(err)}") from None
                problem = f"HTTP {err.code}"
                retry_after = (err.headers.get("Retry-After") or "") if err.headers else ""
                if retry_after.isdigit():
                    delay = max(delay, min(int(retry_after), 300))
            except _Retry as err:
                problem = str(err)
                if err.limit is not None:
                    limit = min(limit, err.limit)
            except (urllib.error.URLError, http.client.HTTPException, OSError) as err:
                problem = getattr(err, "reason", None) or err
            if attempt >= limit:
                raise EUtilsError(f"{endpoint} kept failing ({problem}); "
                                  "check your connection and try again")
            self.log(f"  {endpoint}: {problem} - retrying in {delay}s")
            time.sleep(delay)
        raise AssertionError("unreachable")

    def esearch(self, term, retmax=0, sort=None, datetype=None, mindate=None, maxdate=None):
        params = {"db": "pubmed", "term": term, "retmax": retmax, "retmode": "json"}
        if sort:
            params["sort"] = sort
        if datetype:
            params.update(datetype=datetype, mindate=fmt_date(mindate),
                          maxdate=fmt_date(maxdate))
        return self.call("esearch.fcgi", params, _parse_esearch)

    def efetch(self, pmids):
        params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
        return self.call("efetch.fcgi", params, _parse_efetch)


def _error_body(err):
    try:
        text = err.read().decode("utf-8", "replace")
    except Exception:
        return err.reason
    return " ".join(text.split())[:300] or err.reason


def _parse_esearch(raw):
    try:
        result = json.loads(raw.decode("utf-8", "replace"), strict=False)["esearchresult"]
    except (ValueError, KeyError, TypeError):
        raise _Retry("unreadable search reply") from None
    if "ERROR" in result:
        raise _Retry(f"search error: {result['ERROR']}", limit=2)
    try:
        int(result["count"])
    except (KeyError, TypeError, ValueError):
        raise _Retry("search reply without a count") from None
    return result


def _parse_efetch(raw):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise _Retry("incomplete article data") from None
    if root.tag != "PubmedArticleSet":
        message = _text(root.find(".//ERROR"))
        if "empty" in message.lower():  # none of the PMIDs exist any more
            return []
        raise _Retry(f"fetch error: {message or f'unexpected <{root.tag}> reply'}", limit=2)
    return parse_articles(root)


# --------------------------------------------------------------------------
# Turning PubMed XML into small records


def _text(el, path=None):
    """All text inside an element (inline tags like <i> included), tidied."""
    if el is not None and path:
        el = el.find(path)
    if el is None:
        return ""
    return " ".join("".join(el.itertext()).split())


def _texts(el, path):
    if el is None:
        return []
    return [t for t in (_text(node) for node in el.findall(path)) if t]


def _unique(items):
    return list(dict.fromkeys(items))


def _abstract(el):
    parts = []
    for node in el.findall("Abstract/AbstractText"):
        body = _text(node)
        label = node.get("Label", "")
        if body:
            parts.append(f"{label}: {body}" if label and label != "UNLABELLED" else body)
    return " ".join(parts)


def _authors(el):
    names, affiliations = [], []
    for author in el.findall("AuthorList/Author"):
        last = _text(author, "LastName")
        if last:
            name = f"{last} {_text(author, 'Initials') or _text(author, 'ForeName')}".strip()
        else:
            name = _text(author, "CollectiveName")
        if name:
            names.append(name)
        affiliations.extend(_texts(author, "AffiliationInfo/Affiliation"))
    return names, _unique(affiliations)


def _year(text):
    match = re.search(r"\b\d{4}\b", text)
    return int(match.group()) if match else None


def _pub_date(pubdate):
    """(year, human-readable date) from a <PubDate> element."""
    if pubdate is None:
        return None, ""
    medline = _text(pubdate, "MedlineDate")  # free text such as "1998 Spring"
    parts = [_text(pubdate, tag) for tag in ("Year", "Month", "Day")]
    shown = " ".join(p for p in parts if p) or medline
    return _year(parts[0] or medline), shown


def _article_ids(*id_lists):
    ids = {}
    for id_list in id_lists:
        if id_list is not None:
            for node in id_list.findall("ArticleId"):
                ids.setdefault(node.get("IdType"), _text(node))
    return ids


def parse_pubmed_article(el):
    citation = el.find("MedlineCitation")
    article = citation.find("Article") if citation is not None else None
    if article is None:
        return None
    year, pub_date = _pub_date(article.find("Journal/JournalIssue/PubDate"))
    if year is None:
        year = _year(_text(article, "ArticleDate/Year"))
    authors, affiliations = _authors(article)
    # Only the article's own IDs: the reference list has ArticleIdLists too.
    ids = _article_ids(el.find("PubmedData/ArticleIdList"))
    doi = ids.get("doi") or next(
        (_text(node) for node in article.findall("ELocationID")
         if node.get("EIdType") == "doi"), "")
    pages = _text(article, "Pagination/MedlinePgn") or "-".join(
        p for p in (_text(article, "Pagination/StartPage"),
                    _text(article, "Pagination/EndPage")) if p)
    return {
        "pmid": _text(citation, "PMID"),
        "title": _text(article, "ArticleTitle") or _text(article, "VernacularTitle"),
        "abstract": _abstract(article),
        "authors": authors,
        "affiliations": affiliations,
        "journal": _text(article, "Journal/Title"),
        "journal_abbrev": (_text(article, "Journal/ISOAbbreviation")
                           or _text(citation, "MedlineJournalInfo/MedlineTA")),
        "year": year,
        "pub_date": pub_date,
        "volume": _text(article, "Journal/JournalIssue/Volume"),
        "issue": _text(article, "Journal/JournalIssue/Issue"),
        "pages": pages,
        "doi": doi,
        "pmcid": ids.get("pmc", ""),
        "pub_types": _texts(article, "PublicationTypeList/PublicationType"),
        "mesh": _texts(citation, "MeshHeadingList/MeshHeading/DescriptorName"),
        "keywords": _unique(_texts(citation, "KeywordList/Keyword")),
        "language": ",".join(_texts(article, "Language")),
    }


def parse_book_article(el):
    doc = el.find("BookDocument")
    if doc is None:
        return None
    book = doc.find("Book")
    year, pub_date = _pub_date(book.find("PubDate") if book is not None else None)
    authors, affiliations = _authors(doc)
    if not authors and book is not None:
        authors, affiliations = _authors(book)  # editors
    ids = _article_ids(doc.find("ArticleIdList"), el.find("PubmedBookData/ArticleIdList"))
    return {
        "pmid": _text(doc, "PMID"),
        "title": _text(doc, "ArticleTitle") or _text(book, "BookTitle"),
        "abstract": _abstract(doc),
        "authors": authors,
        "affiliations": affiliations,
        "journal": _text(book, "BookTitle"),
        "journal_abbrev": "",
        "year": year,
        "pub_date": pub_date,
        "volume": _text(book, "Volume"),
        "issue": "",
        "pages": "",
        "doi": ids.get("doi", ""),
        "pmcid": ids.get("pmc", ""),
        "pub_types": _texts(doc, "PublicationType") + _texts(doc, "PublicationTypeList/PublicationType"),
        "mesh": _texts(doc, "MeshHeadingList/MeshHeading/DescriptorName"),
        "keywords": _unique(_texts(doc, "KeywordList/Keyword")),
        "language": ",".join(_texts(doc, "Language")),
    }


ARTICLE_PARSERS = {
    "PubmedArticle": parse_pubmed_article,
    "PubmedBookArticle": parse_book_article,
}


def parse_articles(root):
    """Records for every article in a <PubmedArticleSet>."""
    records = []
    for child in root:
        parse = ARTICLE_PARSERS.get(child.tag)
        record = parse(child) if parse else None
        if record and record["pmid"]:
            records.append(record)
    return records


def shape(record, fields, abstract_chars=None, max_authors=None):
    """Keep only the chosen fields, drop empty ones, apply size limits."""
    out = {}
    for field in fields:
        value = record.get(field)
        if value is None or value == "" or value == []:
            continue
        if field == "abstract" and abstract_chars and len(value) > abstract_chars:
            value = value[:abstract_chars].rstrip() + "..."
        if field == "authors" and max_authors and len(value) > max_authors:
            value = value[:max_authors] + ["et al."]
        out[field] = value
    return out


# --------------------------------------------------------------------------
# Finding the PMIDs


def fmt_date(d):
    return f"{d.year:04d}/{d.month:02d}/{d.day:02d}"


def parse_date(text, end=False):
    """'2020', '2020/05' or '2020-05-17' -> date; end=True rounds up."""
    parts = re.split(r"[/.\-]", text.strip())
    try:
        if not 1 <= len(parts) <= 3:
            raise ValueError
        nums = [int(p) for p in parts]
        year = nums[0]
        month = nums[1] if len(nums) > 1 else (12 if end else 1)
        if len(nums) > 2:
            day = nums[2]
        else:
            day = calendar.monthrange(year, month)[1] if end else 1
        return date(year, month, day)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"not a date: {text!r} (use YYYY, YYYY/MM or YYYY/MM/DD)") from None


def latest_date():
    # Leave room for issues that are dated ahead of time.
    return date(date.today().year + 5, 12, 31)


def collect_pmids(client, term, start, end, limit=None, progress=None, warn=None,
                  datetypes=DATE_TYPES):
    """Every PMID matching `term`, newest publication dates first.

    The date range is halved until each window has at most SEARCH_LIMIT
    hits. Papers can match two windows (print and online dates), so IDs are
    de-duplicated as they come in.
    """
    datetype = datetypes[0]
    found, seen = [], set()
    windows = [(start, end)]
    while windows:
        lo, hi = windows.pop()
        count = int(client.esearch(term, datetype=datetype, mindate=lo, maxdate=hi)["count"])
        if count == 0:
            continue
        if count > SEARCH_LIMIT and lo < hi:
            mid = date.fromordinal((lo.toordinal() + hi.toordinal()) // 2)
            windows.append((lo, mid))
            windows.append((mid + timedelta(days=1), hi))  # newer half pops first
            continue
        if count <= SEARCH_LIMIT:
            ids = client.esearch(term, retmax=count, sort="pub_date", datetype=datetype,
                                 mindate=lo, maxdate=hi)["idlist"]
        elif len(datetypes) > 1:
            day = fmt_date(lo)
            day_term = f'({term}) AND ("{day}"[{datetype}] : "{day}"[{datetype}])'
            left = limit - len(found) if limit else None
            ids = collect_pmids(client, day_term, EARLIEST, latest_date(), left,
                                warn=warn, datetypes=datetypes[1:])
        else:
            if warn:
                warn(f"  {count:,} papers share the date {fmt_date(lo)}; PubMed only "
                     f"lets us list {SEARCH_LIMIT:,} of them.")
            ids = client.esearch(term, retmax=SEARCH_LIMIT, sort="pub_date", datetype=datetype,
                                 mindate=lo, maxdate=hi)["idlist"]
        for pmid in ids:
            if pmid not in seen:
                seen.add(pmid)
                found.append(pmid)
        if progress:
            progress(len(found), lo, hi)
        if limit and len(found) >= limit:
            return found[:limit]
    return found


SEARCH_NOTES = {
    "phrasesnotfound": "not found",
    "fieldsnotfound": "unknown search field",
    "phrasesignored": "ignored",
    "quotedphrasesnotfound": "quoted phrase not found",
    "outputmessages": "message",
}


def report_search_warnings(result, log):
    for key in ("errorlist", "warninglist"):
        for kind, items in (result.get(key) or {}).items():
            if items:
                label = SEARCH_NOTES.get(kind, kind)
                log(f"  PubMed note ({label}): {', '.join(map(str, items))}")


def read_id_file(path):
    """PMIDs from a text file: one per line, or separated by commas/spaces."""
    tokens = re.split(r"[\s,;]+", Path(path).read_text(encoding="utf-8", errors="replace"))
    ids = [re.sub(r"(?i)^pmid:?", "", t) for t in tokens]
    return _unique(t for t in ids if t.isdigit())


def find_pmids(client, args, console):
    """The list of PMIDs to download, plus a little about the search."""
    if args.ids_file:
        pmids = read_id_file(args.ids_file)
        console.log(f"Read {len(pmids):,} PMIDs from {args.ids_file}")
        if args.max:
            pmids = pmids[:args.max]
        return pmids, {"total_matches": len(pmids)}

    dated = bool(args.mindate or args.maxdate)
    start = args.mindate or EARLIEST
    end = args.maxdate or latest_date()
    date_kw = {"datetype": "pdat", "mindate": start, "maxdate": end} if dated else {}
    result = client.esearch(args.query, **date_kw)
    total = int(result["count"])
    translation = result.get("querytranslation") or args.query
    console.log(f"PubMed has {total:,} papers matching: {translation}")
    report_search_warnings(result, console.log)
    info = {"total_matches": total, "query_translation": translation}
    want = min(total, args.max) if args.max else total
    if want == 0:
        return [], info

    if want <= SEARCH_LIMIT:
        sort = "relevance" if args.sort == "relevance" else "pub_date"
        pmids = client.esearch(args.query, retmax=want, sort=sort, **date_kw)["idlist"]
        return _unique(pmids)[:want], info

    if args.sort == "relevance":
        console.log(f"Note: best-match order only works for the top {SEARCH_LIMIT:,}; "
                    "keeping the newest papers instead.")
    console.log(f"More than {SEARCH_LIMIT:,} results, so the search is split by "
                "publication date (PubMed's limit per search)...")

    def progress(n, lo, hi):
        console.status(f"  listed {n:,} of {want:,} PMIDs (up to {fmt_date(lo)})")

    pmids = collect_pmids(client, args.query, start, end, limit=want,
                          progress=progress, warn=console.log)
    if not dated and len(pmids) < want:
        # Papers whose date falls outside the windows (rare, odd dates).
        outside = f'({args.query}) NOT ("{fmt_date(start)}"[pdat] : "{fmt_date(end)}"[pdat])'
        seen = set(pmids)
        extra = client.esearch(outside, retmax=SEARCH_LIMIT)["idlist"]
        pmids += [p for p in extra if p not in seen][:want - len(pmids)]
    if len(pmids) < want:
        console.log(f"Note: PubMed listed {len(pmids):,} of the {want:,} papers it counted.")
    return pmids, info


# --------------------------------------------------------------------------
# Writing and reading the compressed files


def open_text(path, mode, compression):
    """Open a (maybe compressed) UTF-8 text file; mode is 'r' or 'w'."""
    kw = {"encoding": "utf-8", "newline": ""}
    if compression == "xz":
        return lzma.open(path, mode + "t", preset=9 if mode == "w" else None, **kw)
    if compression == "gz":
        return gzip.open(path, mode + "t", compresslevel=9, **kw)
    if compression == "bz2":
        return bz2.open(path, mode + "t", compresslevel=9, **kw)
    return open(path, mode, **kw)


def part_name(number, fmt, compression):
    return f"part-{number:05d}.{fmt}{COMPRESSION_EXT[compression]}"


def detect_file(path):
    """(format, compression) for a file this tool wrote, else None."""
    name = Path(path).name
    compression = "none"
    for comp, ext in COMPRESSION_EXT.items():
        if ext and name.endswith(ext):
            compression, name = comp, name[:-len(ext)]
            break
    fmt = name.rsplit(".", 1)[-1]
    return (fmt, compression) if fmt in FORMATS else None


def _cell(value):
    if isinstance(value, list):
        return LIST_SEP.join(map(str, value))
    return "" if value is None else value


TXT_LABELS = {"journal_abbrev": "Journal (abbrev.)", "pub_date": "Published",
              "pmcid": "PMCID", "pub_types": "Type", "mesh": "MeSH",
              "doi": "DOI", "pmid": "PMID"}


def format_txt(record):
    """A record as a short, readable block of text."""
    journal_key = "journal_abbrev" if record.get("journal_abbrev") else "journal"
    head = [f"PMID {record['pmid']}" if record.get("pmid") else "",
            str(record.get("year") or ""), record.get(journal_key) or "",
            f"doi:{record['doi']}" if record.get("doi") else ""]
    lines = [" | ".join(h for h in head if h)]
    if record.get("title"):
        lines.append(textwrap.fill(record["title"], 100))
    for key, value in record.items():
        if key in ("pmid", "year", "doi", "title", journal_key) or value in (None, "", []):
            continue
        label = TXT_LABELS.get(key, key.replace("_", " ").capitalize())
        lines.append(textwrap.fill(f"{label}: {_cell(value)}", 100, subsequent_indent="  "))
    return "\n".join(line for line in lines if line)


class RecordWriter:
    """Streams records into one compressed file, counting the raw size."""

    def __init__(self, path, fmt, compression, fields):
        self.fmt = fmt
        self.fields = fields
        self.count = 0
        self.raw_bytes = 0
        self._fh = open_text(path, "w", compression)
        if fmt == "csv":
            self._csv = csv.writer(self, lineterminator="\n")
            self._csv.writerow(fields)

    def write(self, text):  # also called by the csv module
        self.raw_bytes += len(text.encode("utf-8"))
        self._fh.write(text)

    def add(self, record):
        if self.fmt == "jsonl":
            self.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        elif self.fmt == "csv":
            self._csv.writerow([_cell(record.get(f)) for f in self.fields])
        else:
            self.write(format_txt(record) + "\n\n")
        self.count += 1

    def close(self):
        self._fh.close()


def iter_records(path):
    """Records from one output file: dicts for jsonl/csv, text blocks for txt."""
    fmt, compression = detect_file(path)
    with open_text(path, "r", compression) as fh:
        if fmt == "jsonl":
            for line in fh:
                if line.strip():
                    yield json.loads(line)
        elif fmt == "csv":
            yield from csv.DictReader(fh)
        else:
            for block in fh.read().split("\n\n"):
                if block.strip():
                    yield block.strip()


def find_part_files(paths):
    files = []
    for path in map(Path, paths):
        if path.is_dir():
            files += sorted(p for p in path.iterdir()
                            if PART_RE.match(p.name) and not p.name.endswith(".tmp"))
        elif path.is_file() and detect_file(path):
            files.append(path)
        else:
            raise SystemExit(f"Error: {path} is not a download folder or data file")
    return files


# --------------------------------------------------------------------------
# Commands


class Console:
    """Progress on stderr: permanent lines plus one self-updating status line."""

    def __init__(self, stream=None):
        self.stream = stream or sys.stderr
        self.live = hasattr(self.stream, "isatty") and self.stream.isatty()
        self._width = 0

    def log(self, message):
        self._clear()
        print(message, file=self.stream, flush=True)

    def status(self, message):
        if self.live:
            pad = " " * max(0, self._width - len(message))
            self.stream.write("\r" + message + pad)
            self.stream.flush()
            self._width = len(message)

    def _clear(self):
        if self._width:
            self.stream.write("\r" + " " * self._width + "\r")
            self._width = 0


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_duration(seconds):
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60:02d}m"
    if seconds >= 60:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds}s"


def resolve_fields(spec):
    if spec in FIELD_PRESETS:
        return list(FIELD_PRESETS[spec])
    fields = [f.strip() for f in spec.split(",") if f.strip()]
    unknown = [f for f in fields if f not in ALL_FIELDS]
    if unknown:
        raise SystemExit(f"Error: unknown field(s): {', '.join(unknown)}\n"
                         f"Choose from: {', '.join(ALL_FIELDS)}")
    return _unique(["pmid"] + fields)


def default_folder(text):
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40].strip("_")
    return f"pubmed_{slug or 'results'}"


def make_client(args, console):
    return EUtils(email=args.email, api_key=args.api_key, log=console.log)


def load_manifest(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        raise SystemExit(f"Error: {path} is damaged; rerun with --fresh to start over") from None


def save_manifest(path, manifest):
    manifest["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def clear_folder(folder):
    """Remove files from an earlier download (and nothing else)."""
    for path in folder.iterdir():
        if PART_RE.match(path.name) or path.name in (
                "manifest.json", "manifest.json.tmp", "pmids.txt.gz"):
            path.unlink()


def cmd_count(args, console):
    client = make_client(args, console)
    date_kw = {}
    if args.mindate or args.maxdate:
        date_kw = {"datetype": "pdat", "mindate": args.mindate or EARLIEST,
                   "maxdate": args.maxdate or latest_date()}
    result = client.esearch(args.query, **date_kw)
    print(f"{int(result['count']):,} papers match")
    print(f"PubMed read the search as: {result.get('querytranslation') or args.query}")
    report_search_warnings(result, print)
    return 0


def cmd_download(args, console):
    if bool(args.query) == bool(args.ids_file):
        raise SystemExit("Error: give either a search query or --ids-file (not both)")
    fields = resolve_fields(args.fields)
    folder = Path(args.output or default_folder(args.query or Path(args.ids_file).stem))
    settings = {
        "query": args.query,
        "ids_file": str(args.ids_file) if args.ids_file else None,
        "from": fmt_date(args.mindate) if args.mindate else None,
        "to": fmt_date(args.maxdate) if args.maxdate else None,
        "max": args.max,
        "sort": args.sort,
        "fields": fields,
        "format": args.format,
        "compress": args.compress,
        "per_file": args.per_file,
        "abstract_chars": args.abstract_chars,
        "max_authors": args.max_authors,
    }
    manifest_path = folder / "manifest.json"
    pmid_path = folder / "pmids.txt.gz"
    manifest = load_manifest(manifest_path)
    if manifest and args.fresh:
        clear_folder(folder)
        manifest = None
    if manifest and manifest.get("settings") != settings:
        raise SystemExit(f"Error: {folder} already holds a different download.\n"
                         "Pick another folder with -o, or add --fresh to replace it.")
    client = make_client(args, console)

    if manifest and pmid_path.exists():
        with gzip.open(pmid_path, "rt", encoding="utf-8") as fh:
            pmids = fh.read().split()
        console.log(f"Resuming download into {folder}/ ({len(pmids):,} papers)")
    else:
        pmids, info = find_pmids(client, args, console)
        if not pmids:
            console.log("Nothing to download.")
            return 0
        folder.mkdir(parents=True, exist_ok=True)
        with gzip.open(pmid_path, "wt", encoding="utf-8") as fh:
            fh.write("\n".join(pmids) + "\n")
        manifest = {
            "tool": f"{TOOL_NAME} {__version__}",
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "settings": settings,
            **info,
            "pmids": len(pmids),
            "parts": {},
            "complete": False,
        }
        save_manifest(manifest_path, manifest)

    if not args.api_key and len(pmids) > 5000:
        console.log("Tip: a free NCBI API key makes this ~3x faster - see README.md.")
    fetch_parts(client, pmids, folder, manifest, args.batch_size, console)

    parts = manifest["parts"]
    records = sum(p["records"] for p in parts.values())
    size = sum(p["bytes"] for p in parts.values())
    raw = sum(p["raw_bytes"] for p in parts.values())
    manifest.update(records=records, bytes=size, complete=True)
    save_manifest(manifest_path, manifest)
    saved = f", {100 - 100 * size / raw:.0f}% smaller than uncompressed" if raw and size < raw else ""
    console.log(f"Done: {records:,} papers in {len(parts)} file(s), {fmt_size(size)} total{saved}.")
    if records < len(pmids):
        console.log(f"  {len(pmids) - records:,} PMIDs had no record (deleted or withdrawn).")
    console.log(f"Saved in {folder}/  -  have a look with:  "
                f"python3 {Path(sys.argv[0]).name} read {shlex.quote(str(folder))} --limit 3")
    return 0


def fetch_parts(client, pmids, folder, manifest, batch_size, console):
    settings = manifest["settings"]
    fmt, compression, per_file = settings["format"], settings["compress"], settings["per_file"]
    chunks = [pmids[i:i + per_file] for i in range(0, len(pmids), per_file)]
    parts = manifest["parts"]
    for name in list(parts):
        if not (folder / name).exists():
            del parts[name]  # deleted by hand: fetch it again
    done = sum(p["pmids"] for p in parts.values())  # PMIDs in finished files
    started, fetched = time.monotonic(), 0  # this run only, for the speed estimate

    for number, chunk in enumerate(chunks, 1):
        name = part_name(number, fmt, compression)
        if name in parts:
            continue
        path = folder / name
        tmp = path.with_name(name + ".tmp")
        writer = RecordWriter(tmp, fmt, compression, settings["fields"])
        try:
            for i in range(0, len(chunk), batch_size):
                batch = chunk[i:i + batch_size]
                for record in client.efetch(batch):
                    writer.add(shape(record, settings["fields"],
                                     settings["abstract_chars"], settings["max_authors"]))
                fetched += len(batch)
                so_far = done + i + len(batch)
                rate = fetched / max(time.monotonic() - started, 1e-6)
                left = (len(pmids) - so_far) / rate
                console.status(f"  file {number}/{len(chunks)}: {so_far:,}/{len(pmids):,} "
                               f"papers, {rate:.0f}/s, about {fmt_duration(left)} left")
            writer.close()
        except BaseException:
            writer.close()
            tmp.unlink(missing_ok=True)
            raise
        os.replace(tmp, path)
        done += len(chunk)
        parts[name] = {"pmids": len(chunk), "records": writer.count,
                       "bytes": path.stat().st_size, "raw_bytes": writer.raw_bytes}
        save_manifest(folder / "manifest.json", manifest)
        console.log(f"  saved {name}: {writer.count:,} papers, {fmt_size(parts[name]['bytes'])}"
                    f"  ({done:,}/{len(pmids):,})")


def _columns(path, record):
    """CSV columns for `read --as csv`: the download's fields, since jsonl
    files leave empty fields out and the first record may lack some."""
    try:
        manifest = json.loads((path.parent / "manifest.json").read_text(encoding="utf-8"))
        fields = list(manifest["settings"]["fields"])
    except (OSError, ValueError, KeyError, TypeError):
        fields = list(ALL_FIELDS)
    return fields + [key for key in record if key not in fields]


def cmd_read(args, console):
    files = find_part_files(args.paths)
    if not files:
        raise SystemExit("Error: no data files found")
    needles = [g.lower() for g in args.grep or []]
    shown = 0
    csv_out = None
    for path in files:
        for record in iter_records(path):
            if isinstance(record, dict):
                haystack = " ".join(str(_cell(v)) for v in record.values()).lower()
            else:
                haystack = record.lower()
            if not all(n in haystack for n in needles):
                continue
            shown += 1
            if not args.count:
                if isinstance(record, str) or args.output_as == "txt":
                    text = record if isinstance(record, str) else format_txt(record)
                    print(text + "\n")
                elif args.output_as == "jsonl":
                    print(json.dumps(record, ensure_ascii=False))
                else:
                    if csv_out is None:
                        csv_out = csv.DictWriter(sys.stdout, _columns(path, record), restval="",
                                                 extrasaction="ignore", lineterminator="\n")
                        csv_out.writeheader()
                    csv_out.writerow({k: _cell(v) for k, v in record.items()})
            if args.limit and shown >= args.limit:
                break
        if args.limit and shown >= args.limit:
            break
    if args.count:
        print(f"{shown:,}")
    return 0


# --------------------------------------------------------------------------
# Guided mode


def _ask(prompt, default=""):
    shown = f" [{default}]" if default else ""
    return input(f"{prompt}{shown}: ").strip() or default


def _ask_choice(prompt, choices, default):
    while True:
        answer = _ask(f"{prompt} ({' / '.join(choices)})", default).lower()
        if answer in choices:
            return answer
        print(f"  Please type one of: {', '.join(choices)}")


def interactive():
    print("PubMed bulk downloader - press Ctrl+C at any time to stop.\n")
    print("Type a search the same way you would on pubmed.ncbi.nlm.nih.gov, e.g.")
    print('   crispr AND 2023[dp]      "heart failure"[mh] AND review[pt]\n')
    console = Console()
    client = EUtils(email=os.environ.get("NCBI_EMAIL"),
                    api_key=os.environ.get("NCBI_API_KEY"), log=console.log)
    try:
        while True:
            query = _ask("Search")
            if not query:
                continue
            try:
                total = int(client.esearch(query)["count"])
            except EUtilsError as err:
                print(f"  {err}")
                continue
            print(f"  {total:,} papers found.")
            if total:
                break
            print("  Try a broader search.")
        while True:
            how_many = _ask("How many do you want? (a number, or Enter for all)")
            if not how_many or (how_many.replace(",", "").isdigit() and int(how_many.replace(",", "")) > 0):
                break
            print("  Please type a whole number.")
        folder = _ask("Save into folder", default_folder(query))
        detail = _ask_choice("How much detail per paper?", list(FIELD_PRESETS), "standard")
        print("  jsonl = for code/pandas, csv = opens in Excel, txt = easy to read")
        fmt = _ask_choice("File format?", list(FORMATS), "jsonl")
    except (KeyboardInterrupt, EOFError):
        print("\nBye.")
        return 130
    argv = ["download", query, "-o", folder, "--fields", detail, "--format", fmt]
    if how_many:
        argv += ["--max", how_many.replace(",", "")]
    print(f"\nNext time you can run this directly:\n  python3 {Path(sys.argv[0]).name} "
          f"{shlex.join(argv)}\n")
    return main(argv)


# --------------------------------------------------------------------------
# Command line


def _positive_int(text):
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value < 1:
        raise argparse.ArgumentTypeError(f"expected a whole number above 0, got {text!r}")
    return value


def _batch_size(text):
    value = _positive_int(text)
    if value > 1000:
        raise argparse.ArgumentTypeError("batch size can be at most 1000")
    return value


DOWNLOAD_EPILOG = f"""\
detail levels for --fields:
  minimal    {', '.join(FIELD_PRESETS['minimal'])}
  standard   {', '.join(FIELD_PRESETS['standard'])}
  full       every field
  or your own list, e.g.  --fields pmid,title,abstract,year
{textwrap.fill(', '.join(ALL_FIELDS), 78, initial_indent="  all fields: ", subsequent_indent=" " * 14)}

examples:
  %(prog)s "crispr AND 2023[dp]"
  %(prog)s "covid-19 vaccine" --from 2022 --to 2023/06 --max 20000 -o vaccines
  %(prog)s "sepsis[mh]" --fields minimal --format csv --compress gz
  %(prog)s --ids-file my_pmids.txt --fields full

Stopped halfway? Run the exact same command again and it carries on.
"""


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--email", default=os.environ.get("NCBI_EMAIL"),
                        help="your email, which NCBI likes heavy users to send (or set NCBI_EMAIL)")
    common.add_argument("--api-key", default=os.environ.get("NCBI_API_KEY"),
                        help="free NCBI API key: 10 requests/s instead of 3 (or set NCBI_API_KEY)")
    dates = argparse.ArgumentParser(add_help=False)
    dates.add_argument("--from", dest="mindate", type=parse_date, metavar="DATE",
                       help="only papers published on/after DATE (YYYY, YYYY/MM or YYYY/MM/DD)")
    dates.add_argument("--to", dest="maxdate", type=lambda s: parse_date(s, end=True),
                       metavar="DATE", help="only papers published on/before DATE")

    parser = argparse.ArgumentParser(
        prog="pubmed_dl.py",
        description="Bulk-download PubMed papers into small compressed files. "
                    "Run with no arguments for a guided mode.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("count", parents=[common, dates],
                       help="show how many papers a search finds",
                       description="Show how many PubMed papers match a search.")
    p.add_argument("query", help='PubMed search, in quotes, e.g. "crispr AND 2023[dp]"')
    p.set_defaults(func=cmd_count)

    p = sub.add_parser("download", parents=[common, dates],
                       help="search PubMed and save the results",
                       description="Search PubMed and save the matching papers as "
                                   "small compressed files.",
                       epilog=DOWNLOAD_EPILOG,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", nargs="?", help='PubMed search, in quotes, e.g. "crispr AND 2023[dp]"')
    p.add_argument("-o", "--output", metavar="FOLDER",
                   help="folder to save into (default: pubmed_<your search>)")
    p.add_argument("-n", "--max", type=_positive_int, metavar="N",
                   help="download at most N papers (default: all of them)")
    p.add_argument("--sort", choices=("newest", "relevance"), default="newest",
                   help="which papers --max keeps (default: newest)")
    p.add_argument("--ids-file", type=Path, metavar="FILE",
                   help="download the PMIDs listed in FILE instead of searching")
    p.add_argument("--fields", default="standard", metavar="LEVEL",
                   help="minimal, standard (default), full, or a comma list - see below")
    p.add_argument("--format", choices=FORMATS, default="jsonl",
                   help="jsonl (default, for code), csv (for Excel) or txt (for reading)")
    p.add_argument("--compress", choices=list(COMPRESSION_EXT), default="xz",
                   help="xz (default, smallest), gz (opens anywhere), bz2 or none")
    p.add_argument("--per-file", type=_positive_int, default=1000, metavar="N",
                   help="papers per file (default: 1000)")
    p.add_argument("--abstract-chars", type=_positive_int, metavar="N",
                   help="shorten abstracts to N characters")
    p.add_argument("--max-authors", type=_positive_int, metavar="N",
                   help="keep only the first N authors")
    p.add_argument("--batch-size", type=_batch_size, default=200, metavar="N",
                   help="papers per request to NCBI (default: 200)")
    p.add_argument("--fresh", action="store_true",
                   help="throw away an earlier download in the folder and start over")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("read", help="show or filter papers from downloaded files",
                       description="Print papers from a download folder or data files.")
    p.add_argument("paths", nargs="+", metavar="PATH", help="download folder(s) or data file(s)")
    p.add_argument("-g", "--grep", action="append", metavar="TEXT",
                   help="only papers containing TEXT (case-insensitive; repeat to require several)")
    p.add_argument("-l", "--limit", type=_positive_int, metavar="N", help="stop after N papers")
    p.add_argument("--as", dest="output_as", choices=FORMATS, default="txt",
                   help="print as txt (default), jsonl or csv")
    p.add_argument("--count", action="store_true", help="only print how many papers match")
    p.set_defaults(func=cmd_read)
    return parser


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    argv = sys.argv[1:] if argv is None else list(argv)
    parser = build_parser()
    if not argv:
        if sys.stdin.isatty():
            return interactive()
        parser.print_help()
        return 2
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    console = Console()
    try:
        return args.func(args, console)
    except EUtilsError as err:
        console.log(f"Error: {err}")
        return 1
    except KeyboardInterrupt:
        hint = " Run the same command again to carry on." if args.command == "download" else ""
        console.log(f"\nStopped.{hint}")
        return 130
    except BrokenPipeError:  # e.g. piped into `head`
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    sys.exit(main())
