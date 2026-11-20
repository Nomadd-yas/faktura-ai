# Волна 1. Сводный анализ спеки (отдел 1, бэкенд)

Документ сводит четыре независимых анализа спеки: инвентаризацию, функциональный разбор, разбор данных и разбор рисков. Роль команды — **BE** из TASKS.md. Извлечение (ML-1) и разбор PDF с оценкой качества (ML-2) делают другие люди. Фронтенд не анализируется.

**Как ссылаться на источники.** В `BACKEND_SPEC.md` есть только разделы §0, §1, §1А, §2, §8 и «Спорные места». Разделы §3–§7 и §9–§14 лежат в `spec/NN-*.md`, поэтому ссылки выглядят как `spec/14 §14.3`. При расхождениях верен `BACKEND_SPEC.md`, за ним идут TASKS.md (раздел «BE — бэкенд») и `spec/*.md`. Вводные координатора стоят выше общих формулировок и обозначаются «Коорд. п.N».

---

## 0. Что проверено

| Что | Результат |
|---|---|
| Совпадает ли локальная `docs/spec_src` с GitHub `Hahora/hack-lock/docs` | **Совпадает побайтно.** Проверено через `gh api` по git blob SHA для `BACKEND_SPEC.md`, `TASKS.md` и всех 11 файлов `spec/*.md`. |
| Совпадает ли `check_submission.py` локально и в репо | Совпадает: в `docs/case5/tools/` репо и в `data/caseB_kejs5/tools/` у нас одинаковый SHA `b791b6e…`. |
| Пути | В спеке `docs/case5/…`, у нас `data/caseB_kejs5/…` (`dev/`, `dev_gold/`, `test/`, `submission_template/`, `tools/`). Путь задаётся конфигурацией и флагом `--check`, в код не зашивается. |
| dev | 21 PDF: D001…D022, **D011 нет**. Вне области D006 и D016. Сканов нет. |
| test | 90 PDF (P001…P090), 1000 страниц, от 5 до 24 страниц на файл, самый большой — 26,4 МБ. Шифрованных и сканов нет, только у P018 две почти пустые страницы (рисунки). OCR для области solar не нужен. |
| `check_submission.py dev_gold dev` | 0 ошибок, 1 предупреждение (D003, цитата с нестандартным символом), около 15 с. Скрипт проверяет только 251 цитату: фрагменты на `Table`, `TABLE` и `Supplementary` он пропускает, а в results таких 445 из 659. |
| dev_gold, results | Всего 659 строк, из них 632 по 11 оцениваемым `metric_name`: PCE 205, Jsc 111, FF 110, Voc 109, Rs 33, Rsh 32, retained_PCE 14, Jsc_EQE 8, stabilized_PCE 7, HI 3, **T80 0**. По `aggregation`: пусто у 342, champion у 251, mean у 66. Ещё 57 строк cited, 48 повторов с `duplicate_of`, 6 строк present_not_extractable. Нормализация затрагивает 74 строки: mV→V у 56, fraction→% у 18. |
| dev_gold, issues | 53 замечания, у 27 пустые `record_ids`. Среди них 7 `jv_consistency`, то есть правило бэка, которое попало в эталон. |
| Окружение | Python 3.12, PyMuPDF, pypdf и pydantic установлены. FastAPI, SQLAlchemy, psycopg, boto3 и Tesseract не установлены. Docker есть. Ollama не ответила, GPU 4 ГБ: как запасное извлечение не годится. |

---

## 1. Требования

Приоритеты:
- **must** — без этого нет сдачи или нарушен контракт;
- **should** — нужно для продукта и фронта, делается после must;
- **could** — задел.

Метка P0/P1/P2 — порядок реализации (раздел 1.17).

### 1.1. Сдача и CLI (P0)

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-001 | `faktura extract --domain solar --in <папка\|ZIP\|.txt с URL> --out <папка> --check <check_submission.py>` проходит весь конвейер без UI и входа, тем же кодом, что API (импорт → прогон → экспорт). Работает **без Postgres, MinIO и сети**, кроме вызова ML. | must | TASKS BE п.8; spec/13 §13.6; Коорд. п.2, п.5 |
| REQ-002 | В `out/` лежат **ровно 7 CSV**: publications, materials, devices, layers, measurements, results, issues. Всё служебное кладётся только в `out/_meta/`: `manifest.json`, `passport.json`, `passport.md`, `files.csv`, `check_report.txt`, `facts_ui.json`, `errors.log`. | must | spec/13 §13.6; spec/14 §14.1 |
| REQ-003 | Выход проходит `check_submission.py` **без ошибок**. Предупреждения сводятся к минимуму и разбираются. | must | spec/14 §14.6; TASKS «Важно» |
| REQ-004 | Коды выхода: `0` — всё разобрано; `1` — ошибка запуска; `2` — есть `failed`, набор всё равно записан; `3` — валидатор нашёл ОШИБКИ **или упал**. Валидатор возвращает 1 или трассировку, бэк переводит это в 3. | must | spec/13 §13.6 |
| REQ-005 | Сбой одного файла не останавливает прогон (кроме `--fail-fast`). Итоговый статус — `done_with_errors`. | must | spec/13 §13.5–13.6 |
| REQ-006 | `check_submission.py` вызывается **как есть**: subprocess с `sys.executable`, аргументы `<папка CSV> <папка PDF>`, переменные `PYTHONUTF8=1` и `PYTHONIOENCODING=utf-8`. Без них на Windows вывод в pipe идёт в cp1251, и символы η, µ, − роняют скрипт. Вывод сохраняется в `_meta/check_report.txt` и разбирается на errors, warnings, quotes_checked и quotes_not_found для manifest. | must | spec/14 §14.6; анализ данных и рисков |
| REQ-007 | Флаги CLI: `--profile`, `--format csv,json,xlsx`, `--concurrency 4`, `--model`, `--extract-url`, `--parse-url`, `--no-dedupe`, `--ocr auto\|off\|force`, `--fail-fast`, `--check`, `--server`/`--api-key` (удалённый режим). `--in` — папка (рекурсивно), ZIP или `.txt` со списком URL. | should | spec/13 §13.6 |
| REQ-008 | Прогресс выводится построчно в stderr в виде `[ 61/148] done  cigs_flex.pdf  facts=16 issues=1`. | should | spec/13 §13.6 |
| REQ-009 | `_meta/manifest.json` содержит: tool, version, started_at, finished_at, domain, split, profile{id,version}, extract_model, parse_model, norm_version, rules_version, dictionary_version, input{path,files,accepted,duplicates}, result{publications,in_scope,results,issues,failed}, check{errors,warnings,quotes_checked,quotes_not_found}. В `_meta/files.csv` на каждый вход: имя, sha256, статус, publication_id, число результатов и замечаний, ошибка. В `_meta/facts_ui.json`: confidence, bbox, review_status для импорта в корпус. | must | spec/13 §13.6; spec/07 §7.0.1 «Версия модели» |
| REQ-010 | `faktura import-raw --in ./raw --corpus <id>` загружает папку JSON ML-1 без вызова контейнера. | should | spec/07 §7.0.1 |
| REQ-011 | Проверка на скрытых примерах: система должна запускаться на **новых PDF** локально без входа или через API с ключом. Упаковка: CLI плюс docker compose с контейнером ML-1. | must | spec/13 вступление, §13.6; spec/10 §10.1 |

