# Polars, pandas chunks и Parquet

Проверено 5 ноября 2026 года. Это варианты обработки входных данных для 24-часового кейса. Схема, размеры таблиц и правила использования инструментов станут известны с брифом.

## Как выбрать движок и не исчерпать RAM

### Главное

При выборе считай пиковую память всей операции, включая вход, временные данные и выход. Сначала отрежь лишние строки и столбцы, затем выбери самый короткий проверяемый путь. Результат, который заведомо крупнее доступной RAM, записывай напрямую на диск. [Руководство pandas по большим данным](https://pandas.pydata.org/docs/user_guide/scale.html), [Polars streaming](https://docs.pola.rs/user-guide/concepts/streaming/).

### Подтверждённые сведения

| Что увидели при получении данных | Первый подход |
| --- | --- |
| Данные, join и модель помещаются с запасом | pandas с `usecols`, явными `dtype` и ранним отбором или Polars lazy, если команда его знает |
| Большой CSV, нужны несколько метрик с ограниченным состоянием | `pandas.read_csv(..., engine="c", chunksize=...)` плюс корректное объединение частичных итогов |
| Большой CSV или много Parquet, нужны фильтры, фичи и агрегации | `pl.scan_csv` / `pl.scan_parquet`, нативные выражения, потоковое выполнение, компактный `.collect()` либо `.sink_parquet()` |
| Две большие таблицы, глобальный дедуп или тяжёлые оконные операции | DuckDB со spill либо разбиение по ключу и обработка bucket за bucket. Сначала проверить объём результата |
| Таблица после агрегации небольшая | Переносить в pandas или NumPy только её, если алгоритму обучения или визуализации это нужно |

Источники к таблице. [pandas scaling](https://pandas.pydata.org/docs/user_guide/scale.html), [Polars streaming](https://docs.pola.rs/user-guide/concepts/streaming/), [DuckDB workload tuning](https://duckdb.org/docs/2.0/guides/performance/how_to_tune_workloads).

- `usecols` уменьшает количество читаемых столбцов. Явные dtypes устраняют смешанный вывод типов. Сохраняй ID и тикеры строками, иначе потеряются лидирующие нули. Смотри `df.memory_usage(deep=True).sum()` для приближённого размера объекта. Уменьшай типы чисел только после проверки диапазона и допустимой погрешности, `category` полезен для повторяющихся значений. В `read_csv` C-парсер с `low_memory=True` сам парсит частями, но всё равно возвращает весь DataFrame. `chunksize` даёт итератор. На pandas 3.0.5 `engine="pyarrow", chunksize=...` отклоняется, а `dtype_backend="pyarrow"` — отдельный параметр представления данных. [pandas.read_csv](https://pandas.pydata.org/docs/reference/api/pandas.read_csv.html), [pandas I/O](https://pandas.pydata.org/docs/user_guide/io.html), [pandas scaling](https://pandas.pydata.org/docs/user_guide/scale.html).
- `scan_*` строит lazy-план, позволяющий опустить выбор столбцов, фильтр и иногда ограничение строк к чтению. `read_csv(...).lazy()` сначала материализует CSV. Parquet позволяет по статистике пропускать row groups, заведомо не подходящие под фильтр. Hive-разбиение позволяет исключать целые партиции. CSV не даёт таких индексов, но ранняя проекция и фильтр сокращают downstream-работу. `schema_overrides` задаёт типы известных полей. При сомнительных колонках сначала прочитай их строками и явно разбери, веди счётчик ошибок конверсии. `infer_schema_length=None` на большом CSV может сканировать весь источник для вывода типов. [Polars scan_csv](https://docs.pola.rs/api/python/stable/reference/api/polars.scan_csv.html), [scan_parquet](https://docs.pola.rs/api/python/stable/reference/api/polars.scan_parquet.html), [оптимизации](https://docs.pola.rs/user-guide/lazy/optimizations/).
- `collect(engine="streaming")` делает DataFrame с итогом, `.sink_parquet(..., engine="streaming")` пишет большой итог по мере готовности. Даже sink не превращает произвольный глобальный оператор в операцию с фиксированной памятью. В Polars 2.x `auto` по умолчанию означает streaming. Явный `engine="streaming"` понятен при чтении и работает на свежих 1.x и 2.x. `collect(streaming=True)` встречается в старых статьях. Смотри оптимизированный логический `.explain()` и физический `q.show_graph(plan_stage="physical", engine="streaming", raw_output=True, show=False)`. `POLARS_VERBOSE=1` даёт диагностику. Для графического рендера нужен Graphviz, для просмотра строки DOT не нужен. [Polars streaming](https://docs.pola.rs/user-guide/concepts/streaming/), [Polars LazyFrame](https://docs.pola.rs/api/python/stable/reference/lazyframe/), [переход на 2.0](https://docs.pola.rs/releases/upgrade/2/).
- Проверка на Windows, Python 3.13 и Polars 2.1.0. Обычный `sort` в проверенном плане был streaming-узлом, но глобальная сортировка по-прежнему требует буферизации или spill. `group_by(maintain_order=True)` добавил сортировку. Групповой exact `quantile(0.5)` и `map_batches(lambda x: x)` со значениями по умолчанию дали `in-memory-map`. `join(..., validate="m:1")` с `engine="streaming"` проверил дубликат, но пошёл через `in-memory-join`. Это результаты конкретных планов и версии. Проверяй свой граф, особенно после Python UDF, оконных функций, сортировки, подсчёта уникальных значений, join и флагов порядка. Для `map_batches(streamable=True)` корректность зависит от независимости batch-вычислений. [Polars streaming](https://docs.pola.rs/user-guide/concepts/streaming/), [Polars LazyFrame](https://docs.pola.rs/api/python/stable/reference/lazyframe/).
- На входе сравнивай dtypes join key и null policy. `pandas.merge` сопоставляет null ключи с null, Polars по умолчанию пропускает это совпадение (`nulls_equal=False`). Укажи ожидаемую кардинальность (`validate="many_to_one"` pandas или `validate="m:1"` Polars). Для крупного streaming join можно отдельно проверить правую сторону через `group_by(key).len().filter(pl.col("len") > 1).limit(1)` и затем выполнить join без встроенного `validate`, если наборы неизменны между проверкой и join. Отслеживай долю unmatched и число строк. Many-to-many даёт произведение кратностей для каждого ключа. Если нужны только наличие или отсутствие ключа, Polars `semi`/`anti` избегают копирования правых колонок и размножения совпадений. [pandas.merge](https://pandas.pydata.org/docs/reference/api/pandas.merge.html), [Polars LazyFrame.join](https://docs.pola.rs/api/python/stable/reference/lazyframe/#polars.LazyFrame.join).
- DuckDB умеет сбрасывать промежуточные данные на диск. Направь `temp_directory` в локальную папку на SSD, задай `threads` и `memory_limit` с запасом для ОС и остальных процессов. DuckDB рекомендует уменьшать `memory_limit` примерно до 50–60% RAM при убийстве процесса ОС, потому что часть выделений обходит buffer manager. `preserve_insertion_order=false` полезен при больших чтениях и выгрузках, когда порядок строк не важен. Пиши большой результат `COPY (SELECT ...) TO ... (FORMAT PARQUET)`. Несколько blocking-операторов или holistic aggregation могут всё равно закончиться OOM. [DuckDB workload tuning](https://duckdb.org/docs/2.0/guides/performance/how_to_tune_workloads), [DuckDB OOM](https://duckdb.org/docs/2.0/guides/performance/oom).

Пример чтения CSV и материализации на диск. Имена полей условные. После появления брифа поменять их, форматы, фильтр и путь.

```python
import polars as pl

orders = (
    pl.scan_csv(
        "orders.csv",
        schema_overrides={"client_id": pl.String, "day": pl.String, "amount": pl.Float64},
    )
    .select("client_id", "day", "amount")
    .with_columns(pl.col("day").str.to_date("%Y-%m-%d"))
    .filter(pl.col("day") >= pl.date(2026, 9, 1))
)
orders.sink_parquet("orders_clean.parquet", engine="streaming")
daily = (
    pl.scan_parquet("orders_clean.parquet")
    .group_by("day")
    .agg(pl.len().alias("n_rows"), pl.col("amount").sum().alias("amount_sum"))
    .sort("day")
    .collect(engine="streaming")
)
```

Тест smoke на синтетических данных проверил типизированный `scan_csv`, сумму по датам и чтение результата `sink_parquet` на Windows. [Polars scan_csv](https://docs.pola.rs/api/python/stable/reference/api/polars.scan_csv.html), [Polars sink_parquet](https://docs.pola.rs/api/python/stable/reference/lazyframe/#polars.LazyFrame.sink_parquet).

### Выводы

- После выдачи данных зафиксируй размеры файлов, формат, кодировку, ID, временные поля, количество групп и дубликатов ключей. Возьми маленький репрезентативный срез и реши бизнес-задачу насквозь до оптимизации. Стабильную узкую таблицу кэшируй Parquet, когда её будет читать несколько человек или экспериментов. Для одного лёгкого запроса стоимость отдельной конвертации может не окупиться.
- Бенч сравнивает одинаковый результат, один набор колонок, одинаковые фильтры и типы. `perf_counter()` и RSS/peak working set процесса измеряй отдельно от setup, но дополнительно покажи время сквозного пути с загрузкой и записью. Каждый вариант запускай в свежем процессе, 3 повтора, отдельно укажи первый и повторные проходы для файлового кэша. Сверяй строки, контрольные суммы или значения с допуском и одинаковой политикой null. На Windows `psutil.Process().memory_info().peak_wset` доступен в проверенном psutil 7.2.2, замер включает весь процесс. Альтернатива — родитель периодически опрашивает RSS процесса и дочерних процессов, понимая, что короткие пики могут остаться между опросами. `tracemalloc` отслеживает Python allocations и не даёт честную общую RAM для нативных движков. [psutil](https://psutil.readthedocs.io/), [Python tracemalloc](https://docs.python.org/3/library/tracemalloc.html).
- В Windows рабочий набор может резко вырасти при одновременном запуске Polars, DuckDB и обучения. Начни с одного процесса. Если надо ограничить Polars, выставь `$env:POLARS_MAX_THREADS = "4"` до запуска Python и проверь `pl.thread_pool_size()`. Для DuckDB есть `SET threads`, для модели своё число потоков. При самостоятельном multiprocessing в Windows работает `spawn`, используй `if __name__ == "__main__":` и передавай пути к отдельным данным, а не DataFrame целиком. [Polars thread_pool_size](https://docs.pola.rs/api/python/stable/reference/api/polars.thread_pool_size.html), [Python multiprocessing](https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods), [DuckDB OOM](https://duckdb.org/docs/2.0/guides/performance/oom).

### Пробелы

- Числа «в X раз быстрее» зависят от размеров, кардинальности, диска и результата. Доступные данные хакатона ещё неизвестны. Локальная проверка была на маленьком искусственном примере, она подтверждает API и семантику без вывода о быстродействии на кейсе.

## Какие результаты можно точно сложить и когда шардировать

### Главное

Чанки ограничивают объём одной порции. Шарды по ключу размещают все совпадения в одной партиции. Для exact-подсчёта определи, какое состояние надо сохранить между чанками или где будут физически находиться все строки одного ключа. [pandas scaling](https://pandas.pydata.org/docs/user_guide/scale.html), [Arrow partitioned datasets](https://arrow.apache.org/docs/python/dataset.html).

### Подтверждённые сведения

| Операция | Что переносить между чанками |
| --- | --- |
| Количество, сумма, min/max | Частичные результаты по тем же группам. Для суммы реши, что делать с группой, где всё null |
| Среднее, доля, weighted mean | Для среднего сумма и число ненулевых наблюдений, для доли числитель и знаменатель, для weighted mean `Σ(x*w)` и `Σw` по одной валидной маске |
| Дисперсия | Объединяемое состояние `count, mean, M2` по Welford/Chan. На больших близких значениях формула через `sum(x²) - sum(x)²/n` теряет точность |
| Exact distinct, dedup | Множество ключей, если оно влезает. При большом множестве шардирование по ключу, затем дедуп внутри каждого shard |
| Group by с миллионами ID | Агрегаты корректно объединяемы, но аккумулятор растёт с кардинальностью. Шарды по ключу группы ограничивают рабочий размер |
| Join большой таблицы с малым справочником | Верифицировать уникальность ключа справочника и держать его в RAM, большую таблицу читать порциями |
| Join двух больших таблиц | Обе таблицы одинаково хешировать по ключу join, обработать пары шардов, писать результат по частям. Либо DuckDB с spill |
| Exact median/percentile | Пересчёт по общим данным или внешний sort/select. Усреднение локальных квантилей не даёт общий квантиль |
| Approx distinct/quantile | HyperLogLog / T-Digest там, где разрешена погрешность. Объединяют состояния sketch, а не опубликованные числа |

Источники к таблице. [pandas scaling](https://pandas.pydata.org/docs/user_guide/scale.html), [DuckDB aggregates](https://duckdb.org/docs/2.0/sql/functions/aggregates), [DuckDB spill](https://duckdb.org/docs/2.0/guides/performance/how_to_tune_workloads), [формулы объединения моментов Pébay, Sandia](https://www.osti.gov/servlets/purl/1028931).

Минимальный пример pandas с общей взвешенной средней и точным количеством уникальных клиентов. Политика примера принимает пропуски в числах как пустое поле, учитывает distinct по всем строкам и предполагает конечные числа с неотрицательными весами. Проверено на pandas 3.0.5.

```python
import math
import pandas as pd

weighted_sum = 0.0
weight_sum = 0.0
clients: set[str] = set()
with pd.read_csv(
    "orders.csv",
    engine="c",
    usecols=["client_id", "value", "weight"],
    dtype={"client_id": "string", "value": "Float64", "weight": "Float64"},
    keep_default_na=False,
    na_values=[""],
    chunksize=200_000,
) as reader:
    for chunk in reader:
        clients.update(chunk["client_id"].dropna().unique())
        valid = chunk["value"].notna() & chunk["weight"].notna()
        weights = chunk.loc[valid, "weight"]
        weighted_sum += float((chunk.loc[valid, "value"] * weights).sum())
        weight_sum += float(weights.sum())

weighted_mean = weighted_sum / weight_sum if weight_sum else math.nan
n_clients = len(clients)
```

Для `001,10,2` / `002,,9` / `001,20,1` / `003,15,1` с `chunksize=2` ответ — `13.75`, distinct `3`. Сумма двух локальных distinct даст `4`, поскольку `001` встречается в обеих порциях. Точность здесь означает полный охват строк без приближения, float-округление остаётся. Общий `set` занимает память пропорционально числу уникальных ключей. [pandas chunking](https://pandas.pydata.org/docs/user_guide/scale.html#use-chunking), [pandas.read_csv](https://pandas.pydata.org/docs/reference/api/pandas.read_csv.html).

- Дата или месяц в пути `ym=2026-09/part.parquet` полезны для запросов за период. Частые запросы за день оправдывают дневное разбиение при достаточном объёме каждой даты. Разбиение по миллионам ID создаёт много мелких файлов. Наблюдай размер файлов и перекос по разделам. Пиши в свежую папку прогона и сохраняй manifest с исходниками, схемой, ключами, числом bucket и строк. Так повторный запуск не смешивает старые и новые данные. [Arrow dataset](https://arrow.apache.org/docs/python/dataset.html), [PyArrow write_dataset](https://arrow.apache.org/docs/python/generated/pyarrow.dataset.write_dataset.html).
- Для равенства ключей используй `bucket = stable_hash(canonical_key) % B` с одинаковой нормализацией, null policy, версией хеша, seed и `B` для обеих сторон. Сам хеш нужен для размещения, join или dedup всегда выполняй по исходному нормализованному ключу. При сохранении shard между запусками Python `hash(str)` меняется между процессами. `Polars Expr.hash(seed=42)` гарантирует стабильность внутри версии Polars, поэтому для быстрых временных bucket закрепи одну версию и пересоздавай обе стороны вместе. Для переносимости используй, например, `int.from_bytes(hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest(), "little") % B`, либо CRC32 для простого ключа с проверкой равномерности. На больших таблицах не превращай Python-хеширование каждой строки в основное узкое место. Составной ключ сериализуй однозначно, например как JSON-массив типизированных полей с зафиксированными правилами представления null и Unicode. [Polars Expr.hash](https://docs.pola.rs/api/python/stable/reference/expressions/api/polars.Expr.hash.html), [Python salted hash](https://docs.python.org/3/reference/datamodel.html#object.__hash__), [hashlib](https://docs.python.org/3/library/hashlib.html#blake2).
- Выбирай число bucket по максимальному ожидаемому shard и доступной RAM. Сначала проверь распределение и top keys. Один горячий ключ остаётся в одном bucket при увеличении `B`. При этом левый или внутренний join можно разбить по частям одной стороны, если небольшая другая сторона реплицируется корректно. Для `full`/`right` join обрабатывай также bucket, присутствующие только справа. Для `distinct` по всей таблице шардируй по distinct-ключу, для grouped distinct можно шардировать по паре `(group, distinct_id)`, затем сложить количество уникальных пар для каждой группы. Для выбора последнего события определи время и стабильный tie-break, потом dedup внутри bucket. Временные окна с переходом между днями требуют соседнего контекста или предыдущего состояния. [pandas merge guide](https://pandas.pydata.org/docs/user_guide/merging.html), [Polars rolling](https://docs.pola.rs/user-guide/transformations/time-series/rolling/).
- `Polars n_unique` включает null, в pandas `nunique()` по умолчанию его пропускает. Если метрика считает только известные ID, в Polars используй `pl.col("client_id").drop_nulls().n_unique()`. Для быстрой EDA `approx_n_unique` в Polars или `approx_count_distinct` и `approx_quantile` в DuckDB. Явно подпиши приближённый результат. [Polars n_unique](https://docs.pola.rs/api/python/stable/reference/expressions/api/polars.Expr.n_unique.html), [Polars approx_n_unique](https://docs.pola.rs/api/python/stable/reference/expressions/api/polars.Expr.approx_n_unique.html), [DuckDB aggregate functions](https://duckdb.org/docs/2.0/sql/functions/aggregates).

Пример Hive-партиций Polars 2.1.0. `orders` из предыдущего примера. API `PartitionBy` помечен нестабильным, поэтому лучше закрепить протестированную версию. Поле партиции хранится в пути, `include_key=False` убирает дублирование внутри файлов. Явный `hive_partitioning=True` полезен с glob.

```python
orders.with_columns(pl.col("day").dt.strftime("%Y-%m").alias("ym")).sink_parquet(
    pl.PartitionBy("by_month", key="ym", include_key=False, max_rows_per_file=250_000),
    engine="streaming",
    mkdir=True,
)
september = pl.scan_parquet(
    "by_month/**/*.parquet", hive_partitioning=True, hive_schema={"ym": pl.String},
).filter(pl.col("ym") == "2026-09")
```

Пример проверен на Windows с Polars 2.1.0. Для временного hash-sharding замени `ym` выражением `(pl.struct(["client_id"]).hash(seed=42) % 16).alias("bucket")` и используй тот же ключ, число bucket и версию для обеих сторон. Количество и размеры файлов после записи проверить. [Polars PartitionBy](https://docs.pola.rs/api/python/stable/reference/api/polars.PartitionBy.html), [Polars scan_parquet](https://docs.pola.rs/api/python/stable/reference/api/polars.scan_parquet.html).

### Выводы

- Если справочник компактный, chunked many-to-one проще hash-sharding. Если нужна точная дедупликация на большом наборе ключей, hash-sharding или движок со spill предсказуемее глобального Python `set`. Если запросы чаще по месяцам, разделение по времени ускоряет их, а второй layout по hash имеет смысл при повторяющихся тяжёлых join по ключу.
- Раздели контроль расчёта и оптимизацию. Перед масштабированием сравни потоковый или шардированный результат с простым точным результатом на малом срезе с повторяющимся ключом между чанками, отсутствующим ключом, null, нулевой суммой весов и дубликатом в справочнике.

### Пробелы

- Выбор размера чанка и числа партиций зависит от плотности строк, ключевого перекоса и свободного диска. Примеры `200_000`, `250_000` и `16` иллюстрируют синтаксис. Подтверди их измерением, когда дадут данные.
