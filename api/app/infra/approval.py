import asyncio
from dataclasses import dataclass, field


@dataclass
class ApprovalStore:
    """
    Coordinates human-in-the-loop approval for tool execution.

    Each pending approval is tracked by a UUID string. The streaming agent
    creates an entry, waits on its event, and the HTTP endpoint resolves it.
    """

    _events: dict[str, asyncio.Event] = field(default_factory=dict)
    _results: dict[str, bool] = field(default_factory=dict)
    _comments: dict[str, str] = field(default_factory=dict)

    def create(self, approval_id: str) -> asyncio.Event:
        """Register a new pending approval and return its asyncio.Event."""
        ev = asyncio.Event()
        self._events[approval_id] = ev
        return ev

    def resolve(self, approval_id: str, approved: bool, comment: str = "") -> bool:
        """
        Resolve a pending approval.

        Returns True if found and resolved, False if the ID is unknown.
        """
        ev = self._events.get(approval_id)
        if ev is None:
            return False
        self._results[approval_id] = approved
        self._comments[approval_id] = comment.strip()
        ev.set()
        return True

    def get_result(self, approval_id: str) -> bool:
        """Return the decision for a resolved approval (defaults to False)."""
        return self._results.get(approval_id, False)

    def get_comment(self, approval_id: str) -> str:
        """Return the user comment for a resolved approval (empty string if none)."""
        return self._comments.get(approval_id, "")

    def cleanup(self, approval_id: str) -> None:
        """Remove the approval entry after it has been consumed."""
        self._events.pop(approval_id, None)
        self._results.pop(approval_id, None)
        self._comments.pop(approval_id, None)