### 1.2. Формат CSV сдачи

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-012 | Колонки и их порядок 1:1 с `TABLES` в `check_submission.py`: publications 7, materials 11, devices 13, layers 13, measurements 16, results 27, issues 7. Заголовки бэк гарантирует сам: без колонки `publication_id` или `result_id` скрипт падает с KeyError. | must | spec/14 §14.1; check_submission.py |
| REQ-013 | UTF-8 **с BOM**, разделитель `,`, десятичная точка, `csv.QUOTE_MINIMAL`. Перевод строки — стандартный `\r\n` модуля csv, скрипт читает с `newline=''`. Булевы значения — `true`/`false` строчными. Списки разделяются `;`. | must | spec/14 §14.2; check_submission.py |
| REQ-014 | Пустая ячейка означает «в статье нет». Никаких `null`, `NA`, `—` или `0` вместо пустого. `null` из API превращается в пустую строку. | must | spec/14 §14.2 |
| REQ-015 | Отдельный писатель для сдачи. CSV «для людей» — другой диалект: `;`, CRLF, десятичная запятая. Один писатель на оба случая не переиспользуется. | must | spec/04 §4.16; spec/14 §14.2 |
| REQ-016 | Словари строгие и с учётом регистра, 1:1 с `VOCAB`: material_class, dimensionality, bandgap_method, device_role, device_type, architecture, substrate_type, area_definition, layer_function (8 значений), measurement_type, scan_direction, stability_protocol, mask_used, certified, in_scope, aggregation, result_origin, extraction_status. Списки значений — в spec/14 §14.3. | must | spec/14 §14.3; check_submission.py VOCAB |
| REQ-017 | Числовые поля `metric_value`, `normalized_value`, `source_page`, `layer_order`, `active_area`, `bandgap_value` проходят `float()`. Значения вроде `1,5`, `~1.9`, `18.2-19.0`, `15.28 mA` недопустимы. Остальные числовые поля (year, n_devices, spread_value, light_intensity, layer_thickness, time_point) тоже пишутся числами, хотя скрипт их не проверяет. | must | check_submission.py NUMERIC; spec/14 §14.6 |
| REQ-018 | **Санитайзер сдачи** — последний слой перед записью, чтобы на любом ответе ML было 0 ошибок валидатора. Значение вне словаря: ячейка пустая, исходник уходит в `note`. Нечисловое значение в числовом поле: привести или очистить и записать в `note`. Висячая ссылка: очистить с записью в `_meta/errors.log`. Повтор `result_id`: переименовать **каскадно** вместе с `duplicate_of` и `record_ids`. | must | анализ рисков NFR-02; spec/14 §14.6 |
| REQ-019 | Ссылочная целостность внутри статьи по паре (publication_id, id): `devices.absorber_material_id`→materials; `layers.device_id`→devices; `measurements.device_id` (через `;`)→devices; `results.device_id`, `measurement_id`, `material_id`→свои таблицы; `results.duplicate_of`→results; `issues.record_ids` (через `;`)→results. Каждый publication_id есть в publications. | must | spec/14 §14.1; check_submission.py |
| REQ-020 | Уникальность внутри статьи для `material_id`, `device_id`, `layer_id`, `measurement_id`, `issue_id` проверяет **бэк**: скрипт проверяет только `result_id`. | must | шаблон README; анализ данных DATA-05 |
| REQ-021 | Собственный валидатор бэка строже скрипта. Он проверяет: `metric_*` = `normalized_*`; единицы из §6.1; `metric_name` из словаря или осознанное нестандартное имя; `check_name` только из каталога §5.2; типы year, n_devices и других полей. | should | анализ данных; spec/14 §14.3 |
| REQ-022 | Порядок строк: по `publication_id`, затем по исходному порядку ML. Повтор идёт после основной записи. Одинаковый вход даёт одинаковую сдачу. | should | анализ данных DATA-27 |

### 1.3. Содержание таблиц сдачи

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-023 | `publication_id` — имя PDF без расширения. | must | spec/14 §14.2; BACKEND «Закрыто архивом» |
| REQ-024 | В publications есть строка на **каждый** входной PDF: статьи вне области, упавшие и не обработанные ML тоже. Скрипт полноту не проверяет, это обязанность бэка. Как заполнять упавший файл — см. решение К-14. | must | spec/14 §14.2; spec/13 §13.6 |
| REQ-025 | При `in_scope=false` заполняется `exclusion_reason` по-русски со ссылкой на раздел 3.1 условий. В остальных шести таблицах строк у такой статьи нет: бэк отбрасывает всё, что вернул ML. Тандемы и расчётные работы всегда `false`. | must | spec/07 §7.0.1, §7.4 п.2; BACKEND «Закрыто архивом» |
| REQ-026 | `source_page` — номер страницы PDF, считая с 1, а не журнальный. `source_fragment` — короткая дословная цитата с этой страницы (примерно 30–80 символов, одна строка, без переноса через колонки). Для ячейки таблицы: «Table N, строка X» с заполненным `source_table`. | must | spec/14 §14.2; BACKEND «Остаётся открытым» п.3 |
| REQ-027 | Одна строка results — один показатель одного прибора при одних условиях, включая направление развёртки и момент времени. Champion и mean записываются разными строками. | must | spec/14 §14.4 |
| REQ-028 | `raw_value` и `raw_unit` пишутся дословно (`0.49`+`fraction`, `1123`+`mV`). `normalized_*` — в ASCII-единицах. `metric_value`/`metric_unit` всегда равны `normalized_*`; ML их не заполняет, а если заполнит, бэк перезапишет. | must | spec/14 §14.4; spec/07 §7.0.1; BACKEND «Закрыто архивом» |
| REQ-029 | Для `result_origin=cited`: `cited_publication_id` как в статье (`[12]`, `[19–21]`, `ref. 13`, `Bai et al., 2018; …`), а `device_id`, `measurement_id` и `material_id` пусты. Id для cited не придумываются. | must | spec/14 §14.4; dev_gold (57 строк) |
| REQ-030 | Для `present_not_extractable`: значения, `raw_*` и `normalized_*` пусты, `source_page` и `source_fragment` указывают место (например, «Supplementary Table S2»). `extraction_status` при сбое нормализации не меняется. | must | spec/14 §14.4; spec/06 §6.2 |
| REQ-031 | Повтор внутри статьи — отдельная строка со своим `source_*` и `duplicate_of` = result_id основной записи. У основной записи поле пустое, цепочек повторов нет. Если значения расходятся, это не повтор: отдельная запись плюс `contradiction`. Одинаковые значения у разных приборов — тоже не повтор (`dedup_trap`). | must | spec/14 §14.4; spec/03 §3.6.1 |
| REQ-032 | Явные повторы, которые ML не связал, бэк связывает сам с note «связано бэком». Совпадать должны прибор, показатель, измерение, агрегация и значение в пределах округления. Для мока режим отключается. | should | spec/03 §3.6.1 |
| REQ-033 | Ничего не восстанавливать по догадке. Если в статье не указаны aggregation, единица, площадь, толщина, scan_direction или условия измерения, поле остаётся пустым. Предположения ML идут только в `note`. Pin=100 используется только для расчёта `jv_consistency` и в данные не пишется. | must | spec/06 §6.4; BACKEND §8.4 |
| REQ-034 | Строки `absent` по умолчанию не выгружаются, включаются профилем. Находки ML с `confidence < 0,5` показываются только в UI. | must | BACKEND «Остаётся открытым» п.4–5; Коорд. п.6 |
| REQ-035 | Слои: подложку (стекло, фольгу, полиимид) не пишем. `layer_order` начинается с 1 с контакта на подложке. CdS и n-слои → `ETL`, металлическая сетка → `front_contact`, просветляющие и прочие слои → `interlayer`, исходное название — в `note`. Отжиг пишется строкой с единицей (`500 °C`, `60 min`). | must | spec/14 §14.3; spec/06 §6.3 |
| REQ-036 | Измерения: прямой и обратный ход — разные measurements. `measurements.device_id` может содержать несколько id через `;`. `light_intensity`/`intensity_unit` пишутся как в статье, без пересчёта. «1 sun» уходит в `note` и не превращается в 100 mW/cm2. Темновая ВАХ — `J-V` с примечанием. Протокол ISOS не назван → `other`, условия — в `stress_conditions`. `AM 1.5G`, `AM1,5G` и `1 sun AM1.5` приводятся к `AM1.5G`, только если в статье сказано AM1.5G. | must | spec/14 §14.3; spec/06 §6.3 |
| REQ-037 | Единица ставится только при значении: `thickness_unit=nm`, `area_unit=cm2`, `bandgap_unit=eV` пишутся, только если есть само значение. | must | анализ данных DATA-11 (эталон) |
| REQ-038 | Материалы: `formula_normalized` для перовскита записывается в виде A-B-X с раскрытыми MA, FA, Cs (`MAPbI3`→`CH3NH3PbI3`, `FAPbI3`→`HC(NH2)2PbI3`). | should | spec/14 §14.3; spec/06 §6.3 |
| REQ-039 | `devices.encapsulation` выгружается как `true`/`false` или пусто (решение К-7). | must | spec/14 §14.2; spec/03 |
| REQ-040 | Нестандартные метрики (Voc_deficit, Jmpp, Vmpp, AVT, CRI, LUE, bifaciality_factor) выгружаются, если их вернул ML, но в оценке не участвуют. | should | spec/14 §14.3; spec/07 §7.0.1 |
| REQ-041 | Выгрузка из фактов. Попадают статусы `new` и `accepted`, `rejected` — нет. Если значение исправлено, `normalized_value`/`metric_value` = `corrected_value`, а `raw_value` остаётся из статьи. Ручные факты выгружаются как `extracted` со своей цитатой. Служебные поля (confidence, bbox, review_status, corrected_value, added_by, anchor_id, quote_found, comparability_key, norm_status) в CSV не попадают. | must | spec/14 §14.5; spec/03 §3.6 |

