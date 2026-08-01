"""
Distinct error types for Yen Max, replacing broad `except Exception`
blocks with classifications that let calling code decide whether to
retry, fail fast, or report differently.
"""


class YenMaxError(Exception):
    """Base class for all Yen Max errors."""
    pass


# ================= TRANSIENT (safe to retry) =================

class TransientError(YenMaxError):
    """Base class for errors that are likely to succeed on retry."""
    pass


class APITimeoutError(TransientError):
    """The Groq API call took too long."""
    pass


class RateLimitError(TransientError):
    """The Groq API returned a rate-limit response (HTTP 429)."""
    pass


class NetworkError(TransientError):
    """A connection-level failure occurred (DNS, connection reset, etc)."""
    pass


# ================= PERMANENT (do not retry) =================

class PermanentError(YenMaxError):
    """Base class for errors that will not resolve by retrying."""
    pass


class AuthenticationError(PermanentError):
    """The Groq API key was rejected (HTTP 401/403)."""
    pass


class InvalidRequestError(PermanentError):
    """The request itself was malformed (HTTP 400)."""
    pass


class ParseError(PermanentError):
    """The model's response could not be parsed into the expected shape."""
    pass


class FilenameSafetyError(PermanentError):
    """A generated or requested filename failed safety validation."""
    pass


class ResourceLimitError(PermanentError):
    """A build exceeded a configured resource limit (files, bytes, API calls)."""
    pass


# ================= CLASSIFICATION HELPER =================

def classify_http_error(status_code: int) -> YenMaxError:
    """
    Map an HTTP status code from the Groq API to the appropriate
    error class.

    Args:
        status_code: HTTP status code returned by the API

    Returns:
        An instance of the appropriate YenMaxError subclass
    """
    if status_code == 429:
        return RateLimitError(f"Rate limited (HTTP {status_code})")
    if status_code in (401, 403):
        return AuthenticationError(f"Authentication failed (HTTP {status_code})")
    if status_code == 400:
        return InvalidRequestError(f"Invalid request (HTTP {status_code})")
    if status_code >= 500:
        # Server-side errors are worth retrying once
        return TransientError(f"Server error (HTTP {status_code})")

    return PermanentError(f"Unexpected API error (HTTP {status_code})")
