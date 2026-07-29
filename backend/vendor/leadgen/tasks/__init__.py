"""Task handlers — one unit of pipeline work each, called by both the daemon and the CLI.

Priority order (opportunity cost, highest first), matching OpenOutreach:
    follow_up > find_email > email > discover_qualify
"""

from . import common, discover_qualify, email, find_email, follow_up

# The daemon claims tasks in this order.
PRIORITY = ["follow_up", "find_email", "email", "discover_qualify"]

HANDLERS = {
    "discover_qualify": discover_qualify.run,
    "find_email": find_email.run,
    "email": email.run,
    "follow_up": follow_up.run,
}

__all__ = ["HANDLERS", "PRIORITY", "common", "discover_qualify", "find_email", "email", "follow_up"]
