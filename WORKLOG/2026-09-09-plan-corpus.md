# 2026-09-09 · Замеры стадии PLAN на корпусе

Один и тот же демо-контент (`examples/content-demo.md`, 5 разделов: вводный
абзац, список из 4 пунктов, метрика с подписью, 2 абзаца, финал) разложен по
каждому из одиннадцати реальных шаблонов **детерминированным планировщиком,
без единого обращения к модели** (`ADR-0009`).

Таблица порождена прогоном, а не написана руками. Пересоздать:
`python tools/report.py plan`.

| Шаблон | Паттернов | Слайдов | Не размещено | Сверх ёмкости | Предупр. | Виды раскладок |
|---|---|---|---|---|---|---|
| `2411-Performance_Up.pptx` | 12 | 6 | 0 | 3 | 0 | cover → bullets → bullets → text → table → section |
| `60042.pptx` | 7 | 7 | 0 | 1 | 1 | cover → two_column → image_text → image_text → other → image_text → section |
| `ArtisticEffectSample.pptx` | 5 | 6 | 1 | 0 | 2 | cover → other → other → other → other → section |
| `SampleShow.pptx` | 7 | 6 | 1 | 1 | 2 | cover → two_column → other → image_text → other → section |
| `WithMaster.pptx` | 7 | 6 | 1 | 0 | 2 | cover → other → section → other → other → section |
| `business-plan-ppt-template-10-slides-creative.pptx` | 14 | 6 | 0 | 2 | 0 | image_text → image_text → image_text → image_text → cards → image_text |
| `business_plan.pptx` | 18 | 6 | 0 | 1 | 0 | text → text → bullets → text → cards → text |
| `prostoj-shablon-prezentacii-dlja-kompanii.pptx` | 14 | 7 | 0 | 5 | 1 | section → bullets → bullets → bullets → bullets → bullets → section |
| `shablon-prezentacii-biznes-plana.pptx` | 9 | 6 | 0 | 4 | 0 | cover → text → text → section → text → section |
| `stilnyj-shablon-korporativnoj-prezentacii.pptx` | 10 | 6 | 0 | 6 | 0 | cover → image_text → image_text → image_text → image_text → cover |
| `svetlaja-tema-dlja-prezentaciipptx.pptx` | 35 | 6 | 0 | 0 | 0 | cover → bullets → cards → image_text → two_column → section |

**Итог: 8 шаблонов из 11 разложены без потерь.** Остальные — двухслайдовые
файлы, где паттерны выведены из макетов (`ADR-0006`); потери там перечислены
в `diagnostics.unplaced`, а не замолчаны.

## Что чинилось по ходу, с числами

| Симптом | Замер | Причина | Правка |
|---|---|---|---|
| 4 раздела из 5 не размещались на дизайнерском шаблоне | все паттерны, кроме одного, отвергали контент | правило «влезает точно либо паттерн непригоден» — слоты подогнаны под короткие тексты дизайнера, в обложку 10.5″ при 138 pt влезает 9 знаков | переполнение до 2.5× допускается, штрафуется и помечается флагом для VERIFY |
| метрика загоняла планировщик в раскладку на 20 слотов | слот `metric_value` был у 3 паттернов из 14, самых плотных | метрика могла лечь только в свой слот | при отсутствии плашки метрика становится обычным текстом |
| все 5 содержательных слайдов брали один паттерн | — | ранг не учитывал предыдущий слайд | мягкий штраф 0.25 за повтор предыдущей раскладки |
| обложка выбиралась в середине колоды | — | позиционная надбавка была только для первого и последнего | штраф 0.6 за `cover`/`closing` в середине |
| подпись под числом не прицеплялась | — | число и подпись разделены пустой строкой, то есть формально два блока | короткий (≤60 знаков) абзац под голым числом поглощается как подпись |
| первый раздел не ложился в обложку | — | титульный слайд не выделялся из заголовка колоды, а обложка обычно однослотовая | заголовок уезжает на титул, его содержимое — на следующий слайд |
