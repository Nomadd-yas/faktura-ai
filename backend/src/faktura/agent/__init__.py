"""Агентный чат разбора (CORE_API §4j, §15.9 / §12.2-12.5 спеки фронта).

Цикл агента живёт в бэкенде (`loop.py`), вызовы модели идут через ML (`llm.py` -> POST /llm/chat), инструменты
только читают данные этого разбора (`tools.py`), каждое число проверяется до отправки (`verify.py`).
Базовый вариант (`faktura.ask`) - запасной путь. Документ: docs/design/AGENT.md."""
from __future__ import annotations

MAX_TOOL_CALLS = 12
MAX_ROWS = 200
MAX_HISTORY_QA = 6
HISTORY_CHARS = 600
TOTAL_TIMEOUT_S = 40.0
MAX_TOKENS = 1200
