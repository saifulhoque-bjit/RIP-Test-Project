"""Shared aioboto3 session factory.

All AWS clients in this codebase (S3, SQS) must obtain their ``aioboto3.Session``
from ``get_aws_session()`` rather than constructing one locally.  This ensures:

- A single set of credential-resolution calls at startup.
- One shared connection pool per process.
- Easy swap to IAM role / instance-profile credentials in production
  (leave AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY empty).

Usage::

    from app.clients.aws_session import get_aws_session

    async with get_aws_session().client("s3") as s3:
        await s3.put_object(...)
"""

from __future__ import annotations

import aioboto3

from app.core.config import settings

_session: aioboto3.Session | None = None


def get_aws_session() -> aioboto3.Session:
    """Return the shared aioboto3 Session (created once per process)."""
    global _session
    if _session is None:
        _session = aioboto3.Session(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
            region_name=settings.AWS_REGION,
        )
    return _session
