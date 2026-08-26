"""
Async token-bucket rate limiter.
One shared instance per API client — import the pre-built singletons below.
"""
from __future__ import annotations

import asyncio
import time

from app.utils.logger import logger


class RateLimiter:
    """
    Async rate limiter using a simple sliding-window / min-interval approach.

    Args:
        calls_per_minute: Maximum requests allowed in any 60-second window.
        name: Human-readable label used in log output.
    """

    def __init__(self, calls_per_minute: int, name: str = "unnamed") -> None:
        if calls_per_minute <= 0:
            raise ValueError("calls_per_minute must be a positive integer")
        self.calls_per_minute = calls_per_minute
        self.name = name
        self._min_interval: float = 60.0 / calls_per_minute
        self._lock: asyncio.Lock = asyncio.Lock()
        self._last_call_at: float = 0.0  # monotonic time of the last allowed call

    async def acquire(self) -> None:
        """
        Wait until it is safe to make the next API call, then return.
        Subsequent callers queue behind the lock so bursts are smoothed out.
        """
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call_at
            wait = self._min_interval - elapsed

            if wait > 0:
                logger.debug(
                    "RateLimiter[%s] sleeping %.3fs before next call", self.name, wait
                )
                await asyncio.sleep(wait)

            self._last_call_at = time.monotonic()
            logger.debug("RateLimiter[%s] acquired", self.name)

    @property
    def seconds_per_call(self) -> float:
        return self._min_interval

    def __repr__(self) -> str:
        return f"RateLimiter(name={self.name!r}, calls_per_minute={self.calls_per_minute})"


# ------------------------------------------------------------------ #
# Pre-built singletons — import these in ingestion clients
# ------------------------------------------------------------------ #

football_data_limiter = RateLimiter(calls_per_minute=10,  name="football_data")
api_football_limiter  = RateLimiter(calls_per_minute=5,   name="api_football")
odds_api_limiter      = RateLimiter(calls_per_minute=3,   name="odds_api")
openligadb_limiter    = RateLimiter(calls_per_minute=20,  name="openligadb")

# Registry — useful for admin / monitoring endpoints
ALL_LIMITERS: dict[str, RateLimiter] = {
    "football_data": football_data_limiter,
    "api_football":  api_football_limiter,
    "odds_api":      odds_api_limiter,
    "openligadb":    openligadb_limiter,
}


def get_limiter(name: str) -> RateLimiter:
    """Return the pre-built limiter for *name*, or raise KeyError."""
    try:
        return ALL_LIMITERS[name]
    except KeyError:
        raise KeyError(f"No rate limiter registered for {name!r}. Known: {list(ALL_LIMITERS)}")
