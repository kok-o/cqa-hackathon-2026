# SetW · запуск и сдача

Стоп-код по объявлениям организаторов — **8 ноября, 12:00 по Астане (UTC+5)**. Выдача контрольных чатов в **11:00** подтверждена в экспорте Q&A от 8 ноября (#3519); после 12:00 нельзя добавлять коммиты (#3560). Фактические способ получения, идентификатор набора и канал передачи ссылки сверить в Announcements. [Уточнения организаторов](ORGANIZER_CLARIFICATIONS.md) · [формат JSON 1.0](briefs/CQA_Student_Guide_Appendix_Results.pdf)

## До выдачи контроля

1. Откройте [текущую проверку оценок](ANNOTATION_QUALITY.md) и [архив проверки интерфейса](SETW_INTEGRATED.md). Запустите приложение из актуального корня проекта. Перед контролем поднимите отдельный сервер с пустой базой и официальным чек-листом.
2. Дайте двум участникам независимо разметить 15 чатов из локального `data/evaluations/setw-night-20261108/blind-review.md`. Файл ключа и предыдущие выводы откройте после фиксации ответов. Спорные случаи сверяйте с PDF и репликами.
3. Сверьте требования к публичному GitHub. В основном дереве должны быть код, README с запуском, описание подхода и `results/dev/results.json`. Второй обязательный файл `results/prod/results.json` появится после работы на реальном контроле. Проверьте публикуемые файлы и историю на ключи, подписанные URL, исходные клиентские диалоги и SQLite.
4. Назначьте ответственных за получение набора, запуск, проверку JSON и GitHub. Изменение видимости, push и передачу ссылки выполняет команда.

## Запуск на проверенной ветке

На актуальном checkout выполните `git rev-parse HEAD` и сверьте SHA с передачей. Скопируйте настройки подключения в локальный `.env` по [README](../README.md). Первый запуск — `./scripts/start.ps1 -Setup`. Следующий — `./scripts/start.ps1`. Приложение по умолчанию доступно на `http://127.0.0.1:8000`.

Актуальный код и DEV находятся в корне проекта. Старые checkout в
`scratch/setw-integrated` и `scratch/setw-release` сохраняют прежние проверки и
используются для отката; их оценки автоматически не обновляются.

На машине подготовки приложение запускается из корня:

```powershell
.\.venv\Scripts\python.exe scripts/run.py --port 8015
```

Для контрольного набора используйте отдельную базу, сохраняя рабочий DEV:

```powershell
$project = (Get-Location).Path
$controlDatabase = Join-Path $project 'data/runtime/control-20261108.sqlite3'
if (Test-Path -LiteralPath $controlDatabase) {
    throw 'База уже существует. Выберите новое имя для нового контрольного запуска.'
}
$previousDatabase = $env:CQA_DATABASE_PATH
try {
    $env:CQA_DATABASE_PATH = $controlDatabase
    & .\.venv\Scripts\python.exe scripts/run.py --port 8061
} finally {
    $env:CQA_DATABASE_PATH = $previousDatabase
}
```

Откройте `http://127.0.0.1:8015` для DEV или `http://127.0.0.1:8061` для контроля.
`GET /health` проверяет сервер, `GET /api/config` показывает модель и версию
промпта. После обновления кода или `.env` перезапустите сервер. Проверяйте версию
по API, а не только по имени модели. Текущая проверка разметки описана в
[ANNOTATION_QUALITY.md](ANNOTATION_QUALITY.md).

```powershell
python -X utf8 -m pip check
python -X utf8 -m unittest discover -s backend/tests -q
npm --prefix frontend ci
npm --prefix frontend run build
python -X utf8 scripts/fault_http_rehearsal.py
```

Для браузерных сценариев запустите `python scripts/ui_test_server.py --port 8021` в одном терминале. В другом, с установленным Playwright и браузером, выполните команды ниже. Сервер тестовый и создаёт временную SQLite.

```powershell
python -X utf8 scripts/browser_smoke.py --url http://127.0.0.1:8021
python -X utf8 scripts/browser_readiness.py --url http://127.0.0.1:8021
python -X utf8 scripts/browser_faults.py --url http://127.0.0.1:8021
python -X utf8 scripts/frontend_review_smoke.py --url http://127.0.0.1:8021
```

## Когда получены десять чатов

1. Сохраните выданный JSON в игнорируемый `data/raw/` под именем `PROD.json` или укажите другой фактический путь. При получении через тот же API можно вызвать `python scripts/fetch_dataset.py --group $controlGroup`, если организаторы подтвердили этот способ и `CQA_DEV_URL` локально указывает на выданный HTTPS-адрес. Скрипт сохраняет `data/raw/<group_id>.json`. Проверьте 10 записей, канал `agent chat`, исходные ID и фактический `dataset_group_id`.
2. На сервере контроля импортируйте файл, выберите группу и **официальный чек-лист**. Очистите языковой и прочие фильтры, чтобы кнопка «Оценить выборку» обработала всю группу. Дождитесь завершения. Повторяйте только задачи с временным сбоем после проверки статуса и настроек. При неполной оценке выясните причину по конкретному критерию.
3. В форме «Файл для сдачи» выберите раздел PROD, группу из исходника, `SetW`, `gpt-6-luna` и «Внешний API». Можно выгрузить тот же файл по HTTP. Сначала сохраняйте его во временный локальный каталог.

```powershell
# Из корня основного проекта на машине подготовки.
$project = (Get-Location).Path
$release = $project
$python = Join-Path $project '.venv/Scripts/python.exe'
$controlDataset = (Resolve-Path -LiteralPath (Join-Path $project 'data/raw/PROD.json')).Path
$source = Get-Content -LiteralPath $controlDataset -Raw -Encoding UTF8 | ConvertFrom-Json
$records = @($source.data.records)
if ($source.data.total_records -ne 10 -or $records.Count -ne 10 -or
    @($records | Where-Object { $_.channel -ne 'agent chat' }).Count -ne 0 -or
    @($records.conversation_id | Sort-Object -Unique).Count -ne 10) { throw 'Проверьте контрольный набор' }
$controlGroup = [Uri]::EscapeDataString($source.data.dataset_group_id)
$runtime = Join-Path $project 'data/runtime'
$staged = Join-Path $runtime 'control-export-validated.json'
New-Item -ItemType Directory -Force -Path $runtime | Out-Null
Invoke-WebRequest "http://127.0.0.1:8061/api/submission/export?group_id=$controlGroup&split=prod&team_name=SetW&model_name=gpt-6-luna&model_mode=external_api" -OutFile $staged
Push-Location -LiteralPath $release
try {
    & $python -X utf8 -m evaluation.submission validate --dataset $controlDataset --split prod --input $staged
    if ($LASTEXITCODE -ne 0) { throw 'Формат или состав файла не прошёл проверку' }
} finally { Pop-Location }
$candidate = Get-Content -LiteralPath $staged -Raw -Encoding UTF8 | ConvertFrom-Json
$candidate.results | Group-Object status | Select-Object Name,Count
```

4. Проверьте количество `ok` и каждую строку `error`. Валидный файл может содержать ошибки обработки. `null` в успешном ответе означает неприменимость критерия. Экспорт для организаторов берёт исходные решения модели по утверждённому чек-листу и совпадающему снимку диалога. История оценок с пересмотром скачивается отдельно. Текущий DEV пересчитан единым профилем v11; доказательства сохранены из ответов модели. Сверьте выгрузку работающего приложения с `results/dev/results.json`.
5. Перенесите проверенный контрольный файл в `results/prod/results.json`. Сверьте DEV по локальному исходнику. Подготовьте коммит из проверенной ветки. Обновите ту ветку публичного репозитория, на которую указывает переданная ссылка, по правилам команды. Откройте обе ссылки без входа до стоп-кода. Подтвердите передачу ссылки по объявленному каналу.

```powershell
Push-Location -LiteralPath $release
try {
    & $python -X utf8 -m evaluation.submission validate --dataset (Join-Path $project 'data/raw/DEV.json') --split dev --input results/dev/results.json
    if ($LASTEXITCODE -ne 0) { throw 'Проверьте DEV перед сдачей' }
    New-Item -ItemType Directory -Force -Path 'results/prod' | Out-Null
    Copy-Item -LiteralPath $staged -Destination 'results/prod/results.json'
    git add -- results/dev/results.json results/prod/results.json
    git status --short --branch
    git diff --cached --check
    git diff --cached --name-only
} finally { Pop-Location }
```

## Откат

Остановите изолированный сервер. Проверенная резервная ветка `codex/setw-verified` закреплена на `acbbf065f63a7481774ee56f5d9444b294ecfcd8` и уже открыта в `scratch/setw-release`. Из корня основного проекта запустите `./data/evaluations/setw-night-20261108/start-demo.ps1` и откройте `http://127.0.0.1:8036`. На другой машине соберите резервный checkout отдельно.

```powershell
git worktree add --detach scratch/setw-rollback acbbf065f63a7481774ee56f5d9444b294ecfcd8
npm --prefix scratch/setw-rollback/frontend ci
npm --prefix scratch/setw-rollback/frontend run build
```

В команде запуска подставьте `scratch/setw-rollback` и путь к рабочей копии `preflight.sqlite3` из локального каталога первого этапа. Скопируйте резервную базу в новый файл до запуска сервера. История оценок и исходный DEV восстановимы из снимка и резервного коммита.
