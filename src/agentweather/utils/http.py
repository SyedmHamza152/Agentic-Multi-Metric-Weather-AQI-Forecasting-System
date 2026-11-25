"""HTTP helper: a session with retries, plus one function that turns HTTP problems into our errors."""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from agentweather.utils.exceptions import APIError, AuthenticationError, RateLimitError
from agentweather.utils.logger import get_logger

logger = get_logger(__name__)

# Hides API keys if a URL ever ends up in a log line or error message.
_SECRET_PATTERN = re.compile(r"(appid|token|api_key|apikey)=([^&\s]+)", re.IGNORECASE)


def redact(text: Any) -> str:
    """Replace secret values in a string with ***."""
    return _SECRET_PATTERN.sub(r"\1=***", str(text))


def build_session(max_retries: int = 3, backoff_factor: float = 1.0) -> requests.Session:
    """Create a requests session that retries temporary server errors (5xx) and connection problems."""
    retry = Retry(
        total=max_retries,
        connect=max_retries,
        read=max_retries,
        status=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,  # we turn bad statuses into our own errors below
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "AgentWeather/0.1 (student project)"})
    return session


def get_json(
    session: requests.Session,
    url: str,
    params: Optional[Mapping[str, Any]],
    timeout: float,
    service: str,
) -> Any:
    """GET a URL and return parsed JSON. Raises one of our APIError types on any problem."""
    try:
        response = session.get(url, params=params, timeout=timeout)
    except requests.exceptions.Timeout:
        raise APIError(f"{service}: request timed out after {timeout}s") from None
    except requests.exceptions.RequestException as exc:
        # "from None" so the original error (which may contain the key in the URL) is not shown.
        raise APIError(f"{service}: network error: {redact(exc)}") from None

    status = response.status_code
    if status in (401, 403):
        raise AuthenticationError(f"{service}: key rejected (HTTP {status})")
    if status == 429:
        raise RateLimitError(f"{service}: rate limit exceeded (HTTP 429)")
    if not response.ok:
        raise APIError(f"{service}: HTTP {status}: {redact(response.text[:200])}")

    try:
        return response.json()
    except ValueError:
        raise APIError(f"{service}: response was not valid JSON") from None
