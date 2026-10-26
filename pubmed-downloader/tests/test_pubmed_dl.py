"""Tests for pubmed_dl, run offline against tests/fake_eutils.py.

    python3 -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import pubmed_dl  # noqa: E402
from fake_eutils import FakePubMed, make_articles  # noqa: E402

SAMPLE_XML = b"""<?xml version="1.0" ?>
<!DOCTYPE PubmedArticleSet PUBLIC "-//NLM//DTD PubMedArticle, 1st January 2025//EN" "https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd">
<PubmedArticleSet>
<PubmedArticle>
  <MedlineCitation Status="MEDLINE" Owner="NLM">
    <PMID Version="1">36000001</PMID>
    <Article PubModel="Print-Electronic">
      <Journal>
        <ISSN IssnType="Electronic">1476-4687</ISSN>
        <JournalIssue CitedMedium="Internet">
          <Volume>612</Volume><Issue>7940</Issue>
          <PubDate><Year>2022</Year><Month>Dec</Month><Day>08</Day></PubDate>
        </JournalIssue>
        <Title>Nature</Title>
        <ISOAbbreviation>Nature</ISOAbbreviation>
      </Journal>
      <ArticleTitle>Prime editing of <i>CFTR</i> in human
        organoids.</ArticleTitle>
      <Pagination><StartPage>123</StartPage><EndPage>130</EndPage><MedlinePgn>123-130</MedlinePgn></Pagination>
      <ELocationID EIdType="pii" ValidYN="Y">S0000-0000(22)00001-1</ELocationID>
      <Abstract>
        <AbstractText Label="BACKGROUND" NlmCategory="BACKGROUND">Cystic fibrosis is caused by <i>CFTR</i> mutations.</AbstractText>
        <AbstractText Label="RESULTS" NlmCategory="RESULTS">Editing reached 30% (P&lt;0.01) using H<sub>2</sub>O controls.</AbstractText>
        <CopyrightInformation>(c) 2022. The Author(s).</CopyrightInformation>
      </Abstract>
      <AuthorList CompleteYN="Y">
        <Author ValidYN="Y"><LastName>Geurts</LastName><ForeName>Maarten H</ForeName><Initials>MH</Initials>
          <AffiliationInfo><Affiliation>Hubrecht Institute, Utrecht, The Netherlands.</Affiliation></AffiliationInfo></Author>
        <Author ValidYN="Y"><LastName>de Poel</LastName><ForeName>Eyleen</ForeName><Initials>E</Initials>
          <AffiliationInfo><Affiliation>Hubrecht Institute, Utrecht, The Netherlands.</Affiliation></AffiliationInfo>
          <AffiliationInfo><Affiliation>UMC Utrecht, The Netherlands.</Affiliation></AffiliationInfo></Author>
        <Author ValidYN="Y"><CollectiveName>CF Organoid Consortium</CollectiveName></Author>
      </AuthorList>
      <Language>eng</Language>
      <PublicationTypeList>
        <PublicationType UI="D016428">Journal Article</PublicationType>
        <PublicationType UI="D013485">Research Support, Non-U.S. Gov't</PublicationType>
      </PublicationTypeList>
      <ArticleDate DateType="Electronic"><Year>2022</Year><Month>11</Month><Day>30</Day></ArticleDate>
    </Article>
    <MedlineJournalInfo><Country>England</Country><MedlineTA>Nature</MedlineTA></MedlineJournalInfo>
    <MeshHeadingList>
      <MeshHeading><DescriptorName UI="D006801" MajorTopicYN="N">Humans</DescriptorName></MeshHeading>
      <MeshHeading><DescriptorName UI="D003550" MajorTopicYN="Y">Cystic Fibrosis</DescriptorName>
        <QualifierName UI="Q000235" MajorTopicYN="N">genetics</QualifierName></MeshHeading>
    </MeshHeadingList>
    <KeywordList Owner="NOTNLM">
      <Keyword MajorTopicYN="N">prime editing</Keyword><Keyword MajorTopicYN="N">organoids</Keyword>
      <Keyword MajorTopicYN="N">prime editing</Keyword>
    </KeywordList>
  </MedlineCitation>
  <PubmedData>
    <PublicationStatus>ppublish</PublicationStatus>
    <ArticleIdList>
      <ArticleId IdType="pubmed">36000001</ArticleId>
      <ArticleId IdType="pmc">PMC9700001</ArticleId>
      <ArticleId IdType="doi">10.1038/s41586-022-00001-1</ArticleId>
    </ArticleIdList>
    <ReferenceList><Reference><Citation>Some cited paper.</Citation>
      <ArticleIdList><ArticleId IdType="doi">10.9999/not-this-one</ArticleId>
      <ArticleId IdType="pmc">PMC0000000</ArticleId></ArticleIdList></Reference></ReferenceList>
  </PubmedData>
