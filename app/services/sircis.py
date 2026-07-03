from app.services.http_client import get

BASE_URL = "https://cris.cobiss.net/ecris/si/sl/service/biblio/researcher"


async def fetch_ids(user_number: str, token: str):
    url = f"{BASE_URL}/{user_number}"

    r = await get(url, headers={"Authorization": token})

    print("CRIS STATUS:", r.status_code)
    print("CRIS RAW:", r.text)   # 👈 ADD THIS

    r.raise_for_status()

    data = r.json()
    return [int(x) for x in data]