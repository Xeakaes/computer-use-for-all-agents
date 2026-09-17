"""Standardized error codes and the ApiError envelope (ROADMAP Phase 2).

Backends and routes raise ApiError instead of returning ad-hoc
{"ok": False, "error": "..."} dicts, so agents get machine-readable
failure reasons plus remediation hints.
"""
from __future__ import annotations

ERROR_CODES = frozenset({
    "PERMISSION_REQUIRED",
    "UNSUPPORTED_PLATFORM",
    "UNSUPPORTED_DISPLAY_SERVER",
    "BACKEND_UNAVAILABLE",
    "WINDOW_NOT_FOUND",
    "FOCUS_MISMATCH",
    "CAPTURE_FAILED",
    "INPUT_BLOCKED",
    "INVALID_TARGET",
    "RESOURCE_LIMIT",
    "OPERATION_TIMEOUT",
})


class ApiError(Exception):
    """An API-level failure with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, status: int = 400,
                 platform: str | None = None, action: str | None = None,
                 remediation: str | None = None) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.platform = platform
        self.action = action
        self.remediation = remediation

    def to_dict(self) -> dict:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "platform": self.platform,
                "action": self.action,
                "remediation": self.remediation,
            },
        }


def permission_required(action: str, remediation: str,
                        platform: str | None = None) -> ApiError:
    return ApiError("PERMISSION_REQUIRED", f"Permission required for {action}",
                    status=403, platform=platform, action=action,
                    remediation=remediation)


def unsupported_platform(backend: str, action: str) -> ApiError:
    return ApiError("UNSUPPORTED_DISPLAY_SERVER",
                    f"{action} is not supported by the {backend} backend",
                    status=501, platform=backend, action=action)


def window_not_found(ident: str) -> ApiError:
    return ApiError("WINDOW_NOT_FOUND", f"window not found: {ident}",
                    status=404, action="window_lookup")


def focus_mismatch(expected: str, got: str) -> ApiError:
    return ApiError("FOCUS_MISMATCH",
                    f"focus mismatch: expected {expected}, foreground is {got}",
                    status=409, action="input",
                    remediation="focus the target window and retry")


def capture_failed(reason: str) -> ApiError:
    return ApiError("CAPTURE_FAILED", reason, status=500, action="capture")


def invalid_target(reason: str) -> ApiError:
    return ApiError("INVALID_TARGET", reason, status=403, action="target_validation")
