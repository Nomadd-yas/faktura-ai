# Подключение фронта к бэкенду (v1)

Бэкенд работает на сервере команды и **наружу не открыт** — доступ только через SSH-туннель. Контракт API: [`CORE_API.md`](CORE_API.md).

## 1. Что нужно один раз

- SSH-доступ к серверу (ключ + алиас в `~/.ssh/config`, например `hima-gpu`) — попроси у Андрея.
- Логин и пароль в приложение — Андрей пришлёт отдельно (не в общий чат).

## 2. Туннель

```bash
ssh -N -L 8000:localhost:8000 hima-gpu
```
Пока команда висит — API доступен на `http://localhost:8000/api/v1`, Swagger — `http://localhost:8000/api/v1/docs`.
Если локальный 8000 занят: `-L 18000:localhost:8000` и дальше везде порт 18000.

На Windows можно скриптом из репо: `backend\deploy\tunnel.ps1 up` (`down`, `status`, `health`).

## 3. Vite: проксировать `/api` (рекомендуется)

```ts
// vite.config.ts
export default defineConfig({
  server: {
    proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } },
  },
})
```
Фронт ходит на относительный `/api/v1/...` — нет CORS, refresh-cookie работает как на одном домене.

Без прокси: бэк разрешает CORS только для `http://localhost:5173`; запросы с `credentials: 'include'`. Другой адрес фронта — скажи, добавим.

## 4. Авторизация

1. `POST /api/v1/auth/login` `{email, password}` → `access_token` (15 мин) + refresh-cookie (`httpOnly`, `SameSite=Strict`, `Path=/api/v1/auth`; 12 ч, с `remember: true` — 30 дней).
2. Все запросы: `Authorization: Bearer <access_token>`.
3. На `401` — `POST /api/v1/auth/refresh` (с cookie, `credentials: 'include'`) → новый `access_token`; снова `401` → экран входа.

## 5. Поведение, о котором спрашивали

| Вопрос | Ответ |
|---|---|
| `has_data` у статей `out` | всегда `false`; открывать по `status: "out"`, причина в `reason` |
| `GET /papers/solar/{pid}`, пока файл в очереди/разборе | `200`, пустые таблицы, `paper_status: "nodata"`; стадия — в `meta.file_status` (`queued`/`running`). `404` — только если статьи нет |
| ZIP + отдельные PDF в одном `POST /runs` | можно: поля `archive` и `file` объединяются в один разбор |
| Поля multipart | `file` (повторяемое), `archive`, `domain` (`solar`), `title` |
| Заголовки выгрузки | `X-Export-Tables`, `X-Export-Results`, `X-Export-Papers`, `X-Export-Filename` (URL-encoded); открыты в `Access-Control-Expose-Headers` |
| Прогресс | опрос `GET /runs/{id}` раз в 2 с (SSE — позже) |

## 6. Данные сейчас

Вместо ML-1 работает мок на эталоне dev: значения появятся **только у PDF из dev** (`D001…D022`). Любой другой PDF разберётся без значений (`nodata`). Когда подключим ML-1 — заработает на любых статьях, API не изменится.

Области `rag`, правки результатов, чат, граф и прочее из §15 — пока заморожены, на этих экранах остаётся мок.
