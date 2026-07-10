from __future__ import annotations


class OnlineStoreClient:
    """Read-only lookups against the Redis online feature store.

    Key layout matches scripts/run_de_pipeline.py: hash `sales_daily:<item_code>`
    holding the latest curated row per SKU. Lazy connection for test friendliness.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._client = None

    def _get_client(self):
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(self._url, decode_responses=True)
        return self._client

    def get_item(self, item_code: str) -> dict[str, str] | None:
        record = self._get_client().hgetall(f"sales_daily:{item_code}")
        return record or None

    def key_count(self) -> int:
        """Number of SKUs currently synced (DBSIZE of the online store)."""
        return int(self._get_client().dbsize())
