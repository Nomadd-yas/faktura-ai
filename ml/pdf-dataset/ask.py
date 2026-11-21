# [faktura-patch] «Вопросы по разбору»: два дешёвых вызова LLM, POST /ask/plan и POST /ask/write (api.py).
#
#   plan:  вопрос + краткое описание разбора (без строк) -> структурированный запрос (strict JSON-schema);
#   write: факты исполненного запроса (верхние строки и счётчики) + шаблонный текст -> короткий русский ответ.
#
# Исполнитель (детерминированный) живёт в бэкенде: LLM не видит весь набор и не считает числа.
# Правила проекта: провайдер ВСЕГДА закреплён (provider.order + allow_fallbacks=false — иначе запрос может уйти
# к провайдеру в разы дороже), рассуждение выключено (reasoning.enabled=false), повторов нет.
# Env: ROUTERAI_API_KEY (обяз.), ASK_MODEL | ROUTERAI_MODEL, ASK_PROVIDER | ROUTERAI_PROVIDER (по умолч. wafer),
#      ASK_PRICE_IN / ASK_PRICE_OUT — цена за 1 млн токенов, RUB (грубая оценка cost, если провайдер не прислал usage.cost).
#      usage.cost у RouterAI - в РУБЛЯХ: meta.cost + meta.currency="RUB" (раньше ошибочно cost_usd).

import argparse
import json
import os
import tempfile
import uuid
from pathlib import Path

MAX_QUESTION = 500
MAX_RUN_CHARS = 24000
MAX_FACTS_CHARS = 12000
MAX_DRAFT_CHARS = 3000
PLAN_MAX_TOKENS = 700
WRITE_MAX_TOKENS = 500

INTENTS = ["results", "best", "issues", "table_rows", "schema", "paper", "rag_list", "count", "unknown"]
RAG_LISTS = ["", "datasets", "configs", "tasks", "generators", "embeddings", "retrievers", "rerankers"]
TABLES = ["publications", "materials", "devices", "layers", "measurements", "results", "issues",
          "configurations", "datasets", "tasks", "experiments"]
MATERIAL_CLASSES = ["perovskite", "CIGS", "CdTe", "kesterite", "Sb2Se3", "thin-film Si", "other"]


class AskError(Exception):
    """Ошибка вопроса: api.py превращает её в HTTP-ответ {code, message, retryable}."""

    def __init__(self, code, message, status=502, retryable=False):
        super().__init__(message)
        self.code, self.message, self.status, self.retryable = code, message, status, retryable


def _arr():
    return {"type": "array", "items": {"type": "string"}}


def plan_schema() -> dict:
    """Строгая схема запроса; должна совпадать с backend faktura.ask.plan.plan_schema()."""
    props = {
        "intent": {"type": "string", "enum": INTENTS},
        "metrics": _arr(),
        "pids": _arr(),
        "material_class": {"type": "array", "items": {"type": "string", "enum": MATERIAL_CLASSES}},
        "layers": _arr(),
        "device": {"type": "string"},
        "aggregation": {"type": "string", "enum": ["", "champion", "mean", "median"]},
        "origin": {"type": "string", "enum": ["primary", "cited"]},
        "min_value": {"type": ["number", "null"]},
        "max_value": {"type": ["number", "null"]},
        "rag_config": {"type": "string"},
        "rag_dataset": {"type": "string"},
        "rag_task": {"type": "string"},
        "rag_list": {"type": "string", "enum": RAG_LISTS},
        "table": {"type": "string", "enum": [""] + TABLES},
        "field": {"type": "string"},
        "check": {"type": "string"},
        "sort": {"type": "string", "enum": ["best", "worst"]},
        "limit": {"type": "integer"},
        "per_paper": {"type": "boolean"},
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


WRITE_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                "additionalProperties": False}

