FROM python:3.11-slim

WORKDIR /app
RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    psycopg2-binary \
    "python-jose[cryptography]" \
    "passlib[bcrypt]" \
    "bcrypt<4.1" \
    python-dotenv \
    httpx \
    python-multipart \
    pymupdf \
    pdfplumber \
    langchain-text-splitters \
    tqdm

COPY src/__init__.py src/__init__.py
COPY src/api/ src/api/
COPY src/ingestion/ src/ingestion/
COPY scripts/ scripts/

EXPOSE 8000

ENTRYPOINT ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
