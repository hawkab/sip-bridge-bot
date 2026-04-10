TELEGRAM_RETRY_DELAYS = [0, 1, 2, 5, 10, 20]


def is_retryable_telegram_error(last_error: str | None) -> bool:
    if not last_error:
        return False
    return last_error.startswith("RetryAfter:") or last_error.startswith("TimedOut:") or last_error.startswith("NetworkError:")
