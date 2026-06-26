from typing import Literal

from pydantic import BaseModel, Field, model_validator


class NonceRequest(BaseModel):
    wallet_address: str = Field(min_length=58, max_length=58)


class Caip122Payload(BaseModel):
    domain: str
    account_address: str
    uri: str
    chain_id: str
    nonce: str
    version: str = "1"
    type: str = "ed25519"
    statement: str | None = None
    issued_at: str | None = Field(default=None, alias="issued-at")
    expiration_time: str | None = Field(default=None, alias="expiration-time")
    not_before: str | None = Field(default=None, alias="not-before")
    request_id: str | None = Field(default=None, alias="request-id")
    resources: list[str] | None = None

    model_config = {"populate_by_name": True}


class NonceResponse(BaseModel):
    wallet_address: str
    nonce: str
    signing_message: str
    caip122: Caip122Payload
    expires_in_seconds: int


class Arc0060Proof(BaseModel):
    data_b64: str
    signature_b64: str
    authenticator_data_b64: str
    domain: str
    request_id: str | None = None


class VerifyRequest(BaseModel):
    wallet_address: str = Field(min_length=58, max_length=58)
    nonce: str
    proof_method: Literal["arc0025_txn", "arc0060", "legacy_message"] = "arc0060"
    signature_b64: str | None = None
    signed_txn_b64: str | None = None
    arc0060: Arc0060Proof | None = None

    @model_validator(mode="after")
    def require_matching_proof(self) -> "VerifyRequest":
        if self.proof_method == "arc0060":
            if self.arc0060 is None:
                msg = "arc0060 proof is required when proof_method is arc0060"
                raise ValueError(msg)
        elif self.proof_method == "arc0025_txn" and not self.signed_txn_b64:
            msg = "signed_txn_b64 is required when proof_method is arc0025_txn"
            raise ValueError(msg)
        elif self.proof_method == "legacy_message" and not self.signature_b64:
            msg = "signature_b64 is required when proof_method is legacy_message"
            raise ValueError(msg)
        return self


class VerifyResponse(BaseModel):
    session_token: str
    wallet_address: str
    issued_at_epoch: int
    expires_in_epoch: int
    expires_in_seconds: int
    proof_method: str


class SessionInfo(BaseModel):
    wallet_address: str
    issued_at_epoch: int
    expires_in_epoch: int
