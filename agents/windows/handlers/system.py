"""What the PC-load widget shows: CPU, memory and network speed.

Read by the poller once a second, and like every state reader only while a
Dashboard is watching -- an unwatched PC pays nothing for this. All from
psutil, which the agent already uses, and all cheap: none of these calls
walks processes or blocks.
"""

import time

import psutil


def cpu_percent() -> int:
    # interval=None: the load since the previous call, without sleeping. The
    # poller's own 1 s tick is the sampling window.
    return round(psutil.cpu_percent(interval=None))


def ram_percent() -> int:
    return round(psutil.virtual_memory().percent)


class NetRate:
    """Bytes per second received and sent, from the counters' growth.

    `down()` takes a sample and `up()` returns the upload half of that same
    sample, so the two keys always describe one interval. The first sample
    only sets the baseline and reports 0.
    """

    def __init__(self, counters=psutil.net_io_counters, clock=time.monotonic) -> None:
        self._counters = counters
        self._clock = clock
        self._last = None
        self._up = 0

    def down(self) -> int:
        now = self._clock()
        sample = self._counters()
        previous, self._last = self._last, (now, sample.bytes_recv, sample.bytes_sent)
        if previous is None or now <= previous[0]:
            self._up = 0
            return 0
        elapsed = now - previous[0]
        # max(0, ...): counters reset when an adapter goes away.
        self._up = max(0, round((sample.bytes_sent - previous[2]) / elapsed))
        return max(0, round((sample.bytes_recv - previous[1]) / elapsed))

    def up(self) -> int:
        return self._up


NET = NetRate()
