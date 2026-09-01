"""How long one media upload is allowed to take.

Not a per-network number: it describes the person's uplink and the size of the file, so both
the Mastodon and the Tumblr upload paths take their budget from here rather than each
reaching for whatever flat timeout its module already had. Sharing it is the point — the two
paths had the same bug, independently, for the same reason.

A socket timeout is not a deadline for the call. It is the deadline for each socket
operation, and writing the request body is one of them. urllib3 arms the socket with the
*connect* timeout before handing the request to http.client, sends the body under it, and
only afterwards lowers it to the read timeout for the response (connectionpool.py,
_make_request) — so the whole body has to leave the machine inside that one window, and
passing a (connect, read) pair does not move it.

A flat timeout is right for a JSON call and hopeless for a video. A 29MB clip needs a
sustained 12Mbit/s uplink to clear 20 seconds, and a domestic connection does not have one.
Worse, an expired write deadline does not surface as a timeout; CPython reports it as

    SSLError(SSLWantWriteError(3, 'The operation did not complete (write) (_ssl.c:2427)'))

which reads like a TLS fault, and is why neither of these was obviously a timeout. So the
budget is sized from the payload instead of fixed.
"""

from __future__ import annotations

#: Enough for connect, TLS handshake and a small body on a poor line.
BASE_SECONDS = 60

#: A ceiling, so a transfer that has stalled outright cannot hold a post open forever.
MAX_SECONDS = 900

#: ~1Mbit/s. Deliberately pessimistic: this is the speed below which waiting is abandoned,
#: not the speed anyone expects. Assuming a good uplink is exactly what broke before.
FLOOR_BYTES_PER_SECOND = 128 * 1024


def upload_timeout(size_bytes: int) -> float:
    """A socket budget a body of ``size_bytes`` can realistically be written inside."""
    return min(BASE_SECONDS + size_bytes / FLOOR_BYTES_PER_SECOND, float(MAX_SECONDS))
