# GroundHog RAG Tutorial (Beginner Friendly)

## 1) What this project is

GroundHog RAG is a private, self-hosted **RAG (Retrieval-Augmented Generation)** app:

- You upload PDFs
- The app chunks and embeds them
- At question time it retrieves relevant chunks
- The LLM answers using retrieved context + shows sources

Core stack in this repo:

- **Frontend:** Streamlit (`src/frontend/app.py`)
- **API:** FastAPI (`src/api/main.py`)
- **Database:** Postgres + pgvector (`config/init.sql`)
- **Embedding + LLM:** Ollama (`nomic-embed-text`, `llama3.2:3b`)
- **Ingestion pipeline:** `src/ingestion/*`
- **Infra/runtime:** Docker Compose (`docker-compose.yml`)

---

## 2) High-level architecture

Flow:

1. User logs in from Streamlit UI
2. UI sends question to FastAPI `/chat`
3. API:
   - embeds question
   - retrieves top-k relevant chunks from pgvector
   - builds prompt
   - streams answer tokens back
4. UI renders response + source citations

For document onboarding:

1. User uploads PDF from UI sidebar
2. API endpoint `/documents/upload` receives files
3. API invokes ingestion pipeline (`process_file`)
4. Pipeline extracts text -> chunks -> embeddings -> stores in DB

---

## 3) Repo tour (important files)

### Runtime / infra

- `docker-compose.yml`  
  Defines `postgres`, `api`, `frontend`, `ingestion` services and network wiring.

- `docker/api/Dockerfile`  
  Builds API image, installs FastAPI + upload/ingestion deps.

- `docker/frontend/Dockerfile`  
  Builds Streamlit image.

- `docker/ingestion/Dockerfile`  
  Builds ingestion image for batch ingestion runs.

### API layer

- `src/api/main.py`
  - Auth login (`/auth/login`)
  - Chat streaming endpoint (`/chat`)
  - List docs (`/documents`)
  - Upload docs (`/documents/upload`)
  - Source lookup (`/sources/{chunk_id}`)

- `src/api/rag.py`  
  Orchestrates retrieval + prompt + generation + audit logging.

- `src/api/retriever.py`  
  Vector similarity retrieval with RBAC/access-group filter.

- `src/api/embedder.py`  
  Calls Ollama embed model for query embeddings.

- `src/api/llm_client.py`  
  Calls Ollama generation model and streams tokens.

- `src/api/auth.py`  
  JWT, password hashing, current-user dependency.

- `src/api/db.py`  
  Postgres connection pool + query helpers.

### Ingestion pipeline

- `src/ingestion/main.py`  
  Main ingestion logic, includes `process_file(...)`.

- `src/ingestion/extractor.py`  
  PDF text extraction.

- `src/ingestion/chunker.py`  
  Splits extracted text into chunks.

- `src/ingestion/embedder.py`  
  Embeds chunk text via Ollama.

- `src/ingestion/loader.py`  
  Inserts documents/chunks into Postgres + pgvector.

### Data schema

- `config/init.sql`
  - `users`
  - `documents`
  - `chunks` (vector column, HNSW index)
  - `audit_log`

---

## 4) Data model basics

### `documents`
Stores each uploaded/ingested file:

- `id`
- `filename`
- `file_hash` (for dedupe)
- `page_count`
- `ingested_at`
- `access_group`

### `chunks`
Stores chunk-level records:

- `document_id`
- `chunk_text`
- `chunk_index`
- `page_number`
- `embedding vector(768)`

### `users`
Auth and RBAC:

- `username`
- `password_hash`
- `access_group`
- `role`

### `audit_log`
Tracks query activity:

- user query
- chunk ids used
- response text snippet

---

## 5) End-to-end request lifecycle

## A) Login
- Streamlit calls `/auth/login`
- API validates credentials from `users`
- JWT returned and stored in frontend session

## B) Ask question
- Streamlit sends `/chat` with question
- API validates JWT, rate limits user
- Retriever fetches top chunks by vector similarity and access group
- Prompt builder combines chunks + user question
- LLM generates streamed response
- API appends citation block
- UI parses and renders answer + sources

## C) Upload file
- Streamlit sends multipart files to `/documents/upload`
- API validates:
  - PDF extension
  - max file count
  - max size
