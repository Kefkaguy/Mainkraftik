FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=1
WORKDIR /worker
COPY workers/sentiment/requirements.lock.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home scorer
COPY workers/sentiment/app.py ./
USER scorer
CMD ["uvicorn","app:app","--host","0.0.0.0","--port","8300","--workers","1","--no-proxy-headers"]
