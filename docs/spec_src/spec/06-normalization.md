# 6. Нормализация

[← К оглавлению](../BACKEND_SPEC.md)

Нормализацию исполняет **конвейер бэка** [BE]; алгоритм и базовый словарь пишет **ML-2** (пакет `faktura_norm` или сервис `/ml/normalize`, §7.0.2) по правилам этого раздела. ML-1 отдаёт значение **дословно** (`raw_value`, `raw_unit`); фронт показывает `display` и цепочку `trace`. В прототипе это `parseRaw()`, `normConditions()`, `fmt()` (`src/data.ts`) и `normTrace()` (`src/norm.ts`) — они переезжают.

Хранятся обе записи: исходная (`raw_value`, `raw_unit`) и нормализованная (`normalized_value`, `normalized_unit`); в `results.csv` `metric_value` и `metric_unit` **совпадают** с нормализованными.

## 6.1. Целевые единицы (шаблон сдачи, ASCII)

В API и выгрузке единицы — **ASCII**, как в шаблоне. По-русски («мА/см²», «В», «эВ») — только в `display` для интерфейса.

| Показатель / поле | `normalized_unit` | Принимаемые `raw_unit` → множитель |
|---|---|---|
| PCE, stabilized_PCE, retained_PCE, HI | `%` | `%` × 1; `fraction` (доля, ≤ 1) × 100 — шкала известна |
| FF | `%` | `%` × 1; `fraction` / без единицы при значении ≤ 1 × 100 |
| Voc, Voc_deficit, Vmpp | `V` | `V` × 1; `mV` × 0.001 |
| Jsc, Jsc_EQE, Jmpp | `mA/cm2` | `mA/cm2`, `mA cm-2`, `mAcm-2` × 1; `A/m2` × 0.1; `A/cm2` × 1000 |
| Rs, Rsh | `Ohm*cm2` | `Ohm*cm2`, `Ω cm2`, `Ω·cm²` × 1; `kOhm*cm2` × 1000; `Ohm` (без площади) — **не пересчитывается** (`norm_status=failed`, нужна площадь — это вывод, а не перевод единиц) |
| T80 | `h` | `h` × 1; `min` × 1/60; `day`, `d` × 24 |
| `materials.bandgap_value` | `eV` | `eV` × 1; край поглощения в `nm` — **не пересчитывается** (1240/λ — вывод) |
| `layers.layer_thickness` | `nm` | `nm` × 1; `µm`, `um`, `μm` × 1000; `Å` × 0.1; `mm` × 10⁶ |
| `devices.active_area` | `cm2` | `cm2` × 1; `mm2` × 0.01 |
| `measurements.light_intensity` | **как в статье** + `intensity_unit` (`mW/cm2`, `W/m2`) | для правила `jv_consistency` бэк считает Pin в mW/cm2 (`W/m2` × 0.1), в данные не пишет |
| `time_point` (стабильность) | **как в статье** + `time_unit` (`h`, `day`) | для T80 — в `h` |
| `annealing_temperature`, `annealing_time`, `scan_rate` | строкой с единицей: «500 °C», «10 min», «0.15 V/s» | не пересчитываются |

Прочие показатели (не из списка `metric_name`) — по возможности в той же системе (`AVT`, `LUE` — `%`; безразмерные — пустая единица).

## 6.2. Алгоритм для числа [BE]

1. Взять `raw_value`, убрать пробелы и неразрывные пробелы, «−» → «-»; десятичная запятая → точка.
2. Диапазон «18.2–19.0» — не выбираем значение: `normalized_value` пусто, `norm_status=failed`, замечание `ambiguity` (если ML не поставил своё).
3. «18.6 ± 0.4» — число 18.6, `spread_value = 0.4`.
4. Единица — `raw_unit` от ML; если пусто — из заголовка столбца таблицы (`document.tables[].header[].unit`); если и там нет — для долей по шкале (§6.5), иначе `norm_status=failed`.
5. Множитель — по таблице §6.1, затем по словарю единиц (`UnitRule`).
6. Нашли — `normalized_value = число × множитель`, `norm_status = ok | converted`. Нет — `normalized_value` пусто, `norm_status = failed`, замечание `unit_inconsistency`. **`extraction_status` не меняется** (остаётся `extracted` — значение в статье есть и извлечено).
7. Округление: не округляем сверх исходной точности (`1197 mV` → `1.197`, `0.71 fraction` → `71.0`). Для показа (`display`) — до `digits` параметра «как в школе» (0,745 → 0,75), с сохранением ещё одного значащего знака исходника.
8. `trace` — шаги «В статье» → «Параметр» → «Число» → «Перевод» → «Итог»/«Исправлено» (§4.8).

Исправление человеком: человек вводит число в целевой единице («19» у PCE → 19 %) — это явный ввод, а не догадка; пишется в `corrected_value` (§3.5), `raw_value` не трогается.