PLAN_SYSTEM = """You turn a user's question (usually Russian) about ONE parsed set of scientific papers into a JSON query.
You get a compact description of the run (domain, papers with titles, metric names with counts, tables, check names).
You never get the data rows. Output only the JSON query; a deterministic engine runs it over the real rows.

Fields (use "" / [] / null for "not set"):
- intent: best = max/min/record/top of a metric ("лучший КПД", "наибольший Answer F1"); results = list or filter of metric
  values, "where is it written" ("КПД выше 20 %", "покажи Voc"); issues = remarks / errors / contradictions;
  table_rows = show rows of a table; schema = meaning of a table or field, format, required fields; paper = about a
  paper by code; rag_list = list of datasets / configs / tasks / models (domain rag only); count = "how many";
  unknown = the question is not about this run's data.
- metrics: metric names EXACTLY as in run.metrics (case as given). Several only if the question names several or a
  metric family (e.g. all Recall@k). If the named metric is not in run.metrics, leave [] .
- pids: paper codes from run.papers named in the question (e.g. D001). Never invent codes.
- material_class (solar): perovskite | CIGS | CdTe | kesterite | Sb2Se3 | thin-film Si | other.
- layers (solar): layer materials named in the question as written in Latin (SnO2, Spiro, NiO, PTAA, carbon, au).
- device (solar): free text matched against device label/type/architecture ("tandem", "n-i-p").
- aggregation: champion | mean | median when the question says so ("лучший прибор" = champion, "в среднем" = mean).
- origin: cited only if the question asks about values cited from other works; otherwise primary.
- min_value / max_value: numeric bounds in the metric's own unit (%, V; Voc "выше 900 мВ" -> 0.9).
- rag_config / rag_dataset / rag_task (rag): substring of a config / dataset name / task type from the question.
- rag_list (rag, intent rag_list): datasets | configs | tasks | generators | embeddings | retrievers | rerankers.
- table (schema / table_rows): entity id; field: column name, or "@required" for "which fields are required".
- check: substring of a check name (issues), e.g. "contradiction", "hysteresis".
- sort: best (default) or worst (the question asks for the lowest / worst value; direction of "better" is handled by the engine).
- limit: how many rows; 5 for best, 50 for results, 10 for table_rows.
- per_paper: true = one best row per paper (default for best); false = several rows of one paper.
Do not answer the question. Do not output anything but the JSON."""

WRITE_SYSTEM = """You write the short answer text (Russian, 1-3 sentences, at most 600 characters) to the user's question.
Input: the question, the intent, FACTS (JSON, computed from the real rows) and DRAFT (a correct template text).
Rules: use ONLY numbers, names and [n] source markers that appear in FACTS or DRAFT; keep each [n] right after the value
it belongs to; never round, convert, or invent numbers; do not mention rows that are not in FACTS; if FACTS say the
list was capped, say so. Plain text without markdown. If DRAFT is already good, return it with light edits."""


def provider() -> str:
    """Закреплённый провайдер: ASK_PROVIDER | ROUTERAI_PROVIDER | wafer. Пустой список недопустим (запрос ушёл бы незакреплённым)."""
    prov = os.environ.get("ASK_PROVIDER") or os.environ.get("ROUTERAI_PROVIDER") or "wafer"
    if not [p for p in prov.split(",") if p.strip()]:
        raise AskError("not_configured", "Провайдер не закреплён: задайте ASK_PROVIDER", status=503)
    return prov


def model() -> str:
    import extract_dataset as ed
    return os.environ.get("ASK_MODEL") or os.environ.get("ROUTERAI_MODEL") or ed.DEFAULT_MODEL


def make_client():
    key = os.environ.get("ROUTERAI_API_KEY")
    if not key:
        raise AskError("not_configured", "Нет ключа RouterAI (ROUTERAI_API_KEY)", status=503)
    from openai import OpenAI
    import extract_dataset as ed
    return OpenAI(api_key=key, base_url=ed.BASE_URL, timeout=25.0, max_retries=0)


