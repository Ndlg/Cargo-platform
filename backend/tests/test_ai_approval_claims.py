import importlib
import json


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool:
        assert nx is True
        assert ex == 300
        if key in self.values:
            return False
        self.values[key] = value
        return True

    def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)


def test_approval_claim_is_opaque_bound_and_consumed_once() -> None:
    module = importlib.import_module("app.services.ai_approval_claims")
    redis = FakeRedis()
    store = module.ApprovalClaimStore(redis)
    payload = {
        "session_id": "session-1",
        "workspace_id": 9,
        "task_id": 61,
        "raw_record_id": 901,
        "document_sequence": 2,
        "fingerprint": "v2:test",
        "fingerprint_code": "CN-PACKAGE-ITEMS",
        "actor": {
            "id": 7,
            "username": "admin",
            "display_name": "管理员",
        },
        "model_candidate_sha256": "a" * 64,
        "administrator_rows_sha256": "b" * 64,
    }

    claim = store.create(payload)
    stored_key, stored_value = next(iter(redis.values.items()))
    first = store.consume(claim)
    second = store.consume(claim)

    assert "admin" not in claim
    assert claim not in stored_key
    assert json.loads(stored_value) == payload
    assert first == payload
    assert second is None


def test_approval_claim_can_be_revoked_before_callback() -> None:
    module = importlib.import_module("app.services.ai_approval_claims")
    store = module.ApprovalClaimStore(FakeRedis())
    claim = store.create(
        {
            "session_id": "session-2",
            "workspace_id": 4,
            "task_id": 62,
            "raw_record_id": 902,
            "document_sequence": 1,
            "fingerprint": "v2:test-2",
            "fingerprint_code": "CN-ITEM-INFO",
            "actor": {"id": 8, "username": "operator", "display_name": ""},
            "model_candidate_sha256": "c" * 64,
            "administrator_rows_sha256": "d" * 64,
        }
    )

    store.revoke(claim)

    assert store.consume(claim) is None
