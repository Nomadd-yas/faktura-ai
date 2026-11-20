FROM python:3.12-slim
ENV PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[pg,s3,img]"
EXPOSE 8000
CMD ["faktura", "serve", "--host", "0.0.0.0", "--port", "8000"]
