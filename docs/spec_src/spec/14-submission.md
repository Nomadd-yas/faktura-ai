# 14. Формат сдачи — обе области

Две области сдаются отдельно, каждая — **своими 7 CSV** и своим `check_submission.py`. Выход CLI и экспорт: `out/solar/` и `out/rag/`. Раздел 14.A (14.1–14.7) — область 1, раздел 14.B — область 2.

# 14.A. Область 1 (солнечные элементы)

[← К оглавлению](../BACKEND_SPEC.md)

Источник — архив организаторов (кейс 5), копия без PDF в `docs/case5/`:
- `README_участникам.md` — состав материалов;
- `submission_template/` — 7 пустых CSV и `README.md` (поля, словари, правила);
- `tools/check_submission.py` — проверка перед сдачей;
- `dev_gold/` — эталон по 21 статье dev (659 результатов, 19 статей в области, 2 вне области).

Корпус: `dev/` — 21 PDF (`D001`…`D022`) с эталоном для разработки; `test/` — 90 PDF (`P001`…`P090`), **по ним идёт оценка**. Сдаётся набор по `test/` (по `dev/` — не обязательно).

Экспорт датасета (`POST /exports` с `format: "submission"`, `GET /runs/{id}/submission.zip`) и выход CLI (`faktura extract --domain solar`) выдают **ровно эти 7 таблиц** (в папке `solar/`) и обязаны проходить `check_submission.py` без ошибок.

## 14.1. Семь таблиц

Колонки — ровно как в шаблоне, в этом порядке (порядок для скрипта не важен, но держим как в шаблоне).

| Файл | Строка — это | Ключ | Колонки |
|---|---|---|---|
| `publications.csv` | статья корпуса (в т. ч. исключённая) | `publication_id` | `publication_id, title, year, DOI, authors, in_scope, exclusion_reason` |
| `materials.csv` | материал поглотителя | `publication_id` + `material_id` | `publication_id, material_id, material_name_raw, formula_normalized, material_class, dimensionality, additives, bandgap_value, bandgap_unit, bandgap_method, note` |
| `devices.csv` | прибор | `publication_id` + `device_id` | `publication_id, device_id, absorber_material_id, device_label, device_role, device_type, architecture, substrate_type, active_area, area_unit, area_definition, encapsulation, note` |
| `layers.csv` | слой прибора | `publication_id` + `layer_id` | `publication_id, layer_id, device_id, layer_order, layer_function, layer_material, layer_thickness, thickness_unit, deposition_method, annealing_temperature, annealing_time, atmosphere, note` |
| `measurements.csv` | условия измерения | `publication_id` + `measurement_id` | `publication_id, measurement_id, device_id, measurement_type, spectrum, light_intensity, intensity_unit, scan_direction, scan_rate, mask_used, certified, certification_lab, temperature, stability_protocol, stress_conditions, note` |
| `results.csv` | одно значение одного показателя | `publication_id` + `result_id` | `publication_id, result_id, device_id, measurement_id, material_id, metric_name, metric_value, metric_unit, aggregation, spread_value, n_devices, time_point, time_unit, raw_value, raw_unit, normalized_value, normalized_unit, result_origin, cited_publication_id, source_page, source_section, source_table, source_figure, source_fragment, extraction_status, duplicate_of, note` |
| `issues.csv` | замечание проверки | `publication_id` + `issue_id` | `publication_id, issue_id, record_ids, check_name, description, source_page, source_fragment` |

Связи (проверяются скриптом):
- `devices.absorber_material_id` → `materials.material_id`;
- `layers.device_id` → `devices`;
- `measurements.device_id` → `devices` (**несколько через `;`**: одно измерение на несколько приборов, `DV1;DV2;DV3`);
- `results.device_id`, `measurement_id`, `material_id` → соответствующие таблицы;
- `results.duplicate_of` → `results.result_id`;
- `issues.record_ids` → `results.result_id` (через `;`);
- каждый `publication_id` всех таблиц есть в `publications`.

## 14.2. Соглашения

