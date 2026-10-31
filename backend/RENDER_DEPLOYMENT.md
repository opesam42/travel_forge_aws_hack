# Deploy the backend to Render

The repository includes a root-level [`render.yaml`](../render.yaml) Blueprint
for the FastAPI backend. It runs the service from `backend/`, uses Python
3.12, and mounts a 1 GB persistent disk at `/var/data` for the MiniLM model
cache.

## Before deployment

You need:

- A PostgreSQL database with the `pgvector` extension available. The Blueprint
  expects you to provide its connection URL; it does not create a new database.
- At least one supported LLM API key. The Blueprint asks for Google AI and
  Groq keys; configure at least one. Google AI is the configured primary and
  Groq is the fallback.
- The public frontend origin, such as `https://your-frontend.example.com`.

Keep database credentials and API keys in Render's environment settings.
Do not commit them to this repository.

The shared chat-model factory supports `google_genai` and `groq` targeting
Google's Gemma 4 (`gemma-4-26b-a4b-it`). Set `LLM_PROVIDER` to override
`PRIMARY_LLM_PROVIDER`, and optionally set `LLM_MODEL` or `LLM_FALLBACK_PROVIDER`.
Provider-specific model defaults and API keys are documented in `.env.example`.

## Create the Render service

1. Push the repository to GitHub.
2. In Render, choose **New + → Blueprint**, then select this repository.
   Render finds `render.yaml` at the repository root.
3. During setup, provide values for `CORS_ORIGIN`, `DATABASE_URL`,
   `GOOGLE_AI_API_KEY`, and `GROQ_API_KEY`. Set `CORS_ORIGIN` to the frontend
   origin only (scheme + host + optional port; no path or trailing slash).
   Leave the key for any unused provider empty if Render permits, but make
   sure at least one configured provider key is valid.
4. Apply the Blueprint and wait for the first deployment to finish. The
   health check uses `/api/health`.
5. Confirm that the service is healthy at
   `https://<your-render-service>.onrender.com/api/health`.
6. Set the frontend's API base URL to `https://<your-render-service>.onrender.com`
   (do not append `/api` if the frontend client adds that prefix), then deploy
   the frontend.

The service is configured with `ENVIRONMENT=production`,
`AUTH_COOKIE_SECURE=true`, and `AUTH_COOKIE_SAMESITE=none`. The browser
frontend must send requests with credentials (`withCredentials: true` for
Axios or `credentials: "include"` for Fetch). Cross-site browser cookie
policies may still block cookies when the frontend and API are on different
sites; deploying them under the same site or proxying API calls through the
frontend origin is more reliable. Keep `CORS_ORIGIN` aligned with the
frontend's exact origin.

## Persistent MiniLM cache

The Blueprint mounts the persistent disk at `/var/data` and configures
`RAG_EMBEDDING_CACHE_DIR=/var/data/all-MiniLM-L6-v2`. On the first chat or
other embedding request, the backend downloads and extracts MiniLM into that
directory. Later service restarts can reuse those files instead of
downloading them again.

Persistent disks are available on paid Render services. A disk is attached
to one service instance and prevents scaling that service to multiple
instances. The included `starter` plan is a low-cost starting point; if the
service runs out of memory while loading MiniLM, increase its instance plan.
The disk size can be increased later, but not decreased.

## Database initialization and existing data

At startup, the backend enables pgvector, creates missing application tables,
and seeds trusted sources. This does not migrate legacy Qdrant vectors or
import legacy rules. If those datasets are needed, follow the migration
instructions in the root README using the same PostgreSQL database before
relying on chat/audit results.

The Render start command runs `alembic upgrade head` before starting FastAPI.
This applies incremental schema migrations to existing databases; on a fresh
database, the initial case-chat migration safely has no existing tables to
alter, and application startup creates the current schema. For local
development, apply migrations from `backend/` with `alembic upgrade head`.
`SQLModel.metadata.create_all()` remains responsible for creating tables on a
fresh database; it does not replace migrations for altering existing tables.

Render persistent storage is only for the model cache in this setup. Accounts,
sessions, chat history, applications, and vectors remain in PostgreSQL.