### 1.4. Нормализация

Поля помечены [BE]. Модуль совместим с будущим `faktura_norm`.

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-042 | Модуль `faktura.norm` с интерфейсом, совместимым с `faktura_norm`: `normalize(metric_name, raw_value, raw_unit, header_unit) -> NormResult{normalized_value, normalized_unit, norm_status ok\|converted\|failed\|not_applicable, spread_value, trace}`, а также `match_metric(text)`, `canon_material(text)`, `layer_function(text, position)`. Реализация подключается через адаптер, словари — данные с `dictionary_version`. | must | BACKEND §1А.1, «Остаётся открытым» п.13; spec/06; Коорд. п.1 |
| REQ-043 | Целевые единицы: PCE, FF, HI, retained_PCE, stabilized_PCE → `%`; Voc → `V`; Jsc, Jsc_EQE → `mA/cm2`; Rs, Rsh → `Ohm*cm2`; T80 → `h`; Eg → `eV`; толщина → `nm`; площадь → `cm2`. FF в процентах, не в долях. | must | spec/06 §6.1; spec/14 §14.3; BACKEND «Закрыто архивом» |
| REQ-044 | Разбор числа: убрать пробелы и NBSP, `−` заменить на `-`, запятую на точку. Диапазон не выбирать: `normalized_value` пусто, `failed`, `ambiguity`. `18.6 ± 0.4` даёт 18.6 и `spread_value` 0.4. Множители по §6.1: mV 0.001, A/m2 0.1, A/cm2 1000, kOhm·cm2 1000, min 1/60, day 24, µm 1000, Å 0.1, mm2 0.01. | must | spec/06 §6.1–6.2 |
| REQ-045 | Единица берётся из `raw_unit`, иначе из заголовка таблицы, иначе по шкале долей. Если ничего нет: `failed` и `unit_inconsistency`. Доли ×100 только при известной шкале. «FF 0.71 %» становится 71 % с пометкой `unit_inconsistency`. Rs/Rsh в Ohm без площади и Eg в nm не пересчитываются. | must | spec/06 §6.2, §6.5 |
| REQ-046 | Арифметика в `Decimal`, без артефактов float. Точность не повышается и не понижается: 1197 mV даёт 1.197, 0.71 даёт 71.0. | must | spec/06 §6.2; анализ рисков NFR-07 |
| REQ-047 | Синонимы `metric_name` ищутся без учёта регистра и целым словом (список в spec/06 §6.3). Несопоставленное сохраняется как в статье. | should | spec/06 §6.3 |
| REQ-048 | Регрессия: на всех 74 строках dev_gold с пересчётом модуль даёт тот же `normalized_value`, что эталон. | must | анализ рисков NFR-06 |

### 1.5. Проверки (issues)

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-049 | Проверки идут **только внутри одной статьи**. Межстатейно допускаются только сопоставимость (как фильтр, без замечаний), дубликаты статей и `result_origin=cited`. | must | BACKEND §1А; spec/05 §5.0; spec/13 §13.5 |
| REQ-050 | Правила бэка: `jv_consistency` (\|Voc·Jsc·FF/Pin − PCE\|/PCE > 3 %; Pin из measurements, W/m2 ×0,1; при AM1.5G без интенсивности берётся 100); `voc_limit` (Voc ≥ Eg); `range` (PCE ∉ (0;100), FF ∉ (0;100], Jsc ≤ 0 или > 50, Voc ≤ 0 или > 3 для cell, Rs/Rsh ≤ 0, retained_PCE > 150); `eqe_consistency` (> 5 %); `champion_vs_mean`; `hysteresis_index` (расхождение > 1 п.п. или HI < 0); `contradiction` по повторам с разными значениями; `unit_inconsistency`; `rounding`/`rounding_inconsistency`; `voc_deficit_consistency`; `missing_conditions`; `plausibility` (таблица рекордов); `figure_mismatch` (> 3 % при `source_figure`); подсказка `dedup_trap`. | must | spec/05 §5.2; TASKS BE п.5; BACKEND §1А.1 |
| REQ-051 | Если расхождение объясняется округлением последнего знака множителей, ставится `rounding` (info) вместо `jv_consistency` или `contradiction`. Допуск откалибровать на D018, D021 и D004 из dev_gold. | must | spec/05 §5.2; анализ рисков R3 |
| REQ-052 | Правила считают по действующему значению `corrected_value ?? normalized_value`. Отклонённые факты и строки с `duplicate_of` не участвуют, кроме правила `contradiction`. Пороги берутся из профиля: `jv_consistency.threshold_pct=3`, `eqe_consistency.threshold_pct=5`. | must | spec/05 §5.0; spec/13 §13.3 |
| REQ-053 | Поля issues.csv: `publication_id`; `issue_id` (в выгрузке I1, I2… по порядку страниц); `record_ids` через `;`, могут быть пустыми; `check_name` **только из каталога** §5.2; `description` по-русски с числами обеих сторон; `source_page` и `source_fragment` обязательны. Исходные записи не удаляются и не меняются. | must | spec/05 §5.1; spec/14 §14.1 |
| REQ-054 | Внутренний `issue_id` детерминирован и переживает пересчёт (формат — К-6). Разбор человека (`fixed`/`ok`) сохраняется при пересчёте. | must | spec/05 §5.0 |
| REQ-055 | Находки ML (`contradiction` текста, `ambiguity`, `typo`, `possible_swap`, `derived_values`, `figure_only`, `figure_mismatch`, `cited_in_table`, `dedup_trap`, `text_mix`, `measurement_conditions`, `comparability`) бэк не пересчитывает. Он проверяет `record_ids` и цитату, а при ошибке ставит пометку, но не выбрасывает находку. При прогоне `full` они заменяются, разбор человека переносится по `check_name` + множеству `record_ids`. | must | spec/05 §5.4; BACKEND §1А.1 |
| REQ-056 | **Слияние** находок ML и правил бэка. При совпадении `check_name` и множества `record_ids`, без учёта порядка, остаётся одно замечание с `computed_by=backend`. | must | анализ рисков NFR-09 (мок отдаёт эталонные `jv_consistency`) |
| REQ-057 | В выгрузку попадают статусы `open` и `fixed`, статус `ok` («не ошибка») — нет. | must | spec/05 §5.1; spec/14 §14.5 |
| REQ-058 | Проверки пересчитываются: после разбора статьи; после каждой правки (accept, reject, PATCH, add, undo), синхронно, до 100 мс на статью; после смены словаря или порогов через `renormalize`/`recheck`. Ответ на изменяющий запрос содержит `issues_changed{opened,closed}` и обновлённые `quality`, `open_issues`, `top_level`. | should | spec/05 §5.4; spec/07 §7.4 |
| REQ-059 | `comparability_key` = material_class \| device_type \| spectrum и intensity \| area_bucket (<0,1; 0,1–1; >1 cm2) \| aggregation \| scan_direction \| certified \| stability. Если поле пустое, ключ помечается «неполный». Ключ используется только как фильтр и в `/data/compare`, в CSV не выгружается. | should | spec/05 §5.3 |
| REQ-060 | Цитата проверяется при сохранении факта (`quote_found`) **той же** функцией `norm()` и тем же **pypdf**, что в валидаторе: лигатуры ﬁ ﬂ ﬀ, U+2212 и U+2013 → `-`, нижний регистр, остаются только `[a-z0-9.%]`. Префиксы `Table`, `TABLE`, `Supplementary` (именно в таком регистре) не проверяются. Текст страниц кешируется один раз на статью: pypdf на test занимает 92 с. Цитата, которая после `norm()` пуста, считается ненайденной (у скрипта она «находится» вырожденно). | must | spec/14 §14.6; check_submission.py |