| Правило | Как делает бэк |
|---|---|
| Кодировка UTF-8, разделитель `,`, десятичная точка, текст с запятыми — в кавычках по правилам CSV | `csv.writer` с `QUOTE_MINIMAL`; файлы шаблона идут с BOM, скрипт читает `utf-8-sig` — пишем **с BOM**, как шаблон |
| **Пустая ячейка = в статье этого нет** | никаких `null`, `NA`, `—`, `0` вместо пустого; не восстанавливаем по догадке |
| Логические поля — `true` / `false` | `in_scope`, `mask_used`, `certified` (и `encapsulation` — так же) |
| Несколько значений — через `;` | `authors`, `measurements.device_id`, `issues.record_ids`, `additives` |
| `publication_id` — имя файла без расширения | `D007`, `P042` (из `CorpusFile.name`) |
| Остальные id — произвольные, уникальные в статье | `M1`, `DV1`, `DV1-L1`, `MS1`, `R001`, `R008-1` (повтор), `I1` |
| `source_page` — номер страницы **в PDF-файле**, с 1 | не номер журнальной страницы |
| `source_fragment` — **короткая дословная цитата** со страницы `source_page` | скрипт нормализует (нижний регистр, только `a-z0-9.%`, лигатуры) и ищет на странице через pypdf. Для значения из таблицы допустимо «Table 2, строка B» (скрипт такие не проверяет — начинаются с `Table`/`TABLE`/`Supplementary`) |
| `note` — свободное примечание | если формулировка статьи не ложится в словарь: в поле словаря — ближайшее значение или пусто, исходная формулировка — в `note` |
| Строка в `publications` для **каждой** статьи | в т. ч. исключённой: `in_scope=false` + `exclusion_reason`; у исключённой в остальных таблицах строк нет |

## 14.3. Словари (1:1 с шаблоном и скриптом)

| Таблица.поле | Допустимые значения |
|---|---|
| `materials.material_class` | `perovskite`, `CIGS`, `CdTe`, `kesterite`, `Sb2Se3`, `thin-film Si`, `other` |
| `materials.dimensionality` | `3D`, `2D`, `2D-3D` |
| `materials.bandgap_method` | `Tauc`, `EQE`, `PL`, `cited` |
| `materials.bandgap_unit` | `eV` |
| `materials.formula_normalized` | для перовскита — вид A-B-X с раскрытыми MA, FA, Cs |
| `devices.device_role` | `control`, `target`, `record` |
| `devices.device_type` | `cell`, `minimodule`, `module` |
| `devices.architecture` | `n-i-p`, `p-i-n`, `substrate`, `superstrate` |
| `devices.substrate_type` | `rigid`, `flexible` |
| `devices.area_definition` | `designated illumination`, `aperture`, `total` |
| `devices.area_unit` | `cm2` |
| `layers.layer_function` | `front_contact`, `ETL`, `absorber`, `passivation`, `HTL`, `interlayer`, `recombination`, `back_contact` |
| `layers.thickness_unit` | `nm` |
| `measurements.measurement_type` | `J-V`, `MPP tracking`, `EQE`, `stability` |
| `measurements.scan_direction` | `reverse`, `forward`, `average` |
| `measurements.stability_protocol` | `ISOS-D`, `ISOS-L`, `ISOS-T`, `other` |
| `measurements.mask_used`, `certified` | `true`, `false` |
| `publications.in_scope` | `true`, `false` |
| `results.metric_name` (оцениваемые) | `PCE`, `Voc`, `Jsc`, `FF`, `Rs`, `Rsh`, `HI`, `Jsc_EQE`, `stabilized_PCE`, `T80`, `retained_PCE`; прочие (`Voc_deficit`, `Jmpp`, `Vmpp`, `AVT`, `CRI`, `LUE`, `bifaciality_factor`…) — можно, в оценке не участвуют |
| `results.normalized_unit` = `metric_unit` | PCE, FF, HI, retained_PCE, stabilized_PCE — `%`; Voc — `V`; Jsc, Jsc_EQE — `mA/cm2`; Rs, Rsh — `Ohm*cm2`; T80 — `h` |
| `results.aggregation` | `champion`, `mean`, `median`; **пусто**, если в статье не сказано |
| `results.result_origin` | `primary`, `cited` |
| `results.extraction_status` | `extracted`, `present_not_extractable`, `absent` |

