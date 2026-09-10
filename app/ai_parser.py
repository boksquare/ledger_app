"""AI-powered structuring of raw statement text into transactions (spec §9).

The active provider is chosen with LEDGER_AI_PROVIDER — one of PROVIDERS below.
Defaults to claude_code (Claude Code CLI, billed to a Claude Pro/Max subscription,
no API key). The others are plain API-key-based HTTP providers; see README for the
env vars each one needs.
"""
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 600  # Claude Code CLI — a subprocess, includes its own startup overhead
# NIM/Gemini/OpenAI-compatible — a single direct API call. Free-tier hosted endpoints can
# have tens of seconds of queuing/cold-start latency before any generation even starts, on
# top of actual response time, so this is deliberately generous. Override with
# LEDGER_AI_HTTP_TIMEOUT if your provider needs longer (or you want to fail over faster).
DEFAULT_HTTP_TIMEOUT_SECONDS = 180

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


class AIParsingError(Exception):
    pass


def _http_timeout_seconds() -> int:
    raw = os.environ.get("LEDGER_AI_HTTP_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_HTTP_TIMEOUT_SECONDS
    try:
        value = int(raw)
        if value <= 0:
            raise ValueError
    except ValueError:
        raise AIParsingError(
            f"Invalid LEDGER_AI_HTTP_TIMEOUT '{raw}' — must be a positive whole number of seconds."
        )
    return value


def _extract_json_object(text: str) -> dict:
    """Pull the JSON object out of the model's reply, tolerating stray prose or fences."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise AIParsingError("The AI's reply contained no JSON object; try re-uploading.")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        raise AIParsingError("The AI returned malformed data; try re-uploading.")


def _require_env(name: str, provider: str, example: str = "") -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        hint = f" (e.g. {example})" if example else ""
        raise AIParsingError(
            f"{name} is not set — required when LEDGER_AI_PROVIDER={provider}{hint}."
        )
    return value


# ---------- Claude Code CLI (default; subscription-based, no API key) ----------

def _find_claude_cli() -> str:
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
    raise AIParsingError(
        "Claude Code CLI not found. Statement parsing uses your Claude subscription "
        "via the `claude` command — install Claude Code (https://claude.com/claude-code) "
        "and sign in with your Pro/Max account, or set CLAUDE_CLI to its full path."
    )


def _parse_via_claude_code(prompt: str) -> str:
    cli = _find_claude_cli()
    cmd = [cli, "-p", "--output-format", "json"]
    model = os.environ.get("LEDGER_AI_MODEL", "").strip()
    effort = os.environ.get("LEDGER_AI_EFFORT", "").strip().lower()
    if model:
        cmd += ["--model", model]
    if effort:
        if effort not in ("low", "medium", "high", "xhigh", "max"):
            raise AIParsingError(
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
        raise AIParsingError(
            f"Could not run the Claude Code CLI at '{cli}'. Check the CLAUDE_CLI path."
        )
    except subprocess.TimeoutExpired:
        raise AIParsingError(
            "Claude took too long parsing this statement. Try a smaller file."
        )

    # --output-format json wraps the reply in an envelope with metadata; on failure
    # (including not-logged-in) the CLI exits non-zero but still prints the envelope.
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise AIParsingError(
            f"Unexpected output from Claude Code (exit {proc.returncode}): {detail}"
        )

    if envelope.get("is_error") or proc.returncode != 0:
        detail = str(envelope.get("result") or proc.stderr or "unknown error")
        if "not logged in" in detail.lower() or "/login" in detail.lower():
            raise AIParsingError(
                "Claude Code is not signed in on this machine. One-time setup: run "
                "`claude` in a terminal and use /login with your Claude Pro/Max account "
                "(or set CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`), then retry."
            )
        raise AIParsingError(f"Claude reported an error: {detail[:300]}")

    return envelope.get("result") or ""


# ---------- HTTP-based providers (API-key billed) ----------

def _post_json(url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_http_timeout_seconds()) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise AIParsingError(f"{url} returned HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise AIParsingError(f"Could not reach {url}: {e.reason}")
    except TimeoutError:
        raise AIParsingError("The AI provider took too long to respond. Try a smaller file.")
    except json.JSONDecodeError:
        raise AIParsingError(f"{url} returned a response that wasn't valid JSON.")


MAX_OUTPUT_TOKENS = 8000  # a statement with many transactions needs a lot of output JSON


def _chat_completion(base_url: str, api_key: str, model: str, prompt: str) -> str:
    """OpenAI-compatible /chat/completions call — shared by NIM and the generic adapter."""
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": MAX_OUTPUT_TOKENS,
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    data = _post_json(url, headers, payload)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIParsingError(f"Unexpected response shape from {base_url}: {json.dumps(data)[:300]}")


def _parse_via_nvidia_nim(prompt: str) -> str:
    api_key = _require_env("NVIDIA_NIM_API_KEY", "nvidia_nim")
    base_url = os.environ.get("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1").strip()
    model = _require_env(
        "LEDGER_AI_MODEL", "nvidia_nim",
        "meta/llama-3.1-70b-instruct — see https://build.nvidia.com for available models",
    )
    return _chat_completion(base_url, api_key, model, prompt)


def _parse_via_openai_compatible(prompt: str) -> str:
    base_url = _require_env(
        "OPENAI_COMPAT_BASE_URL", "openai_compatible",
        "https://api.openai.com/v1, or your provider's/local server's base URL",
    )
    model = _require_env("LEDGER_AI_MODEL", "openai_compatible", "gpt-4o-mini, llama3.1, etc.")
    api_key = os.environ.get("OPENAI_COMPAT_API_KEY", "").strip()
    return _chat_completion(base_url, api_key, model, prompt)


def _parse_via_gemini(prompt: str) -> str:
    api_key = _require_env("GEMINI_API_KEY", "gemini")
    model = _require_env(
        "LEDGER_AI_MODEL", "gemini",
        "gemini-2.0-flash — see https://ai.google.dev/gemini-api/docs/models for available models",
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": MAX_OUTPUT_TOKENS},
    }
    data = _post_json(url, {"x-goog-api-key": api_key}, payload)
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise AIParsingError(f"Unexpected response shape from Gemini: {json.dumps(data)[:300]}")


PROVIDERS = {
    "claude_code": _parse_via_claude_code,
    "nvidia_nim": _parse_via_nvidia_nim,
    "gemini": _parse_via_gemini,
    "openai_compatible": _parse_via_openai_compatible,
}


def parse_statement_text(text: str, category_names: list[str]) -> dict:
    """Run the configured AI provider on the extracted statement text, return structured data.

    Raises AIParsingError with a user-facing message on any failure (missing config,
    provider unreachable/erroring, timeout, malformed output).
    """
    provider_name = os.environ.get("LEDGER_AI_PROVIDER", "claude_code").strip().lower()
    provider_fn = PROVIDERS.get(provider_name)
    if provider_fn is None:
        raise AIParsingError(
            f"Unknown LEDGER_AI_PROVIDER '{provider_name}' — use one of: "
            f"{', '.join(PROVIDERS)}."
        )
    prompt = PROMPT.format(
        categories=", ".join(category_names), output_spec=OUTPUT_SPEC, text=text,
    )
    started = time.monotonic()
    print(f"[ai_parser] {provider_name}: starting ({len(text)} chars of statement text)", flush=True)
    try:
        reply = provider_fn(prompt)
    except AIParsingError as e:
        print(f"[ai_parser] {provider_name}: failed after {time.monotonic() - started:.1f}s — {e}", flush=True)
        raise
    print(
        f"[ai_parser] {provider_name}: succeeded in {time.monotonic() - started:.1f}s "
        f"({len(reply)} chars back)",
        flush=True,
    )
    try:
        return _extract_json_object(reply)
    except AIParsingError:
        preview = reply[:500].replace("\n", " ")
        print(f"[ai_parser] {provider_name}: unparseable reply, preview: {preview!r}", flush=True)
        raise
