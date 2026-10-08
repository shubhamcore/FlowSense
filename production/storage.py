"""Encrypted object-storage and retention helpers.

Raw footage should live in a private S3/MinIO bucket. Derived trajectories may
have a longer retention period. All keys are tenant-scoped to prevent a tenant
from deleting another tenant's objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


@dataclass(frozen=True)
class RetentionPolicy:
    raw_hours: int = 24
    derived_days: int = 90
    versioned_delete: bool = True


def tenant_key(tenant_id: str, store_id: str, category: str, filename: str) -> str:
    if any(part in filename for part in ("..", "\\", "/")):
        raise ValueError("Unsafe object filename")
    if category not in {"raw", "derived", "exports"}:
        raise ValueError("Unsupported object category")
    return f"tenants/{tenant_id}/stores/{store_id}/{category}/{filename}"


def is_expired(created_at: datetime, *, retention_seconds: int, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (now - created_at).total_seconds() >= retention_seconds


def delete_expired_objects(s3_client, *, bucket: str, objects: Iterable[dict], tenant_id: str, policy: RetentionPolicy, now: datetime | None = None) -> int:
    """Delete only expired keys under one tenant prefix; return delete count."""
    now = now or datetime.now(timezone.utc)
    count = 0
    for item in objects:
        key = str(item.get("Key", ""))
        if not key.startswith(f"tenants/{tenant_id}/"):
            continue
        category = key.split("/")[4] if len(key.split("/")) > 4 else ""
        seconds = policy.raw_hours * 3600 if category == "raw" else policy.derived_days * 86400
        modified = item.get("LastModified")
        if modified and is_expired(modified, retention_seconds=seconds, now=now):
            s3_client.delete_object(Bucket=bucket, Key=key)
            count += 1
    return count
