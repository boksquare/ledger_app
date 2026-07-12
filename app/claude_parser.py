"""Claude-powered structuring of raw statement text into transactions (spec §9, Option B).

Shells out to Claude Code's headless print mode (`claude -p`), authenticated with the
user's Claude Pro/Max subscription login — no separate API key or per-token billing.
Usage draws from the subscription's shared limit pool (verified June 2026).
"""
import json
import os
import shutil
import subprocess

TIMEOUT_SECONDS = 600

OUTPUT_SPEC = """{
  "card_name": "string or null — card/account name as shown on the statement",
  "period_start": "YYYY-MM-DD or null — statement period start",
  "period_end": "YYYY-MM-DD or null — statement period end",
  "transactions": [
    {
      "date": "YYYY-MM-DD",
      "description": "string",
      "amount": "number — positive for purchases/charges, negative for refunds",
      "suggested_category": "one of the provided category names, or null if none fits"
    }
  ]
}"""

PROMPT = """Below is the raw text extracted from a credit card or bank statement (PDF text \
extraction or CSV). Extract every purchase/charge transaction into structured form.

Rules:
- Include purchases and charges with positive amounts; include refunds/returns as negative amounts.
- EXCLUDE payments toward the card balance, interest charge summaries duplicated elsewhere, \
and running-balance or summary lines that are not individual transactions.
- Dates must be YYYY-MM-DD. If the statement omits the year on transaction lines, infer it from \
the statement period.
- For each transaction, suggest the best-fitting category from this list (or null if none fits):
{categories}

Respond with ONLY a single JSON object in exactly this shape — no code fences, no commentary:
{output_spec}

Statement text:
<statement>
{text}
</statement>"""


class ClaudeParsingError(Exception):
    pass


def _find_cli() -> str:
    """Locate the Claude Code CLI; overridable via CLAUDE_CLI for non-standard installs."""
    override = os.environ.get("CLAUDE_CLI")
    if override:
        return override
    found = shutil.which("claude")
    if found:
        return found
    # Windows: the Claude desktop app bundles the CLI outside PATH — use the newest copy.
    appdata = os.environ.get("APPDATA")
    if appdata:
        from pathlib import Path

        bundled = sorted(Path(appdata, "Claude", "claude-code").glob("*/claude.exe"))
        if bundled:
            return str(bundled[-1])
    raise ClaudeParsingError(
        "Claude Code CLI not found. Statement parsing uses your Claude subscription "
        "via the `claude` command — install Claude Code (https://claude.com/claude-code) "
        "and sign in with your Pro/Max account, or set CLAUDE_CLI to its full path."
    )


def _extract_json_object(text: str) -> dict:
    """Pull the JSON object out of the model's reply, tolerating stray prose or fences."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ClaudeParsingError("Claude's reply contained no JSON object; try re-uploading.")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        raise ClaudeParsingError("Claude returned malformed data; try re-uploading.")


def parse_statement_text(text: str, category_names: list[str]) -> dict:
    """Run `claude -p` on the extracted statement text, return structured transactions.

    Raises ClaudeParsingError with a user-facing message on any failure
    (CLI missing, not signed in, timeout, malformed output).
    """
    cli = _find_cli()
    prompt = PROMPT.format(
        categories=", ".join(category_names), output_spec=OUTPUT_SPEC, text=text,
    )
    cmd = [cli, "-p", "--output-format", "json"]
    # Optional overrides; unset means the Claude Code default model/effort.
    model = os.environ.get("LEDGER_AI_MODEL", "").strip()
    effort = os.environ.get("LEDGER_AI_EFFORT", "").strip().lower()
    if model:
        cmd += ["--model", model]
    if effort:
        if effort not in ("low", "medium", "high", "xhigh", "max"):
            raise ClaudeParsingError(
                f"Invalid LEDGER_AI_EFFORT '{effort}' — use low, medium, high, xhigh, or max."
            )
        cmd += ["--effort", effort]
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise ClaudeParsingError(
            f"Could not run the Claude Code CLI at '{cli}'. Check the CLAUDE_CLI path."
        )
    except subprocess.TimeoutExpired:
        raise ClaudeParsingError(
            "Claude took too long parsing this statement. Try a smaller file."
        )

    # --output-format json wraps the reply in an envelope with metadata; on failure
    # (including not-logged-in) the CLI exits non-zero but still prints the envelope.
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise ClaudeParsingError(
            f"Unexpected output from Claude Code (exit {proc.returncode}): {detail}"
        )

    if envelope.get("is_error") or proc.returncode != 0:
        detail = str(envelope.get("result") or proc.stderr or "unknown error")
        if "not logged in" in detail.lower() or "/login" in detail.lower():
            raise ClaudeParsingError(
                "Claude Code is not signed in on this machine. One-time setup: run "
                "`claude` in a terminal and use /login with your Claude Pro/Max account "
                "(or set CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`), then retry."
            )
        raise ClaudeParsingError(f"Claude reported an error: {detail[:300]}")

    return _extract_json_object(envelope.get("result") or "")
