from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.api.contract_reviews import create_contract_review_router
from app.api.deps import get_current_user
from app.api.sessions import create_sessions_router
from app.infrastructure.contract_review_repository import _normalize_review_summary_row
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(repository: object) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-1",
        "username": "test-user",
    }
    app.include_router(create_sessions_router(object(), repository))
    return TestClient(app)


def _history_client(repository: object) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "user-1",
        "username": "test-user",
    }
    service = type("Service", (), {"repository": repository})()
    app.include_router(create_contract_review_router(service))
    return TestClient(app)


def _uuid_row() -> dict:
    return {
        "review_id": uuid4(),
        "session_id": uuid4(),
        "filename": "劳动合同.docx",
        "status": "needs_confirmation",
        "confirmation_status": "not_started",
        "created_at": datetime.now(timezone.utc),
        "report_id": uuid4(),
        "report_version": 1,
    }


def test_review_summary_row_normalizes_all_uuid_fields() -> None:
    row = _uuid_row()
    normalized = _normalize_review_summary_row(row)

    assert normalized["review_id"] == str(row["review_id"])
    assert normalized["session_id"] == str(row["session_id"])
    assert normalized["report_id"] == str(row["report_id"])
    assert normalized["created_at"] == row["created_at"]


def test_session_reviews_serializes_uuid_rows_as_strings() -> None:
    session_id = uuid4()
    row = _uuid_row() | {"session_id": session_id}

    class Repository:
        async def list_session_reviews(self, received_session_id: str, user_id: str):
            assert received_session_id == str(session_id)
            assert user_id == "user-1"
            return [_normalize_review_summary_row(row)]

    response = _client(Repository()).get(f"/api/sessions/{session_id}/reviews")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == str(session_id)
    assert isinstance(payload["reviews"][0]["review_id"], str)
    assert isinstance(payload["reviews"][0]["session_id"], str)
    assert isinstance(payload["reviews"][0]["report_id"], str)


def test_session_reviews_rejects_invalid_uuid_before_repository_call() -> None:
    class Repository:
        called = False

        async def list_session_reviews(self, received_session_id: str, user_id: str):
            self.called = True
            return []

    repository = Repository()
    response = _client(repository).get("/api/sessions/not-a-uuid/reviews")

    assert response.status_code == 422
    assert repository.called is False


def test_session_reviews_allows_missing_report_id() -> None:
    session_id = uuid4()
    row = _uuid_row() | {"session_id": session_id, "report_id": None}

    class Repository:
        async def list_session_reviews(self, received_session_id: str, user_id: str):
            return [_normalize_review_summary_row(row)]

    response = _client(Repository()).get(f"/api/sessions/{session_id}/reviews")

    assert response.status_code == 200
    assert response.json()["reviews"][0]["report_id"] is None


def test_contract_history_uses_normalized_repository_rows() -> None:
    row = _uuid_row()

    class Repository:
        async def list_user_reviews(self, user_id: str, *, limit: int = 50):
            assert user_id == "user-1"
            assert limit == 50
            return [_normalize_review_summary_row(row)]

    response = _history_client(Repository()).get("/api/contract-reviews/history")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload["reviews"][0]["review_id"], str)
    assert isinstance(payload["reviews"][0]["session_id"], str)
    assert isinstance(payload["reviews"][0]["report_id"], str)
