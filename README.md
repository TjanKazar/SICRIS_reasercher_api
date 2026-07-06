## MongoDB Atlas cache

Set these environment variables to enable cloud Mongo caching:

```bash
MONGODB_URI="mongodb+srv://<username>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority"
MONGODB_DB="sicris"
MONGODB_COLLECTION="author_queries"
```

`MONGODB_URI` is the Atlas connection string. Put it in your deployment environment, or export it before starting the app locally.

## Docker

Build and run:

```bash
docker compose up --build
```

The compose file loads `app/.env`, so put your Mongo credentials there when running locally in Docker.