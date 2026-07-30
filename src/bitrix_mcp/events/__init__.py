"""Event feed for the Bitrix24 MCP server.

Two independent ways to learn that something changed on the portal:

  PUSH  -- `receiver`: the portal's outgoing webhook POSTs here. Real-time, but
           needs an HTTP transport and a URL the portal can actually reach.
  PULL  -- cursors in `store`: the server asks the portal what changed since the
           last high-water mark. Outbound only, so it works behind NAT, VPN or a
           segmented network, at the cost of latency and blindness to deletions.

Both share one SQLite file. This package is the only stateful part of the
server, so the stdio deployment keeps working exactly as before when neither
path is configured.
"""

from .phpform import parse_php_form
from .store import EventStore

__all__ = ["EventStore", "parse_php_form"]
