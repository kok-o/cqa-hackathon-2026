# DuckDB и SQL аналитика

Проверено 5–6 ноября 2026 года. SQL ниже выполнен на синтетических данных в DuckDB 1.4.4 на Windows. Параметры памяти, схема полей и даты служат примерами. В субботу их нужно заменить по фактическим данным и условиям.

## Когда брать DuckDB и как уложиться в RAM

### Takeaway

DuckDB удобен, когда нужно соединить несколько файлов, проверить ключи, посчитать временные окна и сохранить крупный результат. Самый дешёвый путь обычно выглядит так — отобрать столбцы и строки, типизировать вход один раз, затем выполнять запросы по компактному Parquet.

### Cited Findings

- **Выбор инструмента.** DuckDB — SQL, файловые источники, большие группировки, join, ASOF и сохранение результатов на диск. Polars LazyFrame — удобный вариант, когда команда быстрее пишет выражения в Python; `scan_csv`/`scan_parquet`, projection/predicate pushdown, `collect(engine="streaming")`, проверка плана, так как отдельные операторы откатываются в in-memory. Pandas — небольшие итоговые таблицы, визуализация, интерфейсы ML; `chunksize` подходит для независимых блоков и агрегатов с компактным состоянием. Сравнивать надо на своём запросе с тем же форматом входа и выхода. [DuckDB Tuning](https://duckdb.org/docs/guides/performance/how-to-tune-workloads), [Polars Lazy Optimizations](https://docs.pola.rs/user-guide/lazy/optimizations/), [Polars Streaming](https://docs.pola.rs/user-guide/concepts/streaming/), [pandas Scaling](https://pandas.pydata.org/docs/user_guide/scale.html)
- **Старт на ноутбуке.** Локальная БД и scratch на SSD вне папки OneDrive, достаточно свободного места для временных файлов и результата. Начать с двух потоков и порядка 50–60% физической RAM под лимит, если открыты браузер и IDE. Для 8 ГБ начать примерно с 3–4 ГБ и измерить. Если система тормозит или убивает процесс, снизить число потоков и лимит, проверить диск и промежуточную кардинальность. Указанные числа — стартовые эвристики. [DuckDB OOM](https://duckdb.org/docs/current/guides/performance/oom), [DuckDB Tuning](https://duckdb.org/docs/guides/performance/how-to-tune-workloads)
- **Что реально проливается на диск.** DuckDB умеет выполнять крупные группировки, сортировки, join и многие окна с временным диском. `memory_limit` ограничивает буферный менеджер, а не полный RSS Python-процесса. `list`, `string_agg`, сортирующие или сложные агрегаты и несколько блокирующих стадий одного запроса могут вылететь даже со scratch. Урезать входы, предварительно агрегировать, разделить тяжёлый запрос на этапы с материализацией. `preserve_insertion_order=false` помогает чтению/записи, когда порядок задаётся явным `ORDER BY` там, где он нужен. [DuckDB Tuning](https://duckdb.org/docs/guides/performance/how-to-tune-workloads), [Memory Management](https://duckdb.org/2024/07/09/memory-management), [Pragmas](https://duckdb.org/docs/lts/configuration/pragmas), [Order Preservation](https://duckdb.org/docs/current/sql/dialect/order_preservation)
- **Результат.** Крупный результат писать `COPY (SELECT ...) TO 'out.parquet' (FORMAT parquet, COMPRESSION zstd)` напрямую. Полный `.df()`/`fetchall()` превращает выдачу в объекты в памяти Python. Для анализа использовать `LIMIT` после осмысленного фильтра или отдать небольшую агрегацию. Если нужно потреблять результат по порциям, проверить методы Arrow record batch на своей версии или использовать `fetchmany(n)` для строк. Сама выдача по частям не убирает тяжёлые sort/join в плане. [Python Conversion](https://duckdb.org/docs/current/clients/python/conversion), [Python DB API](https://duckdb.org/docs/api/python/dbapi), [Arrow Export](https://duckdb.org/docs/current/guides/python/export_arrow)
- **Parquet.** Отобрать колонки и типизировать данные при первом чтении CSV. В последующих запросах Parquet позволяет читать только нужные колонки и пропускать группы строк по статистике, если фильтр и данные подходят. Прямой диапазон `event_ts >= start AND event_ts < end` обычно удобнее оборачивания каждой строки в `CAST(event_ts AS DATE)`. Начать с одного/нескольких достаточно крупных файлов и групп примерно 100 тыс.–1 млн строк, затем смотреть размер и план. Сортировка по часто фильтруемому столбцу может улучшить пропуск групп, но сама требует времени и диска. [Parquet Overview](https://duckdb.org/docs/extensions/parquet), [File Formats](https://duckdb.org/docs/lts/guides/performance/file_formats)
- **Физические партиции.** `COPY (SELECT ..., date_trunc('month', event_ts)::DATE AS event_month FROM stage) TO 'new_run_dir' (FORMAT parquet, PARTITION_BY (event_month))` и `read_parquet('new_run_dir/*/*.parquet', hive_partitioning = true)` полезны, если фильтр почти всегда по месяцу и месяцы достаточно велики. Много мелких файлов замедляет чтение. Документация DuckDB предлагает ориентир порядка 100 МБ на партицию, а не деление по каждому клиенту. Пусть каждый прогон пишет в собственную папку, иначе старый shard легко задвоит данные. `union_by_name=true` лечит перестановку колонок, но различия схем и смыслов надо отдельно сверять. [Partitioned Writes](https://www.duckdb.org/docs/current/data/partitioning/partitioned_writes), [Hive Partitioning](https://duckdb.org/docs/current/data/partitioning/hive_partitioning), [Reading Multiple Files](https://duckdb.org/docs/stable/data/multiple_files/overview.html)
- **Профилирование.** `EXPLAIN SELECT …` для плана, `EXPLAIN ANALYZE SELECT …` выполняет запрос, показывает фактические кардинальности и время операторов. Искать раздутый join, чтение лишних столбцов, огромный sort/window и повторное чтение CSV. Сначала замерить один представительный сценарий с фиксированным размером входа и одинаковым выходом, отдельно первый и повторный запуск. [EXPLAIN ANALYZE](https://duckdb.org/docs/current/guides/meta/explain_analyze)

### Inferences

Пример стартовой сессии. Существование папки и доступное место проверить перед прогоном.

```sql
SET threads = 2;
SET memory_limit = '4GB';
SET temp_directory = 'C:/local-datathon/duckdb-tmp';
SET max_temp_directory_size = '25GB';
SET preserve_insertion_order = false;
SELECT version(), current_setting('memory_limit'), current_setting('temp_directory');
```

- Внешнее хеш-шардирование нужно, если одной тяжёлой стадии всё ещё не хватает памяти или необходимо повторно использовать подготовленные шарды. Распределить обе стороны join по одному нормализованному ключу и одному числу бакетов, например `md5_number_lower(client_id) % 32`. Обрабатывать по одному бакету, регистрировать пустые ID отдельно, проверять крупнейший бакет и горячие ключи. Обычный DuckDB `hash` может менять реализацию между версиями. Временные месячные шарды разрывают историю клиента на границах. Точные distinct/квантили нельзя складывать по произвольным чанкам, если один ключ попал в разные чанки. Среднее при склейке берётся из общей суммы и количества. [Utility Functions](https://www.duckdb.org/docs/preview/sql/functions/utility)
- Устойчивый запасной маршрут — уменьшить вход до фактов и ключей, сохранить компактную агрегацию, затем сделать график и отдать воспроизводимый SQL. Ждать окончания большого запроса без проверки плана обычно дороже, чем замерить одну стадию.

### Gaps

- Размеры датасета и ресурсы команды неизвестны. Эти параметры требуют проверки на фактических данных. Тест синтетики проверяет семантику, а скорость и объём scratch нужно измерить на месте.

## Как принять сырьё и поймать потери

### Takeaway

Первые 15–30 минут стоит потратить на словарь таблиц, ключи и контрольные суммы. От этого зависит, можно ли верить воронке и денежным итогам.

### Cited Findings

- **CSV.** Снять `SELECT * FROM sniff_csv('first.csv')` или `SELECT Prompt FROM sniff_csv('first.csv')` и вручную проверить разделитель, header, кавычки и encoding. При стабильной схеме переопределить ID через `types={'client_id':'VARCHAR'}`, при неизвестной — `all_varchar=true` и отдельная валидация преобразований. Ведущие нули, идентификаторы больше 2^53 и идентификаторы с буквами должны пережить вход без преобразования в число. `sample_size=-1` читает весь файл для вывода схемы, поэтому не делать это по умолчанию на гигабайтах. В CSV с multiline quotes физическая строка не эквивалентна записи. [CSV Auto Detection](https://duckdb.org/docs/current/data/csv/auto_detection), [CSV Import](https://duckdb.org/docs/stable/data/csv/overview)
- **Грязные данные.** Первый прогон строгий. Если частичный импорт оправдан, `store_rejects=true` сохраняет проблемы в `reject_scans` и `reject_errors`, при этом строки пропускаются. Считать distinct `(scan_id, file_id, line)` внутри одной полной попытки, поскольку у одной записи может быть несколько записей об ошибке. Projection pushdown может убрать проверку невыбранной колонки при типизированном чтении. `TRY_CAST`/`try_strptime` дают NULL, поэтому считать ошибки отдельно от исходных NULL и пустых строк. `union_by_name=true` не заменяет сверку схемы и null rates по файлам. [Faulty CSV Files](https://duckdb.org/docs/lts/data/csv/reading_faulty_csv_files), [Typecasting](https://duckdb.org/docs/stable/sql/data_types/typecasting), [Date Format](https://duckdb.org/docs/preview/sql/functions/dateformat)
- **Даты.** Разделить бизнес-время `event_ts` и время доступности данных `recorded_at` или `available_at`. Для ISO со смещением использовать `TIMESTAMPTZ` и явно выбрать отчётную зону. Для naive дат подтвердить зону у постановщика и типизировать, прежде чем соединять с UTC. Явно задать единицы unix epoch, проверив min/max. В DuckDB преобразование `TIMESTAMPTZ` и `DATE` зависит от сессионной зоны, поэтому не смешивать `DATE` и timezone timestamps в фильтре. Полуоткрытые окна `[start, end)` упрощают стыковку периодов. [Timestamp Types](https://duckdb.org/docs/lts/sql/data_types/timestamp), [Timestamp Issues](https://duckdb.org/docs/stable/guides/sql_features/timestamps), [Timestamp Functions](https://duckdb.org/docs/current/sql/functions/timestamp)
- **Деньги.** `DECIMAL(p,s)` или целое число в согласованных минимальных единицах. `DECIMAL(18,4)` здесь лишь пример. Проверить диапазон и допустимое число десятичных знаков, так как приведение может округлить. Для валюты определить правила кодов, для разделителей — точный формат каждого файла. Не удалять точку или запятую глобально. Отдельные суммы по валютам до согласованного FX, возвраты с корректным знаком, отдельно комиссия и оборот. Деление DECIMAL в DuckDB даёт результат с плавающей точкой, поэтому отдельно зафиксировать округление финансового отчёта. [DuckDB Numeric Types](https://duckdb.org/docs/current/sql/data_types/numeric)
- **Join.** Перед join проверить уникальность стороны, которая должна быть «1». Пример `SELECT client_id, count(*) FROM dim GROUP BY 1 HAVING count(*) > 1`. Уточнить зерно и полный состав ключа, агрегировать до нужного уровня, отдельно измерить незамэтченные факты. В 1:N не суммировать повторённую родительскую сумму. Если нужен лишь факт существования, `SEMI JOIN` или `EXISTS` возвращает каждую подходящую левую строку один раз, а `ANTI JOIN` помогает снять незамэтченные записи. Обычное `=` не сопоставляет NULL с NULL. [DuckDB Join Clauses](https://duckdb.org/docs/stable/sql/query_syntax/from)
- **ASOF.** Для последней известной котировки `ASOF LEFT JOIN quotes q ON t.symbol=q.symbol AND t.currency=q.currency AND t.event_ts >= q.quote_ts`. На левую строку возвращается не более одной правой. Явно вывести оба времени, особенно если используешь `USING`, проверить разницу между ними, заранее разрешить одинаковые котировки на одном ключе и времени по воспроизводимому бизнес-приоритету. Если котировка опубликована позже наблюдения, одного условия по её effective timestamp мало. Отдельно проверить условие доступности, пропуски и чрезмерно старые курсы. [AsOf Join](https://duckdb.org/docs/preview/guides/sql_features/asof_join), [Join Clauses](https://duckdb.org/docs/stable/sql/query_syntax/from)

### Inferences

- Манифест по каждому файлу/таблице — путь и версия источника, байты и mtime, опционально SHA-256, формат/разделитель/encoding, grain, бизнес-ключи, сырой и ожидаемый тип, принятые записи и reject counts, пустые и некорректные ключи, дубли и пересечения файлов, min/max времени и зона, список валют и суммы по ним, результат преобразования, версия движка. Физические строки CSV не использовать как независимую сверку записей при переносах внутри кавычек. Полезно хранить исходный ID рядом с нормализованным и проверять коллизии нормализации. При объединении нескольких файлов сохранить имя источника, например через виртуальный `filename`, с учётом одноимённых столбцов.
- Шаблон проверки строк. Для входа с подтверждённым форматом CSV можно дополнить `delim`, `quote`, `escape` и `nullstr`. Времена в примере уже в единой документированной зоне, локальные десятичные разделители обработать отдельно. После аудита сохранить typed данные с согласованной политикой ошибок и не терять строки без подсчёта.

```sql
WITH raw AS (
    SELECT client_id AS raw_id, event_time AS raw_time, amount AS raw_amount,
           currency AS raw_currency, filename AS source_path
    FROM read_csv('raw.csv', all_varchar = true, filename = true, header = true)
),
typed AS (
    SELECT *,
           nullif(trim(raw_id), '') AS client_id,
           try_strptime(nullif(trim(raw_time), ''),
                        ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S']) AS event_ts,
           try_cast(nullif(trim(raw_amount), '') AS DECIMAL(18, 4)) AS amount,
           upper(nullif(trim(raw_currency), '')) AS currency
    FROM raw
)
SELECT source_path, count(*) AS parsed_rows,
       count(*) FILTER (WHERE client_id IS NULL) AS empty_id,
       count(*) FILTER (WHERE nullif(trim(raw_time), '') IS NOT NULL
                          AND event_ts IS NULL) AS invalid_time,
       count(*) FILTER (WHERE nullif(trim(raw_amount), '') IS NOT NULL
                          AND amount IS NULL) AS invalid_amount
FROM typed
GROUP BY source_path;
```

- Аудит 1:1 / N:1 после соединения. Пустой результат означает совпадение количества и подписанной суммы в каждой валюте без незамэтченных строк. Если незамэтченные ожидаются, сделать для них отдельный контроль и оговорить допустимость. Для обнаружения компенсаций дополнительно сравнивать уникальные ID фактов и суммы абсолютных значений. Перед этим отдельно проверить уникальность ключа справочника.

```sql
WITH joined AS (
    SELECT l.*, d.segment, d.client_id IS NOT NULL AS matched
    FROM clean_ledger l LEFT JOIN clients_dim d USING (client_id)
),
before_join AS (
    SELECT currency, count(*) AS n, sum(amount_signed) AS amount
    FROM clean_ledger GROUP BY currency
),
after_join AS (
    SELECT currency, count(*) AS n, sum(amount_signed) AS amount,
           count(*) FILTER (WHERE NOT matched) AS unmatched
    FROM joined GROUP BY currency
)
SELECT coalesce(b.currency, a.currency) AS currency,
       b.n AS n_before, a.n AS n_after,
       b.amount AS amount_before, a.amount AS amount_after,
       a.unmatched
FROM before_join b FULL JOIN after_join a
  ON b.currency IS NOT DISTINCT FROM a.currency
WHERE b.n IS DISTINCT FROM a.n
   OR b.amount IS DISTINCT FROM a.amount
   OR a.unmatched > 0;
```

### Gaps

- Без словаря партнёра нельзя определить зерно таблиц, какие статусы считать успехом, что значит каждая денежная колонка, какую зону использовать и какие пропуски допустимы. В начале кейса выяснить у ментора именно эти вопросы.

## Шаблоны воронки и RFM

### Takeaway

Воронка должна подтверждать, что действия случились в нужном порядке в рамках одной заявки и окна. RFM должен фиксировать срез, зерно заказа и валюту, иначе красивый сегмент окажется артефактом.

### Cited Findings

- **Близкий прецедент Freedom Broker.** Победители трека NU Datathon’25 искали потери в онбординге, сегментировали клиентов и предлагали продуктовые действия. Это оправдывает тренировку на клиентских событиях и проверку метрик среза. [Победитель трека](https://kz.linkedin.com/in/adilkhanalpysbay), [итоги DataSci](https://www.linkedin.com/posts/datasci-kz_datathon-datascience-hackathon-activity-7377709238439182336-b0dC)
- **SQL-примитивы.** DuckDB ASOF поддерживает `<` для ближайшего следующего события, `>=` для последнего предшествующего, а левый вариант удерживает ключ без совпадения. `FILTER` на агрегате даёт раздельные счётчики и суммы без склейки групп. [Join Clauses](https://duckdb.org/docs/stable/sql/query_syntax/from), [Aggregate Functions](https://www.duckdb.org/docs/current/sql/functions/aggregates)

### Inferences

**Воронка signup → KYC → пополнение → первая сделка.** Гранулярность `client_id + application_id`, якорь первый signup заявки. Все четыре даты строго возрастают, окно ровно семь дней от signup, верхняя граница не входит. Только наблюдаемые полностью когорты, cutoff данных в примере 4 октября 00:00. Каждый следующий шаг ищет ближайшее подходящее событие той же заявки, NULL останавливает последовательность. С такой общей верхней границей ранний подходящий шаг оставляет максимум возможностей дальше. Если у каждого шага отдельный максимальный промежуток или нужны попытки после нового signup, тогда потребуется другая модель последовательностей и явный идентификатор попытки.

```sql
WITH signups AS (
    SELECT client_id, application_id, min(event_ts) AS signup_ts
    FROM events
    WHERE event_name = 'signup'
      AND client_id IS NOT NULL AND application_id IS NOT NULL
    GROUP BY client_id, application_id
    HAVING min(event_ts) >= TIMESTAMP '2026-09-01'
       AND min(event_ts) <  TIMESTAMP '2026-10-01'
       AND min(event_ts) + INTERVAL '7 days' <= TIMESTAMP '2026-10-04'
),
kyc AS (
    SELECT s.*,
           CASE WHEN e.event_ts < s.signup_ts + INTERVAL '7 days'
                THEN e.event_ts END AS kyc_ts
    FROM signups s
    ASOF LEFT JOIN (SELECT client_id, application_id, event_ts FROM events
                    WHERE event_name = 'kyc_success') e
      ON s.client_id = e.client_id AND s.application_id = e.application_id
     AND s.signup_ts < e.event_ts
),
fund AS (
    SELECT k.*,
           CASE WHEN e.event_ts < k.signup_ts + INTERVAL '7 days'
                THEN e.event_ts END AS funded_ts
    FROM kyc k
    ASOF LEFT JOIN (SELECT client_id, application_id, event_ts FROM events
                    WHERE event_name = 'fund_success') e
      ON k.client_id = e.client_id AND k.application_id = e.application_id
     AND k.kyc_ts < e.event_ts
),
trades AS (
    SELECT f.*,
           CASE WHEN e.event_ts < f.signup_ts + INTERVAL '7 days'
                THEN e.event_ts END AS first_trade_ts
    FROM fund f
    ASOF LEFT JOIN (SELECT client_id, application_id, event_ts FROM events
                    WHERE event_name = 'trade_success') e
      ON f.client_id = e.client_id AND f.application_id = e.application_id
     AND f.funded_ts < e.event_ts
)
SELECT date_trunc('week', signup_ts)::DATE AS cohort_week,
       count(*) AS applications,
       count(kyc_ts) AS reached_kyc,
       count(funded_ts) AS reached_funding,
       count(first_trade_ts) AS reached_trade,
       count(first_trade_ts)::DOUBLE / nullif(count(*), 0) AS signup_to_trade
FROM trades
GROUP BY 1 ORDER BY 1;
```

- Если нужен показатель по **клиентам**, заранее выбрать правило одной заявки на клиента или отдельно формализовать несколько попыток. `count(*)` здесь означает заявки. Если `application_id` отсутствует, совместно согласовать окно/сессию и показать ограничение. Для секундных совпадений использовать реальную последовательность `event_seq`, если она есть. `ROW_NUMBER` на надёжных `event_ts, event_seq` позволяет ASOF по порядковому номеру с отсечкой по реальному времени. Случайный UUID не доказывает порядок. Если порядок неизвестен, показать долю совпадений и явно согласовать трактовку.
- Для крупных входов заранее выделить нужные события, диапазон и ключи в staging. Сохранить достаточно истории, чтобы определить истинный первый signup. Сужение по датам до `min` может превратить повторную регистрацию старого клиента в новую. `date_trunc('week', …)` размечает неделю когорты, начало недели иногда попадает в предыдущий месяц. Конверсию между шагами выводить со знаменателем предыдущего шага через `NULLIF(n, 0)`.
- Проверка на синтетике DuckDB 1.4.4 — 3 допустимые заявки → 2 KYC → 2 пополнения → 1 сделка. Отсеялись операция до предшествующего шага, событие другой заявки, равный таймстамп, сделка ровно на верхней границе и незрелая когорта.

**RFM.** В примере `clean_ledger` содержит уникальные операции, уже прошедшие валидацию статуса и знака, одинаковую точность денег внутри валюты. `buy` означает подтверждённую покупку, `refund` — отдельную операцию с отрицательным знаком. R — дни от последней покупки за доступную историю до cutoff, F — число уникальных order_id в окне 90 дней, M — нетто-сумма операций за это окно. Отдельная строка на клиента и валюту. `recorded_at` отсекает события, которых ещё не было видно в момент среза. Если такой колонки нет, проверить, когда стала доступна информация, и оговорить возможное запаздывание.

```sql
WITH params AS (
    SELECT TIMESTAMP '2026-11-01 00:00:00' AS cutoff
),
eligible AS (
    SELECT l.*, p.cutoff, p.cutoff - INTERVAL '90 days' AS start_ts
    FROM clean_ledger l CROSS JOIN params p
    WHERE l.event_ts < p.cutoff AND l.recorded_at < p.cutoff
      AND l.kind IN ('buy', 'refund')
      AND l.client_id IS NOT NULL AND l.currency IS NOT NULL
)
SELECT client_id, currency,
       date_diff('day',
           max(event_ts::DATE) FILTER (WHERE kind = 'buy'),
           max(cutoff)::DATE
       ) AS recency_days,
       count(DISTINCT order_id) FILTER (
           WHERE kind = 'buy' AND event_ts >= start_ts
       ) AS purchase_orders_90d,
       coalesce(sum(amount_signed) FILTER (
           WHERE event_ts >= start_ts
       ), 0::DECIMAL(38, 4)) AS net_amount_90d,
       count(*) FILTER (
           WHERE kind = 'refund' AND event_ts >= start_ts
       ) AS refund_events_90d
FROM eligible
GROUP BY client_id, currency;
```

- Для клиентов без операций взять явный universe из справочника и сделать левый join по нужной аналитической гранулярности. Recency для отсутствия покупки остаётся NULL, если нет согласованного special bucket. `count(DISTINCT order_id)` не включает NULL, поэтому отдельно проверить, что ID есть для покупок, и не считать строки частичных исполнений как отдельные заказы. Профиль сегмента сравнивать внутри сопоставимой валюты или пересчитать FX перед свёрткой, ведя отдельный контроль пропущенных курсов.
- Уточнить трактовку возвратов. Денежный поток по дате возврата и экономика исходной покупки дают разные окна. При определении «действующего заказа» полные отмены должны менять не только M, но по заданию могут влиять на F и R. У живого снапшота с ревизиями сначала выбрать последнюю известную версию операции на cutoff. Эффект решения проверять на будущем окне после признаков.
- На фикстуре две строки одного ордера на 10 и 2 KZT и возврат −3 дали F=1 и M=9 KZT, USD остался отдельной строкой. Событие ровно на cutoff и поздно попавшее в источник не включились. CSV-проверка сохранила `0007` и `9007199254740993`, раздельно зафиксировала ошибки даты и суммы. Reconciliation нашёл удвоение строк от дубликата справочника. Экспорт и повторное чтение Parquet тоже прошли.

### Gaps

- Условия кейса 7–8 ноября могут предусматривать другие статусы, окна, ключи, валюты и форматы результата. Шаблоны дают проверенный пример логики на синтетических данных, дальнейшая адаптация зависит от ТЗ.