### 1.6. Конвейер и адаптеры ML

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-061 | Конвейер на каждый PDF: ParseAdapter (ML-2) → ExtractAdapter (ML-1) → валидация схемы → нормализация → проверки → сохранение **одной транзакцией** с `model_version` → `done`. | must | TASKS BE п.3; BACKEND §1А.1, §1А.3; spec/07 §7.4 |
| REQ-062 | `ExtractAdapter` в трёх реализациях: `MockGoldExtractor` (строки dev_gold для D*, пустой ответ для остальных), `HttpExtractor` (`POST /ml/extract`), `RawDirExtractor` (import-raw). Канонический формат — **§7.0.1**. | must | TASKS BE п.9; spec/07 §7.0.1; Коорд. п.1 |
| REQ-063 | `ParseAdapter`: пока ML-2 нет, локальная реализация на PyMuPDF даёт page_count, размеры страниц, рендер, текст по страницам и признак `needs_ocr` по §11.3. Признак ставится, если на странице меньше 100 символов и изображения занимают больше 70 % площади, или если мусорных символов больше 30 %. HTTP-реализация — `/ml/parse`. | should | spec/11 §11.3; spec/07 §7.0.2 |
| REQ-064 | Ответ ML валидируется (pydantic). Невалидная запись отбрасывается с warning. Если невалиден весь ответ: `failed` с `ml_schema_mismatch`. Значения вне профиля уходят в `unmapped_values`. | must | spec/07 §7.4; spec/13 §13.3 |
| REQ-065 | Ретраи на 429, 502, 504 и обрыв связи: до 3 раз, паузы 30 с / 2 мин / 10 мин. На 422 ретраев нет. Таймаут — `EXTRACT_TIMEOUT_S + 30` с (330 с). Одновременных запросов не больше `EXTRACT_CONCURRENCY`. Если `/health` отвечает 503, прогоны встают на паузу, ошибка `ml_unavailable`, файл остаётся в очереди. | must | spec/07 §7.0.1 |
| REQ-066 | **Кеш сырых ответов ML** по sha256 файла и model_version. Повторный CLI-прогон, `renormalize` и `recheck` к ML не обращаются, после сбоя прогон возобновляется. | must | spec/13 §13.5; spec/07 §7.0.1 |
| REQ-067 | `_bbox` ML (в пунктах PDF) переводится в `rects` в долях страницы от левого верхнего угла. Начало координат — параметр адаптера (К-2). Если bbox нет (мок), строится запасной якорь: поиск `source_fragment` или `raw_value` на `source_page` через PyMuPDF `search_for`. | should | spec/07 §7.0.1; spec/04 §4.7; анализ рисков NFR-21 |
| REQ-068 | `model_version` записывается в статью, в каждый факт и в manifest. Версии модели, правил, словаря и профиля фиксируются в прогоне. | must | spec/07 §7.3; spec/13 §13.2 |
| REQ-069 | Коллекция определяется так: проект → коллекция проекта; иначе ML-2 при уверенности ≥ 0,7; иначе выбор пользователя. Если человек меняет коллекцию, статья разбирается заново. В CLI коллекция задаётся через `--domain`. | should | BACKEND §1А.1 |
| REQ-070 | Фронт с ML напрямую не общается. Внутренние ML-эндпоинты (`/internal/ml/*`, вебхуки) живут на отдельном порту или в отдельной сети, с HMAC-подписью тела. | should | BACKEND §8.5; spec/07 §7.3 |

### 1.7. Корпус, импорт, прогоны

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-071 | Статья принадлежит ровно одному корпусу. Поштучные загрузки идут в служебный корпус «Загруженные вручную». | should | spec/13 §13.2 |
| REQ-072 | Приём `POST /corpora/{id}/files` возвращает `202 {accepted,duplicates,rejected[],files[]}`. Способы: (1) multipart, до 200 файлов и 1 ГБ; (2) ZIP `archive` или `upload_id` с потоковой распаковкой, берутся только `*.pdf` по сигнатуре, `__MACOSX` и скрытые файлы игнорируются; (3) список URL, до 5000 ссылок; (4) `server_path` — только admin, из белого списка. | must (1–2), should (3–4) | spec/13 §13.4 |
| REQ-073 | Проверки файла: сигнатура `%PDF-` (иначе 415 `unsupported_file`), до 50 МБ (413), до 300 страниц (422 `too_many_pages`), `pdf_encrypted`, `pdf_broken`, SHA-256. Скан без текста принимается; если ML не справился, файл получает `failed` с `pdf_no_text`. | must | BACKEND §8.1, §8.3 |
| REQ-074 | Дедупликация. Точная, по SHA-256: внутри корпуса → `skipped_duplicate` и `duplicate_of`; между корпусами при `reuse_existing=true` (по умолчанию) статья связывается без повторного разбора. Смысловая (DOI; затем название, год и первый автор) ставит `dup_of`/`dup_kind`, данные не сливаются. Отключается через `--no-dedupe`. | must | spec/13 §13.4; BACKEND «Остаётся открытым» п.11 |
| REQ-075 | Статусы файла: `pending\|downloading\|queued\|processing\|done\|failed\|skipped_duplicate\|needs_ocr\|cancelled`. Плюс поля stage, progress, paper_id, duplicate_of, facts, issues, error{code,message}, attempts, model_version. | must | spec/13 §13.2 |
| REQ-076 | Прогон `POST /corpora/{id}/runs`: `kind` ∈ {full, new_only, selected, failed_only, renormalize, recheck}, `concurrency=4`, `keep_review`. На корпус один активный прогон, иначе 409 `conflict` «Прогон уже идёт». Статусы: queued, running, done, done_with_errors, cancelling, cancelled, failed. Счётчики, progress, facts_found, issues_found, eta_s. | must | spec/13 §13.2, §13.5 |
| REQ-077 | Отмена `POST /runs/{id}/cancel` → 202, `cancelling`, затем `cancelled`. Незапущенные файлы получают `cancelled`, уже разобранное сохраняется. Повтор одного файла: `POST /corpora/{id}/files/{file_id}/retry`. | must | spec/13 §13.5 |
| REQ-078 | `keep_review` переносит разбор человека на совпавшие записи: прибор + показатель + измерение + место, значение в пределах 0,5 %. Несовпавшие исправления сохраняются как ручные факты. | should | BACKEND «Остаётся открытым» п.12; spec/07 §7.3 |
| REQ-079 | Защита импорта. ZIP: распаковано не больше 10 ГБ, не больше 20 000 файлов, глубина не больше 10, защита от zip-slip (422 `zip_too_large`/`zip_bomb`). URL: только http(s), без частных адресов (SSRF), таймаут 60 с, до 50 МБ. | must для ZIP, should для URL | spec/13 §13.4; BACKEND §8.5 |

### 1.8. Хранение и инфраструктура

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-080 | Репозитории и хранилище файлов — за абстракцией. Для API: PostgreSQL ≥ 14 (JSONB для layout, trace, answer) и MinIO/S3 (`papers/{workspace_id}/{paper_id}/original.pdf`, `pages/{n}@{scale}.webp`, `exports/{id}.{ext}` со сроком 24 ч). Для CLI и юнит-тестов: SQLite (WAL, один писатель, транзакция на статью) и файловая система. | must | BACKEND §8.2; Коорд. п.5 |
| REQ-081 | Специфика Postgres (JSONB, полнотекстовый поиск, keyset) изолирована в слое хранения. Соединение или пул переиспользуются, SQLite не открывается заново на каждый вызов. | must | анализ рисков R11 |
| REQ-082 | Мягкое удаление: `deleted_at`, окно восстановления 30 суток, затем физическое удаление. После окна — 410 `gone`. | should | BACKEND §8.2 |
| REQ-083 | Файлы отдаются через API: прокси или подписанная ссылка на 5 минут. | should | BACKEND §8.2 |
| REQ-084 | docker compose для Postgres, MinIO и API. Ключ роутера ML в репозитории не хранится. | should | spec/07 §7.0.1; Коорд. п.5 |

