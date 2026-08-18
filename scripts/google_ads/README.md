# Google Ads API credentials

The Marketing Plan's keyword research has four tiers. Only the first one — the Google Ads
API — returns **measured** monthly search volume and top-of-page bid ranges. The rest give
relative interest, or an LLM's clearly-labelled guess. It costs nothing; it just takes a
consent flow to reach.

You need four values. Three you copy out of a console, and one you have to mint:

| Value | Where it comes from |
| --- | --- |
| Client ID | Google Cloud console, OAuth client (Desktop app) |
| Client secret | Same place |
| Developer token | Google Ads account → API Center |
| **Refresh token** | `auth.py` in this directory |
| Login customer ID | Your Google Ads account ID — `auth.py` will list the valid ones |

## 1. OAuth client

1. Open [console.cloud.google.com](https://console.cloud.google.com/) and create a project
   (or pick one).
2. **APIs & Services → Library**, search *Google Ads API*, enable it.
3. **APIs & Services → OAuth consent screen**. External is fine. While it's in *Testing*,
   add your own Google account under **Test users** — otherwise consent is refused with a
   generic error that reads like a bug.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**, application
   type **Desktop app**. Copy the client ID and secret.

Desktop app is the type that matters: it's the one allowed to redirect to `localhost`,
which is how `auth.py` catches the response without you hosting anything.

## 2. Developer token

In [ads.google.com](https://ads.google.com/), **Tools → Setup → API Center**. The token is
issued immediately, but starts with **test account** access, which can only query test
accounts — against a real one it fails with `DEVELOPER_TOKEN_NOT_APPROVED`. Apply for Basic
access from the same page; it's free and usually approved within a day or two.

Basic access allows 15,000 operations/day. Each plan generation is one batched
`GenerateKeywordIdeas` call, so that ceiling is not a practical constraint here.

## 3. Refresh token

```bash
python scripts/google_ads/auth.py --client-id YOUR_ID --client-secret YOUR_SECRET
```

Your browser opens, you approve, and the token is printed once. Add `--developer-token` and
the script goes further — it queries the live API and lists the customer IDs your login can
reach, which is both a real end-to-end check and where the **Login customer ID** value comes
from:

```bash
python scripts/google_ads/auth.py --client-id YOUR_ID --client-secret YOUR_SECRET --developer-token YOUR_TOKEN
```

Nothing is written to disk. Paste the values into **Settings → Google Ads API**, where the
app keeps them encrypted per-user.

## 4. What happens next

The credentials are sent with each plan request to the Marketing Plan Space, so the keyword
tier runs against **your** account and your quota rather than anyone else's. A partial set
is treated as absent rather than merged with the operator's, because a half-and-half
identity authenticates as neither and fails deep inside the client with an error that reads
like an outage.

Once they're set, the keyword sheet gains real `Monthly searches` and `CPC` columns instead
of relative-interest buckets.

## Troubleshooting

**"Google returned no refresh token."** The account had already approved this client, and a
silent re-approval returns only an access token. Revoke it at
[myaccount.google.com/permissions](https://myaccount.google.com/permissions) and re-run.

**`DEVELOPER_TOKEN_NOT_APPROVED`** — the token is still test-only. See step 2.

**`USER_PERMISSION_DENIED`** — the login can't reach the customer ID you gave. Run with
`--developer-token` to see which IDs it actually can reach.

**Consent screen refuses your own account** — add it under **Test users** while the app is
in Testing.