</PubmedArticle>
<PubmedArticle>
  <MedlineCitation Status="MEDLINE" Owner="NLM">
    <PMID Version="1">9500002</PMID>
    <Article PubModel="Print">
      <Journal>
        <JournalIssue CitedMedium="Print"><Volume>12</Volume>
          <PubDate><MedlineDate>1998 Spring-Summer</MedlineDate></PubDate></JournalIssue>
        <Title>Journal of Old Things</Title>
      </Journal>
      <ArticleTitle>An old paper without an abstract.</ArticleTitle>
      <ELocationID EIdType="doi" ValidYN="Y">10.5555/old.2</ELocationID>
      <AuthorList><Author><LastName>Smith</LastName><ForeName>Jane</ForeName></Author></AuthorList>
      <Language>eng</Language><Language>fre</Language>
    </Article>
    <MedlineJournalInfo><MedlineTA>J Old Things</MedlineTA></MedlineJournalInfo>
  </MedlineCitation>
  <PubmedData><ArticleIdList><ArticleId IdType="pubmed">9500002</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle>
<PubmedBookArticle>
  <BookDocument>
    <PMID Version="1">20301428</PMID>
    <ArticleIdList><ArticleId IdType="bookaccession">NBK1250</ArticleId></ArticleIdList>
    <Book>
      <Publisher><PublisherName>University of Washington, Seattle</PublisherName></Publisher>
      <BookTitle book="gene">GeneReviews<sup>(R)</sup></BookTitle>
      <PubDate><Year>1993</Year></PubDate>
      <AuthorList Type="editors"><Author><LastName>Adam</LastName><ForeName>Margaret P</ForeName><Initials>MP</Initials></Author></AuthorList>
    </Book>
    <ArticleTitle book="gene" part="cf">Cystic Fibrosis</ArticleTitle>
    <Language>eng</Language>
    <AuthorList Type="authors"><Author><LastName>Ong</LastName><ForeName>Thida</ForeName><Initials>T</Initials></Author></AuthorList>
    <PublicationType UI="D016454">Review</PublicationType>
    <Abstract><AbstractText Label="CLINICAL CHARACTERISTICS">CF affects the lungs.</AbstractText></Abstract>
  </BookDocument>
  <PubmedBookData><ArticleIdList><ArticleId IdType="pubmed">20301428</ArticleId></ArticleIdList></PubmedBookData>
