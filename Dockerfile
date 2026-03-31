FROM python:3.11-slim

WORKDIR /app

COPY server/requirements-server.txt requirements-server.txt
RUN pip install --no-cache-dir -r requirements-server.txt

COPY server/ server/
COPY teamdb/ teamdb/

EXPOSE 8080

CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8080"]
