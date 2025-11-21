#!/usr/bin/env python3
import os
from requests_oauthlib import OAuth1Session

POST_URL = "https://api.x.com/2/tweets"  # X API v2 endpoint


def main():
    if not os.path.exists("x_status.txt"):
        print("x_status.txt missing — nothing to post.")
        return

    with open("x_status.txt", "r", encoding="utf-8") as f:
        text = f.read().strip()

    if not text:
        print("Empty text — skipping X post.")
        return

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

    session = OAuth1Session(
        api_key,
        client_secret=api_secret,
        resource_owner_key=access_token,
        resource_owner_secret=access_secret,
    )

    payload = {"text": text}
    resp = session.post(POST_URL, json=payload, timeout=20)

    if resp.status_code >= 400:
        print(f"Error posting to X: {resp.status_code} {resp.text}")
        raise SystemExit(1)

    print("Successfully posted to X:", resp.json())


if __name__ == "__main__":
    main()
