# 9. Приложение: примеры статей в формате сдачи

[← К оглавлению](../BACKEND_SPEC.md)

Для обеих областей — ровно формат сдачи (по 7 CSV шаблона, §14.A и §14.B). Область 1 — демо-статья прототипа (`mapbi`, id условный); область 2 — фрагмент настоящего эталона dev (D001). Источник каждого поля — §3.

Общее: `raw_value` — как в статье, `normalized_value` = `metric_value` — в целевой единице (область 1) или по установленной шкале без пересчёта (область 2); пустая ячейка — в статье нет; замечания ссылаются на `result_id`, исходные записи не меняются; в API статья — пара (`domain`, `publication_id`).

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

### Та же статья в API (фрагмент `GET /papers/solar/DEMO1`)
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

## 9.2. RAG — статья D001 из эталона dev (формат сдачи области 2)

Фрагмент **настоящего эталона** `docs/case5_area2/dev_gold/` для статьи D001 («Graph Topology Meets Energy-Driven Pathfinding…»): строки всех 7 таблиц шаблона области 2 (§14.B). Полностью в D001 — 430+ результатов; здесь — по несколько строк каждой таблицы.

Что видно в примере:
- `raw_value` = `.204` без единицы, `normalized_value` = `0.204` / `fraction`: ведущий ноль дописан, шкала не пересчитывалась (пояснение в `note`);
- один и тот же `RA-CoT` с разными генераторами — разные конфигурации `C01` и `C02`;
- `source_fragment` — дословная многострочная строка таблицы из PDF; указание столбца — в `note`;
- `R253` — повтор значения `R091` в тексте раздела 4.2 (`duplicate_of`);
- замечание `I01` с описательным `check_name` (`percentage_vs_percentage_point`) ссылается на много результатов (`record_ids` укорочен);
- нет декартова произведения: строки только для значений, напечатанных в статье.

