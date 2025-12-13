#!/usr/bin/env python3
import os
from requests_oauthlib import OAuth1Session

POST_URL = "https://api.twitter.com/2/tweets"  # X API v2 endpoint


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

    return OAuth1Session(
        api_key,
        client_secret=api_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_secret,
    )


def post_thread(session, base_path: str, label: str):
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

        resp = session.post(POST_URL, json=payload, timeout=20)

        print(f"[{label}] CHUNK {idx} X API status code:", resp.status_code)
        print(f"[{label}] CHUNK {idx} X API raw response:", resp.text)

        if resp.status_code >= 400:
            raise RuntimeError(f"{label} chunk {idx} failed: {resp.status_code} {resp.text}")

        try:
            data = resp.json().get("data", {})
            parent_id = data.get("id", parent_id)
        except Exception:
            print(f"[{label}] Warning: could not parse tweet ID from response JSON.")

        print(f"[{label}] Successfully posted chunk {idx} to X.")
        idx += 1


def main():
    session = get_session()

    # Post FALLERS first so RISERS appear above in timeline
    post_thread(session, "x_status_fallers", "FALLERS")

    # Then RISERS — if this fails, log it but don't fail the entire run
    try:
        post_thread(session, "x_status_risers", "RISERS")
    except Exception as e:
        print(f"[RISERS] WARNING: posting failed after fallers succeeded: {e}")
        # do not exit non-zero


if __name__ == "__main__":
    main()
