from typing import Any


class MolQuarryError(Exception):
    """A stable, machine-readable failure shared by SDK, CLI and MCP."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        source: str | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.source = source
        self.retryable = retryable
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": str(self),
                "source": self.source,
                "retryable": self.retryable,
                "details": self.details,
            },
        }
