# gst-portal-fetcher

Incrementally pull **all** of a single client's data from the Indian GST portal
(`services.gst.gov.in`) into local JSON files, using a **manual-login session
handoff** — you solve the CAPTCHA/OTP, the tool does the rest.

This is a standalone project. It has no dependency on, and is not part of, any
other repository.

## Why a session handoff?

The GST portal has no open public API for full account data, and every
meaningful action sits behind CAPTCHA + OTP login. Rather than fight that, this
tool keeps a human in the loop for authentication:

1. **You** log in once in a real browser (CAPTCHA + OTP).
2. The tool **captures the authenticated session** (cookies + the `authtoken`
   header the portal's own pages send).
3. It then **replays the portal's own internal JSON endpoints** to download
   data, period by period.

Because you authenticate yourself against your own client's account (with their
authorisation), this stays within normal authorised use. It does **not**
automate login, solve CAPTCHAs, or bypass any security control.

## What it fetches

Per the endpoint catalog (`gstfetch/endpoints.py`), for the configured GSTIN:

| Resource          | Scope        | Notes                                  |
|-------------------|--------------|----------------------------------------|
| `profile`         | once         | Registration / taxpayer details        |
| `filing_history`  | per FY       | Return filing status dashboard         |
| `gstr1`           | per month    | Outward supplies                       |
| `gstr3b`          | per month    | Summary return                         |
| `gstr2b`          | per month    | Auto-drafted ITC statement             |
| `ledger_cash`     | per FY       | Electronic cash ledger                 |
| `ledger_credit`   | per FY       | Electronic credit (ITC) ledger         |
| `ledger_liability`| per FY       | Liability ledger                       |
| `notices_orders`  | once         | Notices & orders                       |
| `challans`        | per FY       | Payment challan history                |

## Incremental fetching

A SQLite checkpoint store (`data/checkpoints.sqlite`) records, per
`(gstin, resource, period)`, whether it was fetched and whether the period is
**sealed**:

- **Closed periods** (older than the current open month) of immutable resources
  are sealed once fetched and **skipped** on subsequent runs.
- **Open / recent periods** are always re-fetched so late filings and
  amendments are picked up.
- `--force` re-fetches everything.

So the first run is a full backfill; every run after that is cheap and only
pulls what changed.

## Install

```bash
cd gst-portal-fetcher
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

## Configure

```bash
cp .env.example .env
# edit .env: set GSTFETCH_GSTIN and GSTFETCH_START_PERIOD
```

## Use

```bash
# 1. Log in manually (a browser opens; solve CAPTCHA + OTP, then press Enter).
gstfetch login

# 2. (Recommended once) Verify the portal's real endpoints by recording traffic
#    while you click through the screens you care about.
gstfetch capture            # writes data/capture.har + discovered_endpoints.json

# 3. Fetch everything, incrementally.
gstfetch fetch

# 4. See progress.
gstfetch status

# 5. Export the raw JSON into flat files for Excel / pandas.
gstfetch export --format csv     # or: --format jsonl
#   writes data/<GSTIN>/_export/<resource>.csv  (one file per resource)

# Re-run any time — only new/changed periods are fetched. Force a full refresh:
gstfetch fetch --force
```

Output lands in `data/<GSTIN>/<resource>/<period>.json`.

### Export format

`export` is schema-agnostic (the portal's payload shapes are undocumented):

- `--format jsonl` — one line per period: `{"period": ..., "data": <raw payload>}`.
- `--format csv` — each period flattened to dotted-key columns (e.g.
  `liability.igst`, `items[0].taxableValue`); the header is the union of keys
  seen across all periods, with blanks where a period lacks a key.

## Important caveats

- **Undocumented APIs.** The portal's internal endpoints are not published and
  change without notice. The default paths in `gstfetch/endpoints.py` are a
  best-effort starting point — **verify them with `gstfetch capture`** and
  override via `endpoints.yaml` (see `endpoints.example.yaml`). When a path
  changes, you edit YAML, not code.
- **Sessions expire.** If the portal returns 401/403/redirect, the tool stops
  and asks you to `gstfetch login` again; re-running resumes from the last
  checkpoint.
- **Authorisation.** Only use this against accounts you are authorised to access
  (your own, or a client who has engaged you). Keep `.env`, `session_state.json`,
  `auth_token.txt`, and `data/` out of version control — `.gitignore` already
  excludes them.
- **Be polite.** `GSTFETCH_REQUEST_DELAY` throttles calls; don't set it to 0.

## Develop

```bash
pytest          # offline logic: periods, checkpoints, orchestrator, config
ruff check .
mypy gstfetch
```

The networked pieces (`session.py`, `client.py`) require a real logged-in
browser and are exercised manually; the deterministic core is unit-tested with
a fake client.
