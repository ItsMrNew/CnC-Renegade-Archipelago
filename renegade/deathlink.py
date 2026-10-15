"""Session-only DeathLink queues. Deaths are never persisted or replayed offline."""
from collections import deque
import math
import time


class DeathLinkState:
    def __init__(self):
        self.enabled = False
        self.incoming = deque(maxlen=8)
        self.outgoing = deque(maxlen=8)
        self.seen = deque(maxlen=128)
        self.sent = 0
        self.received = 0

    def clear_pending(self):
        self.incoming.clear()
        self.outgoing.clear()

    def receive(self, packet, own_source):
        tags = packet.get("tags")
        if not self.enabled or not isinstance(tags, list) or "DeathLink" not in tags:
            return False
        data = packet.get("data")
        if not isinstance(data, dict):
            return False
        stamp, source = data.get("time"), data.get("source")
        if (type(stamp) not in (int, float) or not 0 <= stamp <= 1e15 or not math.isfinite(stamp)
                or not isinstance(source, str) or not source or len(source) > 256
                or source == own_source):
            return False
        key = (source, stamp)
        if key in self.seen:
            return False
        self.seen.append(key)
        cause = data.get("cause", "")
        self.incoming.append(dict(time=stamp, source=source, cause=cause[:512] if isinstance(cause, str) else ""))
        self.received += 1
        return True

    def local_death(self, source, map_name):
        if not self.enabled:
            return False
        stamp = time.time()
        packet = dict(cmd="Bounce", tags=["DeathLink"],
                      data=dict(time=stamp, source=source, cause=f"{source} died in Renegade ({map_name})."))
        self.seen.append((source, stamp))
        self.outgoing.append(packet)
        return True
