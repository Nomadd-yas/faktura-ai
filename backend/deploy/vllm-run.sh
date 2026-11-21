#!/usr/bin/env bash
# GLM-OCR в vLLM для ML-сервиса (OCR).
#
#   bash backend/deploy/vllm-run.sh                                   # FP16 (полная модель), порт только для docker
#   VLLM_QUANT=fp8_per_tensor bash backend/deploy/vllm-run.sh         # Q8, как на старом сервере hima-gpu
#
# Переменные: VLLM_NAME (glm-ocr), VLLM_IMAGE (по digest - проверенная версия), VLLM_QUANT ("" = FP16),
# VLLM_MAX_LEN (16384), VLLM_SPEC_TOKENS (1, MTP; 0 = выключить), VLLM_GPU_UTIL (0.8: на vGPU L40S-12Q свободно ~10 из 11.8 ГБ, 0.9 не стартует),
# VLLM_BIND (172.17.0.1 - адрес docker0: ML-контейнер ходит через host.docker.internal:8123, из интернета порт
# закрыт; 0.0.0.0 - открыть всем, только за файрволом или прокси).
# ENTRYPOINT образа уже "vllm serve": первым аргументом идёт модель.
# Веса модели качаются в ~/.cache/huggingface при первом запуске.
set -euo pipefail
NAME="${VLLM_NAME:-glm-ocr}"
IMAGE="${VLLM_IMAGE:-vllm/vllm-openai@sha256:20fdb089454afbb324efa4110a2c025ff7b27cc1a086e07aa580b18df8ad1ce1}"
QUANT="${VLLM_QUANT-}"
MAX_LEN="${VLLM_MAX_LEN:-16384}"
SPEC="${VLLM_SPEC_TOKENS:-1}"
UTIL="${VLLM_GPU_UTIL:-0.8}"
BIND="${VLLM_BIND:-172.17.0.1}"
args=(zai-org/GLM-OCR --host 0.0.0.0 --port 8000 --max-model-len "$MAX_LEN" --gpu-memory-utilization "$UTIL")
if [[ "$SPEC" != "0" ]]; then
  args+=(--speculative-config.method mtp --speculative-config.num_speculative_tokens "$SPEC")
fi
if [[ -n "$QUANT" ]]; then
  args+=(--quantization "$QUANT")
fi
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --gpus all --restart unless-stopped \
  -p "$BIND:8123:8000" \
  -v "$HOME/.cache/huggingface:/root/.cache/huggingface" \
  -e VLLM_USE_FLASHINFER_SAMPLER=0 \
  "$IMAGE" "${args[@]}"
echo "vLLM: $NAME (quant=${QUANT:-fp16}, max_len=$MAX_LEN, mtp=$SPEC). Проверка: curl -s $BIND:8123/v1/models"
