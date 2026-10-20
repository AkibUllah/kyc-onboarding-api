FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml requirements.txt README.md ./
COPY kyc ./kyc
RUN pip install -r requirements.txt && pip install --no-deps .

RUN addgroup --system app && adduser --system --ingroup app app \
    && mkdir -p /data/storage && chown -R app:app /data /app
USER app

ENV STORAGE_LOCAL_DIR=/data/storage
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

CMD ["uvicorn", "kyc.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
