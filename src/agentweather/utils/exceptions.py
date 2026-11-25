"""Custom errors, so the pipeline can tell *what* went wrong and react correctly."""


class AgentWeatherError(Exception):
    """Base class for every error raised by this project."""


class ConfigError(AgentWeatherError):
    """Settings or API keys are missing or invalid."""


class APIError(AgentWeatherError):
    """An API call failed (network error, timeout, bad HTTP status)."""


class AuthenticationError(APIError):
    """The API rejected our key or token."""


class RateLimitError(APIError):
    """We exceeded the API quota."""


class FeatureUnavailableError(APIError):
    """A feature is not available on the current API plan (for example UV index)."""


class DataValidationError(AgentWeatherError):
    """The API answered, but the data is missing, malformed or unusable."""
