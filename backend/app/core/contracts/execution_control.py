"""Non-secret provider controls shared by adapters and durable orchestration."""
from fractions import Fraction
import math


class RejectedSubmission(Exception):
    """A local or contractually definite non-acceptance, never a network ambiguity."""


class QueryBackoff(Exception):
    """Pause queries until Retry-After without treating an accepted job as failed.

    The worker persists the relative delay against its own clock before releasing
    its lease. Restarts and both resume entry points then share the same deadline.
    """

    def __init__(self, retry_after_seconds: int):
        """Accept only bounded safe integers; no upstream message or URL is retained."""
        if type(retry_after_seconds) is not int or not 5 <= retry_after_seconds <= 9007199254740991:
            raise ValueError('Query backoff must be a safe integer of at least five seconds')
        self.retry_after_seconds = retry_after_seconds
        super().__init__('Provider requested a query pause')


def query_backoff_deadline(now: float, seconds: int) -> float:
    """Round an exact deadline upward so SQLite Float storage never shortens a wait."""
    QueryBackoff(seconds)
    if type(now) not in (int, float) or not math.isfinite(now) or not 0 <= now <= 9007199254740991:
        raise ValueError('A finite supported query clock is required')
    exact = Fraction(now) + seconds
    deadline = float(exact)
    return math.nextafter(deadline, math.inf) if Fraction(deadline) < exact else deadline
