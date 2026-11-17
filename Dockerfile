FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

# 国内网络直接走 pypi.org 常超时，构建时可以换源：
#   docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple -t signalwatch .
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" .

# 数据库与日志都落在 /data，挂卷就持久
ENV SIGNALWATCH_HOME=/data
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8766
CMD ["signalwatch", "serve", "--host", "0.0.0.0", "--port", "8766"]
