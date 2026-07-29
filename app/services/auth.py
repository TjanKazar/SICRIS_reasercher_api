"""Authentication for the SICRIS researcher lookup service."""

import httpx

AUTH_URL = "https://cris.cobiss.net/ecris/si/sl/service/getjwt"


async def get_jwt(username: str, password: str) -> str:
    """Get the SICRIS JWT used only to resolve a researcher's COBISS record IDs."""
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            AUTH_URL,
            json={"username": username, "password": password},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    if response.status_code == 401:
        raise RuntimeError("SICRIS authentication failed: check sicris.username and sicris.password")
    response.raise_for_status()
    payload = response.json()
    token = payload.get("jwt") or payload.get("token")
    if not token:
        raise RuntimeError("SICRIS authentication response did not contain a JWT")
    return str(token)
