FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# 数据库与日志都落在 /data，挂卷就持久
ENV SIGNALWATCH_HOME=/data
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8766
CMD ["signalwatch", "serve", "--host", "0.0.0.0", "--port", "8766"]