Правила слоёв: подложка (стекло, фольга) в `layers` **не пишется** — первым идёт контакт на подложке; CdS и аналогичные n-слои → `ETL`; металлическая сетка → `front_contact`; просветляющее покрытие и прочие → `interlayer`; исходное название — в `note`. Температура отжига — в °C («500 °C»), время — в единицах статьи с явной единицей («10 min»).

Правила измерений: прямой и обратный ход — **разные** `measurements`; `light_intensity` + `intensity_unit` как в статье (`100`, `mW/cm2`; `1000`, `W/m2`); темновая ВАХ — `J-V` с примечанием; если протокол ISOS не назван — `stability_protocol=other`, условия — в `stress_conditions`.

## 14.4. Правила `results.csv`

- **Одна строка — один показатель для одного прибора при одних условиях** (включая направление развёртки и момент времени). Лучший прибор и среднее по партии — **разные строки**.
- `raw_value`, `raw_unit` — дословно как в статье: `0.49` + `fraction`, `1123` + `mV`, `12.89` + `%`. `normalized_value`, `normalized_unit` — в целевой единице; `metric_value`, `metric_unit` — **совпадают** с ними.
- `cited`: `cited_publication_id` — ссылка, как в статье (`[12]`, `[19–21]`, `ref. [11]`, «Bai et al., 2018; …»); `device_id` и `measurement_id` **пустые**.
- `present_not_extractable` — значение есть, но извлечь нельзя (только в supplementary, только на графике): `metric_value`, `raw_*`, `normalized_*` пустые, `source_page` и `source_fragment` указывают, где сказано («Supplementary Table S2»).
- **Повторы внутри статьи — `duplicate_of`.** Если то же значение того же прибора упомянуто ещё раз (в аннотации, тексте, другой таблице), повтор — **отдельная строка со своим `source_*`**, в `duplicate_of` — `result_id` основной записи; у основной поле пустое. Пример: `R003` (рис. 10, стр. 13) и `R004` (аннотация, стр. 1, `duplicate_of=R003`). В эталоне dev таких строк 48.
- Время стабильности: `time_point` + `time_unit` (`h`, `day` — как в статье; для `T80` нормализованное значение в `h`).

## 14.5. Как получается выгрузка из модели

| Таблица сдачи | Откуда в модели (§3) |
|---|---|
| `publications` | `Paper` (`publication_id` = имя файла) + `in_scope`, `exclusion_reason` от ML |
| `materials`, `devices`, `layers`, `measurements` | одноимённые сущности ТЗ, поля 1:1 (§3.6) |
| `results` | `result` (§3.6) — строится из фактов: **принятые и новые** (отклонённые человеком — не выгружаются, ссылки на них удаляются из `duplicate_of` и `issues.record_ids`), с действующим значением: если человек исправил, `normalized_value`/`metric_value` = исправленное, `raw_value` остаётся из статьи, в `note` — «Исправлено вручную: было …»; можно выгрузить только выбранные значения (§15.7) |
| `issues` | `Issue` со `status` `open` и `fixed`, `computed_by` любой |

Служебные поля API (`confidence`, `bbox`, `review_status`, `corrected_value`, `added_by`, `anchor_id`…) в сдачу не идут. Ручные факты (выделение человеком) выгружаются как обычные `extracted`-строки со своей цитатой.

## 14.6. Проверка

