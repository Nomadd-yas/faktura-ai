# 9. Приложение: полный пример статьи

[← К оглавлению](../BACKEND_SPEC.md)

Для области 1 — ровно формат сдачи (7 CSV шаблона, §14). Для области 2 — внутренний реляционный формат (§4.16), формат сдачи будет уточнён. Данные — из демо-статей прототипа (`src/data.ts`: `mapbi`, `hybrid`), идентификаторы условные. Источник каждого поля — §3.

Что важно увидеть в примерах:
- `raw_value` хранится как в статье, `normalized_value` — в канонической единице ТЗ (FF «0,77» → 77 %);
- то, чего нет в статье, — `null` (DOI, площадь, направление развёртки, генератор RAG, язык);
- общие для всех образцов факты — `row_id: "*"`;
- замечания ссылаются на факты, записи ТЗ и якоря; исходные записи не изменены.

## 9.1. Солнечные элементы — «Перовскитный солнечный элемент с КПД 18,6 %» (формат сдачи)

Демо-статья прототипа `mapbi`; `publication_id` условный — `DEMO1` (в корпусе это имя файла: `D007`, `P042`). Ровно 7 таблиц шаблона (§14); пустая ячейка — в статье нет. Сверено по виду с `docs/case5/dev_gold/`.

**publications.csv**
```
publication_id,title,year,DOI,authors,in_scope,exclusion_reason
DEMO1,"Перовскитный солнечный элемент с КПД 18,6 %: стабильность и интерфейсы",2024,,Иванов А. В.; Котова Е. С.; et al.,true,
```

**materials.csv**
```
publication_id,material_id,material_name_raw,formula_normalized,material_class,dimensionality,additives,bandgap_value,bandgap_unit,bandgap_method,note
DEMO1,M1,MAPbI3,CH3NH3PbI3,perovskite,,,1.59,eV,,"Ширина зоны «определённая по спектрам поглощения» — метод (Tauc/PL) прямо не назван"
```

**devices.csv**
```
publication_id,device_id,absorber_material_id,device_label,device_role,device_type,architecture,substrate_type,active_area,area_unit,area_definition,encapsulation,note
DEMO1,DV-A,M1,A,,cell,n-i-p,rigid,,,,,
DEMO1,DV-B,M1,B,,cell,n-i-p,rigid,,,,,
DEMO1,DV-C,M1,C,,cell,n-i-p,rigid,,,,,
```
`device_role` пусто: статья не называет контроль и целевой; площадь не указана.

**layers.csv** (подложка-стекло не пишется; у DV-B и DV-C — те же 5 строк с `layer_id` `DV-B-L1`…)
```
publication_id,layer_id,device_id,layer_order,layer_function,layer_material,layer_thickness,thickness_unit,deposition_method,annealing_temperature,annealing_time,atmosphere,note
DEMO1,DV-A-L1,DV-A,1,front_contact,FTO,350,nm,,,,,
DEMO1,DV-A-L2,DV-A,2,ETL,SnO2,30,nm,,,,,
DEMO1,DV-A-L3,DV-A,3,absorber,MAPbI3,450,nm,,,,,«Толщина активного слоя во всех образцах — 450 нм»
DEMO1,DV-A-L4,DV-A,4,HTL,Spiro-MeOTAD,150,nm,,,,,
DEMO1,DV-A-L5,DV-A,5,back_contact,Au,80,nm,,,,,
```

**measurements.csv**
```
publication_id,measurement_id,device_id,measurement_type,spectrum,light_intensity,intensity_unit,scan_direction,scan_rate,mask_used,certified,certification_lab,temperature,stability_protocol,stress_conditions,note
DEMO1,MS1,DV-A;DV-B;DV-C,J-V,AM1.5G,100,mW/cm2,,,,,,25 °C,,,
```
Направление развёртки, маска, сертификация не указаны — пусто.

