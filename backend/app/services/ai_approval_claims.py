from hashlib import sha256
import json
import secrets
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings


CLAIM_TTL_SECONDS = 300
CLAIM_KEY_PREFIX = "cargo-platform:ai-approval:"


class ApprovalClaimStoreUnavailable(RuntimeError):
    pass


class ApprovalClaimStore:
    def __init__(self, redis: Any) -> None:
        self.redis = redis

    @staticmethod
    def _key(claim: str) -> str:
        digest = sha256(claim.encode("utf-8")).hexdigest()
        return f"{CLAIM_KEY_PREFIX}{digest}"

    def create(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        for _attempt in range(3):
            claim = secrets.token_urlsafe(32)
            if self.redis.set(
                self._key(claim),
                encoded,
                nx=True,
                ex=CLAIM_TTL_SECONDS,
            ):
                return claim
        raise ApprovalClaimStoreUnavailable("无法创建一次性审批凭证。")

    def consume(self, claim: str) -> dict[str, Any] | None:
        encoded = self.redis.getdel(self._key(claim))
        if encoded is None:
            return None
        if isinstance(encoded, bytes):
            encoded = encoded.decode("utf-8")
        try:
            payload = json.loads(encoded)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def revoke(self, claim: str) -> None:
        self.redis.getdel(self._key(claim))


def approval_claim_store() -> ApprovalClaimStore:
    settings = get_settings()
    return ApprovalClaimStore(
        Redis.from_url(settings.redis_url, decode_responses=True),
    )


def create_approval_claim(payload: dict[str, Any]) -> str:
    try:
        return approval_claim_store().create(payload)
    except RedisError as exc:
        raise ApprovalClaimStoreUnavailable("审批凭证服务暂时不可用。") from exc


def consume_approval_claim(claim: str) -> dict[str, Any] | None:
    try:
        return approval_claim_store().consume(claim)
    except RedisError as exc:
        raise ApprovalClaimStoreUnavailable("审批凭证服务暂时不可用。") from exc


def revoke_approval_claim(claim: str) -> None:
    try:
        approval_claim_store().revoke(claim)
    except RedisError:
        return