```
python -m pip install pypdf
python docs/case5/tools/check_submission.py ./out/solar test          # test/ — папка с P001…P090.pdf
python docs/case5/tools/check_submission.py docs/case5/dev_gold dev   # пример на эталоне
```
- `ОШИБКА` (нет файла или колонки, значение вне словаря, не число в `metric_value`/`normalized_value`/`source_page`/`layer_order`/`active_area`/`bandgap_value`, битая ссылка, повтор `result_id`) — **сдача не принимается**: CLI завершается с кодом `3`, экспорт в API — `422 submission_invalid` с отчётом.
- `внимание` (собственный результат без `device_id`, `cited` без `cited_publication_id`, нет места извлечения, исключённая без причины, цитата не найдена на странице) — отчёт человеку; бэк дополнительно помечает такие записи в таблице фактов (фильтр «не прошло проверку цитаты»), чтобы их поправили.
- Бэк вызывает скрипт как есть (`subprocess`, версия из `docs/case5/tools/`), ничего в нём не меняя; его же логику цитат повторяет при сохранении факта (`quote_found: bool` у факта), чтобы ловить ненайденные цитаты сразу.
- CLI: `faktura extract --domain solar --in ./test --out ./out --check docs/case5/tools/check_submission.py`.
- API: `POST /exports {"format": "submission", "scope": {"corpus_id": "crp_test"}, "validate": true}` → ZIP с 7 CSV + `check_report.txt`, заголовок `X-Check-Status: ok|warnings|failed`.

## 14.7. Оценка по `dev_gold`

Скрипт оценки пишет ML-2 (см. [TASKS.md](../TASKS.md)); бэк показывает результат на экране «Точность извлечения» (`GET /checks/accuracy`).
- Учитываются **только** `metric_name` из списка условий (PCE, Voc, Jsc, FF, Rs, Rsh, HI, Jsc_EQE, stabilized_PCE, T80, retained_PCE); прочие игнорируются.
- Сопоставление записи с эталоном: `publication_id` + `metric_name` + прибор (по `device_label` → `device_id` эталона) + `aggregation` + направление развёртки (через `measurement_id`) + `time_point`; значение — `normalized_value` с допуском округления последнего знака; `duplicate_of`-строки — отдельно (повтор засчитывается, если основная найдена).
- Метрики: precision, recall, F1 по каждому `metric_name` и в целом; точность `source_page`; точность нормализации (`normalized_value` при верном `raw_value`); точность `aggregation`, `result_origin`, `extraction_status`; точность `in_scope` по статьям; совпадение `issues` по `check_name` + `record_ids`.
- Отчёт — JSON (для API) и Markdown; запускается после каждого изменения ML-1.

## 14.B. Область 2 — RAG

Источник — архив по области 2, копия без PDF в `docs/case5_area2/`:
- `README_участникам.md`, `CHECKLIST.txt` (111 PDF: страницы, байты, SHA-256);
- `submission_template/` — 7 пустых CSV, `README.md`, `schema.json` (`rag-viniti-adaptation-1.0`; адаптация организаторов, не официальный шаблон ВИНИТИ — рекомендуемый формат для машинного сравнения);
- `tools/check_submission.py` + `tools/requirements.txt` (`pypdf>=6,<8`, `PyMuPDF>=1.24`);
- `dev_gold/` — эталон всех 21 dev-статьи (D001–D021, все `in_scope=true`): 37 616 результатов, 4 733 конфигурации, 241 датасет, 90 задач, 142 эксперимента, 134 замечания; `coverage/D0xx_audit.json` (решения по каждой статье), `README_STATUS.md`.

Корпус: `dev/` — 21 PDF (D001–D021, без пропусков); `test/` — 90 PDF (P001–P090), **по ним оценка**. **Около 15 % PDF — синтетические сканы без текстового слоя**: в dev — D002, D013, D021 (3 из 21), в test — 14 из 90. Без OCR их значения не извлечь (§11). Эталон составлен по оригиналам; страницы и значения у сканов те же.

**Коды статей D001… совпадают с областью 1.** Статья везде идентифицируется парой **(`domain`, `publication_id`)**: в API — `/papers/{domain}/{pid}`, в выгрузке — отдельные папки `solar/` и `rag/`.

### 14.B.1. Семь таблиц (колонки — ровно `schema.json`, в этом порядке)