**results.csv**
```
publication_id,result_id,device_id,measurement_id,material_id,metric_name,metric_value,metric_unit,aggregation,spread_value,n_devices,time_point,time_unit,raw_value,raw_unit,normalized_value,normalized_unit,result_origin,cited_publication_id,source_page,source_section,source_table,source_figure,source_fragment,extraction_status,duplicate_of,note
DEMO1,R001,DV-A,MS1,M1,Voc,1.08,V,,,,,,1.08,V,1.08,V,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка A",extracted,,
DEMO1,R002,DV-A,MS1,M1,Jsc,22.9,mA/cm2,,,,,,22.9,mA/cm2,22.9,mA/cm2,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка A",extracted,,
DEMO1,R003,DV-A,MS1,M1,FF,77.0,%,,,,,,0.77,fraction,77.0,%,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка A",extracted,,
DEMO1,R004,DV-A,MS1,M1,PCE,19.0,%,,,,,,19.0,%,19.0,%,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка A",extracted,,
DEMO1,R005,DV-B,MS1,M1,Voc,1.10,V,,,,,,1.10,V,1.10,V,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка B",extracted,,
DEMO1,R006,DV-B,MS1,M1,Jsc,23.4,mA/cm2,,,,,,23.4,mA/cm2,23.4,mA/cm2,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка B",extracted,,
DEMO1,R007,DV-B,MS1,M1,FF,79.0,%,,,,,,0.79,fraction,79.0,%,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка B",extracted,,
DEMO1,R008,DV-B,MS1,M1,PCE,18.6,%,,,,,,18.6,%,18.6,%,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка B",extracted,,
DEMO1,R009,DV-C,MS1,M1,Voc,1.05,V,,,,,,1.05,V,1.05,V,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка C",extracted,,
DEMO1,R010,DV-C,MS1,M1,Jsc,22.1,mA/cm2,,,,,,22.1,mA/cm2,22.1,mA/cm2,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка C",extracted,,
DEMO1,R011,DV-C,MS1,M1,FF,75.0,%,,,,,,0.75,fraction,75.0,%,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка C",extracted,,
DEMO1,R012,DV-C,MS1,M1,PCE,17.4,%,,,,,,17.4,%,17.4,%,primary,,4,3. Результаты и обсуждение,Табл. 2,,"Table 2, строка C",extracted,,
DEMO1,R008-1,DV-B,MS1,M1,PCE,18.6,%,,,,,,18.6,%,18.6,%,primary,,1,,,,"Перовскитный солнечный элемент с КПД 18,6 %",extracted,R008,повторное упоминание (заголовок)
DEMO1,R013,DV-A,MS1,M1,Voc,1.07,V,,,,,,1.07,V,1.07,V,primary,,4,3. Результаты и обсуждение,,Рис. 3,,extracted,,снято с графика ВАХ (пересечение с осью V)
```
- FF в таблице статьи — доля: `raw_value=0.77`, `raw_unit=fraction`, нормализовано в `77.0 %`.
- `aggregation` пусто: статья не говорит, лучший это прибор или среднее.
- `R008-1` — повтор значения B из заголовка (`duplicate_of=R008`), со своим источником.
- `R013` — значение с рисунка отличается от таблицы (1.07 против 1.08) — это не повтор, а отдельная запись; расхождение 0,9 % < 3 % — замечания `figure_mismatch` нет.
- Ширина зоны и толщина — не строки `results`, а поля `materials.bandgap_value` и `layers.layer_thickness`.

**issues.csv**
```
publication_id,issue_id,record_ids,check_name,description,source_page,source_fragment
DEMO1,I1,R005;R006;R007;R008,jv_consistency,"PCE 18.6 % не согласуется с Voc·Jsc·FF/Pin = 20.33 % (расхождение 9.3 %)",4,"Table 2, строка B"
DEMO1,I2,R004;R008,contradiction,"В тексте лучший — образец B, но по Table 2 КПД выше у A: 19.0 % против 18.6 %",4,Наилучшие характеристики демонстрирует образец B
```
`I1` — правило бэка; `I2` — находка ML (утверждение текста против таблицы).

### Та же статья в API (фрагмент `GET /papers/DEMO1/facts`)
Строка `results` + служебные поля интерфейса (не выгружаются):
```json
{ "fact_id": "R008", "publication_id": "DEMO1", "row_id": "DV-B", "param": "PCE", "target": { "table": "results", "id": "R008" },
  "device_id": "DV-B", "measurement_id": "MS1", "material_id": "M1", "metric_name": "PCE",
  "raw_value": "18.6", "raw_unit": "%", "normalized_value": 18.6, "normalized_unit": "%", "metric_value": 18.6, "metric_unit": "%",
  "aggregation": null, "result_origin": "primary", "cited_publication_id": null,
  "source_page": 4, "source_section": "3. Результаты и обсуждение", "source_table": "Табл. 2", "source_figure": null, "source_fragment": "Table 2, строка B",
  "extraction_status": "extracted", "duplicate_of": null, "note": null,
  "norm_status": "ok", "review_status": "new", "corrected_value": null, "added_by": "model", "value": 18.6, "display": "18,6 %",
  "confidence": 0.957, "quote_found": true, "anchor_id": "anc_R008", "duplicates": ["R008-1"],
  "issue_ids": ["I1", "I2"], "comparability_key": "perovskite|cell|AM1.5G-100|area:?|agg:?|scan:?|cert:?|stab:no", "model_version": "extract-0.3" }
```

