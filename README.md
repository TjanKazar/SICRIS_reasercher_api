# SICRIS / COBISS API

FastAPI service for retrieving researcher bibliographies from SICRIS and exporting each COBISS record through the official COBISS REST API. It does not scrape COBISS HTML pages.

## Configuration

Credentials are supplied only to `POST /auth/tokens`; they are not read from `.env` and are not stored by the service. The root `.env` remains available for optional MongoDB cache settings and is ignored by Git and excluded from Docker build context.

## Run

```bash
uv sync
uv run python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Or with Docker:

```bash
docker compose up --build
```

OpenAPI documentation is available at `http://localhost:8000/docs`.

## API behaviour

First obtain both API tokens with provider credentials:

```http
POST /auth/tokens
Content-Type: application/json

{
  "sicris": {
    "username": "sicris-username",
    "password": "sicris-password"
  },
  "cobiss": {
    "username": "cobiss-username",
    "password": "cobiss-password"
  }
}
```

The response contains `sicris_authorization` (the exact `Bearer ...` value returned by SICRIS) and `cobiss_session_id`. All data requests must provide them as:

```http
Authorization: <sicris_authorization>
CSESSIONID: <cobiss_session_id>
```

The service resolves the researcher's COBISS IDs through SICRIS, then calls:

```text
POST https://ws.cobiss.net/cobiss-rest/auth
GET  https://ws.cobiss.net/cobiss-rest/ris/{cobiss-id}?database=si
```

Each result includes convenient fields (`id`, `title`, `authors`, `podrobni_podatki`) and the unmodified `ris` export object returned by COBISS.

## MongoDB cache

Add the following to the root `.env` file to enable the cache:

```dotenv
MONGODB_URI="mongodb+srv://<username>:<password>@<cluster-host>/?retryWrites=true&w=majority"
MONGODB_DB="sicris"
MONGODB_COLLECTION="author_queries"
```

For a local MongoDB server, use `MONGODB_URI="mongodb://localhost:27017"` instead. If the username or password contains reserved URL characters such as `@`, `:`, `/`, or `#`, URL-encode it before inserting it into the connection string.

For a deployed instance, you can set the connection at runtime instead. First obtain API tokens, then call:

```http
POST /config/mongodb
Authorization: <sicris_authorization>
CSESSIONID: <cobiss_session_id>
Content-Type: application/json

{
  "mongodb_uri": "mongodb+srv://<username>:<password>@<cluster-host>/?retryWrites=true&w=majority",
  "database": "sicris",
  "collection": "author_queries"
}
```

The endpoint validates the connection, creates the cache index, and never returns the URI. This setting is kept only in the running process: configure it again after a restart or set `MONGODB_URI` in the deployment's environment settings for persistence.

When `MONGODB_URI` is set, a complete researcher bibliography is saved after it is queried. Cached results are served only for seven days after `updated_at`. On the next request after that period, the service fetches fresh SICRIS/COBISS data and overwrites the stored document; stale records are never returned. `POST /records/{user_number}/cache` reports whether the stored entry is fresh or stale.

## Endpoints

```text
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
POST /records/{user_number}/search?q=machine
POST /records/{user_number}/types

POST /authors/unique?user_numbers=35512&user_numbers=27561
POST /authors/collaborations?user_numbers=35512&user_numbers=27561
POST /authors/common?user_numbers=35512&user_numbers=27561
POST /config/mongodb
```

The COBISS session lasts 15 minutes. Request new tokens when it expires.

Import `postman_collection.json`, set its `sicris_username`, `sicris_password`, `cobiss_username`, and `cobiss_password` variables, then run `Auth / Get API tokens`. Its test script saves both tokens, and its collection-level pre-request script forwards the SICRIS `Authorization` value unchanged and adds the COBISS `CSESSIONID` header to the remaining requests.
