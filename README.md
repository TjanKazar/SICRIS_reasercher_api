# SICRIS / COBISS API

FastAPI servis za pridobivanje bibliografij raziskovalcev iz SICRIS-a, branje podrobnosti zapisov iz COBISS-a in osnovne poizvedbe nad deli.

Primeri raziskovalcev:

- `35512`
- `27561`
- `39791`

## Zagon

```bash
uv run uvicorn app.main:app --reload
```

Privzeti URL:

```text
http://localhost:8000
```

OpenAPI dokumentacija:

```text
http://localhost:8000/docs
```

## Avtentikacija

Najprej pridobi token:

```http
POST /auth/token
```

```json
{
  "username": "sicris-uporabnik",
  "password": "sicris-geslo"
}
```

Odgovor:

```json
{
  "token_type": "bearer",
  "access_token": "..."
}
```

Pri ostalih zahtevkih uporabi:

```http
Authorization: Bearer <access_token>
```

Način, kjer se `username` in `password` posljeta v telesu vsake zahteve deluje, vendar je Bearer token priporocen.

## Endpointi

```http
POST /records/{user_number}
POST /records/{user_number}?limit=20
POST /records/{user_number}/refresh
POST /records/{user_number}/cache
POST /records/{user_number}/latest?limit=20
POST /records/{user_number}/year/{year}
POST /records/{user_number}/years
POST /records/{user_number}/coauthored
POST /records/{user_number}/solo
POST /records/{user_number}/doi
POST /records/{user_number}/types
```

Kratek pomen:

- `records`: vsa dela raziskovalca.
- `limit`: vrne samo prvih N zapisov.
- `refresh`: prisilno osvezi podatke iz SICRIS/COBISS in jih shrani v MongoDB.
- `cache`: vrne podatke o MongoDB predpomnilniku.
- `latest`: najnovejsa dela po polju `Leto`.
- `year` / `years`: dela po letu oziroma povzetek po letih.
- `coauthored`: dela z dvema ali vec avtorji.
- `solo`: dela z enim ali brez navedenih avtorjev.
- `doi`: dela z DOI identifikatorjem.
- `types`: povzetek po tipu dela.

DOI pomeni Digital Object Identifier. To je stalni identifikator publikacije, pogosto uporabljen pri znanstvenih clankih.

Poizvedbe cez vec avtorjev:

```http
POST /authors/unique?user_numbers=35512&user_numbers=27561&user_numbers=39791
POST /authors/collaborations?user_numbers=35512&user_numbers=27561&user_numbers=39791
POST /authors/common?user_numbers=35512&user_numbers=27561
```

- `unique`: vsa unikatna dela vec raziskovalcev.
- `collaborations`: skupna/soavtorska dela.
- `common`: dela, ki se pojavijo pri vseh podanih raziskovalcih.

## Postman

Datoteko `postman_collection.json` lahko uvozis v Postman.

Nastavi spremenljivke:

- `base_url`: npr. `http://localhost:8000`
- `username`: SICRIS uporabnisko ime
- `password`: SICRIS geslo
- `author_id`: privzeto `35512`
- `author_id_2`: privzeto `27561`
- `author_id_3`: privzeto `39791`

Najprej zazeni `Auth / Get token`. Postman sam shrani `access_token` v spremenljivko `token`.

## MongoDB cache

Za predpomnjenje nastavi:

```bash
MONGODB_URI="mongodb+srv://<username>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority"
MONGODB_DB="sicris"
MONGODB_COLLECTION="author_queries"
```

Ce `MONGODB_URI` ni nastavljen, API vseeno deluje, samo cache se preskoci. Pri poizvedbah se najprej preveri MongoDB; ob cache miss se podatki pridobijo iz SICRIS/COBISS in shranijo v MongoDB.

## Docker

```bash
docker compose up --build
```
