import json
import logging
import httpx
from api.app.messages import Message
from typing import Optional, Any, AsyncGenerator

_logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterClient:
    """
    Drop-in replacement for AIHubClient that calls OpenRouter's OpenAI-compatible API.
    Uses a static API key instead of the identity/token service.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://finops-agent.demo",  # Optional, for OpenRouter rankings
            "X-Title": "FinOps Agent",
        }

    async def ainvoke(
        self,
        messages: list[Message],
        tool: Optional[list[dict[str, Any]]] = None,
    ):
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
        }

        if tool:
            payload["tools"] = tool
            payload["tool_choice"] = "auto"

        _logger.debug("OpenRouter request payload:\n%s", json.dumps(payload, indent=2, ensure_ascii=False))

        async with httpx.AsyncClient(timeout=120.0) as client:
            res = await client.post(OPENROUTER_BASE_URL, json=payload, headers=self._headers())

            if res.is_error:
                _logger.error(
                    "OpenRouter %s — response body: %s",
                    res.status_code,
                    res.text,
                )
                res.raise_for_status()

            data = res.json()
            _logger.debug("OpenRouter response:\n%s", json.dumps(data, indent=2, ensure_ascii=False))
            return data["choices"][0]["message"]

    async def astream_text(
        self,
        messages: list[Message],
    ) -> AsyncGenerator[str, None]:
        """
        Call the LLM in streaming mode WITHOUT tools and yield raw text delta strings.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "stream": True,
        }
        _logger.debug("OpenRouter stream text:\n%s", json.dumps(payload, indent=2, ensure_ascii=False))

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", OPENROUTER_BASE_URL, json=payload, headers=self._headers()) as res:
                if res.is_error:
                    await res.aread()
                    _logger.error("OpenRouter stream %s: %s", res.status_code, res.text)
                    res.raise_for_status()

                async for line in res.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    raw_data = line[6:].strip()
                    if raw_data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw_data)
                        text = chunk["choices"][0]["delta"].get("content") or ""
                        if text:
                            _logger.debug("OpenRouter stream delta: %r", text)
                            yield text
                    except (json.JSONDecodeError, KeyError, IndexError):
                        _logger.debug("Skipping unparseable SSE chunk: %s", raw_data)

    async def astream_invoke(
        self,
        messages: list[Message],
        tool: Optional[list[dict[str, Any]]] = None,
    ):
        """
        Call the LLM in streaming mode WITH tool support.
        Accumulates the full streamed response (including tool calls) and returns
        the assembled message dict — same shape as ainvoke.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "stream": True,
        }

        if tool:
            payload["tools"] = tool
            payload["tool_choice"] = "auto"

        _logger.debug("OpenRouter stream invoke:\n%s", json.dumps(payload, indent=2, ensure_ascii=False))

        assembled: dict[str, Any] = {"role": "assistant", "content": None}
        tool_calls_map: dict[int, dict] = {}

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", OPENROUTER_BASE_URL, json=payload, headers=self._headers()) as res:
                if res.is_error:
                    await res.aread()
                    _logger.error("OpenRouter stream invoke %s — response body: %s", res.status_code, res.text)
                    res.raise_for_status()

                async for line in res.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                        delta = chunk["choices"][0].get("delta", {})

                        # Accumulate text content
                        if delta.get("content"):
                            assembled["content"] = (assembled["content"] or "") + delta["content"]

                        # Accumulate tool calls
                        for tc_delta in delta.get("tool_calls", []):
                            idx = tc_delta["index"]
                            if idx not in tool_calls_map:
                                tool_calls_map[idx] = {
                                    "id": tc_delta.get("id", ""),
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            tc = tool_calls_map[idx]
                            fn = tc_delta.get("function", {})
                            if fn.get("name"):
                                tc["function"]["name"] += fn["name"]
                            if fn.get("arguments"):
                                tc["function"]["arguments"] += fn["arguments"]
                            if tc_delta.get("id"):
                                tc["id"] = tc_delta["id"]

                    except (json.JSONDecodeError, KeyError, IndexError):
                        _logger.debug("Skipping unparseable SSE chunk: %s", raw)

        if tool_calls_map:
            assembled["tool_calls"] = [tool_calls_map[i] for i in sorted(tool_calls_map)]

        _logger.debug("OpenRouter assembled message: %s", json.dumps(assembled, indent=2, ensure_ascii=False))
        return assembled