## 6.3. Синонимы

### Показатели (`metric_name`)
Как назван в статье (`param_raw` / заголовок) → имя шаблона. Без учёта регистра, целым словом:

| `metric_name` | Синонимы |
|---|---|
| `PCE` | PCE, η, efficiency, power conversion efficiency, КПД, эффективность преобразования |
| `Voc` | Voc, V_OC, VOC, open-circuit voltage, Uхх, напряжение холостого хода |
| `Jsc` | Jsc, J_SC, JSC, short-circuit current density, плотность тока короткого замыкания |
| `FF` | FF, fill factor, фактор заполнения |
| `Rs` | Rs, R_s, series resistance |
| `Rsh` | Rsh, R_sh, Rp, shunt resistance |
| `HI` | HI, hysteresis index |
| `Jsc_EQE` | integrated Jsc, Jsc from EQE, integrated current density |
| `stabilized_PCE` | stabilized PCE, SPO, steady-state efficiency, MPP efficiency |
| `T80` | T80, T₈₀ |
| `retained_PCE` | retained PCE, retains X % of initial efficiency, normalized PCE |
| (доп.) `Voc_deficit`, `Jmpp`, `Vmpp`, `AVT`, `CRI`, `LUE` | как в статье |

Не сопоставилось — значение сохраняется с `metric_name` как в статье (допустимо по шаблону, не оценивается) и `note`.

### Материалы
`formula_normalized` для перовскита — вид A-B-X с раскрытыми MA, FA, Cs (`MAPbI3` → `CH3NH3PbI3`, `FAPbI3` → `HC(NH2)2PbI3`, `CsPbI2Br`). Словарь материалов (из прототипа): MAPbI₃, FAPbI₃, Cu(In,Ga)Se₂ (CIGS), CdTe, FASnI₃, SnO₂, Spiro-OMeTAD, + кестерит (CZTS, CZTSSe), Sb₂Se₃. `material_class` — словарь шаблона.

### Слои
`layer_function` по словарю шаблона: FTO/ITO/AZO/Mo → `front_contact` (или `back_contact` по положению); TiO₂, SnO₂, ZnO, PCBM, C60, **CdS** → `ETL`; Spiro-OMeTAD, PTAA, NiOx, PEDOT:PSS → `HTL`; Au, Ag, Al, Cu, углерод → `back_contact`; металлическая сетка → `front_contact`; BCP, LiF, MoO₃, просветляющее покрытие → `interlayer`; пассивирующие (PEAI и т. п.) → `passivation`; рекомбинационный слой → `recombination`. **Подложка (стекло, фольга, полиимид) в `layers` не пишется.** Исходное название — в `note`.

### Условия измерения
`spectrum`: «AM 1.5G», «AM1,5G», «1 sun AM1.5» → `AM1.5G`. `light_intensity`/`intensity_unit` — как в статье («1 sun» → `light_intensity` пусто или `100` + `mW/cm2` только если «100 mW/cm2» сказано; «1 sun» — в `note`). `scan_direction`: «reverse», «FB–SC», «from Voc to Jsc» → `reverse`; «forward», «SC–FB» → `forward`; «average of both» → `average`. `measurement_type`, `stability_protocol` — словари шаблона.

### Изменение словаря
Свой синоним [USER] → бэк пересопоставляет значения статей и запускает `renormalize` + `recheck` (§13.5). Встроенные синонимы не удаляются.

## 6.4. «Не восстанавливать неизвестное по догадке» (ТЗ и шаблон)

**Пустая ячейка = в статье этого нет.** Бэк никогда не заполняет поле, которого нет в статье:
- нет единицы ни у значения, ни в заголовке, шкала не очевидна — `normalized_value` пусто;
- нет условий — поля `measurements` пустые, замечание `missing_conditions`; Pin = 100 в `jv_consistency` — только для расчёта и только при AM1.5G;
- `aggregation` пусто, если не сказано, лучший это прибор или среднее (в dev_gold так у 342 из 659 строк);
- нет площади, толщины, слоя, `scan_direction` — пусто;
- значения из других работ — `result_origin = cited`, без `device_id`/`measurement_id`;
- ML может предложить значение в `note` («вероятно, типичный прибор»), но не в поле словаря.

## 6.5. Доли и проценты

Пересчёт допустим только при известной шкале (ТЗ):
- `FF`, `PCE`, `retained_PCE` в долях (`raw_unit=fraction` или значение ≤ 1 без единицы в столбце, где все значения ≤ 1) → × 100, `%`.
- «FF 0.71 %» — значение и единица не согласуются: нормализуем по шкале (71 %) и ставим `unit_inconsistency` (так в dev_gold, D001).
- Для области 2 (RAG): Recall/MRR — доля 0…1; «74 %» → 0.74. Формат сдачи для области 2 уточнить.
