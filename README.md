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
  locally (pdfplumber/openpyxl) and structured by AI into transactions with suggested categories.
  Review/edit/select in a staging table, then confirm — nothing counts until confirmed.
  Scanned (image-only) PDFs are detected and rejected with a clear message.
  Pick the AI provider with `LEDGER_AI_PROVIDER`: **Claude Code** (default — your Claude
  Pro/Max subscription, no API key), **NVIDIA NIM**, **Google Gemini**, or any
  **OpenAI-compatible** API/local server. If the configured provider is unavailable, a
  built-in offline parser is used instead (with a warning to double-check the results).

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
| `LEDGER_AI_PROVIDER` | `claude_code` | Which AI provider does statement parsing: `claude_code`, `nvidia_nim`, `gemini`, or `openai_compatible`. |

Statement parsing is the only feature that talks to an AI provider; everything else works
offline. Each provider needs its own env vars, below — only fill in the block for the one
you're using (see `.env.example`).

**`claude_code`** (default) — subscription-based, no API key or per-token billing.

| Env var | Default | Purpose |
|---|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | — | Subscription auth for headless Claude Code in Docker (from `claude setup-token`). Not needed locally if `claude` is already signed in. |
| `CLAUDE_CLI` | auto-detected | Full path to the `claude` binary if it isn't on PATH (on Windows the desktop-app bundle is found automatically). |
| `LEDGER_AI_MODEL` | Claude Code default | An alias (`sonnet`, `opus`, `haiku`, `fable`) or full model id. `sonnet` is plenty for extraction and lighter on subscription usage. |
| `LEDGER_AI_EFFORT` | Claude Code default | `low`, `medium`, `high`, `xhigh`, or `max`. `low`/`medium` recommended — parsing is extraction, not reasoning. |

Usage draws from your subscription's shared limit pool, same as chatting in the app.

**`nvidia_nim`** — [build.nvidia.com](https://build.nvidia.com) API key, or a self-hosted NIM container.

| Env var | Default | Purpose |
|---|---|---|
| `NVIDIA_NIM_API_KEY` | — | Required. |
| `NVIDIA_NIM_BASE_URL` | `https://integrate.api.nvidia.com/v1` | Point at a self-hosted NIM instance instead if you're running one. |
| `LEDGER_AI_MODEL` | — | Required — e.g. `meta/llama-3.1-70b-instruct`. See the model catalog at build.nvidia.com. |

**`gemini`** — [Google AI Studio](https://aistudio.google.com/apikey) API key.

| Env var | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | — | Required. |
| `LEDGER_AI_MODEL` | — | Required — e.g. `gemini-2.0-flash`. See [ai.google.dev](https://ai.google.dev/gemini-api/docs/models) for current model ids. |

**`openai_compatible`** — OpenAI itself, or anything speaking the same `/chat/completions`
shape: Groq, Together, Mistral, a local Ollama/LM Studio server, etc.

| Env var | Default | Purpose |
|---|---|---|
| `OPENAI_COMPAT_BASE_URL` | — | Required — e.g. `https://api.openai.com/v1`, or your provider's/local server's base URL. |
| `OPENAI_COMPAT_API_KEY` | — | Sent as a bearer token if set; leave blank for a local server that doesn't need one. |
| `LEDGER_AI_MODEL` | — | Required — e.g. `gpt-4o-mini`. |

All three of the above (`nvidia_nim`, `gemini`, `openai_compatible`) share one more setting:

| Env var | Default | Purpose |
|---|---|---|
| `LEDGER_AI_HTTP_TIMEOUT` | `180` (seconds) | How long to wait before falling back to the built-in parser. Free-tier hosted endpoints can have tens of seconds of queuing/cold-start latency before generation even starts — raise this if a provider that does eventually respond keeps hitting "AI provider took too long to respond." |

## Notes

- Billing cycle is the calendar month (1st–end). Currency is USD.
- No auth — intended for LAN use on a home server. Use Tailscale or similar for
  remote/phone access rather than exposing the port publicly.