| Файл | Ключ | Колонки |
|---|---|---|
| `publications.csv` | `publication_id` | `publication_id, title, year, DOI, authors, in_scope, exclusion_reason` |
| `configurations.csv` | `config_id` | `publication_id, config_id, rag_variant, retrieval_method, retriever_model, embedding_model, embedding_version, chunking_method, chunk_size, chunk_overlap, chunk_unit, index_type, vector_store, top_k, reranker_model, generator_model, generator_version, prompt_strategy, context_strategy, note` |
| `datasets.csv` | `dataset_id` | `publication_id, dataset_id, dataset_name, dataset_version, language, data_split, scientific_domain, note` |
| `tasks.csv` | `task_id` | `publication_id, task_id, task_type, note` |
| `experiments.csv` | `experiment_id` | `publication_id, experiment_id, evaluation_protocol, baseline_name, hardware, latency_ms, tokens_per_query, sample_size, note` |
| `results.csv` | `result_id` | `publication_id, result_id, experiment_id, config_id, dataset_id, task_id, metric_group, metric_name, metric_variant, metric_direction, metric_k, metric_value, metric_unit, baseline_value, aggregation, sample_size, raw_value, raw_unit, normalized_value, normalized_unit, result_origin, cited_publication_id, source_page, source_section, source_table, source_figure, source_fragment, extraction_status, duplicate_of, note` |
| `issues.csv` | `issue_id` | `publication_id, issue_id, record_ids, check_name, description, source_page, source_fragment` |

Связи (проверяет скрипт): `results.experiment_id/config_id/dataset_id/task_id/duplicate_of` и `issues.record_ids` — на записи **той же публикации**; у статьи `in_scope=false` строк в остальных шести таблицах нет (ошибка); у `in_scope=true` `exclusion_reason` пуст (ошибка). Поля модели предметной области 1 (связь прибора с материалом) в RAG не переносятся.

### 14.B.2. Словари и типы
| Что | Правило скрипта |
|---|---|
| Закрытые словари | **только** `in_scope` (`true`/`false`), `result_origin` (`primary`/`cited`), `extraction_status` (`extracted`/`present_not_extractable`/`absent`) |
| `metric_direction` | рекомендуемые `higher_is_better`, `lower_is_better`, `target`; иное — **предупреждение**; неизвестно — пусто |
| Остальные поля | открытый текст по статье (`retrieval_method`, `task_type`, `metric_group`, `metric_unit`, `aggregation`, `check_name`) |
| Числа | `year`, `source_page` — целые > 0; `top_k`, `metric_k` — целые ≥ 0 (`0` — абляция без контекста); `chunk_size`, `chunk_overlap`, `latency_ms`, `tokens_per_query`, `sample_size` — ≥ 0 (`sample_size` целое); `metric_value`, `baseline_value`, `normalized_value` — конечные числа (не `NaN`, не бесконечность) |
| Id | `[A-Za-z0-9][A-Za-z0-9_-]*` |

### 14.B.3. Правила `results.csv` (из README шаблона)
- `metric_value`/`metric_unit` = `normalized_value`/`normalized_unit` (иначе ошибка).
- **Не пересчитывать** доли, проценты, BLEU/ROUGE и единицы времени по одному имени метрики — нужна установленная шкала и единица; исходная запись — в `raw_value`/`raw_unit` (§6.6).
- **`top_k` ≠ `metric_k`**: `top_k` — у конфигурации (сколько извлекается), `metric_k` — у результата (k метрики @k). Метрика вида `…@5` без `metric_k` — предупреждение.
- **`baseline_value`** — только для сопоставимых задачи, набора, определения метрики, шкалы и протокола.
- **Не декартово произведение**: нет искусственных строк `absent` на все пары метрика × конфигурация; пустые поля шаблона — не разметка `absent`.
- **Разные генераторы и абляции — разные конфигурации.**
- **Диапазон** параметра — числовое поле пусто, диапазон — в `note`.
- **Задержка и токены** не распространяются на все конфигурации эксперимента: отдельные результаты-метрики или разные эксперименты.
- `cited` — только если число перенесено из другой работы; воспроизведённый baseline — `primary`. У `cited` нужен `cited_publication_id` (иначе ошибка), у `primary` он пуст (иначе ошибка).
- `extracted` требует `raw_value`, `source_page` и хотя бы один из `source_fragment`/`source_table`/`source_figure`; без `metric_value` — предупреждение.
- `source_fragment` — **буквальная** цитата страницы; «таблица 3, строка X» — в `source_table`/`note`, не в цитату. Значение с графика — `source_figure` + способ чтения в `note` (визуальная проверка, предупреждение скрипта).
- `duplicate_of` — повтор того же значения из другого места; совпадают все связи и определение метрики, значение — с допустимым более грубым округлением; циклы запрещены. Разные числа — две записи + замечание.
- Значение за пределами шкалы метрики (Accuracy/Precision/Recall/F1/EM/Hit/MRR/NDCG при `fraction` или `%`) — **сохранить** и завести замечание (`metric_scale`).

