"""Tests for /chat/{stream,completions,approval} (Sprint 9)."""
from __future__ import annotations

import json
from typing import AsyncGenerator

import pytest
from fastapi.testclient import TestClient

from api.app.infra.approval import ApprovalStore
from api.app.main import app


class FakeAgent:
    async def run(self, message: str) -> str:
        return f"echo: {message}"

    async def run_stream(self, message: str, approval_store=None) -> AsyncGenerator[str, None]:
        yield json.dumps({"type": "agent_start", "agent": "ForecastAgent"})
        yield json.dumps({"type": "delta", "content": "Top SKU ", "agent": "ForecastAgent"})
        yield json.dumps({"type": "delta", "content": "for 2025-09-10", "agent": "ForecastAgent"})
        yield json.dumps({"type": "done"})


@pytest.fixture
def client() -> TestClient:
    app.state.team_lead = FakeAgent()
    app.state.approval_store = ApprovalStore()
    return TestClient(app)


def test_chat_completions_returns_answer(client: TestClient) -> None:
    resp = client.post("/chat/completions", json={"message": "hello"})
    assert resp.status_code == 200
    assert resp.json() == {"answer": "echo: hello"}


def test_chat_stream_emits_sse_events(client: TestClient) -> None:
    with client.stream("POST", "/chat/stream", json={"message": "top SKUs?"}) as resp:
        assert resp.status_code == 200
        body = b"".join(resp.iter_bytes()).decode()
    assert "agent_start" in body
    assert "Top SKU" in body
    assert "[DONE]" in body


def test_chat_approval_unknown_id_404(client: TestClient) -> None:
    resp = client.post("/chat/approval/does-not-exist", json={"approved": True})
    assert resp.status_code == 404