### 1.9. API — общие соглашения (P1, обязательны для всех эндпоинтов)

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-085 | Базовый путь `/api/v1`. Формат: JSON UTF-8, `snake_case`, числа с точкой, даты ISO 8601 UTC. Id — непрозрачные строки (`pap_…`, `fct_…`). | must | BACKEND §0 |
| REQ-086 | Списки возвращаются как `{data,total,next_cursor}`: `limit` по умолчанию 50, максимум 500, плюс `cursor`, `sort`, `dir`. Справочники — `{data:[…]}` целиком. | must | BACKEND §0 |
| REQ-087 | Ошибки — `{error:{code,message,field,details}}`, `message` по-русски, коды из §8.3; у 5xx есть `request_id`. В каждом ответе заголовок `X-Request-Id`. | must | BACKEND §0, §8.3 |
| REQ-088 | Каждый изменяющий запрос пишет `Action` с состояниями «до» и «после» и возвращает `action_id` в теле и в `X-Action-Id`. Журнал закладывается с первого изменяющего эндпоинта. | must | BACKEND §0 «Действие и отмена»; анализ рисков NFR-20 |
| REQ-089 | CORS по белому списку из конфига (прод и `http://localhost:5173`), не `*`. `Allow-Credentials` включён при cookie. Разрешённые заголовки: Authorization, Content-Type, X-Request-Id, Last-Event-ID, Idempotency-Key. Открытые (expose): X-Action-Id, X-Request-Id, Content-Disposition. | must | BACKEND §8.5 |
| REQ-090 | `Idempotency-Key` хранится 24 ч на `POST /papers`, `/chats/ask`, `/chats/{id}/turns`, `/exports`. | should | BACKEND §8.5 |
| REQ-091 | Лимиты: 600 запросов в минуту на пользователя, 60 загрузок в час, 30 вопросов в чат в минуту. При превышении — 429. | should | BACKEND §8.3 |
| REQ-092 | Цитаты и тексты из PDF отдаются как текст. Единицы в API ASCII, по-русски только поле `display`. Фронт ничего не вычисляет: качество, покрытие, агрегаты и фильтры считаются на сервере. | must | BACKEND §8.5, §1А.2; spec/04 §4.8 |
| REQ-093 | Контракт для фронта не изобретается. Уточнения фиксируются в `docs/wave1/api_contract.md` со ссылками на разделы, реестр эндпоинтов — spec/04 §4.22. До реализации эндпоинт P2 может отдавать заглушку с корректной формой ответа. | must | Коорд. п.3; spec/04 §4.22 |

### 1.10. Авторизация и пользователи (P1)

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-094 | Access-токен — JWT на 15 мин, claims `sub`, `wid`, `exp`, `iat`, `jti`. Refresh-токен непрозрачный: 30 дней, при `remember=false` 12 ч. На сервере хранится хеш, токен одноразовый с ротацией; повторное предъявление гасит всю цепочку. Refresh передаётся в cookie `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth`. | must | BACKEND §2.1; «Остаётся открытым» п.9 |
| REQ-095 | Эндпоинты: `POST /auth/login`, `/auth/refresh`, `/auth/logout` (204, `all`), `GET /auth/me`. Ошибки: 400 `validation_error`, 401 `invalid_credentials`/`refresh_invalid`/`unauthorized`/`token_expired`, 403 `account_disabled`, 429 `too_many_attempts` (5 за 15 минут на почту, 20 на IP, `details.retry_after`). Без токена доступны только login, refresh и `/health`. | must | BACKEND §2.1 |
| REQ-096 | Пароль не короче 8 символов, хеш argon2id или bcrypt с cost ≥ 12. | must | BACKEND §2.1 |
| REQ-097 | Учётные записи заводит бэк: CLI `faktura users create\|disable\|enable\|reset-password\|list`, админ из `FAKTURA_ADMIN_EMAIL`/`FAKTURA_ADMIN_PASSWORD`, `GET/POST/PATCH /admin/users`. `POST /auth/register` по умолчанию отвечает 403 `registration_disabled`, включается `FAKTURA_ALLOW_SIGNUP=true`. | must (CLI и админ из env), should (эндпоинты admin) | BACKEND §2.2 |
| REQ-098 | Права: статьи, факты, замечания, словарь и журнал общие для рабочего пространства; проекты, чаты и виды личные (`owner_id`). | should | BACKEND §2.5; «Остаётся открытым» п.8 |
| REQ-099 | OAuth (VK, Яндекс), восстановление пароля, API-ключи (`/me/api-keys`) — позже. | could | BACKEND §2.3–2.5 |

### 1.11. API основных экранов (P1)

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-100 | Корпус: `POST/GET /corpora`, `GET/PATCH/DELETE /corpora/{id}` (и restore), `GET /corpora/{id}/files` (status, q, has_errors, sort, limit ≤ 500, cursor), `GET /corpora/{id}/runs`, `GET /runs/{id}`, `GET /runs/{id}/output` (ZIP выходной папки). | must | spec/13 §13.4–13.6; TASKS BE п.2 |
| REQ-101 | Сводка: `GET /corpus/overview` (≤ 100 мс из кеша агрегатов, `empty:true`) и `GET /corpora/{id}/summary` (files, papers, facts, issues.by_rule, coverage[], not_found[], errors[]). | must | spec/13 §13.8 |
| REQ-102 | Профиль: `GET /profiles?collection=`, `GET/POST/PATCH /profiles/{id}`. В профиле fields[] и rules[], версия растёт при каждом изменении, встроенный профиль меняется только через копию. PATCH возвращает `rerun_hint`: новые поля → full, пороги → recheck, единицы → renormalize. Стандартный профиль solar строится по шаблону и 11 metric_name. | must | spec/13 §13.3 |
| REQ-103 | Прогресс через SSE `GET /events`: Authorization или одноразовый `?token=` из `POST /events/token`, `Last-Event-ID`, пинг каждые 25 с. События: `run.progress` (не чаще раза в секунду), `run.file`, `run.done`, `paper.*`, `facts.changed`, `issues.changed`, `dictionary.renormalized`, `export.ready`. Запасной вариант — опрос `GET /runs/{id}` каждые 2–5 с. | must | spec/04 §4.20; spec/13 §13.5 |
| REQ-104 | `GET /facts/table`: область, q, text, param, entity, metric_name, metric_k, min/max, статусы, has_issues, check_name, severity, paper_id, годы, topic, confidence, sort (несколько полей), dir, limit (100, максимум 1000), cursor (keyset, устойчивый к правкам), columns, facets. Ответ: data[], total, next_cursor, filters[], facets. 100 строк должны отдаваться за ≤ 300 мс при 100 тыс. фактов. | must | spec/13 §13.7 |
| REQ-105 | Язык запросов `parseQuery`. Поддерживает: темы, числа с операторами («КПД выше 18»), годы, замечания. Синонимы параметров берутся из словаря. Возвращаются `filters[]` и `unparsed`. Есть `GET /data/query/parse`; при `strict=true` неразобранный запрос даёт 400 `query_unparsed`. | should | spec/04 §4.11; spec/13 §13.7 |
| REQ-106 | Статья: `GET /papers`, `GET /papers/{id}` (processing, quality, coverage_summary, open_issues, top_level, dup_of, topic_by, model_version), `/pages/{n}` (scale, webp или png, ETag), `/file` (Range), `/layout`, `/anchors`, `/coverage`, `/structure`, `/links`, `/processing`, `POST /papers/{id}/reprocess`, `DELETE`. Если статья не готова — 422 `paper_not_ready`. | must (кроме structure и links — should) | spec/04 §4.6–4.7 |
| REQ-107 | Факты: `GET /facts`, `GET /papers/{id}/facts`, `GET /facts/{id}`, `/source`, `/trace`; `POST /facts/{id}/accept\|reject\|restore`; `PATCH /facts/{id} {raw}` (при ошибке 422 `fact_value_unrecognized`, field=raw); `POST /facts/bulk` (fact_ids или filter, до 5000 фактов, одна запись журнала). Поля факта — по spec/04 §4.8. | must | spec/04 §4.8 |
| REQ-108 | Ручной факт: `POST /papers/{id}/facts/guess`, `POST /normalize/preview` (всегда 200), `POST /papers/{id}/facts` (`manual` создаётся в статусе accepted с confidence 1, `figure` — в статусе new с 0,7). | should | spec/04 §4.9 |
| REQ-109 | Замечания: `GET /issues`, `GET /papers/{id}/issues` (по умолчанию status=all), `POST /issues/resolve {issue_ids, how fixed\|ok, accept_fact_id?}` (409 `already_resolved`), `POST /issues/{id}/reopen`. Поля: severity, ui_level, plugin, fact_ids, anchor_ids, numbers, computed_by, confidence, stale, rule_version. | must | spec/04 §4.10; spec/05 §5.1 |
| REQ-110 | Журнал: `GET /history`, `POST /history/undo`, `POST /actions/{id}/undo`. Ошибки 409 `conflict`, 409 `already_undone`. Групповое действие откатывается целиком. Глубина — 50 действий за 24 ч. | must (журнал), should (undo) | BACKEND §0; spec/04 §4.15 |
| REQ-111 | `GET /health` без авторизации возвращает `{status, db, storage, ml: ok\|degraded}`. | must | spec/04 §4.21 |

