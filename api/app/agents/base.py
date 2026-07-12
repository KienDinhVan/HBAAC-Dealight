import asyncio
import json
import logging
import uuid
from typing import TYPE_CHECKING, Any, AsyncGenerator, Sequence

if TYPE_CHECKING:
    from api.app.infra.approval import ApprovalStore

from api.app.clients.openrouter import OpenRouterClient as AIHubClient
from api.app.tools.base import Tool, AgentTool
from api.app.messages import AssistantMessage, Message, SystemMessage, ToolMessage, UserMessage
from api.app.agents.context import ContextManager

_FINISH_TOOL = "finish_conversation"
_DEFAULT_MAX_ROUNDS = 10
# Maximum times any single tool may be called in one conversation.
# Prevents runaway retry loops (e.g. repeated failed SQL queries).
_DEFAULT_MAX_TOOL_CALLS = 3
_FORCED_SYNTHESIS_PROMPT = (
    "You have used all available reasoning steps. "
    "Based on everything you have gathered so far, provide the best and most complete answer you can — "
    "even if some information is missing. Do NOT call any more tools."
)


class ReactAgent:
    """
    A base ReAct (Reason + Act) agent that runs a tool-calling loop
    against an LLM until the model either calls `finish_conversation`,
    returns a plain-text answer, or exhausts the maximum allowed rounds.

    Usage::

        agent = ReactAgent(
            client=aihub_client,
            system_prompt="You are a FinOps assistant.",
            tools=[my_tool_a, my_tool_b, finish_conversation],
        )
        answer = await agent.run("What is the EC2 cost for last month?")
    """

    def __init__(
        self,
        client: AIHubClient,
        system_prompt: str,
        tools: Sequence[Tool],
        max_rounds: int = _DEFAULT_MAX_ROUNDS,
        max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS,
        agent_name: str = "ReActAgent",
        context_manager: ContextManager | None = None,
    ) -> None:
        self._client = client
        self._system_prompt = system_prompt
        self._tools = {t.name: t for t in tools}
        self._max_rounds = max_rounds
        self._max_tool_calls = max_tool_calls
        self._agent_name = agent_name
        self._ctx = context_manager or ContextManager()
        self.logger = logging.getLogger(self.__class__.__name__)

    async def run(self, user_message: str) -> str:
        """
        Execute the ReAct loop for a given user message.

        Args:
            user_message: The natural-language request from the user.

        Returns:
            The final answer string produced by the agent.
        """
        messages: list[Message] = [
            SystemMessage(content=self._system_prompt),
            UserMessage(content=user_message),
        ]
        tool_schemas = [t.to_openai_schema() for t in self._tools.values()]
        tool_call_counts: dict[str, int] = {}

        for round_num in range(1, self._max_rounds + 1):
            self.logger.debug("Round %d/%d", round_num, self._max_rounds)

            response = await self._client.ainvoke(messages, tool=tool_schemas)
            self.logger.debug("LLM response: %s", response)

            tool_calls = response.get("tool_calls", [])

            if tool_calls:
                messages.append(AssistantMessage(tool_calls=tool_calls))

                for tool_call in tool_calls:
                    function = tool_call.get("function", {})
                    tool_name = function.get("name")
                    raw_arguments = function.get("arguments", "{}")

                    # finish_conversation → exit early
                    if tool_name == _FINISH_TOOL:
                        try:
                            final_message = json.loads(raw_arguments).get("message", "Task completed")
                        except json.JSONDecodeError:
                            final_message = "Task completed"
                        self.logger.info("Agent finished with message: %s", final_message)
                        return final_message

                    # Parse tool arguments
                    try:
                        tool_input = json.loads(raw_arguments)
                    except json.JSONDecodeError:
                        tool_input = {}

                    # Guard: stop calling a tool that has already hit the per-tool cap
                    tool_call_counts[tool_name] = tool_call_counts.get(tool_name, 0) + 1
                    if tool_call_counts[tool_name] > self._max_tool_calls:
                        self.logger.warning(
                            "Tool '%s' hit the call cap (%d). Injecting stop message.",
                            tool_name, self._max_tool_calls,
                        )
                        messages.append(
                            ToolMessage(
                                tool_call_id=tool_call.get("id", ""),
                                name=tool_name,
                                content=(
                                    f"Tool '{tool_name}' has already been called "
                                    f"{self._max_tool_calls} times. Stop retrying and "
                                    "summarise what you know so far for the user."
                                ),
                            )
                        )
                        continue

                    self.logger.info("Executing tool '%s' with input: %s", tool_name, tool_input)

                    # Execute tool
                    try:
                        tool_output = await self._execute_tool(tool_name, tool_input)
                    except Exception as exc:
                        tool_output = f"Tool execution failed: {exc}"
                        self.logger.exception("Tool '%s' raised an error", tool_name)

                    messages.append(
                        ToolMessage(
                            tool_call_id=tool_call.get("id", ""),
                            name=tool_name,
                            content=self._ctx.truncate_tool_output(str(tool_output)),
                        )
                    )

                # Compress before the next LLM call
                messages = await self._ctx.maybe_compress(messages, self._client)
                continue

            content = response.get("content", "")
            if content:
                self.logger.info("Agent returned final answer")
                return content

        self.logger.warning(
            "Max rounds (%d) reached for '%s' — forcing final synthesis.",
            self._max_rounds, self._agent_name,
        )
        messages.append(UserMessage(content=_FORCED_SYNTHESIS_PROMPT))
        try:
            response = await self._client.ainvoke(messages)  # no tools → guaranteed text
            content = response.get("content", "")
            if content:
                return content
        except Exception:
            self.logger.exception("Forced synthesis call failed for '%s'", self._agent_name)
        return "I was unable to complete the request within the allowed steps."

    async def run_stream(
        self,
        user_message: str,
        approval_store: "ApprovalStore | None" = None,
    ) -> AsyncGenerator[str, None]:
        """
        Streaming variant of run().

        Yields JSON-encoded event strings:
          {"type": "tool_approval_request", ...}  — awaiting user approval
          {"type": "tool_denied",            ...}  — user denied the tool call
          {"type": "tool_start", "name": ...}      — tool execution begins
          {"type": "tool_done",  "name": ...}      — tool execution finished
          {"type": "delta",      "content": ...}   — final answer token
          {"type": "done"}                          — stream complete

        Tool-calling rounds use ainvoke() (full response needed to parse tool calls).
        The final synthesis round switches to astream_text() for real token streaming.
        """
        messages: list[Message] = [
            SystemMessage(content=self._system_prompt),
            UserMessage(content=user_message),
        ]
        tool_schemas = [t.to_openai_schema() for t in self._tools.values()]
        tool_call_counts: dict[str, int] = {}

        for round_num in range(1, self._max_rounds + 1):
            self.logger.debug("Stream round %d/%d", round_num, self._max_rounds)

            response = await self._client.ainvoke(messages, tool=tool_schemas)
            tool_calls = response.get("tool_calls", [])

            if tool_calls:
                messages.append(AssistantMessage(tool_calls=tool_calls))

                for tool_call in tool_calls:
                    function = tool_call.get("function", {})
                    tool_name = function.get("name")
                    raw_arguments = function.get("arguments", "{}")

                    # finish_conversation → emit its payload as streaming deltas and exit
                    if tool_name == _FINISH_TOOL:
                        try:
                            final_message = json.loads(raw_arguments).get("message", "Task completed")
                        except json.JSONDecodeError:
                            final_message = "Task completed"
                        self.logger.info("finish_conversation called, streaming answer")
                        for word in final_message.split(" "):
                            yield json.dumps({"type": "delta", "content": word + " ", "agent": self._agent_name})
                        yield json.dumps({"type": "done"})
                        return

                    try:
                        tool_input = json.loads(raw_arguments)
                    except json.JSONDecodeError:
                        tool_input = {}

                    # Guard: stop calling a tool that has already hit the per-tool cap
                    tool_call_counts[tool_name] = tool_call_counts.get(tool_name, 0) + 1
                    if tool_call_counts[tool_name] > self._max_tool_calls:
                        self.logger.warning(
                            "Tool '%s' hit the call cap (%d). Injecting stop message.",
                            tool_name, self._max_tool_calls,
                        )
                        messages.append(
                            ToolMessage(
                                tool_call_id=tool_call.get("id", ""),
                                name=tool_name,
                                content=(
                                    f"Tool '{tool_name}' has already been called "
                                    f"{self._max_tool_calls} times. Stop retrying and "
                                    "summarise what you know so far for the user."
                                ),
                            )
                        )
                        continue

                    tool_obj = self._tools.get(tool_name)
                    if isinstance(tool_obj, AgentTool):
                        # ── sub-agent delegation ──────────────────────────────
                        yield json.dumps({"type": "agent_start", "agent": tool_obj.agent_name})
                        self.logger.info("Delegating to agent '%s'", tool_obj.agent_name)
                        final_parts: list[str] = []
                        try:
                            async for sub_event in tool_obj.run_stream(
                                **tool_input, approval_store=approval_store
                            ):
                                data = json.loads(sub_event)
                                if data.get("type") == "done":
                                    continue  # suppress sub-agent sentinel; orchestrator emits its own
                                if data.get("type") == "delta":
                                    final_parts.append(data.get("content", ""))
                                yield sub_event
                        except Exception as exc:
                            self.logger.exception("AgentTool '%s' raised an error", tool_name)
                            final_parts = [f"Agent execution failed: {exc}"]
                        tool_output = "".join(final_parts)
                        yield json.dumps({"type": "agent_done", "agent": tool_obj.agent_name})
                        tool_content = self._ctx.truncate_tool_output(tool_output)
                    else:
                        # ── regular tool call ─────────────────────────────────

                        # ── approval gate ─────────────────────────────────────
                        user_comment = ""
                        if tool_obj and tool_obj.requires_approval and approval_store is not None:
                            approval_id = str(uuid.uuid4())
                            ev = approval_store.create(approval_id)
                            yield json.dumps({
                                "type": "tool_approval_request",
                                "name": tool_name,
                                "agent": self._agent_name,
                                "input": tool_input,
                                "approval_id": approval_id,
                            })
                            self.logger.info(
                                "Waiting for approval of tool '%s' (id=%s)",
                                tool_name, approval_id,
                            )
                            try:
                                await asyncio.wait_for(ev.wait(), timeout=300.0)
                            except asyncio.TimeoutError:
                                approval_store.cleanup(approval_id)
                                self.logger.warning(
                                    "Approval for '%s' timed out — treating as denial", tool_name
                                )
                                yield json.dumps({
                                    "type": "tool_denied",
                                    "name": tool_name,
                                    "agent": self._agent_name,
                                    "approval_id": approval_id,
                                })
                                messages.append(
                                    ToolMessage(
                                        tool_call_id=tool_call.get("id", ""),
                                        name=tool_name,
                                        content="Tool execution was not approved (request timed out).",
                                    )
                                )
                                continue

                            approved = approval_store.get_result(approval_id)
                            user_comment = approval_store.get_comment(approval_id)
                            approval_store.cleanup(approval_id)

                            if not approved:
                                self.logger.info("Tool '%s' was denied by the user", tool_name)
                                yield json.dumps({
                                    "type": "tool_denied",
                                    "name": tool_name,
                                    "agent": self._agent_name,
                                    "approval_id": approval_id,
                                })
                                denial_content = "Tool execution was denied by the user."
                                if user_comment:
                                    denial_content += f" User comment: {user_comment}"
                                messages.append(
                                    ToolMessage(
                                        tool_call_id=tool_call.get("id", ""),
                                        name=tool_name,
                                        content=denial_content,
                                    )
                                )
                                continue

                            self.logger.info("Tool '%s' approved by the user", tool_name)
                            if user_comment:
                                self.logger.info("User approval comment: %s", user_comment)
                        # ── end approval gate ──────────────────────────────────

                        yield json.dumps({"type": "tool_start", "name": tool_name, "agent": self._agent_name, "input": tool_input})
                        self.logger.info("Executing tool '%s' with input: %s", tool_name, tool_input)
                        try:
                            tool_output = await self._execute_tool(tool_name, tool_input)
                        except Exception as exc:
                            tool_output = f"Tool execution failed: {exc}"
                            self.logger.exception("Tool '%s' raised an error", tool_name)

                        # Intercept chart payloads — emit a dedicated SSE event and
                        # replace the tool output with a short summary for the LLM.
                        tool_output_str = str(tool_output)
                        # Prepend any approval comment so the LLM sees it alongside the result.
                        if user_comment:
                            tool_output_str = f"[User comment on approval: {user_comment}]\n" + tool_output_str
                        try:
                            payload = json.loads(tool_output_str)
                            if isinstance(payload, dict) and payload.get("type") == "chart":
                                yield json.dumps({"type": "chart", "spec": payload["spec"], "agent": self._agent_name})
                                record_count = len(payload["spec"].get("data", {}).get("values", []))
                                tool_output_str = (
                                    f"Chart generated successfully with {record_count} data point(s). "
                                    "The chart has been sent to the frontend for display."
                                )
                            elif isinstance(payload, dict) and payload.get("type") == "query_error":
                                # Surface the structured error as a readable string for the agent
                                tool_output_str = (
                                    f"Query failed — status: {payload.get('status')}, "
                                    f"error_type: {payload.get('error_type')}, "
                                    f"reason: {payload.get('reason')}. "
                                    f"Suggestion: {payload.get('suggestion')}"
                                )
                        except (json.JSONDecodeError, KeyError, TypeError):
                            pass

                        yield json.dumps({"type": "tool_done", "name": tool_name, "agent": self._agent_name})
                        tool_content = self._ctx.truncate_tool_output(tool_output_str)

                    messages.append(
                        ToolMessage(
                            tool_call_id=tool_call.get("id", ""),
                            name=tool_name,
                            content=tool_content,
                        )
                    )

                # Compress before the next LLM call
                messages = await self._ctx.maybe_compress(messages, self._client)
                continue

            # Re-invoke with stream=True to get real token-level streaming.
            self.logger.info("Streaming plain-text final answer via astream_text")
            async for text in self._client.astream_text(messages):
                yield json.dumps({"type": "delta", "content": text, "agent": self._agent_name})
            yield json.dumps({"type": "done"})
            return

        self.logger.warning(
            "Max rounds (%d) reached for '%s' — forcing final synthesis.",
            self._max_rounds, self._agent_name,
        )
        messages.append(UserMessage(content=_FORCED_SYNTHESIS_PROMPT))
        try:
            async for text in self._client.astream_text(messages):  # no tools → guaranteed text
                yield json.dumps({"type": "delta", "content": text, "agent": self._agent_name})
        except Exception:
            self.logger.exception("Forced synthesis stream failed for '%s'", self._agent_name)
            yield json.dumps({"type": "delta", "content": "I was unable to complete the request within the allowed steps.", "agent": self._agent_name})
        yield json.dumps({"type": "done"})

    async def _execute_tool(self, tool_name: str, tool_input: dict[str, Any]) -> Any:
        """
        Look up the tool by name and call it.

        Raises:
            KeyError: If the tool name is not registered.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise KeyError(f"Unknown tool: '{tool_name}'. Available: {list(self._tools)}")
        return await tool.run(**tool_input)
