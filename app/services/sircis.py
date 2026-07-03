from app.services.http_client import get

BASE_URL = "https://cris.cobiss.net/ecris/si/sl/service/biblio/researcher"
SEARCH_URL = "https://cris.cobiss.net/ecris/si/sl/service/researcher/search"


async def fetch_ids(user_number: str, token: str):
    url = f"{BASE_URL}/{user_number}"

    r = await get(url, headers={"Authorization": token})

    r.raise_for_status()

    data = r.json()
    return [int(x) for x in data]


async def fetch_author_basic_data(user_number: str, token: str):
    r = await get(
        SEARCH_URL,
        params={"query": user_number},
        headers={"Authorization": token},
    )
    r.raise_for_status()

    data = r.json()
    if not isinstance(data, list) or not data:
        raise Exception(f"Researcher search returned no results for user number {user_number}")

    researcher = data[0]
    classifications = researcher.get("classificationDescr") or []
    classification = classifications[0] if isinstance(classifications, list) and classifications else None

    return {
        "classification": classification,
        "first_name": researcher.get("firstName"),
        "last_name": researcher.get("lastName"),
        "title": researcher.get("title"),
        "type_description": researcher.get("typeDescription"),
    }