import logging
import os
import tempfile
import time
from collections import defaultdict
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.auth import create_token, get_current_user, verify_password
from src.api.db import get_connection, put_connection
from src.api.health import check_health
from src.api.middleware.log_sanitizer import install_globally
from src.api.rag import answer
from src.ingestion.main import process_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
install_globally()
logger = logging.getLogger(__name__)

RATE_LIMIT_MAX = 30
RATE_LIMIT_WINDOW = 60  # seconds
MAX_UPLOAD_FILES = 10
MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB per file

app = FastAPI(title="GroundHog RAG API", docs_url=None, redoc_url=None)

frontend_origin = os.environ.get("FRONTEND_ORIGIN")
allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
if frontend_origin:
    allowed_origins.append(frontend_origin)

# CORS — localhost + optional public frontend origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Rate limiter (in-memory, per-user) ---

_rate_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(user_id: str) -> None:
    now = time.monotonic()
    window_start = now - RATE_LIMIT_WINDOW

    hits = _rate_store[user_id]
    _rate_store[user_id] = [t for t in hits if t > window_start]

    if len(_rate_store[user_id]) >= RATE_LIMIT_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: {RATE_LIMIT_MAX} requests per minute",
        )

    _rate_store[user_id].append(now)


# --- Request models ---


from pydantic import Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=200)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=5000)
    top_k: int = Field(default=10, ge=1, le=50)


# --- Endpoints ---


@app.get("/health")
def health():
    return check_health()


@app.post("/auth/login")
def login(body: LoginRequest):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, password_hash, access_group, role FROM users WHERE username = %s",
                (body.username,),
            )
            row = cur.fetchone()
    finally:
        conn.rollback()
        put_connection(conn)

    if row is None or not verify_password(body.password, row[1]):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    user_id, _, access_group, role = str(row[0]), row[1], row[2], row[3]
    token = create_token(user_id, body.username, access_group, role)

    logger.info("User authenticated: %s", body.username)
    return {"access_token": token, "token_type": "bearer"}


@app.post("/chat")
def chat(body: ChatRequest, user: dict = Depends(get_current_user)):
    _check_rate_limit(user["user_id"])

    def event_stream():
        for token in answer(
            question=body.question,
            user_id=user["user_id"],
            access_group=user["access_group"],
            top_k=body.top_k,
        ):
            yield f"data: {token}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/documents")
def list_documents(user: dict = Depends(get_current_user)):
    _check_rate_limit(user["user_id"])

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, filename, page_count, ingested_at, access_group
                FROM documents
                WHERE (access_group = %s OR %s IS NULL)
                ORDER BY ingested_at DESC
                """,
                (user["access_group"], user["access_group"]),
            )
            rows = cur.fetchall()
    finally:
        conn.rollback()
        put_connection(conn)

    return [
        {
            "id": str(row[0]),
            "filename": row[1],
            "page_count": row[2],
            "ingested_at": row[3].isoformat() if row[3] else None,
            "access_group": row[4],
        }
        for row in rows
    ]


@app.post("/documents/upload")
def upload_documents(
    files: list[UploadFile] = File(...),
    user: dict = Depends(get_current_user),
):
    _check_rate_limit(user["user_id"])

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum allowed is {MAX_UPLOAD_FILES}",
        )

    results: list[dict] = []

    for upload in files:
        filename = upload.filename or "uploaded.pdf"
        if not filename.lower().endswith(".pdf"):
            results.append(
                {
                    "filename": filename,
                    "status": "failed",
                    "detail": "Only PDF files are supported",
                }
            )
            continue

        try:
            file_bytes = upload.file.read()
        finally:
            upload.file.close()

        if len(file_bytes) > MAX_UPLOAD_SIZE_BYTES:
            results.append(
                {
                    "filename": filename,
                    "status": "failed",
                    "detail": f"File too large. Max size is {MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)} MB",
                }
            )
            continue

        safe_name = Path(filename).name
        suffix = Path(safe_name).suffix or ".pdf"
        temp_path: str | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                suffix=suffix,
                prefix="upload_",
                dir=tempfile.gettempdir(),
            ) as tmp:
                tmp.write(file_bytes)
                temp_path = tmp.name

            ingest_result = process_file(
                Path(temp_path),
                dry_run=False,
                reindex=False,
                access_group=user["access_group"] or "default",
            )

            results.append(
                {
                    "filename": filename,
                    "status": ingest_result["status"],
                    "pages": ingest_result.get("pages", 0),
                    "chunks": ingest_result.get("chunks", 0),
                }
            )
        except Exception as exc:
            logger.error("Upload ingestion failed for %s", filename, exc_info=True)
            results.append(
                {
                    "filename": filename,
                    "status": "failed",
                    "detail": str(exc),
                }
            )
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    return {"results": results}


@app.get("/sources/{chunk_id}")
def get_source(chunk_id: str, user: dict = Depends(get_current_user)):
    _check_rate_limit(user["user_id"])

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.id, c.chunk_index, c.page_number, c.created_at,
                    d.id, d.filename, d.page_count, d.ingested_at, d.access_group
                FROM chunks c
                JOIN documents d ON d.id = c.document_id
                WHERE c.id = %s::uuid
                """,
                (chunk_id,),
            )
            row = cur.fetchone()
    finally:
        conn.rollback()
        put_connection(conn)

    if row is None:
        raise HTTPException(status_code=404, detail="Chunk not found")

    # RBAC: check access group
    doc_access_group = row[8]
    if user["access_group"] is not None and doc_access_group != user["access_group"]:
        raise HTTPException(status_code=403, detail="Access denied")

    return {
        "chunk": {
            "id": str(row[0]),
            "chunk_index": row[1],
            "page_number": row[2],
            "created_at": row[3].isoformat() if row[3] else None,
        },
        "document": {
            "id": str(row[4]),
            "filename": row[5],
            "page_count": row[6],
            "ingested_at": row[7].isoformat() if row[7] else None,
            "access_group": row[8],
        },
    }