</PubmedBookArticle>
</PubmedArticleSet>
"""


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.records = pubmed_dl.parse_articles(ET.fromstring(SAMPLE_XML))

    def test_modern_article(self):
        rec = self.records[0]
        self.assertEqual(rec["pmid"], "36000001")
        self.assertEqual(rec["title"], "Prime editing of CFTR in human organoids.")
        self.assertEqual(rec["abstract"],
                         "BACKGROUND: Cystic fibrosis is caused by CFTR mutations. "
                         "RESULTS: Editing reached 30% (P<0.01) using H2O controls.")
        self.assertEqual(rec["authors"], ["Geurts MH", "de Poel E", "CF Organoid Consortium"])
        self.assertEqual(rec["affiliations"], ["Hubrecht Institute, Utrecht, The Netherlands.",
                                               "UMC Utrecht, The Netherlands."])
        self.assertEqual((rec["journal"], rec["journal_abbrev"]), ("Nature", "Nature"))
        self.assertEqual((rec["year"], rec["pub_date"]), (2022, "2022 Dec 08"))
        self.assertEqual((rec["volume"], rec["issue"], rec["pages"]), ("612", "7940", "123-130"))
        self.assertEqual(rec["doi"], "10.1038/s41586-022-00001-1")
        self.assertEqual(rec["pmcid"], "PMC9700001")
        self.assertEqual(rec["mesh"], ["Humans", "Cystic Fibrosis"])
        self.assertEqual(rec["keywords"], ["prime editing", "organoids"])
        self.assertEqual(rec["pub_types"], ["Journal Article", "Research Support, Non-U.S. Gov't"])
        self.assertEqual(rec["language"], "eng")

    def test_old_article_fallbacks(self):
        rec = self.records[1]
        self.assertEqual((rec["year"], rec["pub_date"]), (1998, "1998 Spring-Summer"))
        self.assertEqual(rec["doi"], "10.5555/old.2")  # from ELocationID
        self.assertEqual(rec["pmcid"], "")
        self.assertEqual(rec["journal_abbrev"], "J Old Things")  # from MedlineTA
        self.assertEqual(rec["authors"], ["Smith Jane"])
        self.assertEqual(rec["abstract"], "")
        self.assertEqual(rec["language"], "eng,fre")

    def test_book_chapter(self):
        rec = self.records[2]
        self.assertEqual(rec["pmid"], "20301428")
        self.assertEqual(rec["title"], "Cystic Fibrosis")
        self.assertEqual(rec["journal"], "GeneReviews(R)")
        self.assertEqual(rec["year"], 1993)
        self.assertEqual(rec["authors"], ["Ong T"])
        self.assertEqual(rec["pub_types"], ["Review"])
        self.assertEqual(rec["abstract"], "CLINICAL CHARACTERISTICS: CF affects the lungs.")

    def test_shape_trims_and_drops_empty(self):
        rec = dict(self.records[0], abstract="x" * 50)
        out = pubmed_dl.shape(rec, ["pmid", "abstract", "authors", "pmcid", "issue"],
                              abstract_chars=10, max_authors=2)
        self.assertEqual(out, {"pmid": "36000001", "abstract": "x" * 10 + "...",
                               "authors": ["Geurts MH", "de Poel E", "et al."],
                               "pmcid": "PMC9700001", "issue": "7940"})
        self.assertNotIn("doi", pubmed_dl.shape(self.records[2], ["pmid", "doi"]))

    def test_txt_block(self):
        text = pubmed_dl.format_txt(pubmed_dl.shape(self.records[0], pubmed_dl.FIELD_PRESETS["standard"]))
        self.assertTrue(text.startswith("PMID 36000001 | 2022 | Nature | doi:10.1038/"))
        self.assertIn("\nAuthors: Geurts MH; de Poel E; CF Organoid Consortium\n", text)
        self.assertNotIn("\n\n", text)

    def test_error_and_empty_replies(self):
        with self.assertRaises(pubmed_dl._Retry):
            pubmed_dl._parse_efetch(SAMPLE_XML[:500])
        empty = b"<eFetchResult><ERROR>Empty result - nothing to do</ERROR></eFetchResult>"
        self.assertEqual(pubmed_dl._parse_efetch(empty), [])
        with self.assertRaises(pubmed_dl._Retry):
            pubmed_dl._parse_esearch(b'{"esearchresult": {"ERROR": "Invalid query"}}')


class HelperTests(unittest.TestCase):
    def test_parse_date(self):
        self.assertEqual(pubmed_dl.parse_date("2020"), date(2020, 1, 1))
        self.assertEqual(pubmed_dl.parse_date("2020", end=True), date(2020, 12, 31))
        self.assertEqual(pubmed_dl.parse_date("2020/02", end=True), date(2020, 2, 29))
        self.assertEqual(pubmed_dl.parse_date("2020-05-17"), date(2020, 5, 17))
        for bad in ("", "20x0", "2020/13", "2020/1/2/3"):
            with self.assertRaises(pubmed_dl.argparse.ArgumentTypeError):
                pubmed_dl.parse_date(bad)

    def test_read_id_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "ids.txt")
            path.write_text("PMID: 123\n456, 789;123\nnot-an-id\n\n", encoding="utf-8")
            self.assertEqual(pubmed_dl.read_id_file(path), ["123", "456", "789"])

    def test_resolve_fields(self):
        self.assertEqual(pubmed_dl.resolve_fields("title,doi"), ["pmid", "title", "doi"])
        with self.assertRaises(SystemExit):
            pubmed_dl.resolve_fields("title,colour")

    def test_default_folder(self):
        self.assertEqual(pubmed_dl.default_folder('"Heart Failure"[mh] AND 2020[dp]'),
                         "pubmed_heart_failure_mh_and_2020_dp")


class ServerTestCase(unittest.TestCase):
    """Runs the CLI against a fake NCBI, with sleeping switched off."""

    articles = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.fake = FakePubMed(dict(self.articles or make_articles(50)))
        self.fake.__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        for patch in (mock.patch.dict(os.environ, {"PUBMED_EUTILS_URL": self.fake.url}),
                      mock.patch("pubmed_dl.time.sleep")):
            patch.start()
            self.addCleanup(patch.stop)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = pubmed_dl.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def client(self):
        return pubmed_dl.EUtils()

    def records(self, folder):
        recs = []
        for path in pubmed_dl.find_part_files([folder]):
            recs.extend(pubmed_dl.iter_records(path))
        return recs


class DownloadTests(ServerTestCase):
    def test_download_jsonl_xz_in_parts(self):
        out = self.dir / "tumor"
        code, _, err = self.cli("download", "tumor", "-o", str(out), "--per-file", "20",
                                "--batch-size", "7")
        self.assertEqual(code, 0, err)
        names = sorted(p.name for p in out.iterdir())
        self.assertEqual(names, ["manifest.json", "part-00001.jsonl.xz", "part-00002.jsonl.xz",
                                 "part-00003.jsonl.xz", "pmids.txt.gz"])
        recs = self.records(out)
        self.assertEqual(len(recs), 50)
        self.assertEqual(len({r["pmid"] for r in recs}), 50)
        self.assertEqual(set(recs[0]), {"pmid", "title", "abstract", "authors", "journal",
                                        "year", "doi", "mesh", "keywords"})
        manifest = json.loads((out / "manifest.json").read_text())
        self.assertTrue(manifest["complete"])
        self.assertEqual((manifest["records"], manifest["total_matches"]), (50, 50))
        self.assertIn("Done: 50 papers in 3 file(s)", err)
        self.assertTrue(all(len(batch) <= 7 for batch in self.fake.fetched))

    def test_formats_and_compression_round_trip(self):
        for fmt, comp in (("csv", "gz"), ("txt", "none"), ("jsonl", "bz2")):
            with self.subTest(fmt=fmt, comp=comp):
                out = self.dir / f"{fmt}_{comp}"
                code, _, err = self.cli("download", "tumor", "-o", str(out), "--format", fmt,
                                        "--compress", comp, "--fields", "minimal")
                self.assertEqual(code, 0, err)
                part = out / pubmed_dl.part_name(1, fmt, comp)
                self.assertTrue(part.exists())
                code, text, _ = self.cli("read", str(out), "--count")
                self.assertEqual(text.strip(), "50")
                code, text, _ = self.cli("read", str(out), "--grep", "study 7 ", "--grep", "TUMOR")
                self.assertIn("Study 7 of tumor growth", text)
                self.assertNotIn("Study 8 ", text)

    def test_read_output_styles(self):
        out = self.dir / "r"
        self.cli("download", "tumor", "-o", str(out), "--fields", "pmid,title,authors")
        _, text, _ = self.cli("read", str(out), "--as", "csv", "--limit", "2")
        lines = text.splitlines()
        self.assertEqual(lines[0], "pmid,title,authors")
        self.assertEqual(len(lines), 3)
        _, text, _ = self.cli("read", str(out / "part-00001.jsonl.xz"), "--as", "jsonl", "-l", "1")
        self.assertEqual(set(json.loads(text)), {"pmid", "title", "authors"})

    def test_read_csv_keeps_columns_missing_from_jsonl(self):
        out = self.dir / "gaps"
        self.cli("download", "tumor", "-o", str(out), "--fields", "pmid,pmcid,title")
        self.assertNotIn("pmcid", self.records(out)[0])  # empty, so left out of the jsonl
        _, text, _ = self.cli("read", str(out), "--as", "csv", "--limit", "1")
        self.assertEqual(text.splitlines()[0], "pmid,pmcid,title")

    def test_max_sort_and_trimming(self):
        out = self.dir / "small"
        code, _, err = self.cli("download", "tumor", "-o", str(out), "--max", "5",
                                "--abstract-chars", "12", "--max-authors", "1")
        self.assertEqual(code, 0, err)
        recs = self.records(out)
        newest = sorted(self.fake.articles, key=lambda p: self.fake.articles[p]["pdat"])[-5:]
        self.assertEqual({int(r["pmid"]) for r in recs}, set(newest))
        self.assertTrue(all(len(r["abstract"]) <= 15 for r in recs))
        self.assertTrue(all(len(r["authors"]) <= 2 for r in recs))

        out = self.dir / "best"
        self.cli("download", "tumor", "-o", str(out), "--max", "3", "--sort", "relevance")
        self.assertEqual(sorted(int(r["pmid"]) for r in self.records(out)), [1000, 1001, 1002])

    def test_date_range(self):
        out = self.dir / "dated"
        code, _, err = self.cli("download", "tumor", "-o", str(out), "--from", "2016/03",
                                "--to", "2017")
        self.assertEqual(code, 0, err)
        expected = {p for p, a in self.fake.articles.items()
                    if any(date(2016, 3, 1) <= d <= date(2017, 12, 31)
                           for d in (a["pdat"], a["epub"]) if d)}
        self.assertTrue(expected)
        self.assertEqual({int(r["pmid"]) for r in self.records(out)}, expected)

    def test_ids_file_and_missing_records(self):
        ids = self.dir / "mine.txt"
        ids.write_text("1003\n1004\n999999\n", encoding="utf-8")
        out = self.dir / "ids"
        code, _, err = self.cli("download", "--ids-file", str(ids), "-o", str(out))
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(r["pmid"] for r in self.records(out)), ["1003", "1004"])
        self.assertIn("1 PMIDs had no record", err)

    def test_resume_only_fetches_missing_parts(self):
        out = self.dir / "resume"
        args = ("download", "tumor", "-o", str(out), "--per-file", "10", "--batch-size", "10")
        self.assertEqual(self.cli(*args)[0], 0)
        first_run = len(self.fake.fetched)
        (out / "part-00003.jsonl.xz").unlink()
        code, _, err = self.cli(*args)
        self.assertEqual(code, 0, err)
        self.assertIn("Resuming", err)
        self.assertEqual(len(self.fake.fetched), first_run + 1)
        self.assertEqual(len(self.records(out)), 50)

        code, _, err = self.cli("download", "tumor", "-o", str(out), "--format", "csv")
        self.assertNotEqual(code, 0)
        code, _, err = self.cli("download", "tumor", "-o", str(out), "--format", "csv", "--fresh")
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["manifest.json", "part-00001.csv.xz", "pmids.txt.gz"])

    def test_interrupted_part_leaves_no_file(self):
        out = self.dir / "stop"
        real = pubmed_dl.EUtils.efetch
        calls = []

        def flaky(client, pmids):
            calls.append(pmids)
            if len(calls) == 3:
                raise KeyboardInterrupt
            return real(client, pmids)

        with mock.patch.object(pubmed_dl.EUtils, "efetch", flaky):
            code, _, err = self.cli("download", "tumor", "-o", str(out), "--per-file", "20",
                                    "--batch-size", "10")
        self.assertEqual(code, 130)
        self.assertIn("Run the same command again", err)
        self.assertEqual(sorted(p.name for p in out.iterdir()),
                         ["manifest.json", "part-00001.jsonl.xz", "pmids.txt.gz"])
        code, _, err = self.cli("download", "tumor", "-o", str(out), "--per-file", "20",
                                "--batch-size", "10")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.records(out)), 50)

    def test_retries_then_succeeds(self):
        self.fake.failures = ["503", "truncated", "429"]
        out = self.dir / "retry"
        code, _, err = self.cli("download", "tumor", "-o", str(out))
        self.assertEqual(code, 0, err)
        self.assertEqual(len(self.records(out)), 50)
        self.assertIn("HTTP 503 - retrying", err)
        self.assertIn("incomplete article data - retrying", err)

    def test_permanent_error_stops(self):
        self.fake.failures = ["400"]
        code, _, err = self.cli("download", "tumor", "-o", str(self.dir / "bad"))
        self.assertEqual(code, 1)
        self.assertIn("HTTP 400", err)
        self.assertIn("API key invalid", err)

    def test_export_command(self):
        out = self.dir / "ex"
        self.cli("download", "tumor", "-o", str(out))
        code, _, err = self.cli("export", str(out), "--words", "600", "-o", str(self.dir / "ai"))
        self.assertEqual(code, 0, err)
        files = sorted((self.dir / "ai").glob("papers-*.md"))
        self.assertGreater(len(files), 1)
        self.assertTrue(all(len(f.read_text().split()) <= 700 for f in files))
        text = "".join(f.read_text() for f in files)
        self.assertEqual(text.count("\n## Study "), 50)
        self.assertTrue((self.dir / "ai.zip").exists())
        code, _, err = self.cli("export", str(out), "--as", "csv", "--grep", "study 4")
        self.assertEqual(code, 0, err)
        (csv_file,) = (out / "exports").glob("spreadsheet *.csv")
        self.assertEqual(len(csv_file.read_text(encoding="utf-8-sig").splitlines()), 1 + 11)

    def test_count_and_no_results(self):
        code, text, _ = self.cli("count", "tumor")
        self.assertEqual((code, text.splitlines()[0]), (0, "50 papers match"))
        code, _, err = self.cli("download", "zebrafish", "-o", str(self.dir / "none"))
        self.assertEqual(code, 0)
        self.assertIn("Nothing to download", err)

    def test_query_or_ids_file_required(self):
        code, _, err = self.cli("download")
        self.assertNotEqual(code, 0)


class BigSearchTests(ServerTestCase):
    """More than 9,999 hits: the search must be split into date windows."""

    articles = make_articles(25_000, word="all")

    def test_collects_everything_once(self):
        # One paper with a date far outside the windows, found by the sweep.
        self.fake.articles[999] = {"pdat": date(1500, 1, 1), "epub": None,
                                   "edat": date(2000, 1, 1), "title": "ancient"}
        args = mock.Mock(ids_file=None, query="all", mindate=None, maxdate=None,
                         max=None, sort="newest")
        console = pubmed_dl.Console(io.StringIO())
        pmids, info = pubmed_dl.find_pmids(self.client(), args, console)
        self.assertEqual(info["total_matches"], 25_001)
        self.assertEqual(len(pmids), len(set(pmids)))
        self.assertEqual(set(map(int, pmids)), set(self.fake.articles))
        # Newest first, window by window.
        pdats = [self.fake.articles[int(p)]["pdat"] for p in pmids[:-1]]
        self.assertEqual(pdats[0], max(a["pdat"] for a in self.fake.articles.values()))
        self.assertEqual(pmids[-1], "999")

    def test_limit_keeps_newest(self):
        pmids = pubmed_dl.collect_pmids(self.client(), "all", pubmed_dl.EARLIEST,
                                        pubmed_dl.latest_date(), limit=12_000)
        self.assertEqual(len(pmids), 12_000)
        self.assertEqual(len(set(pmids)), 12_000)
        dates = sorted(a["pdat"] for a in self.fake.articles.values())
        kept = {self.fake.articles[int(p)]["pdat"] for p in pmids}
        self.assertIn(dates[-1], kept)
        self.assertNotIn(dates[0], kept)


class CrowdedDayTests(ServerTestCase):
    """12,000 papers on one publication day: split again by entry date."""

    articles = {**make_articles(12_000, start=date(2020, 1, 1), days=0, epub_every=0, word="all"),
                **make_articles(500, first_pmid=50_000, word="all")}

    def test_single_day_is_split_by_entry_date(self):
        pmids = pubmed_dl.collect_pmids(self.client(), "all", pubmed_dl.EARLIEST,
                                        pubmed_dl.latest_date())
        self.assertEqual(len(pmids), 12_500)
        self.assertEqual(set(map(int, pmids)), set(self.fake.articles))


if __name__ == "__main__":
    unittest.main()
