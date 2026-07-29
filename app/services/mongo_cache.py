from datetime import datetime, timedelta, timezone
import os
from urllib.parse import urlparse

from motor.motor_asyncio import AsyncIOMotorClient

_client: AsyncIOMotorClient | None = None
_index_ready = False
CACHE_MAX_AGE = timedelta(days=7)
_runtime_config: dict[str, str] = {}


def _mongodb_uri() -> str | None:
    return _runtime_config.get("uri") or os.getenv("MONGODB_URI")


def _mongodb_db_name() -> str:
    return _runtime_config.get("database") or os.getenv("MONGODB_DB", "sicris")


def _mongodb_collection_name() -> str:
    return _runtime_config.get("collection") or os.getenv("MONGODB_COLLECTION", "author_queries")


def _cache_cutoff() -> datetime:
    return datetime.now(timezone.utc) - CACHE_MAX_AGE


def is_cache_fresh(document: dict, *, now: datetime | None = None) -> bool:
    """Return whether a completed cache document was refreshed within seven days."""
    updated_at = document.get("updated_at")
    if not document.get("complete") or not isinstance(updated_at, datetime):
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at >= (now or datetime.now(timezone.utc)) - CACHE_MAX_AGE


def _mongodb_uri_summary() -> str:
    uri = _mongodb_uri()
    if not uri:
        return "missing"

    parsed = urlparse(uri)
    host = parsed.hostname or "unknown-host"
    return f"present host={host} db={_mongodb_db_name()} collection={_mongodb_collection_name()}"


def _get_client() -> AsyncIOMotorClient:
    global _client
    uri = _mongodb_uri()
    if not uri:
        print("[mongo] MONGODB_URI is missing")
        raise RuntimeError("MONGODB_URI is not set")

    if _client is None:
        print(f"[mongo] creating client ({_mongodb_uri_summary()})")
        _client = AsyncIOMotorClient(uri)

    return _client


async def _get_collection():
    global _index_ready
    client = _get_client()
    collection = client[_mongodb_db_name()][_mongodb_collection_name()]

    if not _index_ready:
        await collection.create_index("user_number", unique=True)
        _index_ready = True

    return collection


async def configure_mongodb(uri: str, database: str = "sicris", collection: str = "author_queries") -> dict[str, str]:
    """Validate and activate MongoDB settings for the current application process."""
    global _client, _index_ready

    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        raise ValueError("mongodb_uri must start with mongodb:// or mongodb+srv://")
    if not database.strip() or not collection.strip():
        raise ValueError("database and collection must not be empty")

    candidate = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    try:
        await candidate.admin.command("ping")
    except Exception:
        candidate.close()
        raise

    previous_client = _client
    previous_index_ready = _index_ready
    previous_config = dict(_runtime_config)
    _runtime_config.update(
        {"uri": uri, "database": database.strip(), "collection": collection.strip()}
    )
    _client = candidate
    _index_ready = False
    try:
        await _get_collection()
    except Exception:
        candidate.close()
        _runtime_config.clear()
        _runtime_config.update(previous_config)
        _client = previous_client
        _index_ready = previous_index_ready
        raise

    if previous_client is not None and previous_client is not candidate:
        previous_client.close()

    return {
        "database": _mongodb_db_name(),
        "collection": _mongodb_collection_name(),
        "connection": _mongodb_uri_summary(),
    }


async def get_cached_query(user_number: str):
    if not _mongodb_uri():
        print(f"[mongo] cache lookup skipped for {user_number}: config missing")
        return None

    print(f"[mongo] cache lookup start for {user_number} ({_mongodb_uri_summary()})")
    collection = await _get_collection()
    cached = await collection.find_one(
        {"user_number": user_number, "complete": True, "updated_at": {"$gte": _cache_cutoff()}},
        {"_id": False},
    )
    print(f"[mongo] cache lookup {'fresh hit' if cached else 'miss or stale'} for {user_number}")
    return cached


async def get_cache_metadata(user_number: str):
    if not _mongodb_uri():
        print(f"[mongo] metadata lookup skipped for {user_number}: config missing")
        return {
            "user_number": user_number,
            "cache_enabled": False,
            "cached": False,
        }

    print(f"[mongo] metadata lookup start for {user_number} ({_mongodb_uri_summary()})")
    collection = await _get_collection()
    cached = await collection.find_one(
        {"user_number": user_number},
        {
            "_id": False,
            "user_number": True,
            "author": True,
            "total_count": True,
            "complete": True,
            "created_at": True,
            "updated_at": True,
        },
    )

    if not cached:
        print(f"[mongo] metadata lookup miss for {user_number}")
        return {
            "user_number": user_number,
            "cache_enabled": True,
            "cached": False,
        }

    fresh = is_cache_fresh(cached)
    print(f"[mongo] metadata lookup {'fresh' if fresh else 'stale'} for {user_number}")
    return {
        "cache_enabled": True,
        "cached": fresh,
        "stale": bool(cached.get("complete")) and not fresh,
        "max_age_days": CACHE_MAX_AGE.days,
        **cached,
    }


async def save_query_document(document: dict):
    if not _mongodb_uri():
        print(f"[mongo] save skipped for {document.get('user_number')}: config missing")
        return

    print(f"[mongo] saving document for {document.get('user_number')} ({_mongodb_uri_summary()})")
    collection = await _get_collection()
    payload = dict(document)
    payload["updated_at"] = datetime.now(timezone.utc)
    print(f"[mongo] save payload complete={payload.get('complete')} total_count={payload.get('total_count')}")
    await collection.update_one(
        {"user_number": document["user_number"]},
        {"$set": payload, "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
        upsert=True,
    )
    print(f"[mongo] save finished for {document.get('user_number')}")
