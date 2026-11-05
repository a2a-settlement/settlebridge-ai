"""Job outcome signals raised by GitHub job handlers."""


class RetryableJobError(Exception):
    """Transient failure; the job is retried with backoff."""


class PermanentJobError(Exception):
    """The job can never succeed; it is marked failed without retry."""