### Статья вне области
```
publications.csv
DEMO2,"Optimization and Numerical Modeling of TCO/SnO2/CdS/CdTe Solar Cells",2023,10.1155/2023/7184080,Khaledi P.; Behboudnia M.; Karimi M.,false,"Только численное моделирование; изготовленных образцов и измеренных характеристик нет. По разделу 3.1 расчётные работы к предметной области не относятся."
```
В остальных шести таблицах строк для `DEMO2` нет (как `D016` в `dev_gold`).

## 9.2. RAG — «Гибридный поиск BM25 + плотные эмбеддинги для RAG»

> **Формат сдачи для области 2 уточнить, когда будет архив по области 2.** Ниже — внутренний реляционный формат по полям ТЗ.

```json
{
  "format": "faktura-dataset",
  "version": "1.0",
  "collection": "rag",
  "model_version": "extractor-2026.11",
  "profile": { "id": "prf_rag_tz_v1", "version": 2 },

  "publications": [
    { "publication_id": "pap_hybrid", "title": "Гибридный поиск BM25 + плотные эмбеддинги для RAG", "year": 2024, "DOI": null,
      "authors": ["Соколов М. И.", "Дьячкова А. Р."], "authors_et_al": false, "journal": "Труды конференции «Диалог», 2024",
      "keywords": ["гибридный", "BM25", "dense", "RRF"], "page_count": 11, "in_scope": true, "corpus_id": "crp_rag" }
  ],

  "rows": [
    { "row_id": "bm25", "paper_id": "pap_hybrid", "label": "BM25", "order": 1, "entity": { "config_id": "cfg_hybrid_bm25", "experiment_id": "exp_hybrid_1", "dataset_id": "ds_hybrid_1", "task_id": "tsk_hybrid_1" } },
    { "row_id": "dense", "paper_id": "pap_hybrid", "label": "Dense", "order": 2, "entity": { "config_id": "cfg_hybrid_dense", "experiment_id": "exp_hybrid_1", "dataset_id": "ds_hybrid_1", "task_id": "tsk_hybrid_1" } },
    { "row_id": "hybrid", "paper_id": "pap_hybrid", "label": "Hybrid", "order": 3, "entity": { "config_id": "cfg_hybrid_hybrid", "experiment_id": "exp_hybrid_1", "dataset_id": "ds_hybrid_1", "task_id": "tsk_hybrid_1" } }
  ],

  "datasets_tasks": [
    { "dataset_id": "ds_hybrid_1", "publication_id": "pap_hybrid", "dataset_name": null, "dataset_description": "корпус из 12 тыс. документов", "dataset_version": null,
      "task_id": "tsk_hybrid_1", "task_type": "retrieval", "language": null, "data_split": null, "scientific_domain": null }
  ],

  "experiments": [
    { "experiment_id": "exp_hybrid_1", "publication_id": "pap_hybrid", "dataset_id": "ds_hybrid_1", "task_id": "tsk_hybrid_1", "evaluation_protocol": null, "baseline_name": null,
      "hardware": null, "latency_ms": null, "tokens_per_query": null, "sample_size": null, "config_ids": ["cfg_hybrid_bm25", "cfg_hybrid_dense", "cfg_hybrid_hybrid"] }
  ],

  "rag_configs": [
    { "config_id": "cfg_hybrid_bm25", "publication_id": "pap_hybrid", "row_id": "bm25", "rag_variant": "BM25", "retrieval_method": "sparse", "retriever_model": "BM25",
      "embedding_model": null, "embedding_version": null, "chunking_method": null, "chunk_size": 512, "chunk_overlap": null, "chunk_unit": "tokens", "index_type": null, "vector_store": null, "top_k": null,
      "reranker_model": null, "fusion_method": null, "generator_model": null, "generator_version": null, "prompt_strategy": null, "context_strategy": null, "fact_ids": ["fct_hybrid_c_bm25_chunk"] },
    { "config_id": "cfg_hybrid_dense", "publication_id": "pap_hybrid", "row_id": "dense", "rag_variant": "Dense", "retrieval_method": "dense", "retriever_model": null,
      "embedding_model": null, "embedding_version": null, "chunking_method": null, "chunk_size": 512, "chunk_overlap": null, "chunk_unit": "tokens", "index_type": null, "vector_store": null, "top_k": null,
      "reranker_model": null, "fusion_method": null, "generator_model": null, "generator_version": null, "prompt_strategy": null, "context_strategy": null, "fact_ids": ["fct_hybrid_c_dense_chunk"] },
    { "config_id": "cfg_hybrid_hybrid", "publication_id": "pap_hybrid", "row_id": "hybrid", "rag_variant": "Hybrid", "retrieval_method": "hybrid", "retriever_model": "BM25 + e5-large",
      "embedding_model": "e5-large", "embedding_version": null, "chunking_method": null, "chunk_size": 512, "chunk_overlap": null, "chunk_unit": "tokens", "index_type": null, "vector_store": null, "top_k": null,
      "reranker_model": null, "fusion_method": "RRF", "generator_model": null, "generator_version": null, "prompt_strategy": null, "context_strategy": null,
      "fact_ids": ["fct_hybrid_c_hybrid_chunk", "fct_hybrid_t_hybrid_retriever"] }
  ],

  "metric_results": [
    { "result_id": "mr_hybrid_bm25_r5", "experiment_id": "exp_hybrid_1", "config_id": "cfg_hybrid_bm25", "dataset_id": "ds_hybrid_1", "task_id": "tsk_hybrid_1",
      "metric_group": "retrieval", "metric_name": "Recall", "metric_variant": null, "metric_direction": "higher_is_better", "metric_k": 5, "metric_value": 0.61, "metric_unit": "fraction",
      "baseline_value": null, "aggregation": null, "sample_size": null,
      "publication_id": "pap_hybrid", "result_origin": "primary", "cited_publication_id": null, "source_page": 5, "source_section": "4. Эксперименты", "source_table": "Табл. 2", "source_figure": null,
      "source_fragment": "Качество поиска на корпусе из 12 тыс. документов — BM25: 0,61",
      "raw_value": "0,61", "raw_unit": "", "normalized_value": 0.61, "normalized_unit": "fraction", "extraction_status": "extracted", "fact_id": "fct_hybrid_c_bm25_r5", "review_status": "new",
      "comparability_key": "retrieval|12k-docs@?|Recall|?|5|?" },
    { "result_id": "mr_hybrid_bm25_r10", "config_id": "cfg_hybrid_bm25", "metric_name": "Recall", "metric_k": 10, "metric_value": 0.72, "metric_unit": "fraction", "metric_direction": "higher_is_better", "raw_value": "0,72", "extraction_status": "extracted", "source_page": 5, "source_table": "Табл. 2", "fact_id": "fct_hybrid_c_bm25_r10", "...": "остальное как выше" },
    { "result_id": "mr_hybrid_bm25_mrr", "config_id": "cfg_hybrid_bm25", "metric_name": "MRR", "metric_k": null, "metric_value": 0.48, "metric_unit": "fraction", "metric_direction": "higher_is_better", "raw_value": "0,48", "extraction_status": "extracted", "source_page": 5, "source_table": "Табл. 2", "fact_id": "fct_hybrid_c_bm25_mrr" },
    { "result_id": "mr_hybrid_dense_r5", "config_id": "cfg_hybrid_dense", "metric_name": "Recall", "metric_k": 5, "metric_value": 0.68, "metric_unit": "fraction", "raw_value": "0,68", "fact_id": "fct_hybrid_c_dense_r5" },
    { "result_id": "mr_hybrid_dense_r10", "config_id": "cfg_hybrid_dense", "metric_name": "Recall", "metric_k": 10, "metric_value": 0.79, "metric_unit": "fraction", "raw_value": "0,79", "fact_id": "fct_hybrid_c_dense_r10" },
    { "result_id": "mr_hybrid_dense_mrr", "config_id": "cfg_hybrid_dense", "metric_name": "MRR", "metric_k": null, "metric_value": 0.55, "metric_unit": "fraction", "raw_value": "0,55", "fact_id": "fct_hybrid_c_dense_mrr" },
    { "result_id": "mr_hybrid_hybrid_r5", "config_id": "cfg_hybrid_hybrid", "metric_name": "Recall", "metric_k": 5, "metric_value": 0.74, "metric_unit": "fraction", "raw_value": "0,74", "fact_id": "fct_hybrid_c_hybrid_r5" },
    { "result_id": "mr_hybrid_hybrid_r10", "config_id": "cfg_hybrid_hybrid", "metric_name": "Recall", "metric_k": 10, "metric_value": 0.85, "metric_unit": "fraction", "raw_value": "0,85", "fact_id": "fct_hybrid_c_hybrid_r10" },
    { "result_id": "mr_hybrid_hybrid_mrr", "config_id": "cfg_hybrid_hybrid", "metric_name": "MRR", "metric_k": null, "metric_value": 0.61, "metric_unit": "fraction", "raw_value": "0,61", "fact_id": "fct_hybrid_c_hybrid_mrr" }
  ],

  "facts": [
    { "fact_id": "fct_hybrid_c_hybrid_r5", "publication_id": "pap_hybrid", "row_id": "hybrid", "param": "r5", "param_raw": "Recall@5", "target": { "entity": "metric_result", "id": "mr_hybrid_hybrid_r5", "field": "metric_value" },
      "raw_value": "0,74", "raw_unit": "", "normalized_value": 0.74, "normalized_unit": "fraction", "extraction_status": "extracted", "review_status": "new", "value": 0.74, "display": "0,74",
      "confidence": 0.962, "added_by": "auto", "result_origin": "primary", "source_page": 5, "source_section": "4. Эксперименты", "source_table": "Табл. 2",
      "source_fragment": "Качество поиска на корпусе из 12 тыс. документов — Hybrid: 0,74", "source_kind": "table", "source_ref": "Табл. 2", "source_where": "строка Hybrid",
      "anchor_id": "anc_hybrid_c_hybrid_r5", "issue_ids": ["pap_hybrid:contradiction:clm_hybrid_eq"],
      "cross": { "other_fact_id": "fct_hybrid_f_hybrid_r5", "fig": "0,72", "tbl": "0,74", "pct": 2.7, "over": false, "text": "на рисунке 0,72, в таблице 0,74 — расхождение 2,7 %" } },
    { "fact_id": "fct_hybrid_t_hybrid_retriever", "publication_id": "pap_hybrid", "row_id": "hybrid", "param": "retriever", "target": { "entity": "rag_config", "id": "cfg_hybrid_hybrid", "field": "retriever_model" },
      "raw_value": "BM25 + e5-large, слияние RRF", "raw_unit": null, "normalized_value": null, "normalized_text": "Hybrid", "extraction_status": "text", "review_status": "new", "display": "BM25 + e5-large, слияние RRF",
      "confidence": 0.842, "added_by": "auto", "result_origin": "primary", "source_page": 5, "source_fragment": "Гибридный вариант объединяет BM25 + e5-large, слияние RRF.",
      "source_kind": "text", "source_ref": "текст", "source_where": "абзац 1", "anchor_id": "anc_hybrid_t_hybrid_retriever", "issue_ids": [] },
    { "fact_id": "fct_hybrid_f_hybrid_r5", "publication_id": "pap_hybrid", "row_id": "hybrid", "param": "r5", "raw_value": "0,72", "normalized_value": 0.72, "normalized_unit": "fraction", "extraction_status": "extracted",
      "review_status": "new", "display": "0,72", "confidence": 0.683, "source_page": 5, "source_figure": "Рис. 2", "source_kind": "figure", "source_ref": "Рис. 2", "source_where": "столбец диаграммы", "anchor_id": "anc_hybrid_f_hybrid_r5" },
    { "...": "ещё 11 табличных фактов (chunk, r5, r10, mrr для каждой строки) и 2 значения с рис. 2 (Recall@5 Dense и BM25)" }
  ],

  "claims": [
    { "claim_id": "clm_hybrid_best", "paper_id": "pap_hybrid", "row_id": "hybrid", "param": "r5", "kind": "best", "text": "Гибридный поиск оказался лучшим по всем метрикам", "place": "text", "anchor_id": "anc_hybrid_claim_best", "source_page": 5, "confidence": 0.9 },
    { "claim_id": "clm_hybrid_eq", "paper_id": "pap_hybrid", "row_id": "hybrid", "param": "r5", "kind": "equals", "value": 0.76, "value_raw": "0,76", "text": "Recall@5 гибридного поиска достигает 0,76", "place": "abstract", "anchor_id": "anc_hybrid_claim_eq", "source_page": 1, "confidence": 0.86 }
  ],

  "issues": [
    { "issue_id": "pap_hybrid:contradiction:clm_hybrid_eq", "check_name": "contradiction", "plugin": "claims", "scope": "paper", "collection": "rag", "paper_id": "pap_hybrid",
      "severity": "error", "ui_level": "conflict", "title": "Аннотация расходится с таблицей", "text": "В тексте Recall@5 = 0,76, в таблице — 0,74.",
      "row_id": "hybrid", "row_label": "Hybrid", "param": "r5", "param_label": "Recall@5", "value": "0,74",
      "fact_ids": ["fct_hybrid_c_hybrid_r5"], "main_fact_id": "fct_hybrid_c_hybrid_r5", "anchor_ids": ["anc_hybrid_claim_eq", "anc_hybrid_c_hybrid_r5"],
      "related": [ { "entity": "claim", "id": "clm_hybrid_eq" }, { "entity": "metric_result", "id": "mr_hybrid_hybrid_r5" } ],
      "numbers": { "claimed": 0.76, "table": 0.74, "tolerance": 0.005 },
      "status": "open", "touched_fact_ids": [], "computed_by": "backend", "rule_version": "1", "stale": false }
  ],

  "structure": {
    "pipeline": [ { "row_id": "hybrid", "nodes": [
      { "key": "data", "title": "Датасет", "value": "корпус из 12 тыс. документов", "sub": "подпись к табл. 2", "fact_id": null },
      { "key": "chunk", "title": "Разбиение на чанки", "value": "512 ток.", "sub": "Табл. 2", "fact_id": "fct_hybrid_c_hybrid_chunk" },
      { "key": "ret", "title": "Ретривер", "value": "BM25 + e5-large, слияние RRF", "sub": "текст", "fact_id": "fct_hybrid_t_hybrid_retriever" },
      { "key": "rr", "title": "Переранжирование", "value": null, "fact_id": null },
      { "key": "gen", "title": "Генератор", "value": null, "fact_id": null },
      { "key": "met", "title": "Метрики", "items": [ { "label": "Recall@5", "value": "0,74", "fact_id": "fct_hybrid_c_hybrid_r5" }, { "label": "Recall@10", "value": "0,85", "fact_id": "fct_hybrid_c_hybrid_r10" }, { "label": "MRR", "value": "0,61", "fact_id": "fct_hybrid_c_hybrid_mrr" } ] } ] } ] },

  "references": [
    { "paper_id": "pap_hybrid", "n": 1, "status": "ok", "key": "p:pap_chunks", "target_paper_id": "pap_chunks", "authors": "Захарова Н. В.", "title": "Размер чанка и качество ответов RAG-систем", "venue": "Информатика и её применения, 2023, т. 17, № 2", "year": 2023, "doi": null,
      "mentions": ["Размер чанка выбран по результатам [1].", "Влияние длины фрагмента на полноту поиска — [1]."] },
    { "paper_id": "pap_hybrid", "n": 5, "status": "ok", "key": "x:cormack2009", "external_id": "cormack2009", "authors": "Cormack G. V., Clarke C. L. A., Büttcher S.", "title": "Reciprocal rank fusion outperforms Condorcet and individual rank learning methods", "venue": "SIGIR 2009, 758–759", "year": 2009, "doi": "10.1145/1571941.1572114",
      "mentions": ["Слияние списков выполнено по правилу RRF [5]."] },
    { "...": "ещё 7 ссылок, одна нераспознанная (препринт 2023)" }
  ],

  "quality": { "total": 17, "sourced_pct": 100, "normalized_pct": 100, "accepted": 0, "issues": 1 },
  "coverage_summary": { "filled": 15, "total": 15 }
}
```

В примере RAG видно правило «не восстанавливать по догадке»: название набора, язык, протокол, базовая линия, генератор, `top_k` — `null`, потому что статья их не называет; `dataset_description` хранит то, что сказано.