### 14.B.4. Проверка `check_submission.py` (область 2)

```
python -m pip install -r docs/case5_area2/tools/requirements.txt
python docs/case5_area2/tools/check_submission.py ./out/rag test --json-report report.json
python docs/case5_area2/tools/check_submission.py ./out/rag test --ocr --ocr-language eng --tesseract /usr/bin/tesseract --json-report report.json
python docs/case5_area2/tools/check_submission.py docs/case5_area2/submission_template --template
```
| Код | Смысл | Что делает бэк |
|---|---|---|
| **0** | все выполненные проверки прошли | экспорт / CLI — успех |
| **1** | ошибки структуры, значений или связей | CLI — код `3`, экспорт — `422 submission_invalid` с отчётом |
| **2** | нужны проверки источников или предупреждения (цитата не найдена, источник только в рисунке, нет PDF-каталога, OCR не распознал) | успех с пометкой «требует проверки», `X-Check-Status: warnings`; список — в таблице результатов фильтром «цитата не подтверждена» |

- Поиск цитат: независимые извлечения **pypdf и PyMuPDF**, достаточно буквального совпадения в одном движке на той же странице; тексты движков и страниц не склеиваются. Нормализация **только** Unicode NFKC, мягкий перенос, пробелы, перенос слова через дефис на границе строк — **цифры, знаки, кириллица сохраняются** (в отличие от области 1, где скрипт оставляет только `a-z0-9.%`). Бэк для `quote_found` использует ту же нормализацию.
- `--ocr` (нужен Tesseract с языковыми данными): страницы без текста растрируются (`--ocr-dpi`, по умолчанию 300) и распознаются как дополнительный вариант текста той же страницы. Нераспознанная буквально цитата остаётся «непроверенной» (код 2), а не ошибкой. Бэк запускает скрипт с `--ocr` всегда, когда в разборе есть сканы.
- `--json-report` — бэк кладёт его в `_meta/check_report.json` и разбирает для интерфейса (`errors`, `warnings`, `quotes.requested/matched/unresolved`).
- Скрипт проверяет, что для каждого PDF каталога есть строка `publications`, и наоборот.
- Скрипт **не скорер**: полнота и правильность привязки считаются отдельно (скрипт ML-2 по `dev_gold`, §14.7).

### 14.B.5. Оценка по `dev_gold` (область 2)
Скрипт ML-2 `faktura-eval --domain rag --gold docs/case5_area2/dev_gold --pred ./out_dev/rag`: сопоставление по статье, `metric_name` + `metric_variant` + `metric_k`, конфигурации (по `rag_variant` + `generator_model`), набору (`dataset_name` + `data_split`), задаче, эксперименту и источнику; значение — `normalized_value` при той же единице. Метрики — precision, recall, F1 по строкам `results` (в целом, по `metric_group`, по статье — D014 даёт 76 % строк, поэтому отчёт и **в среднем по статьям**), точность `source_page`, буквальность цитат, отдельно по сканам (D002, D013, D021). Решения разметчиков по статьям — `dev_gold/coverage/*.json`.

