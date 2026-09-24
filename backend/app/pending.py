import asyncio

_pending: dict[str, asyncio.Task] = {}


def track(req_id: str | None, timeout_seconds: float, on_timeout) -> None:
    # No req_id, nothing any client could match a synthetic result against.
    if req_id is None:
        return

    # A repeated req_id replaces the earlier timer rather than racing it: left
    # running, the old timer would find the *new* entry under the same key
    # when it fired, delete it and report a timeout for a command still in
    # flight.
    previous = _pending.pop(req_id, None)
    if previous is not None:
        previous.cancel()

    async def _timer() -> None:
        await asyncio.sleep(timeout_seconds)
        # Only fire if this timer is still the pending one: resolve() may have
        # already cancelled it, but a cancellation can lose the race against
        # the sleep completing, so re-check rather than trusting the cancel.
        if _pending.get(req_id) is task:
            del _pending[req_id]
            await on_timeout(req_id)

    task = asyncio.ensure_future(_timer())
    _pending[req_id] = task


def resolve(req_id: str | None) -> None:
    # A late or duplicate result (agent replies twice, or replies after its
    # own timer already fired) must never crash the caller, so unknown
    # req_ids are a silent no-op rather than a KeyError.
    task = _pending.pop(req_id, None)
    if task is not None:
        task.cancel()