### 1.12. Экспорт и паспорт

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-112 | `POST /exports {format:"submission", scope:{corpus_id}, validate:true}` возвращает ZIP из 7 CSV и `check_report.txt` с заголовком `X-Check-Status: ok\|warnings\|failed`. Если валидатор нашёл ошибки — 422 `submission_invalid` с отчётом. | must | spec/14 §14.6; spec/04 §4.16; TASKS BE п.7 |
| REQ-113 | `POST /exports/preview` отдаёт первые 50 строк. Форматы csv, xlsx, json, tsv; layout flat или relational. Scope: corpus_id, table_filter, collection, project_id, paper_ids, rows, query. До 50 тыс. фактов экспорт синхронный, больше — 202 с `export_id`, затем `GET /exports/{id}` и событие `export.ready`. Если выгружать нечего — 422 `nothing_to_export`. | should | spec/04 §4.16 |
| REQ-114 | Паспорт `GET /passport?collection=&profile_id=&format=json\|md\|xlsx` генерируется из профиля и конфигурации: таблицы, поля, типы, единицы, правила, статусы, сопоставимость. CLI кладёт `passport.json` и `passport.md` в `_meta/`. | must | spec/04 §4.17; spec/13 §13.6; TASKS BE п.7 |
| REQ-115 | `POST /extract` (multipart archive + domain + profile_id) возвращает 202 `{run_id}`. | should | spec/04 §4.16 |

### 1.13. Оценка качества

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-116 | В оценку идут только 11 `metric_name`: PCE, Voc, Jsc, FF, Rs, Rsh, HI, Jsc_EQE, stabilized_PCE, T80, retained_PCE. | must | spec/14 §14.7; Коорд. п.7 |
| REQ-117 | Сопоставление по §14.7: publication_id + metric_name + прибор (device_label → device_id эталона, метка после casefold и схлопывания пробелов) + aggregation + scan_direction через measurement_id + time_point. Значение сравнивается по `normalized_value` с допуском на последний знак. Отдельно считается точность source_page, нормализации, aggregation, result_origin, extraction_status, in_scope. Замечания сравниваются по check_name и множеству record_ids. | must | spec/14 §14.7 (решение К-3) |
| REQ-118 | `GET /checks/accuracy?collection=&corpus_id=` отдаёт форму ответа из spec/04 §4.13 (total, normalized, linked, by_param, by_source, by_paper, errors) с подсчётом по REQ-117. Если эталона нет — 404. Загрузка эталона — `POST /admin/gold`. | should | spec/04 §4.13; BACKEND §1А.1 |
| REQ-119 | `faktura-eval` пишет ML-2. Бэк вызывает его через адаптер `EvalAdapter`. До его появления работает собственный регрессионный модуль сравнения с dev_gold по REQ-116–117 для тестов конвейера. | should | TASKS ML-2 п.4; Коорд. п.1 |
| REQ-120 | **Приёмочный roundtrip:** dev_gold → мок → БД → нормализация → проверки → экспорт воспроизводит dev_gold. Допускаются отличия в порядке строк, нумерации `issue_id`, `\r\n` и задокументированных нормализациях (К-7). `check_submission.py out dev` выдаёт 0 ошибок. | must | TASKS BE п.9; анализ данных DATA-25 |

### 1.14. Вторичные API (P2: после CLI, проверок, экспорта и P1)

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-121 | Словарь: `GET /dictionary`, `POST/DELETE` синонимов (встроенные не удаляются — 403; дубль — 409 `synonym_exists`), `POST /dictionary/units`. После правки автоматически запускаются renormalize и recheck. | should | spec/04 §4.14; TASKS BE п.6 |
| REQ-122 | Сравнение `GET /data/compare?ids=` (2–3 статьи, `comparable{ok,reasons[]}`), `GET /data/rows`, `/data/filter-options`, CRUD `/views`. | should | spec/04 §4.11; TASKS BE п.6 |
| REQ-123 | Граф: `GET /graph?collection=`, `/graph/edges.csv`, `/entities/{id}/papers`; рёбра cites, shares_material, shares_method, external. Источник рёбер — ML-2 `/ml/links`; до него рёбра строятся из общих материалов и методов. | should | spec/04 §4.12; TASKS BE п.6 |
| REQ-124 | Проекты и чаты (`/projects`, `/chats`, `/chats/ask` и turns по SSE). Режим по умолчанию — базовый, без RAG: поиск по таблице фактов и шаблонный ответ. Каждое число в ответе проверяется на соответствие `fact_id`. | could | spec/04 §4.4–4.5; BACKEND «Остаётся открытым» п.6 |
| REQ-125 | `GET /search` (⌘K), `GET /collections`, `/collections/{id}/stats`, `GET/PATCH /me`, `/me/stats`, `POST /papers` (одиночная загрузка). | could | spec/04 §4.2–4.3, §4.6, §4.19 |

### 1.15. Нефункциональные требования

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-126 | Время ответа: `GET /papers/{id}`, `/facts` и `/issues` статьи — до 200 мс; `/data/rows` — до 500 мс; `/facts/table` (100 строк) — до 300 мс; `/corpus/overview` — до 100 мс; проверки статьи — до 100 мс. | should | BACKEND §8.6; spec/13 §13.7–13.8 |
| REQ-127 | Секреты (ключ роутера, JWT-секрет) берутся из окружения и маскируются в логах. | must | spec/07 §7.0.1 |
| REQ-128 | Юнит-тесты без внешних сервисов. Интеграционные тесты на Postgres и MinIO — опционально, в docker compose или testcontainers. | must | Коорд. п.5 |

### 1.16. Задел (не реализуется в волне 1)

| ID | Требование | Пр. | Источник |
|---|---|---|---|
| REQ-129 | OCR не делается. Поля `ocr_*` принимаются как необязательные. Статус `needs_ocr`; правило `ocr_low_confidence` в CSV не попадает. Файл, у которого больше 50 % страниц — сканы: `failed`/`pdf_no_text`, но строка в publications остаётся. | could | spec/11; Коорд. |
| REQ-130 | RAG (область 2): только задел модели. Поле `collection=rag` и `domain=rag` в контрактах сохраняются; таблицы datasets, tasks, experiments, rag_configs, metric_results описаны в spec/03 §3.7. | could | spec/12; spec/03 §3.7; BACKEND «Остаётся открытым» п.2; Коорд. |

### 1.17. Порядок реализации

- **P0** — CLI, конвейер с моком, нормализация, проверки бэка, санитайзер, экспорт submission, паспорт, roundtrip-тест. Требования REQ-001…REQ-069 и REQ-112, REQ-114, REQ-116, REQ-117, REQ-120.
- **P1** — хранение Postgres+MinIO, соглашения API, вход, корпус и прогоны, SSE, таблица фактов, статья, факты, замечания, журнал, health.
- **P2** — словарь, сравнение, граф, accuracy, история и undo в полном объёме, экспорт для людей, затем чат, поиск и проекты.

Минимум к сдаче по TASKS — этапы 1–6, 9 и 10. Для BE это этапы 5, 6 и 10.

---

## 2. Противоречия и их разрешение

