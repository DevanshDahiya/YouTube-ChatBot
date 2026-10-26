FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    LLM_PROVIDER=gemini \
    DATA_DIR=/data

WORKDIR /app

# First download the dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Run as a normal user, not root. /data holds the vector store and cache
RUN useradd --create-home appuser && mkdir -p /data && chown appuser /data
USER appuser

EXPOSE 8000

CMD ["sh","-c" ,"uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

