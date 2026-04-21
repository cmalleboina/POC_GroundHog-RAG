import logging
import re

# --- PII patterns ---

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE = re.compile(
    r"(?<!\d)"                      # not preceded by digit
    r"(?:\+?1[-.\s]?)?"             # optional country code
    r"(?:\(?\d{3}\)?[-.\s]?)"       # area code
    r"\d{3}[-.\s]?\d{4}"            # number
    r"(?!\d)"                       # not followed by digit
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# JWT: header.payload.signature (each part is base64url)
_JWT_RE = re.compile(r"eyJ[a-zA-Z0-9_-]+\.eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+")

# Long strings threshold
_LONG_STRING_THRESHOLD = 100
_LONG_STRING_RE = re.compile(r"[^\s]{" + str(_LONG_STRING_THRESHOLD) + r",}")

PII_PATTERNS = [
    (_SSN_RE, "[REDACTED:SSN]"),
    (_EMAIL_RE, "[REDACTED:EMAIL]"),
    (_PHONE_RE, "[REDACTED:PHONE]"),
]


def sanitize(text: str) -> str:
    """Sanitize a log message string.

    1. Redact JWT tokens (keep last 8 chars for correlation).
    2. Redact PII patterns (SSNs, emails, phone numbers).
    3. Redact long continuous strings (likely chunk text / embeddings).
    """
    if not isinstance(text, str):
        return text

    # Truncate JWTs: keep last 8 chars for debugging correlation
    def _redact_jwt(match: re.Match) -> str:
        token = match.group(0)
        return f"[JWT:...{token[-8:]}]"

    text = _JWT_RE.sub(_redact_jwt, text)

    # Redact PII
    for pattern, replacement in PII_PATTERNS:
        text = pattern.sub(replacement, text)

    # Redact long continuous strings (chunk text, embeddings, base64 blobs)
    def _redact_long(match: re.Match) -> str:
        return f"[REDACTED:{len(match.group(0))} chars]"

    text = _LONG_STRING_RE.sub(_redact_long, text)

    return text


class LogSanitizingFilter(logging.Filter):
    """Logging filter that sanitizes log records before they reach handlers.

    Applies PII redaction, JWT truncation, and long-string redaction
    to the formatted log message.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # Sanitize the main message
        if record.args:
            # Format the message with args, then sanitize
            try:
                record.msg = sanitize(record.msg % record.args)
                # Keep args as an empty tuple so logging formatters that
                # inspect/iterate args (e.g. uvicorn access logger) do not
                # crash on None.
                record.args = ()
            except (TypeError, ValueError):
                record.msg = sanitize(str(record.msg))
                record.args = ()
        else:
            record.msg = sanitize(str(record.msg))
            record.args = ()

        return True


def install(logger_name: str | None = None) -> None:
    """Install the sanitizing filter on a logger.

    Args:
        logger_name: logger name, or None for the root logger.
    """
    target = logging.getLogger(logger_name)
    target.addFilter(LogSanitizingFilter())


def install_globally() -> None:
    """Install the sanitizing filter on the root logger and common loggers."""
    sanitizing_filter = LogSanitizingFilter()

    # Root logger
    logging.getLogger().addFilter(sanitizing_filter)

    # Common loggers used in the app
    for name in ("src.api", "src.ingestion", "uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(sanitizing_filter)
