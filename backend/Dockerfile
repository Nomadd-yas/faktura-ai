FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.16 /uv /usr/local/bin/uv
ENV PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_NO_CACHE=1 UV_SYSTEM_PYTHON=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv pip install --system ".[pg,s3,img]"
RUN useradd --create-home --uid 1000 app && mkdir -p /app/var && chown app /app/var
USER app
EXPOSE 8000
CMD ["faktura", "serve", "--host", "0.0.0.0", "--port", "8000"]
