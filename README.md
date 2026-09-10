# Bill-Split Tracker

Self-hosted monthly expense and bill-split tracker. Tracks expenses, splits them
50/50 or 100%, and shows a running split total per calendar month.
No real money movement — calculation and reporting only.

## Features

- **Dashboard** — headline "Her" total, 50/50 vs 100% breakdown, interactive
  pie chart (hover for amounts, click a slice to filter the expense list), quick-add form,
  inline edit/delete.
- **Personal Tracker** — a fully isolated tab for her own personal spending (own tables,
  never counted in the split math). Monthly total, pie chart, inline-editable expense
  list, and inline-managed recurring entries. Categories are shared with the main tracker.
- **Categories** — seeded defaults plus add/rename/deactivate.
- **Recurring expenses** — rules auto-generate an expense on their day of month
  (clamped to month end, backfilled from `start_date`, idempotent).
- **Monthly history** — same dashboard view for any past month.
- **Export** — Excel (native embedded pie chart, interactive in Excel) and PDF
  (rendered chart image + itemized table).
- **Statement import** — upload PDF, CSV, or Excel (.xlsx) statements; text is extracted
  locally (pdfplumber/openpyxl) and structured by Claude into transactions with suggested categories.
  Review/edit/select in a staging table, then confirm — nothing counts until confirmed.
  Scanned (image-only) PDFs are detected and rejected with a clear message.
  Parsing runs through Claude Code's headless mode (`claude -p`) using your **Claude
  Pro/Max subscription** — no API key or per-token billing.

## Run locally (development)

Requires [uv](https://docs.astral.sh/uv/):

```sh
uv sync
uv run uvicorn app.main:app --reload
```

Open http://localhost:8000. Data lives in `./data/` (SQLite file + uploaded statements).

## Deploy with Docker

Images are built and published to GHCR by [a GitHub Actions workflow](.github/workflows/docker-publish.yml)
on every push to `main` — the server just pulls, it never builds.

```sh
# optional — only needed for statement import. On any machine where Claude Code
# is signed in with your Pro/Max account, run `claude setup-token`, then:
echo "CLAUDE_CODE_OAUTH_TOKEN=<token from claude setup-token>" > .env

docker compose pull
docker compose up -d
```

To update after a new push, just re-run those two commands — no git clone/pull needed
on the server at all.

Open `http://<server-ip>:8321`. The SQLite database and uploaded statements persist
in the named volume `bills_data` (mounted at `/data`), so they survive updates.
Back up by copying `/data/expenses.db` out of the volume.

**One-time setup:** the GHCR image is private (it follows this repo's visibility), so the
server needs to authenticate once before it can pull:

```sh
# create a token at https://github.com/settings/tokens/new with the read:packages
# scope only, then on the server:
echo "<token>" | docker login ghcr.io -u boksquare --password-stdin
```

Docker caches that login, so every later `docker compose pull` just works with no
further auth. If you'd rather not manage a token, making the package public
(`https://github.com/boksquare/ledger_app/pkgs/container/ledger_app` → Package settings
→ Change visibility) removes this step, at the cost of the image itself being pullable
by anyone.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `DATA_DIR` | `./data` (repo) / `/data` (Docker) | Where the SQLite DB and statements live |
| `CLAUDE_CODE_OAUTH_TOKEN` | — | Subscription auth for headless Claude Code in Docker (from `claude setup-token`). Not needed locally if `claude` is already signed in. |
| `CLAUDE_CLI` | auto-detected | Full path to the `claude` binary if it isn't on PATH (on Windows the desktop-app bundle is found automatically) |
| `LEDGER_AI_MODEL` | Claude Code default | Model for statement parsing: an alias (`sonnet`, `opus`, `haiku`, `fable`) or full model id. `sonnet` is plenty for extraction and lighter on subscription usage. |
| `LEDGER_AI_EFFORT` | Claude Code default | Effort level for statement parsing: `low`, `medium`, `high`, `xhigh`, or `max`. `low`/`medium` recommended — parsing is extraction, not reasoning. |

Statement parsing is the only feature that talks to Claude; everything else works
offline. Usage draws from your subscription's shared limit pool, same as chatting
in the app.

## Notes

- Billing cycle is the calendar month (1st–end). Currency is USD.
- No auth — intended for LAN use on a home server. Use Tailscale or similar for
  remote/phone access rather than exposing the port publicly.
