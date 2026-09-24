"""What the PC-load widget shows: CPU, memory and network speed.

Read by the poller once a second, and like every state reader only while a
Dashboard is watching -- an unwatched PC pays nothing for this. All from
psutil, which the agent already uses, and all cheap: none of these calls
walks processes or blocks.
"""

import time

import psutil


# A reader may answer None: "no honest value this tick" (poller.read_snapshot
# leaves the key out). That is how a fresh baseline stays off the phone.
_cpu_primed = False


def cpu_percent():
    # interval=None: the load since the previous call, without sleeping. The
    # poller's own 1 s tick is the sampling window -- so the first call after
    # a reset only sets the baseline.
    global _cpu_primed
    value = psutil.cpu_percent(interval=None)
    if not _cpu_primed:
        _cpu_primed = True
        return None
    return round(value)


def reset_baselines() -> None:
    """Start the next readings from now.

    Called when the poller resumes after a pause (nobody was watching). The
    first CPU and network readings after an hour's pause would otherwise be
    that hour's average -- shown on the phone as if it were this second.
    """
    global _cpu_primed
    _cpu_primed = False
    NET.reset()


def ram_percent() -> int:
    return round(psutil.virtual_memory().percent)


class NetRate:
    """Bytes per second received and sent, from the counters' growth.

    `down()` takes a sample and `up()` returns the upload half of that same
    sample, so the two keys always describe one interval. The first sample
    (and the first after reset()) only sets the baseline and reports None.
    """

    def __init__(self, counters=psutil.net_io_counters, clock=time.monotonic) -> None:
        self._counters = counters
        self._clock = clock
        self._last = None
        self._up = None

    def reset(self) -> None:
        self._last = None
        self._up = None

    def down(self):
        now = self._clock()
        sample = self._counters()
        previous, self._last = self._last, (now, sample.bytes_recv, sample.bytes_sent)
        if previous is None or now <= previous[0]:
            # Baseline only: no rate to report yet.
            self._up = None
            return None
        elapsed = now - previous[0]
        # max(0, ...): counters reset when an adapter goes away.
        self._up = max(0, round((sample.bytes_sent - previous[2]) / elapsed))
        return max(0, round((sample.bytes_recv - previous[1]) / elapsed))

    def up(self):
        return self._up


NET = NetRate()
