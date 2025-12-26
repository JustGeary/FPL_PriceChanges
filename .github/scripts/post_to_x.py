#!/usr/bin/env python3
import os
import time
from requests_oauthlib import OAuth1Session

# Use api.x.com (often avoids Cloudflare behaviour seen on api.twitter.com from GitHub runners)
POST_URL = "https://api.x.com/2/tweets"


def get_session():
    api_key = os.getenv("X_API_KEY")
    api_secret = os.getenv("X_API_KEY_SECRET")
    access_token = os.getenv("X_ACCESS_TOKEN")
    access_secret = os.getenv("X_ACCESS_TOKEN_SECRET")

    missing = [
        name
        for name, val in [
            ("X_API_KEY", api_key),
            ("X_API_KEY_SECRET", api_secret),
            ("X_ACCESS_TOKEN", access_token),
            ("X_ACCESS_TOKEN_SECRET", access_secret),
        ]
        if not val
    ]
    if missing:
        print(f"Missing required env vars: {', '.join(missing)}")
        raise SystemExit(1)

    sess = OAuth1Session(
        api_key,
        client_secret=api_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_secret,
    )

    # Helps with some WAF/CDN heuristics; harmless otherwise.
    sess.headers.update(
        {
            "User-Agent": "FPLPriceBot/1.0 (+https://github.com/JustGeary/FPL_PriceChanges)",
            "Accept": "application/json",
        }
    )
    return sess


def looks_like_cloudflare(resp_text: str) -> bool:
    """
    Cloudflare challenge returns HTML like 'Just a moment... Enable JavaScript and cookies to continue'
    instead of JSON. Detect that and treat it as retryable.
    """
    if not resp_text:
        return False
    t = resp_text.lstrip().lower()
    if t.startswith("<!doctype html") or t.startswith("<html"):
        return ("just a moment" in t) or ("cf_chl" in t) or ("challenge" in t) or ("cloudflare" in t)
    return False


def post_with_retries(session, payload: dict, label: str, idx: int, max_attempts: int = 5):
    """
    Retry on:
      - Cloudflare challenge HTML
      - 429 rate limiting
      - transient 5xx errors

    Returns (resp_status_code, resp_text).
    """
    backoffs = [2, 5, 10, 20, 30]  # seconds

    last_status = None
    last_text = ""

    for attempt in range(1, max_attempts + 1):
        resp = session.post(POST_URL, json=payload, timeout=30)
        last_status = resp.status_code
        last_text = resp.text or ""

        # Light logging each attempt (avoid dumping massive HTML every time)
        trunc = (last_text[:350] + "…") if len(last_text) > 350 else last_text
        print(f"[{label}] CHUNK {idx} attempt {attempt}/{max_attempts} status: {last_status}")
        print(f"[{label}] CHUNK {idx} attempt {attempt} body (trunc): {trunc}")

        # Cloudflare HTML challenge → retry
        if looks_like_cloudflare(last_text):
            wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
            print(f"[{label}] CHUNK {idx} Cloudflare challenge detected. Waiting {wait}s then retrying…")
            time.sleep(wait)
            continue

        # Retryable HTTP status codes
        if last_status in (429, 500, 502, 503, 504):
            wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
            print(f"[{label}] CHUNK {idx} transient status {last_status}. Waiting {wait}s then retrying…")
            time.sleep(wait)
            continue

        # Non-retryable (success or real error)
        return last_status, last_text

    return last_status or 0, last_text


def post_thread(session, base_path: str, label: str, soft_fail: bool = False):
    idx = 1
    parent_id = None

    while True:
        path = f"{base_path}_{idx}.txt"
        if not os.path.exists(path):
            if idx == 1:
                print(f"[{label}] No files found starting with {base_path}_1.txt — nothing to post.")
            else:
                print(f"[{label}] Completed thread: {idx-1} tweet(s) posted.")
            break

        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()

        if not text:
            print(f"[{label}] {path} is empty, skipping.")
            idx += 1
            continue

        print(f"===== {label} CHUNK {idx} MESSAGE PREVIEW =====")
        print(text)
        print(f"===== {label} CHUNK {idx} LENGTH: {len(text)} =====")

        payload = {"text": text}
        if parent_id is not None:
            payload["reply"] = {"in_reply_to_tweet_id": parent_id}

        status, body = post_with_retries(session, payload, label, idx, max_attempts=5)

        print(f"[{label}] CHUNK {idx} final status:", status)
        print(f"[{label}] CHUNK {idx} final raw response (trunc 800):", (body[:800] + "…") if len(body) > 800 else body)

        if status >= 400:
            msg = f"{label} chunk {idx} failed: {status} {body[:300]}"
            if soft_fail:
                print(f"[{label}] WARNING: {msg}")
                return False
            raise RuntimeError(msg)

        # Parse parent tweet id for threading
        try:
            data = __import__("json").loads(body).get("data", {})
            parent_id = data.get("id", parent_id)
        except Exception:
            print(f"[{label}] Warning: could not parse tweet ID from response JSON.")

        print(f"[{label}] Successfully posted chunk {idx} to X.")
        idx += 1

    return True


def main():
    session = get_session()

    # Optional: allow FALLERS to soft-fail too, if Cloudflare blocks.
    # Set in workflow env: X_SOFT_FAIL_FALLERS=true
    soft_fail_fallers = os.getenv("X_SOFT_FAIL_FALLERS", "false").lower() == "true"

    # Post FALLERS first so RISERS appear above in timeline
    post_thread(session, "x_status_fallers", "FALLERS", soft_fail=soft_fail_fallers)

    # Then RISERS — keep your existing behaviour: warn but do not fail the whole run
    try:
        post_thread(session, "x_status_risers", "RISERS", soft_fail=True)
    except Exception as e:
        print(f"[RISERS] WARNING: posting failed after fallers succeeded: {e}")


if __name__ == "__main__":
    main()
