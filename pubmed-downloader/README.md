# PubMed Downloader

Download as many PubMed papers as you want, on any topic, keep them in small
compressed files, and turn them into files you can give to an AI (ChatGPT,
Claude, Gemini, NotebookLM) or open in Excel.

- **Pick papers by keyword**: type the words papers must (or must not)
  mention, pick study types and years, and see live how many papers match.
- **No 10,000 cap**: PubMed only lists the first 9,999 hits of a search, so
  bigger searches are split by date automatically. Downloads of hundreds of
  thousands of papers work; they just take a while.
- **Small files**: choose how much detail to keep per paper, optionally
  shorten abstracts, and everything is stored compressed.
- **Ready for AI**: one click turns a collection (or only the papers
  mentioning certain words) into Markdown files sized for AI tools, plus a
  zip of them.
- **Resumable**: stop any time and carry on later from where it stopped.

There are two ways to use it: the **Mac app** (no Terminal) or the
**command line** (any computer).

## Mac app (no Terminal needed)

### Setting it up (once)

1. Download **[PubMed-Downloader-Mac.zip](PubMed-Downloader-Mac.zip)**
   (on GitHub, open the file and click the download button), then
   double-click it in your Downloads folder.
2. Drag **PubMed Downloader** into your **Applications** folder.
3. Double-click it. The first time, macOS warns that it can't check the app,
   because it isn't from the App Store. Allow it once:
   - **macOS 15 Sequoia or newer:** click *Done*, open **System Settings →
     Privacy & Security**, scroll down to the message about PubMed
     Downloader, click **Open Anyway**, and confirm.
   - **macOS 14 or older:** right-click (or Control-click) the app, choose
     **Open**, then click **Open** again.
