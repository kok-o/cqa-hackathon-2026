# Стартовый набор Data Challenge ’26

Это заготовки для работы с данными и проверки решений на ноутбуке. Начни с трёх учебных запусков ниже. При выдаче задания сверь условия использования готового кода, сторонних API и данных. [Полевой справочник](<../reports/Инструменты для Data Challenge 2026.md>) · [Формат хакатона](https://datachallenge.datasci.website/#mission)

## Прогон перед хакатоном

В PowerShell из корня проекта используй подготовленное окружение Windows x64 с Python 3.13.

```powershell
$kitPy = Join-Path $env:LOCALAPPDATA 'DataChallenge26\venv\Scripts\python.exe'
& $kitPy -B '.\hackathon-kit\case_lab.py'
& $kitPy -B '.\hackathon-kit\models.py'
& $kitPy -B '.\hackathon-kit\benchmark.py' --rows 500000 --repeat 2
```

Если путь к готовому окружению виден только из Codex, его физический путь на этом ноутбуке — `C:\Users\esenb\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\DataChallenge26\venv\Scripts\python.exe`. Либо создай окружение на своём ноутбуке по командам ниже.

На другом компьютере с Python 3.13 и Windows x64 создай свой venv. Для установки без интернета подготовь или скопируй каталог с wheels под ту же платформу. На этом ноутбуке рядом с venv уже лежит `DataChallenge26\wheelhouse` с 29 пакетами на 285 MiB. `pip` проверил полный набор зависимостей с `--no-index`. Виртуальное окружение привязано к установленному Python и пути, поэтому для команды переноси wheels и папку набора. [venv](https://docs.python.org/3.13/library/venv.html) · [pip download](https://pip.pypa.io/en/stable/cli/pip_download/)

```powershell
$kitEnv = Join-Path $env:LOCALAPPDATA 'DataChallenge26\venv'
$kitPy = Join-Path $kitEnv 'Scripts\python.exe'
$kitWheels = Join-Path $env:LOCALAPPDATA 'DataChallenge26\wheelhouse'
py -3.13 -m venv $kitEnv

# Download once while online, or point $kitWheels at the copied folder.
& $kitPy -m pip download --only-binary=:all: -r '.\hackathon-kit\requirements.txt' -d $kitWheels
# Install from local files, including with no internet connection.
& $kitPy -m pip install --no-index --find-links $kitWheels -r '.\hackathon-kit\requirements.txt'
& $kitPy -m pip check
```

## Когда выдали файлы

1. Выпиши, что означает строка и ключ каждой таблицы, какой момент наблюдения и что оценивает жюри. Сверь формат с примером сабмита.
2. Запусти профиль на первых строках для схемы, затем по всему файлу на важных ключах и датах. Проверь делитель, кодировку, пропуски, уникальность и диапазон. Первые строки могут быть отсортированы по дате, поэтому проверь хвосты и другие периоды отдельно.
3. Если будет много повторных проходов, сделай типизированный Parquet. Начни с обычного файла и добавляй бакеты, если тяжёлые операции по одному ключу требуют больше RAM.
4. До большого join посчитай его ожидаемую кардинальность. После него сравни строки, уникальные ID и суммы по статусу и валюте. Сохрани маленький тестовый пример, на котором результат можно проверить вручную.

```powershell
& $kitPy -B '.\hackathon-kit\data_tools.py' inspect '.\raw\events.csv' --key client_id --time event_at --limit 50000
& $kitPy -B '.\hackathon-kit\data_tools.py' inspect '.\raw\events.csv' --key client_id --time event_at
& $kitPy -B '.\hackathon-kit\data_tools.py' parquet '.\raw\events.csv' '.\stage\events.parquet' --type 'event_at=TIMESTAMPTZ' --type 'amount_minor=BIGINT'
& $kitPy -B '.\hackathon-kit\data_tools.py' shard '.\stage\events.parquet' '.\stage\events_by_client' --key client_id --buckets 16
```

[data_tools.py](data_tools.py) принимает один локальный CSV, TSV, их gzip-варианты или Parquet. Для выгрузки с точкой с запятой задай `--delimiter ';'`. При чтении CSV столбцы считаются строками до явного `--type`. ID вроде `0007` сохраняется, если он так записан в исходнике. В примерах даты без зоны трактуются как UTC. Если источник использует местное время, сначала уточни зону и нормализуй даты. Конвертация останавливается на ошибке приведения. Проверь масштаб и округление денежных значений отдельно. Существующие выходы защищены. Для публикации Parquet используй локальную ФС с поддержкой жёстких ссылок, например NTFS.

`--threads 2` и `--memory 2GB` — стартовые значения, их можно поменять для каждой команды. Лимит DuckDB управляет буферным менеджером, а RSS может быть выше. `--spill-dir` принимает существующую папку на локальном SSD. Бакеты пишутся как `bucket=N/*.parquet`. Чтобы объединить две большие стороны по бакетам, одинаково нормализуй ключи, используй одно число бакетов и сравнивай сами ключи внутри бакета. Перед полным прогоном посмотри размеры бакетов и размер результата join. [DuckDB по памяти](https://duckdb.org/docs/current/guides/performance/oom) · [Hive-партиции](https://duckdb.org/docs/current/data/partitioning/hive_partitioning)

Для компактного XLSX начни с `pd.ExcelFile(path, engine="openpyxl").sheet_names`, затем прочитай нужные лист и столбцы через `pd.read_excel(..., sheet_name=..., usecols=..., dtype={"client_id": "string"})`. Сверь заголовки, объединённые ячейки и числовые ID. Если нули были только оформлением ячейки, строковый тип сам по себе их не вернёт. Для JSONL сначала проверь структуру и вложенные списки, затем извлеки нужные поля. Перед `UNNEST` определи, сколько строк должно получиться из одной записи. [pandas.read_excel](https://pandas.pydata.org/docs/reference/api/pandas.read_excel.html) · [Чтение JSON в DuckDB](https://duckdb.org/docs/stable/data/json/overview)

## Шаблоны под задачу

| Что требуется | Что открыть | Что проверить перед применением |
|---|---|---|
| Воронка, конверсия, узкий шаг | [case_lab.sql](case_lab.sql), `ordered_funnel` | Один клиент или заявка, строгий порядок, единое окно и полностью наблюдаемая когорта |
| Активность и ценность сегментов | [case_lab.sql](case_lab.sql), `rfm` | Заказы и частичные исполнения, статус возврата, валюта и дата среза |
| Join перед расчётом или витриной | [case_lab.sql](case_lab.sql), `join_cardinality` | Сколько строк даст join, где справочник имеет несколько совпадений, что потерялось |
| Прогноз активации, оттока или отклика | [models.py](models.py), `score_activation` | Признаки только из прошлого, зрелый таргет, validation и будущий holdout, AP и качество в реальной очереди top-K |
| Классификация обращений | [models.py](models.py), `ticket_pipeline` | Локальный TF-IDF и логрег, разбиение по времени или группам, ошибки по языкам и классам. Маршрутизацию и ручной fallback проверяй отдельно |
| Слишком большой вход | [benchmark.py](benchmark.py) и [справочник](<../reports/Инструменты для Data Challenge 2026.md>) | Измерь пик на выбранном запросе. Убедись, что выход и состояние агрегата тоже влезают |

`case_lab.py` проверяет ответы на синтетике, включая чужую заявку, пограничный таймстамп и неполное окно наблюдения. `models.py` содержит офлайн-прогон обоих бейзлайнов. Функция скоринга принимает компактную витрину с `as_of`, бинарным `target` и явным списком признаков. Код исключает строки, у которых окно расчёта таргета не закончилось к границе выборки. Дату доступности признаков и определение таргета нужно обеспечить при сборке витрины. [CatBoost fit](https://catboost.ai/docs/en/concepts/python-reference_catboostclassifier_fit) · [Ошибки валидации scikit-learn](https://scikit-learn.org/stable/common_pitfalls.html)

## Что показал замер на этом ноутбуке

Windows, Python 3.13, Ryzen 7 7840HS и 23,8 GiB RAM. **1 000 000 синтетических строк, CSV 107 MiB.** Считывались два нужных столбца, считались суммы в целых единицах и количество непустых значений по 100 группам. Три свежих процесса на вариант. Время включает чтение и агрегацию, а пик RSS учитывает весь дочерний процесс. Для DuckDB и Polars задано до четырёх потоков. Все результаты совпали.

| Движок | Медиана, с | Максимальный пик RSS, MiB |
|---|---:|---:|
| pandas | 0,388 | 129,7 |
| pandas, чанки по 50 000 | 0,669 | 103,7 |
| Polars lazy streaming | 0,122 | 170,2 |
| DuckDB | 0,248 | 162,0 |

Здесь Polars быстрее всего, а pandas с чанками экономит больше памяти. Для большого join, distinct, широкой витрины или другого формата запусти свой замер. Бенчмарк повторно читает файл, поэтому цифры включают влияние кэша ОС. [Методика в benchmark.py](benchmark.py)

## Перед отправкой

Сверь число строк и порядок столбцов с sample submission, уникальность ID, типы, NaN, бесконечности и диапазон предсказаний. Запусти расчёт один раз из чистых путей и сохрани предыдущий валидный результат. В защите покажи, где проблема, что изменилось относительно baseline при том же знаменателе и какой конкретный шаг предлагает команда.