def _price(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _meta(usage: dict, seconds: float, mdl: str, prov: str) -> dict:
    tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    cost = usage.get("cost")
    if not isinstance(cost, (int, float)):
        cost = (tin * _price("ASK_PRICE_IN", 0.15) + tout * _price("ASK_PRICE_OUT", 0.60)) / 1e6
    return {"model": mdl, "provider": prov, "tokens_in": tin, "tokens_out": tout, "cost": round(float(cost), 6), "currency": "RUB",
            "seconds": seconds}


def _call(client, messages, name, schema, max_tokens, label):
    """Один вызов через extract_dataset.call_llm: провайдер закреплён, рассуждение выключено, без повторов."""
    import extract_dataset as ed
    prov, mdl = provider(), model()
    args = argparse.Namespace(model=mdl, provider=prov, no_strict=False)
    report: list = []
    raw = Path(tempfile.gettempdir()) / f"ask_{uuid.uuid4().hex}.raw"
    try:
        out = ed.call_llm(client, args, messages, name, schema, max_tokens, label, raw, report,
                          retry=False, reasoning_tokens=0)
    except ed.LLMError as e:
        raise AskError("llm_bad_output", str(e)[:300], retryable=True)
    except AskError:
        raise
    except Exception as e:  # noqa: BLE001 - сеть, таймаут, 4xx/5xx провайдера
        raise AskError("ml_error", f"{type(e).__name__}: {str(e)[:200]}", retryable=True)
    finally:
        raw.unlink(missing_ok=True)
    rep = report[0] if report else {}
    return out, _meta(rep.get("usage") or {}, rep.get("seconds") or 0.0, mdl, prov)


def _check_question(question):
    if not isinstance(question, str) or not question.strip():
        raise AskError("bad_request", "Нужен вопрос (question)", status=400)
    if len(question) > MAX_QUESTION:
        raise AskError("bad_request", f"Вопрос длиннее {MAX_QUESTION} символов", status=400)
    return question.strip()


def plan(question, run, client=None) -> dict:
    """{plan, meta}: вопрос -> структурированный запрос. run — краткое описание разбора (без строк)."""
    question = _check_question(question)
    if not isinstance(run, dict):
        raise AskError("bad_request", "Нужно описание разбора (run)", status=400)
    run_json = json.dumps(run, ensure_ascii=False, separators=(",", ":"))
    if len(run_json) > MAX_RUN_CHARS:
        raise AskError("bad_request", "Описание разбора слишком большое", status=413)
    client = client or make_client()
    messages = [{"role": "system", "content": PLAN_SYSTEM},
                {"role": "user", "content": f"Question: {question}\n\nRun description (JSON):\n{run_json}"}]
    out, meta = _call(client, messages, "ask_plan", plan_schema(), PLAN_MAX_TOKENS, "ask:plan")
    return {"plan": out, "meta": meta}


def write(question, intent, facts, draft, client=None) -> dict:
    """{text, meta}: факты и шаблонный текст -> короткий русский ответ."""
    question = _check_question(question)
    if not isinstance(facts, dict) or not isinstance(draft, str) or not draft.strip():
        raise AskError("bad_request", "Нужны facts (объект) и draft (текст)", status=400)
    facts_json = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    if len(facts_json) > MAX_FACTS_CHARS or len(draft) > MAX_DRAFT_CHARS:
        raise AskError("bad_request", "Факты или шаблон слишком большие", status=413)
    client = client or make_client()
    messages = [{"role": "system", "content": WRITE_SYSTEM},
                {"role": "user", "content": f"Question: {question}\nIntent: {intent}\nFACTS: {facts_json}\nDRAFT: {draft}"}]
    out, meta = _call(client, messages, "ask_write", WRITE_SCHEMA, WRITE_MAX_TOKENS, "ask:write")
    text = out.get("text") if isinstance(out, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise AskError("llm_bad_output", "Пустой текст ответа", retryable=True)
    return {"text": text.strip(), "meta": meta}


# --- /llm/chat: прокси LLM для агента чата разбора (цикл агента живёт в бэкенде, ключ RouterAI - только здесь) ---
# Бэк присылает сообщения и описание инструментов в формате OpenAI, получает сообщение ассистента (content или
# tool_calls) и meta. Правила те же: провайдер ВСЕГДА закреплён (без него отказ), рассуждение выключено по умолчанию.
CHAT_MAX_CHARS = 150000
CHAT_MAX_TOOLS = 24
CHAT_MAX_TOKENS = 2000
CHAT_DEFAULT_TOKENS = 1200
CHAT_ROLES = ("system", "user", "assistant", "tool")


def agent_model() -> str:
    return os.environ.get("ASK_AGENT_MODEL") or model()


def _pinned_extra(reasoning) -> dict:
    """extra_body: провайдер закреплён (provider() бросает not_configured, если пусто); reasoning выключено, если не просили."""
    providers = [p.strip().lower() for p in provider().split(",") if p.strip()]
    if not providers:
        raise AskError("not_configured", "Провайдер не закреплён: задайте ASK_PROVIDER", status=503)
    extra = {"provider": {"order": providers, "allow_fallbacks": False}}
    if reasoning is True:
        extra["reasoning"] = {"enabled": True}
    elif isinstance(reasoning, int) and not isinstance(reasoning, bool) and reasoning > 0:
        extra["reasoning"] = {"max_tokens": int(reasoning)}
    else:
        extra["reasoning"] = {"enabled": False}
    return extra


def _check_chat(messages, tools, max_tokens):
    if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) and m.get("role") in CHAT_ROLES
                                                                  for m in messages):
        raise AskError("bad_request", "Нужен непустой список messages с ролями system|user|assistant|tool", status=400)
    if tools is not None and (not isinstance(tools, list) or len(tools) > CHAT_MAX_TOOLS
                              or not all(isinstance(t, dict) and t.get("type") == "function" for t in tools)):
        raise AskError("bad_request", f"tools: список function-инструментов (до {CHAT_MAX_TOOLS})", status=400)
    if len(json.dumps(messages, ensure_ascii=False)) > CHAT_MAX_CHARS:
        raise AskError("bad_request", "messages слишком большие", status=413)
    if max_tokens is None:
        return CHAT_DEFAULT_TOKENS
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 16 <= max_tokens <= CHAT_MAX_TOKENS:
        raise AskError("bad_request", f"max_tokens: целое от 16 до {CHAT_MAX_TOKENS}", status=400)
    return max_tokens