| № | Противоречие | Решение | Обоснование |
|---|---|---|---|
| К-1 | Контракт ML-1 описан по-разному: в spec/07 §7.0.1 это строки 7 таблиц с `_bbox`/`_confidence`, в §7.2 A4–A6 и spec/10 §10.1 шаг 8 — `ref`, `field`, `device_ref`, `source_type`. Словари A5/A6 устарели (`aggregation` max/delta, нет `average`, другие layer_function). | **Канон — §7.0.1.** Форматы A4–A6 и §10.1 не поддерживаем, пока ML-1 сам их не пришлёт. Адаптер изолирует формат. | В spec/07 §7.2 прямо сказано: «Главный контракт извлечения — §7.0.1 … где расходятся, верны §7.0.1 и §14». BACKEND «Остаётся открытым» п.1. |
| К-2 | bbox: в §7.0.1 это `_bbox` `[x0,y0,x1,y1]` в пунктах PDF, начало координат «уточнить»; в §7.2 — доли `{x,y,w,h}`. | От ML-1 принимаем пункты (§7.0.1). Наружу (API) отдаём `rects` в долях от левого верхнего угла (§7.2, §4.7). Начало координат — параметр адаптера, по умолчанию левый верхний угол. В тестах проверять переворот оси Y. | §7.0.1: «Бэк переводит в `rects` в долях». |
| К-3 | Правило «факт верен» в оценке: spec/04 §4.13 (paper_id + row_id + param + source_kind, совпадение raw, bbox ≥ 50 %) против spec/14 §14.7 (publication_id + metric_name + device_label + aggregation + scan_direction + time_point, normalized с допуском). | **Сопоставление по §14.7.** От §4.13 берём только форму ответа. Метрика `linked` (bbox ≥ 50 %) считается, только если у эталона есть bbox, иначе `null`. | BACKEND §1А.1 отсылает к §14.7; сам §4.13 говорит «считает `faktura-eval` (§14.7)»; Коорд. п.7. |
| К-4 | `fact_id`: в spec/03 §3.5 «для показателя = `result_id`», в BACKEND §0 id непрозрачные (`fct_…`). При этом `result_id` уникален только внутри статьи. | `fact_id` в API — непрозрачный глобальный id (`fct_…`). `result_id` и `publication_id` — отдельные поля факта. | Главный документ — BACKEND §0. Иначе id факта не уникален. |
| К-5 | `added_by`: в spec/03 §3.5 `model\|user`, в spec/04 §4.8–4.9 `auto\|manual`. | В API и хранении используем `auto\|manual`: это контракт, который читает фронт. Фиксируется в api_contract.md. | Фронт работает по API (spec/04). В BACKEND значения не заданы. |
| К-6 | `issue_id`: в spec/05 §5.0 `I-{check_name}-{result_id}`, в примере spec/04 `pap_x:check:row`. Анализ рисков предлагает хеш от record_ids. У правила с несколькими записями (`jv_consistency` — четыре записи) не определено, какой `result_id` брать. | Внутренний ключ `I-{check_name}-{result_id якоря}`, где якорь — первая запись в упорядоченных `record_ids` (для `jv_consistency` — PCE). Если `record_ids` пусты: `I-{check_name}-{sha8(параметров находки)}`. Ключ уникален в статье. В API `issue_id` непрозрачный глобальный: `iss_{paper}_{ключ}`. В выгрузке — `I1…` по порядку страниц. | Сохраняется формат §5.0 (главная цель — переживать пересчёт) и уникальность. |
| К-7 | `devices.encapsulation`: спека (spec/14 §14.2, spec/03, spec/07) требует bool, в dev_gold у 4 из 89 строк стоит `unencapsulated`. | Тип bool. Нормализатор переводит `unencapsulated`/`without encapsulation` в `false`, `encapsulated` в `true`; непонятное значение — пусто, исходник в `note`. В roundtrip-тесте это задокументированное отличие (4 ячейки). | Спека главнее эталона в типах полей; скрипт это поле не проверяет. Отвечаем на вопрос анализа данных. |
| К-8 | В примере spec/10 §10.1 шаг 12 `by_rule` содержит `check_name:"physics"`. | Ошибка примера. `physics` — только группа интерфейса (`plugin`, spec/03 §3.8). В `by_rule` пишутся только `check_name` из §5.2. | BACKEND «Закрыто архивом»: «прежние имена прототипа (`physics`…) остались только как группы интерфейса». |
| К-9 | В примере паспорта (spec/04 §4.17) 9 metric_name, нет stabilized_PCE и retained_PCE. | Паспорт генерируется из профиля и содержит все 11 оцениваемых метрик. | spec/14 §14.3, §14.7. |
| К-10 | Пути: в спеке и TASKS `docs/case5/…`, у нас `data/caseB_kejs5/…`. | Корень кейса задаётся конфигурацией (`FAKTURA_CASE_DIR`) и флагом `--check`. Скрипт идентичен (SHA проверен). | Раздел 0. |
| К-11 | Число dev-статей: «21 PDF (D001…D022)». | 21 файл, D011 нет. Нельзя рассчитывать, что номера идут подряд. | Проверено по каталогу. |
| К-12 | Приоритет графа, сравнения, словаря и истории: в функциональном анализе could или P2, в TASKS BE п.6 они перечислены как API для интерфейса. В анализе рисков история стоит в P2 (NFR-17), но там же сказано закладывать её сразу (NFR-20). | Граф, сравнение и словарь — should, P2. **Журнал Action — must, P1, с первого изменяющего эндпоинта.** Эндпоинты undo — P1 в конце. | TASKS BE п.6 включает их в объём BE; BACKEND §0 требует `action_id` в каждом изменяющем запросе. |
| К-13 | Кто пишет нормализацию: спека — ML-2 пишет `faktura_norm`, бэк исполняет; координатор — бэк делает модуль с совместимым интерфейсом. | Бэк пишет `faktura.norm` по spec/06 с интерфейсом `faktura_norm` и подключает через адаптер. Когда ML-2 поставит пакет, переключаемся и сверяем по регрессии на dev_gold. Нужно предупредить ML-2, чтобы не делать работу дважды (В-9). | Коорд. п.1 приоритетнее; интерфейс совместим со спекой. |
| К-14 | Строка publications для упавшего файла. В спеке не определено. Функциональный анализ предлагает `in_scope=false` с причиной, анализ рисков — только publication_id. | Пишем строку с `publication_id`, остальные поля пустые, включая `in_scope`: скрипт пустое значение допускает. Ошибка уходит в `_meta/files.csv` и `errors.log`, код выхода 2. `in_scope=false` не ставим: это утверждение о содержании статьи, и оно портит точность in_scope. Метаданные из PDF не подставляем (это было бы «по догадке»). | spec/14 §14.2 (строка на каждую статью), spec/06 §6.4. |
| К-15 | Мок отдаёт эталонные замечания, включая `jv_consistency`, и бэк посчитает их ещё раз. | Слияние по `check_name` и множеству `record_ids` (REQ-056). У мока есть режим `issues=all\|ml_only`, по умолчанию `all`. | Избегаем дублей в issues.csv и в оценке по замечаниям. |
| К-16 | Как оценивают: только 7 CSV по test или запуск на скрытых PDF (вопрос анализа рисков). | И то и другое. Сдаём 7 CSV по test (spec/10 §10.1, TASKS этап 12), систему прогонят на новых PDF (spec/13 вступление и §13.6). Значит, CLI запускается автономно (REQ-011). | spec/13: «оценивают … по независимым проверочным примерам — то есть систему прогонят на новых PDF». |
| К-17 | Ретраи в CLI: по спеке 30 с / 2 мин / 10 мин; анализ рисков предлагает 5 / 15 / 30 с. | Воркер API использует значения спеки. В CLI есть флаг `--retry-delays`, по умолчанию `5,15,30`. | Иначе один сбойный файл держит CLI-прогон больше 12 минут. Не противоречит контракту ML. |
| К-18 | Число эндпоинтов: 121 в инвентаризации, около 115 в анализе рисков. | Реестр — таблица spec/04 §4.22. | Других расхождений по составу нет. |
| К-19 | Лимиты загрузки: `POST /papers` — 20 файлов и 200 МБ, `POST /corpora/{id}/files` — 200 файлов и 1 ГБ. | Это не противоречие: это два разных эндпоинта, лимиты в конфиге задаются раздельно. | BACKEND §8.1; spec/13 §13.4. |
| К-20 | Измерения без `measurement_id`: по спеке primary «при одних условиях», в эталоне есть один primary без `measurement_id`. | Для primary `measurement_id` желателен, но не обязателен. Скрипт предупреждает только об отсутствии `device_id`. Пустой `measurement_id` не заполняем по догадке. | spec/06 §6.4; check_submission.py. |
| К-21 | `light_intensity = 0` в эталоне. | Пишем как есть, не «чиним». Правило `jv_consistency` при Pin ≤ 0 не считается. | Не восстанавливать по догадке; без деления на ноль. |

---

## 3. Риски и меры