4. If your Mac doesn't have Python yet, the app offers to install it. Click
   **Install**, wait a few minutes for it to finish, then open the app again.
   (Or install Python yourself from
   [python.org](https://www.python.org/downloads/macos/).)

That's it. From then on, just double-click the app.

### Using it

The app opens in your web browser (it runs only on your own Mac; nothing is
sent anywhere except searches to PubMed).

1. **New collection**: type your keywords, for example *must mention*
   `intermittent fasting, insulin` and *leave out* `mice, rats`. Pick years
   and study types if you like. The box at the bottom shows how many papers
   match.
2. Choose how many papers (all, or only the newest N) and how much detail
   (Minimal, Standard or Everything), then click **Download papers**. You can
   keep using the app, or close the tab, while it works. Click **Stop** any
   time and **Resume** later.
3. **Library**: browse and search your collections, see where they are in
   Finder, and export them:
   - **Export for AI** makes Markdown (`.md`) files plus one `.zip`. Choose
     the file size: *Small* (~20,000 words, easy to attach to any chat),
     *Medium* (~100,000 words, for tools that read long documents) or
     *Large* (~400,000 words, for tools that take very big sources like
     NotebookLM). You can export only the papers that mention certain words.
   - **Spreadsheet** makes one CSV file that opens in Excel or Numbers.
4. **Settings**: paste a free NCBI API key to download up to ~3x faster (the
   page explains how to get one).

Your collections are in the **PubMed Library** folder in your home folder.
Click **Quit** at the top right to close the app. It also closes by itself
about 20 minutes after you close the tab, unless a download is running.

### Giving your papers to an AI

- **ChatGPT, Claude, Gemini**: start a chat, attach one or more of the
  `papers-001.md` files (paperclip or "+" button), and ask your question, for
  example *"Summarise what these studies found about insulin resistance, and
  cite PMIDs."* For a big collection, export with a keyword filter or use a
  project/knowledge feature so the AI can work across many files.
- **NotebookLM**: create a notebook and add the `.md` files as sources
  (choose the *Large* size so you need fewer files).
- Every paper in the files starts with its title, then PMID, year, journal
  and DOI, so the AI can point you back to the original papers.
- AI tools have limits on how much text they read at once. If a collection
  is too big, narrow it down: export only papers mentioning a keyword, or
  download fewer papers.

### If something goes wrong

- The app keeps a log at `~/Library/Logs/PubMed Downloader/app.log`
  (in Finder: **Go → Go to Folder…** and paste that path).
- "Couldn't reach PubMed": check your internet connection.
- The browser tab says the app is closed: double-click the app again.

## Command line

The same engine also works from the Terminal on any computer with Python 3.8
or newer. Nothing to install.

### Quick start

```bash
python3 pubmed_dl.py
```

With no arguments it asks you a few questions (what to search for, how many
papers, which folder) and then downloads. It also prints the full command, so
next time you can skip the questions. On Windows, type `python` or `py`
instead of `python3`.

### Commands

**Check how big a search is first:**

```bash
python3 pubmed_dl.py count "crispr AND 2023[dp]"
```

**Download:**

```bash
# Everything matching a search, into the folder "crispr2023"
python3 pubmed_dl.py download "crispr AND 2023[dp]" -o crispr2023

# Only the newest 20,000 papers from a date range
python3 pubmed_dl.py download "covid-19 vaccine" --from 2022 --to 2023/06 --max 20000

# Very small files: titles, years, journals and DOIs only, as CSV for Excel
python3 pubmed_dl.py download "sepsis[mh]" --fields minimal --format csv

# Choose your own fields and shorten the abstracts
python3 pubmed_dl.py download "alzheimer disease" --fields pmid,title,abstract,year --abstract-chars 500

# A list of PMIDs you already have (one per line)
python3 pubmed_dl.py download --ids-file my_pmids.txt --fields full
```

**Look at or search what you downloaded** (it reads the compressed files for you):

```bash
python3 pubmed_dl.py read crispr2023 --limit 5
python3 pubmed_dl.py read crispr2023 --grep "off-target" --grep mice
python3 pubmed_dl.py read crispr2023 --grep "base editing" --count
python3 pubmed_dl.py read crispr2023 --as csv > crispr2023.csv
```

**Make AI-ready files or a spreadsheet:**

```bash
python3 pubmed_dl.py export crispr2023                       # Markdown files + zip, ~100k words each
python3 pubmed_dl.py export crispr2023 --words 20000 --grep mice
python3 pubmed_dl.py export crispr2023 --as csv              # one Excel-friendly CSV
```

**Run the app on any computer** (it opens in your browser):

```bash
python3 pubmed_app.py
```

Run any command with `-h` to see every option.

### Search tips

| You want | Type |
| --- | --- |
| A phrase | `"heart failure"` |
| A MeSH subject heading | `"heart failure"[mh]` |
| An author | `doudna j[au]` |
| A journal | `nature[ta]` |
| A publication year or range | `2023[dp]`, `2020:2023[dp]` |
| Reviews only | `review[pt]` |
| Free full text only | `free full text[sb]` |
| Combine | `crispr AND (mice OR rats) NOT review[pt]` |

Put the whole search in quotes on the command line.

### Download options

| Option | What it does |
| --- | --- |
| `-o FOLDER` | Where to save (default: `pubmed_<your search>`) |
| `--max N` | Stop after N papers |
| `--sort newest` / `relevance` | Which papers `--max` keeps (relevance only works within the top 9,999) |
| `--from DATE` / `--to DATE` | Publication date range: `2020`, `2020/06` or `2020/06/15` |
| `--fields` | `minimal`, `standard` (default), `full`, or a comma list |
| `--format` | `jsonl` (default, for code), `csv` (for Excel), `txt` (for reading) |
| `--compress` | `xz` (default, smallest), `gz` (opens anywhere), `bz2`, `none` |
| `--per-file N` | Papers per file (default 1000) |
| `--abstract-chars N` | Shorten abstracts to N characters |
| `--max-authors N` | Keep only the first N authors (adds "et al.") |
| `--ids-file FILE` | Download these PMIDs instead of searching |
| `--fresh` | Throw away an earlier download in the same folder and start over |
| `--api-key KEY` | Your NCBI API key, for faster downloads (see below) |
| `--email ADDRESS` | Your email; NCBI asks heavy users for one |

#### Fields

| Level | Fields |
| --- | --- |
| `minimal` | pmid, title, year, journal, doi |
| `standard` | pmid, title, abstract, authors, journal, year, doi, mesh, keywords |
| `full` | all of the fields below |

All fields: `pmid`, `title`, `abstract`, `authors`, `affiliations`,
`journal`, `journal_abbrev`, `year`, `pub_date`, `volume`, `issue`, `pages`,
`doi`, `pmcid`, `pub_types`, `mesh`, `keywords`, `language`.
The `pmid` is always kept. Empty fields are left out of the jsonl files to
save space.

### What you get

```
crispr2023/
  part-00001.jsonl.xz    papers 1-1000
  part-00002.jsonl.xz    papers 1001-2000
  ...
  pmids.txt.gz           every PMID in the download, in order
  manifest.json          the search, settings, and size of each file
```

Each line of a jsonl file is one paper:

```json
{"pmid":"36000001","title":"Prime editing of CFTR in human organoids.","abstract":"BACKGROUND: ...","authors":["Geurts MH","de Poel E"],"journal":"Nature","year":2022,"doi":"10.1038/...","mesh":["Humans","Cystic Fibrosis"],"keywords":["prime editing"]}
```

When it finishes, the tool tells you how many papers it saved, how big the
files are in total, and how much smaller they are than the uncompressed text.

#### Using the files in other programs

- **Python / pandas** reads the compressed files directly:

  ```python
  from pathlib import Path
  import pandas as pd

  files = sorted(Path("crispr2023").glob("part-*.jsonl.xz"))
  papers = pd.concat(pd.read_json(f, lines=True) for f in files)
  ```

  Without pandas: `lzma.open(path, "rt")` (or `gzip.open` for `.gz`) and
  `json.loads` each line.
- **Excel**: download with `--format csv --compress gz` (or `--compress none`),
  unzip if needed, then open the `.csv` file.
  Or turn any download into one CSV with `read FOLDER --as csv > out.csv`.
- **Unzipping by hand**: `.xz` and `.gz` files open with 7-Zip (Windows),
  The Unarchiver (Mac), or `xz -dk` / `gunzip -k` (Linux/Mac terminal).

### Speed, limits and being polite to NCBI

- Without an API key NCBI allows 3 requests a second; with one, 10. The tool
  waits between requests on its own and fetches 200 papers per request. A
  free key makes big downloads up to about 3x faster: sign in at
  https://account.ncbi.nlm.nih.gov/, open *Account settings*, and create one
  under *API Key Management*. Then either pass `--api-key KEY` or set it once:

  ```bash
  export NCBI_API_KEY=your_key_here      # Mac/Linux
  setx NCBI_API_KEY your_key_here        # Windows (open a new terminal afterwards)
  ```

  You can set `NCBI_EMAIL` the same way.
- NCBI asks that very large jobs run on weekends or between 9 pm and 5 am
  US Eastern time on weekdays.
- If it fails partway through (network drop, laptop sleeps, Ctrl+C), just run
  the same command again. Finished files are kept and only the rest is
  downloaded. Files are written under a temporary name first, so a half-written
  file is never mistaken for a finished one.
- For *all* of PubMed (tens of millions of records), NCBI's yearly baseline
  files at https://ftp.ncbi.nlm.nih.gov/pubmed/baseline/ are a better fit
  than searching.

## For developers

- `pubmed_dl.py`: the download engine and command line.
- `pubmed_app.py` + `pubmed_app.html`: the app (a small web server bound to
  127.0.0.1, protected by a random token).
- `mac/`: the Mac app's launcher script, Info.plist, icon and read-me.
  `build_mac_app.py` packs them with the Python files into
  `PubMed-Downloader-Mac.zip`; rerun it after changing any of those files.
- Tests use a small fake NCBI server, so they run offline:

  ```bash
  python3 -m unittest discover -s tests -v
  ```