**publications.csv**
```
publication_id,title,year,DOI,authors,in_scope,exclusion_reason

D001,Graph Topology Meets Energy-Driven Pathfinding: Preserving Contextual Consistency in Retrieval-Augmented Generation,2026,10.18653/2026.findings-emnlp.2324,Yufeng Li; Zaijing Li; Fuchen Zhang; Zongzong Wu; Ming Zhao; Fengxiao Tang,true,
```
**configurations.csv**
```
publication_id,config_id,rag_variant,retrieval_method,retriever_model,embedding_model,embedding_version,chunking_method,chunk_size,chunk_overlap,chunk_unit,index_type,vector_store,top_k,reranker_model,generator_model,generator_version,prompt_strategy,context_strategy,note

D001,C01,RA-CoT,,,,,,,,,,,,,LLaMA3-8B-Instruct,,,,"PDF p. 6, Table 1 row RA-CoT, generator LLaMA3-8B-Instruct."

D001,C02,RA-CoT,,,,,,,,,,,,,Qwen3.5 plus,,,,"PDF p. 6, Table 1 row RA-CoT, generator Qwen3.5 plus."

D001,C16,HippoRAG,,,,,,,,,,,,,LLaMA3-8B-Instruct,,,,"PDF p. 6, Table 1 row HippoRAG, generator LLaMA3-8B-Instruct."
```
**datasets.csv**
```
publication_id,dataset_id,dataset_name,dataset_version,language,data_split,scientific_domain,note

D001,DS1,HotpotQA,,English,,,"PDF pp. 6, 11. The evaluation split is referenced to earlier works but not named; sample count not inferred from dataset size."

D001,DS2,2WikiMultihopQA,,,,,"PDF pp. 6, 11. The evaluation split is referenced to earlier works but not named; sample count not inferred from dataset size."
```
**tasks.csv**
```
publication_id,task_id,task_type,note

D001,T1,open-domain multi-hop question answering,"PDF p. 6, section 4.1."
```
**experiments.csv**
```
publication_id,experiment_id,evaluation_protocol,baseline_name,hardware,latency_ms,tokens_per_query,sample_size,note

D001,E1,"Table 1 main evaluation. Retrieval corpus aggregates supporting and distractor passages; standard text normalization of predictions and gold answers before exact match and token-level F1. Same data splits as Trivedi et al. (2023) and Li and Du (2023), no explicit count. ToG and PoG adapted to the local KG used by LightRAG.",RA-CoT;ReAct;IRCoT;Self-Ask;Iter-RetGen;HippoRAG;LightRAG;CoTKR;EFSUM;ToG;PoG;ERA-CoT;knowtrace,4 NVIDIA A6000 GPUs,,,,"PDF p. 6, section 4.1. Model names preserve Table 1 notation; versions not independently inferred."
```
**results.csv**
```
publication_id,result_id,experiment_id,config_id,dataset_id,task_id,metric_group,metric_name,metric_variant,metric_direction,metric_k,metric_value,metric_unit,baseline_value,aggregation,sample_size,raw_value,raw_unit,normalized_value,normalized_unit,result_origin,cited_publication_id,source_page,source_section,source_table,source_figure,source_fragment,extraction_status,duplicate_of,note

D001,R001,E1,C01,DS1,T1,answer quality,EM,exact match after standard text normalization,higher_is_better,,0.204,fraction,,,,.204,,0.204,fraction,primary,,6,4.1; 4.2,Table 1,,"RA-CoT
.204
.330
.202
.261
.142
.210
.312
.429
.268
.372
.162
.249
.310
.425
.264
.359
.158
.243",extracted,,"Table 1: row RA-CoT; generator LLaMA3-8B-Instruct; dataset HotpotQA; column EM. Leading zero added without scale conversion. No unit symbol is printed in the table, so raw_unit is blank. Fraction scale is consistent with the metric definitions and the absolute EM comparisons on p. 7. Table reproduced from authors experiments, including rerun baselines."

D001,R002,E1,C01,DS1,T1,answer quality,F1,token-level overlap after standard text normalization,higher_is_better,,0.330,fraction,,,,.330,,0.330,fraction,primary,,6,4.1; 4.2,Table 1,,"RA-CoT
.204
.330
.202
.261
.142
.210
.312
.429
.268
.372
.162
.249
.310
.425
.264
.359
.158
.243",extracted,,"Table 1: row RA-CoT; generator LLaMA3-8B-Instruct; dataset HotpotQA; column F1. Leading zero added without scale conversion. No unit symbol is printed in the table, so raw_unit is blank. Fraction scale is consistent with the metric definitions and the absolute EM comparisons on p. 7. Table reproduced from authors experiments, including rerun baselines."

D001,R003,E1,C01,DS2,T1,answer quality,EM,exact match after standard text normalization,higher_is_better,,0.202,fraction,,,,.202,,0.202,fraction,primary,,6,4.1; 4.2,Table 1,,"RA-CoT
.204
.330
.202
.261
.142
.210
.312
.429
.268
.372
.162
.249
.310
.425
.264
.359
.158
.243",extracted,,"Table 1: row RA-CoT; generator LLaMA3-8B-Instruct; dataset 2Wiki; column EM. Leading zero added without scale conversion. No unit symbol is printed in the table, so raw_unit is blank. Fraction scale is consistent with the metric definitions and the absolute EM comparisons on p. 7. Table reproduced from authors experiments, including rerun baselines."

D001,R091,E1,C16,DS1,T1,answer quality,EM,exact match after standard text normalization,higher_is_better,,0.324,fraction,,,,.324,,0.324,fraction,primary,,6,4.1; 4.2,Table 1,,"HippoRAG
.324
.403
.318
.411
.202
.274
.418
.557
.466
.569
.234
.333
.412
.549
.458
.572
.219
.327",extracted,,"Table 1: row HippoRAG; generator LLaMA3-8B-Instruct; dataset HotpotQA; column EM. Leading zero added without scale conversion. No unit symbol is printed in the table, so raw_unit is blank. Fraction scale is consistent with the metric definitions and the absolute EM comparisons on p. 7. Table reproduced from authors experiments, including rerun baselines."

D001,R253,E1,C16,DS1,T1,answer quality,EM,exact match after standard text normalization,higher_is_better,,0.324,fraction,,,,0.324,,0.324,fraction,primary,,6,4.2,,,0.324 vs. 0.304,extracted,R091,"Repeated comparison in section 4.2 on the same PDF page; same Table 1 experiment, generator and dataset."
```
**issues.csv**
```
publication_id,issue_id,record_ids,check_name,description,source_page,source_fragment

D001,I01,R261;R263;R265;…,percentage_vs_percentage_point,"Table 2 labels absolute EM changes with %. For the first HotpotQA ablation 0.422-0.238=0.184, hence 18.4 percentage points, not a relative decrease of 18.4%. Original symbol retained; normalization explicitly documented.",7,Table 2: Ablation study
```


В API та же статья — `GET /papers/rag/D001` (§15.4): эти 7 массивов строк + `reviews` + `meta`.
