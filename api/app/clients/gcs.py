from __future__ import annotations


class GcsUploader:
    """Thin wrapper around google-cloud-storage for landing-zone uploads.

    The storage client is created lazily so app startup and unit tests never
    touch the network.
    """

    def __init__(self, bucket_name: str, project: str | None = None) -> None:
        self._bucket_name = bucket_name
        self._project = project
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google.cloud import storage

            self._client = storage.Client(project=self._project)
        return self._client

    def upload_bytes(
        self, blob_name: str, data: bytes, content_type: str = "text/csv"
    ) -> str:
        blob = self._get_client().bucket(self._bucket_name).blob(blob_name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{self._bucket_name}/{blob_name}"

    def list_blobs(self, prefix: str, limit: int | None = None) -> list:
        """Blobs under a prefix (name + time_created are what callers use)."""
        iterator = self._get_client().list_blobs(
            self._bucket_name, prefix=prefix, max_results=limit
        )
        return list(iterator)

    def download_bytes(
        self, blob_name: str, start: int | None = None, end: int | None = None
    ) -> bytes:
        """Download a blob; pass start/end for a ranged read of large files."""
        blob = self._get_client().bucket(self._bucket_name).blob(blob_name)
        return blob.download_as_bytes(start=start, end=end)
