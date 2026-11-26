FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/apps/api
WORKDIR /app
COPY apps/api/requirements.lock.txt /app/requirements.lock.txt
RUN pip install --no-cache-dir -r requirements.lock.txt && useradd --uid 10001 --create-home researcher
COPY apps/api /app/apps/api
COPY database /app/database
RUN mkdir -p /app/.local/data && chown -R researcher:researcher /app/.local
USER researcher
WORKDIR /app/apps/api
CMD ["uvicorn","intelligence.main:app","--host","0.0.0.0","--port","8100","--no-proxy-headers"]
