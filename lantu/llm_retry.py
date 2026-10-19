from __future__ import annotations

import random
from dataclasses import dataclass

from lantu.client import LLMError, NetworkError, RateLimitError


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry policy for one logical model request."""

    max_attempts: int = 3
    max_delay_seconds: float = 30.0
    base_delay_seconds: float = 1.0
    jitter_seconds: float = 0.25

    def should_retry(self, error: LLMError, attempt: int) -> bool:
        if attempt >= self.max_attempts:
            return False
        if isinstance(error, (NetworkError, RateLimitError)):
            return True
        # Provider clients may expose transient 5xx errors through status_code.
        status = getattr(error, "status_code", None)
        return isinstance(status, int) and status in {500, 502, 503, 504}

    def delay_seconds(self, error: LLMError, attempt: int) -> float:
        retry_after = getattr(error, "retry_after", None)
        if isinstance(retry_after, (int, float)) and retry_after >= 0:
            return min(float(retry_after), self.max_delay_seconds)
        delay = min(
            self.base_delay_seconds * (2 ** max(attempt - 1, 0)),
            self.max_delay_seconds,
        )
        if self.jitter_seconds > 0:
            delay += random.uniform(0, self.jitter_seconds)
        return min(delay, self.max_delay_seconds)
