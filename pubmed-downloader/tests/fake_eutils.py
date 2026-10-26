"""A small stand-in for NCBI E-utilities so pubmed_dl can be tested offline.

It copies the behaviour pubmed_dl depends on: esearch refuses to list past
the first 9,999 hits, publication-date searches also match a paper's online
(epub) date, and efetch returns PubMed-style XML. Failures can be queued to
test the retry logic.
"""

import json
import re
import threading
import urllib.parse
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from xml.sax.saxutils import escape

SEARCH_LIMIT = 9999
RANGE_RE = re.compile(r'(AND|NOT) \("(\d{4}/\d\d/\d\d)"\[(\w+)\] : "(\d{4}/\d\d/\d\d)"\[\w+\]\)')
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def parse_day(text):
    y, m, d = (int(x) for x in text.split("/"))
    return date(y, m, d)


def make_articles(n, start=date(2015, 1, 1), days=3650, first_pmid=1000, word="tumor",
                  epub_every=7):
    """`n` articles spread evenly over `days`; every `epub_every`-th also has an
    online date 40 days before its print date."""
    articles = {}
    for i in range(n):
        pdat = start + timedelta(days=(i * days) // max(n, 1))
        articles[first_pmid + i] = {
            "pdat": pdat,
            "epub": pdat - timedelta(days=40) if epub_every and i % epub_every == 0 else None,
            "edat": pdat + timedelta(days=i % 30),
            "title": f"Study {i} of {word} growth",
        }
    return articles


class FakePubMed:
    def __init__(self, articles):
        self.articles = articles
        self.calls = {"esearch": 0, "efetch": 0}
        self.fetched = []          # every PMID batch efetch was asked for
        self.failures = []         # queued efetch failures: "503", "429", "truncated", "400"
        self._lock = threading.Lock()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    # -- search ------------------------------------------------------------

    def _dates(self, article, datetype):
        if datetype == "pdat":
            return [d for d in (article["pdat"], article["epub"]) if d]
        return [article[datetype]]

    def _in_range(self, article, datetype, lo, hi):
        return any(lo <= d <= hi for d in self._dates(article, datetype))

    def search(self, params):
        term = params["term"]
        ranges = RANGE_RE.findall(term)
        base = RANGE_RE.sub("", term).strip().strip("()").strip().lower()
        hits = []
        for pmid, art in self.articles.items():
            if base != "all" and base not in art["title"].lower():
                continue
            ok = True
            for op, lo, datetype, hi in ranges:
                inside = self._in_range(art, datetype.lower(), parse_day(lo), parse_day(hi))
                ok &= inside if op == "AND" else not inside
            if ok and "datetype" in params:
                ok = self._in_range(art, params["datetype"], parse_day(params["mindate"]),
                                    parse_day(params["maxdate"]))
            if ok:
                hits.append(pmid)
        if params.get("sort") == "pub_date":
            hits.sort(key=lambda p: (max(self._dates(self.articles[p], "pdat")), p), reverse=True)
        elif params.get("sort") == "relevance":
            hits.sort()
        else:
            hits.sort(reverse=True)
        retstart = int(params.get("retstart", 0))
        retmax = int(params.get("retmax", 20))
        if retstart + retmax > SEARCH_LIMIT:
            return {"esearchresult": {"ERROR": "Search Backend failed: Exception:\n'retstart' "
                                               "cannot be larger than 9998."}}
        return {"esearchresult": {
            "count": str(len(hits)), "retmax": str(min(retmax, len(hits))),
            "retstart": str(retstart),
            "idlist": [str(p) for p in hits[retstart:retstart + retmax]],
            "querytranslation": term,
        }}

    # -- fetch -------------------------------------------------------------

    def article_xml(self, pmid):
        art = self.articles[pmid]
        d = art["pdat"]
        if pmid % 2:
            abstract = f"<AbstractText>Plain abstract about {escape(art['title'])}.</AbstractText>"
        else:
            abstract = ('<AbstractText Label="BACKGROUND">Why <i>this</i> matters.</AbstractText>'
                        '<AbstractText Label="RESULTS">It worked (P&lt;0.05).</AbstractText>')
        authors = "".join(
            f"<Author><LastName>Author{k}</LastName><ForeName>A</ForeName><Initials>A</Initials>"
            f"<AffiliationInfo><Affiliation>Lab {k % 2}</Affiliation></AffiliationInfo></Author>"
            for k in range(1 + pmid % 6))
        return f"""<PubmedArticle>
<MedlineCitation Status="MEDLINE" Owner="NLM"><PMID Version="1">{pmid}</PMID>
<Article PubModel="Print"><Journal><JournalIssue CitedMedium="Print"><Volume>{d.year - 1990}</Volume>
<Issue>{d.month}</Issue><PubDate><Year>{d.year}</Year><Month>{MONTHS[d.month - 1]}</Month>
<Day>{d.day:02d}</Day></PubDate></JournalIssue><Title>Journal of Tests</Title>
<ISOAbbreviation>J Tests</ISOAbbreviation></Journal>
<ArticleTitle>{escape(art['title'])}</ArticleTitle><Pagination><MedlinePgn>1-9</MedlinePgn></Pagination>
<Abstract>{abstract}</Abstract><AuthorList CompleteYN="Y">{authors}</AuthorList>
<Language>eng</Language><PublicationTypeList><PublicationType UI="D016428">Journal Article</PublicationType>
</PublicationTypeList></Article>
<MeshHeadingList><MeshHeading><DescriptorName UI="D006801">Humans</DescriptorName></MeshHeading>
<MeshHeading><DescriptorName UI="D009369">Neoplasms</DescriptorName></MeshHeading></MeshHeadingList>
<KeywordList Owner="NOTNLM"><Keyword>growth</Keyword></KeywordList></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="pubmed">{pmid}</ArticleId>
<ArticleId IdType="doi">10.1000/test.{pmid}</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle>"""

    def fetch(self, params):
        pmids = [int(p) for p in params["id"].split(",") if p]
        with self._lock:
            self.fetched.append(pmids)
        body = "".join(self.article_xml(p) for p in pmids if p in self.articles)
        return ('<?xml version="1.0" ?>\n<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD '
                'PubMedArticle, 1st January 2025//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/'
                f'out/pubmed_250101.dtd">\n<PubmedArticleSet>{body}</PubmedArticleSet>')

    # -- HTTP --------------------------------------------------------------

    def _handler_class(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, body, ctype="text/plain", headers=()):
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for key, value in headers:
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                params = dict(urllib.parse.parse_qsl(self.rfile.read(length).decode()))
                endpoint = self.path.rsplit("/", 1)[-1]
                if params.get("db") != "pubmed" or params.get("tool") != "pubmed_dl":
                    return self._reply(400, "bad params")
                if endpoint == "esearch.fcgi":
                    fake.calls["esearch"] += 1
                    return self._reply(200, json.dumps(fake.search(params)), "application/json")
                if endpoint == "efetch.fcgi":
                    fake.calls["efetch"] += 1
                    failure = fake.failures.pop(0) if fake.failures else None
                    if failure == "503":
                        return self._reply(503, "Service unavailable")
                    if failure == "429":
                        return self._reply(429, '{"error":"API rate limit exceeded"}',
                                           headers=[("Retry-After", "1")])
                    if failure == "400":
                        return self._reply(400, '{"error":"API key invalid"}')
                    xml = fake.fetch(params)
                    if failure == "truncated":
                        xml = xml[: len(xml) // 2]
                    return self._reply(200, xml, "text/xml")
                return self._reply(404, "unknown endpoint")

        return Handler
