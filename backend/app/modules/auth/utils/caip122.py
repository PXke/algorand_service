"""CAIP-122 sign-in message construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Caip122Message:
    """CAIP-122 / ARC-0060 AUTH payload (JSON object signed under ARC-0060)."""

    domain: str
    account_address: str
    uri: str
    chain_id: str
    nonce: str
    version: str = "1"
    type: str = "ed25519"
    statement: str | None = None
    issued_at: str | None = None
    expiration_time: str | None = None
    not_before: str | None = None
    request_id: str | None = None
    resources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this message to the CAIP-122 JSON payload shape."""
        payload: dict[str, Any] = {
            "domain": self.domain,
            "account_address": self.account_address,
            "uri": self.uri,
            "version": self.version,
            "chain_id": self.chain_id,
            "nonce": self.nonce,
            "type": self.type,
        }
        if self.statement:
            payload["statement"] = self.statement
        if self.issued_at:
            payload["issued-at"] = self.issued_at
        if self.expiration_time:
            payload["expiration-time"] = self.expiration_time
        if self.not_before:
            payload["not-before"] = self.not_before
        if self.request_id:
            payload["request-id"] = self.request_id
        if self.resources:
            payload["resources"] = self.resources
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Caip122Message:
        """Reconstruct a Caip122Message from its CAIP-122 JSON payload shape."""
        return cls(
            domain=str(data["domain"]),
            account_address=str(data["account_address"]),
            uri=str(data["uri"]),
            chain_id=str(data["chain_id"]),
            nonce=str(data["nonce"]),
            version=str(data.get("version", "1")),
            type=str(data.get("type", "ed25519")),
            statement=data.get("statement"),
            issued_at=data.get("issued-at"),
            expiration_time=data.get("expiration-time"),
            not_before=data.get("not-before"),
            request_id=data.get("request-id"),
            resources=list(data.get("resources") or []),
        )


def utc_now_iso() -> str:
    """Return the current UTC time as a second-precision ISO 8601 string with a Z suffix."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