| № | Риск | Влияние | Мера |
|---|---|---|---|
| Р-1 | **Качество сдачи целиком зависит от ML-1.** Мок отдаёт для test пустой ответ: 90 строк publications только с id, precision и recall около 0. Формат проверку пройдёт, оценка — нет. | Критическое, вне BE | Зафиксировать срок поставки JSON и контейнера ML-1 (вопрос Б-1). Конвейер строить так, чтобы реальный ML-1 подключался одной настройкой (`--extract-url`). Держать `import-raw`, чтобы принять результаты ML-1 файлами в любой момент. |
| Р-2 | Контракт ML-1 предварительный, точный JSON может отличаться. | Высокое | Адаптер, pydantic-схема, отбрасывание битых записей с warning, санитайзер словарей. Контрактный тест на примере §7.0.1. Когда придёт JSON, сверить в первую очередь. |
| Р-3 | Ошибки валидатора на реальном ML: значения вне словаря, нечисла, висячие ссылки, повторы `result_id`. | Высокое (код 3, сдача не принята) | Санитайзер сдачи (REQ-018) и собственный строгий валидатор (REQ-021). Fuzz-тест санитайзера на испорченных данных dev_gold. |
| Р-4 | Ложные и задвоенные замечания. Простая формула `jv_consistency` даёт 2 ложных срабатывания на D021 (эффект округления) и пропускает D004; эталонные замечания мока совпадают с правилами бэка. | Среднее (оценка по issues) | Допуск на округление, перевод в `rounding`, слияние находок (REQ-051, REQ-056), калибровка на dev_gold. |
| Р-5 | Ошибки нормализации: float, доли без единицы, смешанные единицы. | Среднее | Decimal, правило «шкала известна», регрессия на 74 строках dev_gold (REQ-048). |
| Р-6 | Цитаты. Валидатор проверяет только около трети фрагментов, pypdf видит текст иначе, чем PyMuPDF. На Windows вывод скрипта в cp1251 ломает кодировку. | Среднее | Для `quote_found` — та же `norm()` и pypdf; `PYTHONUTF8=1`; разбор отчёта; политика «Table N, строка X» только для настоящих таблиц. |
| Р-7 | Время и стоимость прогона 90 PDF через LLM: 45–90 минут, в худшем случае несколько часов. | Среднее | Кеш сырых ответов по sha256 и model_version, возобновление прогона, `renormalize`/`recheck` без ML, короткие ретраи в CLI. |
| Р-8 | Объём API (около 115 эндпоинтов) на одного разработчика BE: можно не успеть к P0. | Высокое | Приоритеты P0/P1/P2. Для P2 — заглушки с корректной формой ответа. Журнал закладывается сразу. Контракт фиксируется в api_contract.md, чтобы фронт не ждал. |
| Р-9 | Две СУБД: SQLite и Postgres по-разному работают с JSON, полнотекстовым поиском и блокировками. | Среднее | ORM (SQLAlchemy 2), переносимые JSON-типы, поиск за интерфейсом, один писатель и транзакция на статью, WAL. CI-прогон тестов на обеих СУБД (Postgres — опционально). |
| Р-10 | Каскадное переименование id (повторы `result_id` от ML) ломает `duplicate_of` и `record_ids`. | Среднее | Переименование выполняется одной функцией с картой старый→новый и применяется ко всем ссылкам. Тест на это. |
| Р-11 | Мок не даёт bbox, подсветку на фронте нечем показать. Начало координат `_bbox` не согласовано, возможен переворот оси Y. | Низкое для сдачи, среднее для фронта | Запасной якорь через `search_for` (REQ-067), параметр начала координат, тест на известной странице. |
| Р-12 | Безопасность импорта: zip-slip, zip-бомба, SSRF при загрузке по URL. | Среднее | Лимиты и нормализация путей (REQ-079). Импорт по URL и tus — во второй волне; до этого 400 `validation_error` с понятным текстом. |
| Р-13 | dev_gold мал и смещён: T80 0 строк, HI 3, на D021 приходится 122 строки. Регрессия на моке даёт «ложные 100 %». | Среднее | Метрики по редким показателям помечать «мало данных». Регрессия на моке проверяет конвейер, а не качество. Качество меряет `faktura-eval` на реальном ML-1. |
| Р-14 | Упаковка для скрытых примеров: нужен контейнер ML-1, ключ роутера и интернет. | Среднее | CLI в docker-образе. Compose поднимает ML-1, ключ передаётся переменной окружения. Инструкция запуска в README. |
| Р-15 | Коллизия `publication_id`: одинаковые имена файлов в разных подпапках или в ZIP. | Низкое (в test имена уникальны) | Повторное имя: второй файл получает `rejected` с кодом `duplicate_name` и попадает в `files.csv`. Суффиксы не добавляем: id обязан совпадать с именем файла. |
| Р-16 | Неточности спеки (К-5, К-8, К-9) путают фронт. | Низкое | Решения фиксируются в api_contract.md со ссылками на разделы. |
| Р-17 | Двойная работа с ML-2 по нормализации и оценке. | Низкое | Интерфейсы `faktura_norm` и `faktura-eval` за адаптерами. Согласовать владение (В-9, В-10). |

---

## 4. Вопросы

### 4.1. Блокирующие

Архитектуру можно проектировать без ответа: адаптеры покрывают все варианты. Но без ответа нельзя зафиксировать объём работ волны.

| № | Вопрос | Почему блокирует | Кому |
|---|---|---|---|
| Б-1 | **Когда ML-1 пришлёт итоговый JSON `/ml/extract` и рабочий контейнер? Что сдаём по test, если ML-1 не успеет?** Варианты: (а) валидный, но почти пустой набор; (б) BE делает **не-LLM запасной экстрактор** на правилах (таблицы PyMuPDF плюс словарь синонимов) как ещё одну реализацию `ExtractAdapter`. Координатор запретил BE писать LLM-извлечение, а про запасной вариант без LLM решения нет. | Вариант (б) — отдельный крупный модуль в плане волны. | Координатор, ML-1 |

### 4.2. Неблокирующие (с принятым умолчанием)

| № | Вопрос | Умолчание |
|---|---|---|
| В-1 | Единицы и начало координат `_bbox`. | Пункты PDF, левый верхний угол. Параметр адаптера. |
| В-2 | Строка publications для упавшего PDF. | Только `publication_id`, остальное пусто (К-14). |
| В-3 | Тип `encapsulation`. | bool со словарём синонимов (К-7). |
| В-4 | Выгружать ли `absent` и находки ML с `confidence < 0,5`. | Нет: включается профилем или видно только в UI (BACKEND «Остаётся открытым» п.4–5). |
| В-5 | Перевод строки и BOM в CSV сдачи. | BOM и `\r\n` модуля csv. |
| В-6 | Нестандартные метрики (Voc_deficit, Jmpp, …). | Выгружаем, если их вернул ML. |
| В-7 | `fact_id` и `issue_id` в API. | Непрозрачные глобальные id (К-4, К-6). |
| В-8 | `added_by`. | `auto\|manual` (К-5). |
| В-9 | Нормализация. | Пишет BE с интерфейсом `faktura_norm`. ML-2 предупредить, чтобы не дублировать. |
| В-10 | `faktura-eval`. | Пишет ML-2. До его появления BE держит свой регрессионный модуль (REQ-119). |
| В-11 | Режим CLI. | Локальный, без входа: SQLite во временной папке или `--db`, файлы на диске. Удалённый — `--server`/`--api-key`. |
| В-12 | Нужен ли ML-2 `/ml/parse` в волне 1. | Нет. Локальный `ParseAdapter` на PyMuPDF (REQ-063). |
| В-13 | Автосвязывание повторов. | Включено для реального ML, выключено для мока. |
| В-14 | Ретраи в CLI. | `--retry-delays 5,15,30`; воркер API работает по значениям спеки (К-17). |
| В-15 | Объём API в волне 1. | P0 и P1 полностью. P2 — заглушки с корректной формой, реализация по мере готовности. Фиксируется в api_contract.md. |
| В-16 | Импорт по URL, tus, `server_path`. | Во второй волне. До этого 400 `validation_error` «Пока не поддерживается». |
| В-17 | Коллизия имён файлов. | `rejected` с `duplicate_name`, без суффиксов (Р-15). |
| В-18 | Сравнение `device_label` в оценке. | casefold и схлопывание пробелов. `record_ids` сравниваются как множество. |
| В-19 | Порядок строк в CSV. | По `publication_id`, затем по исходному порядку ML. |
| В-20 | Ожидаемая доля `in_scope=false` в test. | Ничего не предполагаем: решает ML-1, в dev это 2 из 21. |
| В-21 | Остальные открытые пункты BACKEND «Остаётся открытым» п.1–13. | Принимаются по Коорд. п.6: refresh в cookie, `reuse_existing=true`, перенос правок, базовый чат без RAG, личное и общее по п.8. |
