from __future__ import annotations

from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.app.agents.team import TeamLeadAgent
from api.app.deps import get_approval_store, get_team_lead
from api.app.infra.approval import ApprovalStore

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    answer: str


class ApprovalRequest(BaseModel):
    approved: bool
    comment: str = ""


@router.post("/completions", response_model=ChatResponse)
async def chat_completions(
    request: ChatRequest,
    agent: TeamLeadAgent = Depends(get_team_lead),
) -> ChatResponse:
    answer = await agent.run(request.message)
    return ChatResponse(answer=answer)


async def _stream_agent(
    message: str,
    agent: TeamLeadAgent,
    approval_store: ApprovalStore,
) -> AsyncIterator[str]:
    async for event in agent.run_stream(message, approval_store=approval_store):
        yield f"data: {event}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    agent: TeamLeadAgent = Depends(get_team_lead),
    approval_store: ApprovalStore = Depends(get_approval_store),
) -> StreamingResponse:
    return StreamingResponse(
        _stream_agent(request.message, agent, approval_store),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/approval/{approval_id}")
async def submit_approval(
    approval_id: str,
    body: ApprovalRequest,
    approval_store: ApprovalStore = Depends(get_approval_store),
) -> dict:
    resolved = approval_store.resolve(approval_id, body.approved, body.comment)
    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"Approval request '{approval_id}' not found or already resolved.",
        )
    return {"ok": True}
