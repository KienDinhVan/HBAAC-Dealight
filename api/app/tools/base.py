import inspect
import json
from typing import TYPE_CHECKING, AsyncGenerator, Callable, get_origin, get_args, Union, Any

if TYPE_CHECKING:
    from api.app.agents.base import ReactAgent


def _convert_to_openai_type(py_type: type) -> str:
    mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
    }
    return mapping.get(py_type, "string")


def _get_fn_signature(fn: Callable) -> dict[str, Any]:
    properties: dict = {}
    required: list[str] = []

    sig = inspect.signature(fn)
    hints = fn.__annotations__

    for name, param in sig.parameters.items():
        annotation = hints.get(name)
        if annotation is None:
            continue

        origin = get_origin(annotation)
        args = get_args(annotation)
        is_optional = origin is Union and type(None) in args

        if is_optional:
            actual_type = next(a for a in args if a is not type(None))
        else:
            actual_type = annotation
            if param.default is inspect.Parameter.empty:
                required.append(name)

        param_schema: dict[str, Any] = {
            "type": _convert_to_openai_type(actual_type)
        }

        # Extract per-parameter description from docstring if available
        docstring = fn.__doc__ or ""
        for line in docstring.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{name}:") or stripped.startswith(f"{name} ("):
                desc = stripped.split(":", 1)[-1].strip()
                if desc:
                    param_schema["description"] = desc
                break

        properties[name] = param_schema

    parameters: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        parameters["required"] = required

    lines = (fn.__doc__ or "").strip().splitlines()
    description = lines[0].strip() if lines else ""

    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": description,
            "parameters": parameters,
        },
    }


class Tool:
    """
    Wraps a callable into a Tool with a name, function, and OpenAI-compatible
    JSON schema signature that can be sent directly to an LLM.
    """

    def __init__(
        self,
        name: str,
        fn: Callable,
        fn_signature: dict[str, Any],
        requires_approval: bool = False,
    ) -> None:
        self.name = name
        self.fn = fn
        self.fn_signature = fn_signature
        self.requires_approval = requires_approval

    def __str__(self) -> str:
        return json.dumps(self.fn_signature, indent=2)

    def __repr__(self) -> str:
        return f"Tool(name={self.name!r})"

    async def run(self, **kwargs: Any) -> Any:
        """Execute the underlying function, supporting both sync and async callables."""
        if inspect.iscoroutinefunction(self.fn):
            return await self.fn(**kwargs)
        return self.fn(**kwargs)

    def to_openai_schema(self) -> dict[str, Any]:
        """Return the OpenAI-compatible function schema for this tool."""
        return self.fn_signature


def tool(fn: Callable = None, *, requires_approval: bool = False):
    """
    Decorator that wraps a function into a Tool with an auto-generated
    OpenAI-compatible schema derived from type hints and the docstring.

    Can be used with or without arguments::

        @tool
        def get_cost(service: str) -> float: ...

        @tool(requires_approval=True)
        async def run_flow(flow_name: str) -> str: ...
    """
    def _make_tool(f: Callable) -> Tool:
        fn_signature = _get_fn_signature(f)
        return Tool(
            name=fn_signature["function"]["name"],
            fn=f,
            fn_signature=fn_signature,
            requires_approval=requires_approval,
        )

    if fn is not None:
        # Used as bare @tool (no parentheses)
        return _make_tool(fn)
    # Used as @tool(...) factory
    return _make_tool


class AgentTool(Tool):
    """
    Wraps a ReactAgent as a delegatable Tool for use in an orchestrator agent.

    The LLM sees a single ``question: str`` parameter; the full sub-agent
    execution (its own tool-calling loop, streaming deltas, etc.) is handled
    transparently.

    - ``run(**kwargs)``        → returns the sub-agent's final answer string
    - ``run_stream(**kwargs)`` → yields the sub-agent's raw SSE event strings
    """

    def __init__(self, name: str, description: str, agent: "ReactAgent") -> None:
        self._agent_ref = agent
        fn_signature: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "The question or task to send to this specialist agent.",
                        }
                    },
                    "required": ["question"],
                },
            },
        }
        # fn is never invoked directly; run() / run_stream() delegate to the agent
        super().__init__(name=name, fn=agent.run, fn_signature=fn_signature)

    @property
    def agent_name(self) -> str:
        """The display name of the wrapped agent."""
        return self._agent_ref._agent_name

    async def run(self, **kwargs: Any) -> Any:
        """Delegate to the sub-agent and return its final answer string."""
        return await self._agent_ref.run(kwargs.get("question", ""))

    async def run_stream(self, **kwargs: Any) -> AsyncGenerator[str, None]:
        """Proxy the sub-agent's SSE event stream verbatim."""
        approval_store = kwargs.pop("approval_store", None)
        async for event in self._agent_ref.run_stream(
            kwargs.get("question", ""), approval_store=approval_store
        ):
            yield event