def _tool_calls(msg) -> list:
    out = []
    for tc in getattr(msg, "tool_calls", None) or []:
        fn = getattr(tc, "function", None)
        out.append({"id": getattr(tc, "id", None) or f"call_{len(out)}", "type": "function",
                    "function": {"name": getattr(fn, "name", "") or "", "arguments": getattr(fn, "arguments", "") or "{}"}})
    return out


def chat(messages, tools=None, max_tokens=None, temperature=0.0, reasoning=False, tool_choice=None, client=None) -> dict:
    """{message: {role, content, tool_calls?}, finish_reason, meta}: один ход модели. Провайдер закреплён, повторов нет."""
    import time
    max_tokens = _check_chat(messages, tools, max_tokens)
    extra = _pinned_extra(reasoning)  # до make_client: без закрепления модель не вызывается
    client = client or make_client()
    mdl, prov = agent_model(), provider()
    kw = {"model": mdl, "messages": messages, "temperature": float(temperature or 0.0), "max_tokens": max_tokens,
          "extra_body": extra}
    if tools:
        kw["tools"] = tools
        kw["tool_choice"] = tool_choice if tool_choice in ("auto", "none", "required") else "auto"
    t0 = time.monotonic()
    try:
        resp = client.chat.completions.create(**kw)
    except Exception as e:  # noqa: BLE001 - сеть, таймаут, 4xx/5xx провайдера
        raise AskError("ml_error", f"{type(e).__name__}: {str(e)[:200]}", retryable=True)
    seconds = round(time.monotonic() - t0, 3)
    if not getattr(resp, "choices", None):
        raise AskError("llm_bad_output", "Пустой ответ модели", retryable=True)
    ch = resp.choices[0]
    msg = ch.message
    calls = _tool_calls(msg)
    out = {"role": "assistant", "content": getattr(msg, "content", None) or ""}
    if calls:
        out["tool_calls"] = calls
    usage = getattr(resp, "usage", None)
    usage = usage.model_dump() if hasattr(usage, "model_dump") else dict(usage or {})
    return {"message": out, "finish_reason": getattr(ch, "finish_reason", None) or "stop",
            "meta": _meta(usage, seconds, mdl, prov)}