- API writes temp file and calls ingestion `process_file(...)`
- Ingestion inserts document/chunks/embeddings
- UI refreshes sidebar document list

---

## 6) Why RAG works here (conceptually)

Without RAG:
- LLM answers from general training memory only.

With RAG:
- LLM gets relevant chunks from your own docs as context.
- This improves factual grounding for your specific PDFs.
- Citations increase trust and inspectability.

---

## 7) How to run locally (quick path)

1. Create `.env` from `.env.template`
2. Ensure Ollama running and models pulled:
   - `nomic-embed-text`
   - `llama3.2:3b`
3. Start services:
   - `docker compose up -d --build`
4. Open frontend (`:3000`)
5. Login with created user
6. Upload PDFs and query

---

## 8) EC2 deployment essentials (what tripped people most)

1. **Ollama host reachability from containers**
   - On Linux, `host.docker.internal` may not always work.
   - Use whichever endpoint is reachable from API container (`172.17.0.1` often works).

2. **CORS**
   - API must allow your public frontend origin.
   - Configure via `FRONTEND_ORIGIN`.

3. **Port bindings + Security Groups**
   - Expose `3000` (and optionally `8000`) correctly.
   - Open SG inbound rules accordingly.
   - Keep `5432` private.

4. **EntryPoint vs command duplication**
   - If Dockerfile sets `ENTRYPOINT`, avoid conflicting `command` in compose.

---

## 9) Security model in this repo

- JWT-based auth
- Passwords hashed (bcrypt/passlib)
- Access group filtering at retrieval time
- Basic rate limiting
- Audit logging
- Intended private/self-host deployment

Recommended next hardening steps:

- HTTPS reverse proxy (Nginx/Caddy)
- Restrict CORS precisely
- Limit upload size/count by env
- Private API behind reverse proxy
- Backups for DB + uploaded docs

---

## 10) How to extend this app (beginner roadmap)

### Phase 1: Better UX
- upload progress
- ingestion status (`queued/processing/indexed/failed`)
- retry failed ingestion

### Phase 2: Better retrieval quality
- tune chunk size/overlap
- hybrid retrieval (vector + keyword)
- reranking model
- per-doc metadata filters

### Phase 3: Better operations
- structured metrics/logging
- background worker queue (Celery/RQ)
- async ingestion pipeline
- CI/CD (Bitbucket pipelines to EC2)

### Phase 4: Better model strategy
- bigger local model or hosted LLM fallback
- prompt templates per use case
- eval harness for answer quality

---

## 11) Common debugging checklist

### API unhealthy
- check `docker compose logs api`
- verify env vars (`OLLAMA_HOST`, DB vars)
- verify no entrypoint/command conflict

### Ollama connection errors
- host tags endpoint works?
- reachable from inside `api` container?
- correct host/IP in `.env`

### No documents in sidebar
- ingestion failed or skipped
- access-group mismatch
- DB empty

### Bad answers
- retrieval not finding relevant chunks
- poor PDF extraction
- chunking too large/small
- model too small for task complexity

---

## 12) Mental model to keep

This app has two independent pipelines:

1. **Indexing time pipeline (offline-ish):** PDF -> chunks -> embeddings -> DB  
2. **Query time pipeline (online):** question -> retrieve chunks -> LLM answer

If query quality is bad, diagnose indexing first, then retrieval, then prompting/model.

---

## 13) Suggested learning exercises

1. Upload one short PDF and ask direct factual questions.
2. Add logs around retrieved chunks and inspect why certain chunks were chosen.
3. Change chunk size in chunker and compare answer quality.
4. Add one metadata filter (e.g., only one document) and test retrieval behavior.
5. Swap LLM model and compare latency + quality.

---

## 14) Quick glossary

- **RAG:** Retrieval-Augmented Generation
- **Embedding:** numeric vector representation of text meaning
- **Vector DB search:** nearest-neighbor search in embedding space
- **Chunking:** splitting long docs into smaller retrievable text units
- **Top-k:** number of retrieved chunks fed to model
- **RBAC:** role/access-based controls (here via access groups)

---

## 15) Final tip

As you build RAG apps, optimize in this order:

1. ingestion correctness
2. retrieval quality
3. prompt design
4. model size
5. UX polish

Most failures are in 1-2, not the LLM itself.