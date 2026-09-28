# API + web estática. Compila primero el frontend (repo fmp) y copia dist/frontend/browser a ./web
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fmp ./fmp
COPY web ./web
ENV FMP_DIST=/app/web FMP_DB=/app/data/fmp.db
VOLUME ["/app/data"]
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "fmp.api:app", "--host", "0.0.0.0", "--port", "8000"]
