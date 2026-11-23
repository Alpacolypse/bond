"""Blobs in an S3 bucket, or any store that speaks the S3 API (MinIO, R2, Ceph).

Bond doesn't depend on an AWS SDK: the host passes a boto3 S3 client it
configured itself (credentials, region, endpoint, retries), and with it, botocore.
S3 is strongly consistent after a write, so no read retries are needed. A blob is
named by its bytes, so it is written once: a put is conditional (If-None-Match),
and finding the blob already there is success, not an overwrite.
"""

from __future__ import annotations

import base64
from typing import Protocol, TypedDict

from botocore.exceptions import ClientError

# A conditional put that loses to another one in flight for the same key (409) is tried again, this many times.
_CONFLICT_RETRIES = 5


class Body(Protocol):
    def read(self) -> bytes: ...


class GetObjectOutput(TypedDict):
    Body: Body


class S3Client(Protocol):
    """What Bond needs of a boto3 S3 client."""

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ChecksumSHA256: str, IfNoneMatch: str) -> object: ...

    def get_object(self, *, Bucket: str, Key: str) -> GetObjectOutput: ...


class S3Blobs:
    """Blobs as objects in bucket, each keyed by prefix + its hash."""

    def __init__(self, client: S3Client, bucket: str, prefix: str = ""):
        self.client = client
        self.bucket = bucket
        self.prefix = prefix

    def key(self, content_hash: str) -> str:
        return self.prefix + content_hash

    def put(self, content_hash: str, data: bytes) -> None:
        # The hash is the object's sha256, so S3 checks the upload against it and refuses corrupted bytes.
        checksum = base64.b64encode(bytes.fromhex(content_hash)).decode()
        for attempt in range(_CONFLICT_RETRIES + 1):
            try:
                self.client.put_object(Bucket=self.bucket, Key=self.key(content_hash), Body=data,
                                       ChecksumSHA256=checksum, IfNoneMatch="*")
                return
            except ClientError as e:  # the SDK's only way of saying how a request failed
                code = _code(e)
                if code == "PreconditionFailed":  # already stored: the same bytes, under their own hash
                    return
                # Another upload of this key is in flight; it may yet fail, so this one can't stop here.
                if code != "ConditionalRequestConflict" or attempt == _CONFLICT_RETRIES:
                    raise

    def get(self, content_hash: str) -> bytes | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.key(content_hash))
        except ClientError as e:
            if _code(e) == "NoSuchKey":
                return None
            raise
        return response["Body"].read()


def _code(e: ClientError) -> str:
    """The S3 error code of a failed request, e.g. "NoSuchKey"."""
    return str(e.response.get("Error", {}).get("Code", ""))
