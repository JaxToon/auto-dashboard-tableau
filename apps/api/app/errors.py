"""Stable error codes and user-safe messages. No stack traces reach the browser."""

from __future__ import annotations


class AppError(Exception):
    status_code = 400
    code = "BAD_REQUEST"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.details = details or {}


class NotAuthenticated(AppError):
    status_code = 401
    code = "NOT_AUTHENTICATED"


class Forbidden(AppError):
    status_code = 403
    code = "FORBIDDEN"


class NotFound(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ValidationFailed(AppError):
    status_code = 422
    code = "VALIDATION_FAILED"


class Conflict(AppError):
    status_code = 409
    code = "CONFLICT"


class FeatureDisabled(AppError):
    status_code = 503
    code = "FEATURE_DISABLED"


class IntegrationError(AppError):
    status_code = 502
    code = "INTEGRATION_ERROR"


class RateLimited(AppError):
    status_code = 429
    code = "RATE_LIMITED"
