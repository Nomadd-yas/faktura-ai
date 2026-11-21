#!/usr/bin/env bash
# GLM-OCR в vLLM для ML-сервиса (OCR). Параметры - как на рабочем сервере на 21.11.2026:
# Q8 (fp8_per_tensor), контекст 16k, спекулятивное декодирование MTP 1 токен.
# Образ - по digest (тот же, что работал); веса модели качаются в ~/.cache/huggingface при первом запуске.
#
#   bash backend/deploy/vllm-run.sh            # на сервере
#
# ML-контейнер ходит к vLLM через host.docker.internal:8123, поэтому порт публикуется на хосте.
# Если OCR не должен быть доступен из интернета, закройте 8123 снаружи файрволом
# (например: ufw deny 8123/tcp, затем ufw allow from 172.16.0.0/12 to any port 8123 - сеть docker).
set -euo pipefail
NAME="${VLLM_NAME:-glm-ocr}"
IMAGE="${VLLM_IMAGE:-vllm/vllm-openai@sha256:20fdb089454afbb324efa4110a2c025ff7b27cc1a086e07aa580b18df8ad1ce1}"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --gpus all --restart unless-stopped \
  -p 8123:8000 \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -e VLLM_USE_FLASHINFER_SAMPLER=0 \
  "$IMAGE" \
  serve zai-org/GLM-OCR --host 0.0.0.0 --port 8000 \
  --max-model-len 16384 \
  --speculative-config.method mtp --speculative-config.num_speculative_tokens 1 \
  --quantization fp8_per_tensor
echo "vLLM запущен: $NAME. Проверка: curl -s localhost:8123/v1/models"
