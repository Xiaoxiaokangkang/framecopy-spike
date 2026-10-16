FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRAMECOPY_DATA_DIR=/srv/framecopy/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/framecopy
COPY pyproject.toml uv.lock README.md ./
COPY framecopy ./framecopy
RUN pip install --no-cache-dir uv \
    && uv sync --frozen --extra queue --extra ocr --no-dev \
    && uv pip install paddlepaddle==3.0.0 --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/

EXPOSE 8000
CMD [".venv/bin/framecopy", "--host", "0.0.0.0", "--port", "8000"]

