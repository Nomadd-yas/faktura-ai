FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/hf-cache

# libgomp1 нужен torch, libglib2.0-0 — opencv
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# [faktura-patch] torch — CPU-сборка (layout на CPU, GPU занята vLLM): без CUDA-библиотек образ легче на гигабайты
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# Layout-модель PP-DocLayoutV3 запекаем в образ (~1–2 ГБ при сборке).
# Если у сборщика нет доступа к huggingface.co:
#   docker build --build-arg PRELOAD_LAYOUT_MODEL=0 -t pdf-dataset .
# и смонтируйте HF-кэш при запуске: -v hf-cache:/hf-cache
ARG PRELOAD_LAYOUT_MODEL=1
RUN if [ "$PRELOAD_LAYOUT_MODEL" = "1" ]; then \
      python -c "from huggingface_hub import snapshot_download; snapshot_download('PaddlePaddle/PP-DocLayoutV3_safetensors')"; \
    fi

COPY pipeline.py extract_dataset.py strip_tail.py figures.py ask.py ocr_service.py glmocr.config.yaml api.py entrypoint.sh ./
RUN chmod +x entrypoint.sh

# /out — результаты: <stem>.dataset.json, <stem>.meta.json, <stem>.md, work/, uploads/
VOLUME /out
EXPOSE 8080

# По умолчанию ждёт аргументы pipeline.py (pdf-dataset /data/D001.pdf);
# `pdf-dataset serve` поднимает HTTP API на :8080 (см. README).
ENTRYPOINT ["/app/entrypoint.sh"]
