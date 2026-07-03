from datetime import datetime, timezone
import os
from urllib.parse import urlparse

from motor.motor_asyncio import AsyncIOMotorClient

from app.services.auth import get_jwt
from app.services.cobiss import scrape_cobiss_many
from app.services.sircis import fetch_author_basic_data, fetch_ids

_client: AsyncIOMotorClient | None = None
_index_ready = False


def _mongodb_uri() -> str | None:
    return os.getenv("MONGODB_URI")


def _mongodb_db_name() -> str:
    return os.getenv("MONGODB_DB", "sicris")


def _mongodb_collection_name() -> str:
    return os.getenv("MONGODB_COLLECTION", "author_queries")


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


async def get_cached_query(user_number: str):
    if not _mongodb_uri():
        print(f"[mongo] cache lookup skipped for {user_number}: config missing")
        return None

    print(f"[mongo] cache lookup start for {user_number} ({_mongodb_uri_summary()})")
    collection = await _get_collection()
    cached = await collection.find_one({"user_number": user_number, "complete": True}, {"_id": False})
    print(f"[mongo] cache lookup {'hit' if cached else 'miss'} for {user_number}")
    return cached


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


async def refresh_and_store_query(user_number: str, username: str, password: str):
    print(f"[mongo] background refresh start for {user_number}")
    token = await get_jwt(username, password)
    author = await fetch_author_basic_data(user_number, token)
    ids = await fetch_ids(user_number, token)
    results, errors = await scrape_cobiss_many(ids, token)

    if ids and not results:
        raise Exception(f"Failed to scrape all COBISS records. Sample error: {errors[0] if errors else 'unknown'}")

    await save_query_document(
        {
            "user_number": user_number,
            "author": author,
            "results": results,
            "total_count": len(results),
            "complete": not errors,
        }
    )
    print(f"[mongo] background refresh done for {user_number}")
