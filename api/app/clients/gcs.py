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

    def upload_bytes(
        self, blob_name: str, data: bytes, content_type: str = "text/csv"
    ) -> str:
        if self._client is None:
            from google.cloud import storage

            self._client = storage.Client(project=self._project)
        blob = self._client.bucket(self._bucket_name).blob(blob_name)
        blob.upload_from_string(data, content_type=content_type)
        return f"gs://{self._bucket_name}/{blob_name}"
