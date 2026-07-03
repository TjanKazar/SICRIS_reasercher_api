import httpx

AUTH_URL = "https://cris.cobiss.net/ecris/si/sl/service/getjwt"


async def get_jwt(username: str, password: str) -> str:
    payload = {
        "username": username,
        "password": password
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(AUTH_URL, json=payload, headers=headers)

        # IMPORTANT: show real error body if it fails
        if r.status_code != 200:
            raise Exception(f"JWT failed: {r.status_code} - {r.text}")

        data = r.json()

    token = data.get("jwt") or data.get("token")

    if not token:
        raise Exception(f"No token in response: {data}")

    return token