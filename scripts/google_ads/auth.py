"""Mint a Google Ads API refresh token for the Marketing Plan's keyword research.

Google Ads is the only source in this app that returns *measured* search volume and CPC
rather than relative interest or an LLM's guess. It is free, but getting to it means an
OAuth consent flow, and the refresh token that comes out is the one value you cannot read
off a settings page in the Google Cloud console. Hence this script.

    python scripts/google_ads/auth.py --client-id ... --client-secret ...

It opens your browser, catches the redirect on localhost, exchanges the code, and prints
the refresh token. Pass --developer-token as well and it goes one step further: it calls
the API for real and lists the customer IDs your login can reach, which is where the
"Login customer ID" field in Settings comes from.

See README.md in this directory for how to obtain the client id, secret and developer
token in the first place.

Nothing is written to disk. The refresh token is printed once, for you to paste into
Settings -> Google Ads API, because that is where this app keeps it (encrypted, per-user)
and a copy lying around in a file is a copy that can leak.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import secrets
import socket
import sys
import threading
import urllib.parse
import webbrowser

import requests

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/adwords"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catches the one redirect Google sends back, then gets out of the way."""

    result: dict = {}

    def do_GET(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        _CallbackHandler.result = {k: v[0] for k, v in params.items()}

        ok = "code" in _CallbackHandler.result
        body = (
            "<h2>Authorised.</h2><p>Back to the terminal — you can close this tab.</p>"
            if ok
            else f"<h2>Authorisation failed.</h2><p>{_CallbackHandler.result.get('error', 'no code returned')}</p>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, *args) -> None:
        """Silence the default per-request logging; the query string holds the auth code."""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pkce_pair() -> tuple[str, str]:
    """PKCE verifier and challenge.

    A desktop client's secret is not really secret — it ships inside the app — so Google's
    loopback flow leans on PKCE to stop anyone who intercepts the redirect from trading the
    code for a token.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def authorise(client_id: str, client_secret: str, timeout: int = 300) -> str:
    """Run the consent flow and return a refresh token."""
    port = _free_port()
    redirect_uri = f"http://localhost:{port}"
    state = secrets.token_urlsafe(24)
    verifier, challenge = _pkce_pair()

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = timeout
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    url = AUTH_URI + "?" + urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            # offline is what makes Google issue a refresh token at all; consent forces the
            # screen even for an account that has already approved this client, because a
            # silent re-approval returns an access token and no refresh token, which looks
            # like the script is broken.
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )

    print("\nOpening your browser to authorise. If nothing happens, paste this in yourself:\n")
    print(f"  {url}\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - a headless box just uses the printed URL
        pass

    print(f"Waiting for the redirect on {redirect_uri} ...")
    thread.join(timeout + 5)
    server.server_close()

    result = _CallbackHandler.result
    if not result:
        raise SystemExit("Timed out waiting for the browser redirect.")
    if "error" in result:
        raise SystemExit(f"Google refused the request: {result['error']}")
    if not secrets.compare_digest(result.get("state", ""), state):
        raise SystemExit("State mismatch — discarding this response rather than trusting it.")
    code = result.get("code")
    if not code:
        raise SystemExit("No authorisation code came back.")

    response = requests.post(
        TOKEN_URI,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise SystemExit(f"Token exchange failed ({response.status_code}): {response.text}")

    payload = response.json()
    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        raise SystemExit(
            "Google returned no refresh token. That normally means this account had already "
            "approved the client; revoke it at https://myaccount.google.com/permissions and "
            "run this again."
        )
    return refresh_token


def list_customers(client_id: str, client_secret: str, refresh_token: str, developer_token: str) -> list[str]:
    """Customer IDs this login can reach — the candidates for 'Login customer ID'.

    Doubles as the only proof that matters: if this returns, the four values work together
    against the real API rather than merely looking well-formed.
    """
    from google.ads.googleads.client import GoogleAdsClient

    client = GoogleAdsClient.load_from_dict(
        {
            "developer_token": developer_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "use_proto_plus": True,
        }
    )
    service = client.get_service("CustomerService")
    accessible = service.list_accessible_customers()
    return [name.split("/")[-1] for name in accessible.resource_names]


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--client-id", required=True, help="OAuth client ID (Desktop app)")
    parser.add_argument("--client-secret", required=True, help="OAuth client secret")
    parser.add_argument(
        "--developer-token",
        default="",
        help="Optional. Supply it and the script verifies the credentials against the live "
        "API and lists your accessible customer IDs.",
    )
    args = parser.parse_args()

    refresh_token = authorise(args.client_id.strip(), args.client_secret.strip())

    print("\n" + "=" * 72)
    print("Refresh token (treat this like a password):\n")
    print(f"  {refresh_token}")
    print("=" * 72)

    if not args.developer_token.strip():
        print(
            "\nPaste it into Settings -> Google Ads API, along with your client ID, client\n"
            "secret, developer token and login customer ID.\n"
            "\nRe-run with --developer-token to have this script confirm the credentials\n"
            "work and tell you which customer IDs you can use."
        )
        return

    print("\nVerifying against the live API ...")
    try:
        customers = list_customers(
            args.client_id.strip(), args.client_secret.strip(), refresh_token, args.developer_token.strip()
        )
    except Exception as err:  # noqa: BLE001 - the library raises a wide variety here
        print(f"\n  Could not list customers: {type(err).__name__}: {err}")
        # Three failures dominate here and they have nothing to do with each other. Naming
        # the wrong one sends people to re-mint a token that was never the problem, so the
        # hint is chosen from the error rather than assumed.
        text = f"{type(err).__name__}: {err}".lower()
        if "invalid_client" in text or "unauthorized_client" in text:
            hint = (
                "The client ID or secret doesn't match the OAuth client this token was\n"
                "  minted for. Check both against the Cloud console."
            )
        elif "developer_token" in text or "not_approved" in text:
            hint = (
                "The developer token isn't approved for production yet — a token in 'test\n"
                "  account' status can only query test accounts. Apply for Basic access in\n"
                "  the Google Ads API Center; see README.md here."
            )
        elif "permission" in text or "customer" in text:
            hint = (
                "Authenticated, but this login has no access to the account. Use a login\n"
                "  that manages a Google Ads account, or its manager (MCC)."
            )
        else:
            hint = "See the Troubleshooting section of README.md in this directory."
        print(f"\n  The refresh token above is still valid. {hint}")
        raise SystemExit(1) from err

    if not customers:
        print("\n  Authenticated, but this login can't reach any Google Ads accounts.")
        print("  Create one at https://ads.google.com, or use a login that manages one.")
        return

    print(f"\n  Works. Accessible customer IDs ({len(customers)}):\n")
    for customer_id in customers:
        pretty = f"{customer_id[:3]}-{customer_id[3:6]}-{customer_id[6:]}" if len(customer_id) == 10 else customer_id
        print(f"    {pretty}   (enter as {customer_id})")
    print(
        "\n  Use your manager (MCC) account for 'Login customer ID' if you have one,\n"
        "  otherwise the account the keywords should be costed against."
    )


if __name__ == "__main__":
    sys.exit(main())
