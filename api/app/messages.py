from typing import Any, Literal, Optional
from pydantic import BaseModel

class SystemMessage(BaseModel):
    role: Literal["system"] = "system"
    content: str


class UserMessage(BaseModel):
    role: Literal["user"] = "user"
    content: str


class ToolCall(BaseModel):
    id: str
    type: Literal["function"] = "function"
    function: dict[str, Any]


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: Optional[str] = None
    tool_calls: Optional[list[ToolCall]] = None


class ToolMessage(BaseModel):
    role: Literal["tool"] = "tool"
    tool_call_id: str
    name: str
    content: str


# Union type for a full conversation turn
Message = SystemMessage | UserMessage | AssistantMessage | ToolMessage

