"""One tiny, dependency-free LLM client with free providers and automatic failover.

complete(system, user, max_tokens, ai_cfg) -> str

Providers are tried in order; only those whose API-key env var is set are used.
A provider that is rate limited (429) is waited on briefly, then skipped.
A provider whose daily quota is spent is skipped for the rest of the run.

All "openai" providers speak the standard /chat/completions protocol, so any
other OpenAI-compatible service can be added from config.yaml (ai.providers).
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# name -> defaults. `key_env` is the env var (GitHub secret) that switches it on.
# `gap` is the minimum seconds between calls, to stay under per-minute limits.
DEFAULTS = [
    {"name": "gemini", "kind": "openai", "key_env": "GEMINI_API_KEY",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
     "model": "gemini-3.8-flash", "gap": 7},
    {"name": "groq", "kind": "openai", "key_env": "GROQ_API_KEY",
     "base_url": "https://api.groq.com/openai/v1",
     "model": "openai/gpt-oss-120b", "gap": 20},
    {"name": "openrouter", "kind": "openai", "key_env": "OPENROUTER_API_KEY",
     "base_url": "https://openrouter.ai/api/v1",
     "model": "openrouter/free", "gap": 4},
    {"name": "anthropic", "kind": "anthropic", "key_env": "ANTHROPIC_API_KEY",
     "base_url": "https://api.anthropic.com",
     "model": "claude-sonnet-5-5", "gap": 0},
]

_last_call = {}
_dead = set()


class LLMError(RuntimeError):
    pass


class NoProvider(LLMError):
    pass


def providers(ai_cfg=None):
    """Configured providers that have a key, in priority order."""
    ai_cfg = ai_cfg or {}
    base = {p["name"]: dict(p) for p in DEFAULTS}
    order = list(ai_cfg.get("order") or [p["name"] for p in DEFAULTS])
    for extra in ai_cfg.get("providers") or []:       # overrides or brand-new providers
        base.setdefault(extra["name"], {}).update(extra)
        if extra["name"] not in order:
            order.append(extra["name"])
    out = []
    for name in order:
        p = base.get(name)
        if not p or name in _dead:
            continue
        if p.get("key_env") and not os.environ.get(p["key_env"]):
            continue
        out.append(p)
    return out


def available(ai_cfg=None):
    return bool(providers(ai_cfg))


def _post(url, headers, body, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _call_openai(p, system, user, max_tokens):
    key = os.environ.get(p.get("key_env", ""), "")
    body = {"model": p["model"], "max_tokens": int(max_tokens * 2),   # headroom: reasoning models spend tokens thinking
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    data = _post(p["base_url"].rstrip("/") + "/chat/completions",
                 {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}, body)
    text = (data["choices"][0]["message"].get("content") or "")
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def _call_anthropic(p, system, user, max_tokens):
    key = os.environ.get(p["key_env"], "")
    body = {"model": p["model"], "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}]}
    data = _post(p["base_url"].rstrip("/") + "/v1/messages",
                 {"Content-Type": "application/json", "x-api-key": key,
                  "anthropic-version": "2023-06-01"}, body)
    return "".join(b.get("text", "") for b in data.get("content", [])).strip()


def _wait_turn(p):
    gap = float(p.get("gap", 0))
    wait = _last_call.get(p["name"], 0) + gap - time.time()
    if wait > 0:
        time.sleep(wait)


def _try(p, system, user, max_tokens, tries=3):
    """One provider, with polite retries. Returns text or raises LLMError."""
    fn = _call_anthropic if p["kind"] == "anthropic" else _call_openai
    last = "unknown error"
    for attempt in range(tries):
        _wait_turn(p)
        _last_call[p["name"]] = time.time()
        try:
            text = fn(p, system, user, max_tokens)
            if not text:
                raise LLMError("empty reply")
            return text
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            last = f"HTTP {e.code} {detail}"
            if e.code == 429:
                if re.search(r"per.?day|daily|exhausted|billing", detail, re.I):
                    _dead.add(p["name"])
                    raise LLMError(f"{p['name']}: daily quota spent ({last})")
                try:
                    pause = float(e.headers.get("Retry-After", ""))
                except (TypeError, ValueError):
                    pause = 20 * (attempt + 1)
                time.sleep(min(max(pause, 1), 70))
                continue
            if e.code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            if e.code in (401, 403):
                _dead.add(p["name"])         # bad key: do not keep hammering
            raise LLMError(f"{p['name']}: {last}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = f"network: {e}"
            time.sleep(3 * (attempt + 1))
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"{p['name']}: unexpected reply shape ({e})")
    raise LLMError(f"{p['name']}: gave up after {tries} tries ({last})")


def complete(system, user, max_tokens=2000, ai_cfg=None):
    """Ask the first working provider. Raises NoProvider if no key is set at all."""
    plist = providers(ai_cfg)
    if not plist:
        raise NoProvider("no LLM key found. Set GEMINI_API_KEY (free) or GROQ_API_KEY (free); "
                         "see README")
    errors = []
    for p in plist:
        if p["name"] in _dead:
            continue
        try:
            return _try(p, system, user, max_tokens)
        except LLMError as e:
            errors.append(str(e))
            print(f"  llm: {e}; trying next provider", file=sys.stderr)
    raise LLMError("all providers failed: " + " | ".join(errors))


def extract_json(text):
    """Pull the first JSON object out of a reply, tolerating ``` fences and chatter."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    return json.loads(m.group(0))
