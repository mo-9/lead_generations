# Dubai Leads: Google Maps lead scraper

Builds a CSV of Dubai business owners and high-earning professionals (clinics, agencies, law firms, trading companies...) from Google Maps, then visits each business's website for its email, socials and a named decision-maker.

---

## 1. Install (once)

You need **Python 3.10 or newer**. Check with `python --version`. If you don't have it, install it from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"** in the installer.

**Windows**

1. Unzip `dubai_leads.zip`, for example to `C:\dubai_leads`.
2. Open that folder in File Explorer, click the address bar, type `powershell` and press Enter.
3. Run these three commands, one at a time:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m playwright install chromium
```

**Mac**

1. Unzip `dubai_leads.zip`, open Terminal and `cd` into the folder.
2. Run:

```
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
```

---

## 2. Choose what to scrape

Open **`settings.py`** in Notepad, VS Code or any text editor. Everything is there:

| Setting | What it does |
|---|---|
| `SEARCH_PERSONAS` | Persona groups to search, e.g. `["Healthcare"]`, or `["ALL"]` |
| `SEARCH_CATEGORIES` | Extra single categories, e.g. `["law firm", "PR agency"]` |
| `SEARCH_AREAS` | Areas, e.g. `["DIFC", "Business Bay"]`, or `["ALL"]` |
| `MIN_RATING`, `MIN_REVIEWS`, `MAX_REVIEWS` | Rating and review filters (`MAX_REVIEWS = 300` targets newer businesses) |
| `WEBSITE`, `PHONE` | `"must_have"` / `"must_not_have"` / `"either"` |
| `ENRICH_WEBSITES` | Visit websites for email / socials / decision-maker |
| Speed & safety | Delays, breaks, backoff, when to stop |
| `PERSONAS`, `AREAS` | The category and area lists. Add or remove freely |
| `EXCLUDE_...` | Words that exclude competitors (real estate) and government |

The defaults are a **small test run** (dental clinics in Business Bay). Check its results, then widen.

---

## 3. Run

**Windows:** `.venv\Scripts\python run.py`
**Mac:** `.venv/bin/python run.py`

A browser window opens and works on its own. Leave it alone (minimizing it is fine). The terminal shows each search, each lead (`LEAD`) or skipped place (`skip` + reason), then the website visits, and a summary at the end.

- **Stop any time** with `Ctrl+C`. Progress is saved.
- **Resume**: run the same command again. Finished searches are skipped.
- **Change filters later**: just run again. Saved data is reused, and only what's missing is fetched.

---

## 4. Results (in the `output` folder)

| File | What it is |
|---|---|
| `leads_<date>_<time>.csv` | The lead list. Opens in Excel with Arabic names intact. A new file each run |
| `progress.db` | Saved progress. Delete it to start completely from scratch |
| `run.log` | Full log of every run |
| `browser_profile/` | The scraper's own browser profile (never signed in to Google) |

The CSV only contains businesses matching the **current** settings (personas, categories, areas, filters).

---

## 5. If Google pushes back

The scraper keeps a slow, randomized pace and takes regular long breaks. If Google responds slowly or errors, it slows down further and backs off (each wait doubles). If Google shows a CAPTCHA / "unusual traffic" page 3 times in a row, it **stops, saves, writes the CSV and tells you**. Wait a few hours, then run again. It continues where it stopped.

Tips:
- Run big jobs in pieces (a persona or a few areas at a time).
- Prefer a home connection over the office network. If Google rate-limits the office IP, everyone there sees CAPTCHAs.
- Never sign in to Google in the scraper's browser window.

---

## How it works (short)

- Each category × area search uses the area's map box. If Google cuts the list off (~120 results), the box is split into 4 smaller boxes and each is searched, repeating until lists are no longer cut off.
- Results outside the chosen area are ignored. Duplicates are merged by Google place ID (falling back to name + phone).
- **is_chain = Yes** when the same business name appears at 2+ locations among everything the scraper has seen. Branch suffixes like "- JLT" are ignored when comparing names.
- Websites: only the homepage plus one contact, one about and one team page are visited, and robots.txt is respected.
"# lead_generations" 
