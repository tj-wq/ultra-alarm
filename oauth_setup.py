"""One-time OAuth setup for MCP authentication with UltraRun.Club.

Opens a browser for the OAuth flow, captures the callback on a local HTTP
server, and saves the access_token, refresh_token, and expires_at to
config.json.

Usage:
    python3 oauth_setup.py [--config config.json]
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = "config.json"
_CALLBACK_PORT = 8742
_CALLBACK_PATH = "/callback"
_AUTHORIZE_URL = "https://ultrarun.club/oauth/authorize"
_TOKEN_URL = "https://ultrarun.club/oauth/token"
_CLIENT_ID = "ultra-alarm-pi"
_REDIRECT_URI = f"http://localhost:{_CALLBACK_PORT}{_CALLBACK_PATH}"


# ---------------------------------------------------------------------------
# OAuth callback handler
# ---------------------------------------------------------------------------

class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth authorization code."""

    auth_code: str | None = None
    state: str | None = None
    error: str | None = None

    def do_GET(self) -> None:
        """Handle the OAuth redirect GET request."""
        parsed = urlparse(self.path)
        if parsed.path != _CALLBACK_PATH:
            self.send_response(404)
            self.end_headers()
            return

        params = parse_qs(parsed.query)

        if "error" in params:
            _OAuthCallbackHandler.error = params["error"][0]
            self._respond("OAuth error. You can close this tab.")
            return

        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]

        if code:
            _OAuthCallbackHandler.auth_code = code
            _OAuthCallbackHandler.state = state
            self._respond("Authorization successful! You can close this tab.")
        else:
            _OAuthCallbackHandler.error = "No authorization code received."
            self._respond("No authorization code received. You can close this tab.")

    def _respond(self, message: str) -> None:
        """Send a simple HTML response."""
        body = f"<html><body><h2>{message}</h2></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format: str, *args: object) -> None:
        """Suppress default request logging."""
        pass


# ---------------------------------------------------------------------------
# Token exchange
# ---------------------------------------------------------------------------

def _exchange_code_for_tokens(auth_code: str) -> dict:
    """Exchange the authorization code for access and refresh tokens.

    Returns a dict with access_token, refresh_token, and expires_at.
    """
    import httpx

    resp = httpx.post(
        _TOKEN_URL,
        json={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": _REDIRECT_URI,
            "client_id": _CLIENT_ID,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Config read/write
# ---------------------------------------------------------------------------

def _load_config(path: str) -> dict:
    """Load existing config.json or return empty dict."""
    config_path = Path(path)
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_config(path: str, data: dict) -> None:
    """Write config dict to JSON file."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def run_oauth_flow(config_path: str) -> None:
    """Run the full OAuth authorization flow.

    1. Start a local HTTP server for the callback.
    2. Open the browser to the authorization URL.
    3. Wait for the callback with the authorization code.
    4. Exchange the code for tokens.
    5. Save tokens to config.json.
    """
    state = secrets.token_urlsafe(32)
    auth_url = (
        f"{_AUTHORIZE_URL}"
        f"?client_id={_CLIENT_ID}"
        f"&redirect_uri={_REDIRECT_URI}"
        f"&response_type=code"
        f"&state={state}"
    )

    # Start local server in a thread
    server = HTTPServer(("localhost", _CALLBACK_PORT), _OAuthCallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print(f"Opening browser for authorization...")
    print(f"URL: {auth_url}")
    print()
    webbrowser.open(auth_url)

    print(f"Waiting for callback on http://localhost:{_CALLBACK_PORT}{_CALLBACK_PATH} ...")
    server_thread.join(timeout=120)
    server.server_close()

    if _OAuthCallbackHandler.error:
        print(f"[error] OAuth failed: {_OAuthCallbackHandler.error}")
        sys.exit(1)

    if not _OAuthCallbackHandler.auth_code:
        print("[error] No authorization code received (timed out?).")
        sys.exit(1)

    if _OAuthCallbackHandler.state != state:
        print("[error] State parameter mismatch — possible CSRF attack.")
        sys.exit(1)

    print("Authorization code received. Exchanging for tokens...")

    try:
        tokens = _exchange_code_for_tokens(_OAuthCallbackHandler.auth_code)
    except Exception as exc:
        print(f"[error] Token exchange failed: {exc}")
        sys.exit(1)

    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    expires_at = tokens.get("expires_at", "")

    if not access_token:
        print("[error] No access_token in response.")
        sys.exit(1)

    # Save to config
    config = _load_config(config_path)
    config["mcp_oauth_access_token"] = access_token
    config["mcp_oauth_refresh_token"] = refresh_token
    config["mcp_oauth_expires_at"] = expires_at
    _save_config(config_path, config)

    print()
    print(f"Tokens saved to {config_path}")
    print(f"  access_token:  {access_token[:20]}...")
    if refresh_token:
        print(f"  refresh_token: {refresh_token[:20]}...")
    if expires_at:
        print(f"  expires_at:    {expires_at}")
    print()
    print("Done! The coach will use this token for MCP authentication.")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="One-time OAuth setup for UltraRun.Club MCP authentication"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=_DEFAULT_CONFIG_PATH,
        help=f"Path to config.json (default: {_DEFAULT_CONFIG_PATH})",
    )
    args = parser.parse_args()
    run_oauth_flow(args.config)


if __name__ == "__main__":
    main()
