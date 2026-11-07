# API приложения CQA

Все пути относительные. Ошибки возвращают `{ "detail": "понятное сообщение" }`, некорректные данные — HTTP 422, отсутствующий объект — 404, конфликт версии или повторного ключа — 409. Ключи модели и подписанные URL конфигурации не возвращаются. JSON-файл импорта имеет исходную структуру `DatasetExport` из `backend/cqa/models.py`.

| Метод | Путь | Формат |
|---|---|---|
| GET | `/health` | `{status, phase, version}` |
| GET | `/api/config` | `{provider_configured, provider, model, mode, prompt_version, team_name}` |
| POST | `/api/datasets/import` | `{dataset: DatasetExport}` либо `{source: "dev"}` / `{source: "demo"}`; результат `{group_id, imported, updated, unchanged, total, current_group_total, retained_from_previous_import}` |
| GET | `/api/conversations` | Массив `{conversation_id,group_id,channel,language,message_count,preview,latest_evaluation_id,latest_score,latest_status,latest_gross_error}`; фильтры `group_id`, `language`, `channel`, `gross_error=true/false` |
| GET | `/api/conversations/{id}?group_id=DEV` | Полный `Conversation` |
| GET | `/api/audio-source?group_id=DEV&conversation_id=...&evaluation_id=...` | `{audio_url, resolved_from:"html"|"direct"}`; `evaluation_id` необязателен, выбирает запись из исторического снимка |
| GET | `/api/checklists` | Массив всех `ChecklistVersion`, новые версии первыми |
| POST | `/api/checklists` | `ChecklistVersion`; сервер устанавливает `version=1` |
| POST | `/api/checklists/{id}/versions` | `{title,provenance,criteria}`; сервер увеличивает версию |
| POST | `/api/evaluation-jobs` | `{conversation_id,group_id,checklist_id,checklist_version,idempotency_key?}`; HTTP 202, объект задачи |
| GET | `/api/evaluation-jobs/{id}` | `{job_id,status,error,evaluation_id,error_code?,model_info?}`; status: `queued/running/completed/failed`; необязательные диагностика и usage сохраняются также для сбоя evaluator |
| GET | `/api/evaluations` | Массив полных оценок; фильтры `group_id`, `conversation_id`, `gross_error=true/false` |
| GET | `/api/evaluations/export` | Тот же массив как скачиваемый JSON; те же фильтры |
| GET | `/api/evaluations/{id}` | Полная оценка |
| POST | `/api/evaluations/{id}/appeals` | `{criterion_id,comment,idempotency_key?}` → апелляция |
| POST | `/api/appeals/{id}/decision` | `{action:"uphold"|"replace",reason,replacement_result:CriterionResult|null,expected_revision,idempotency_key?}` → обновлённая полная оценка |

Полная оценка:

```text
{
  evaluation_id, conversation: Conversation, checklist: ChecklistVersion,
  original_results: CriterionResult[], results: CriterionResult[],
  original_score: Score, score: Score, model_info: object,
  revision: number, appeals: Appeal[], created_at
}
```

`Score`: `{total:number|null,status:"complete"|"incomplete"|"not_applicable",coverage:number,earned_weight:number,applicable_weight:number,criteria:[{criterion_id,status,normalized_score:number|null,weight,contribution:number|null}],sections:Record<string,number|null>,gross_error:boolean}`. Coverage — доля критериев без `needs_review` от 0 до 1. Contribution — вклад в итоговую шкалу 0–100, доступен только при полном результате. При `needs_review` итог отсутствует. Все N/A или нулевые применимые веса также не создают фиктивный итог. Формула официального чек-листа: глобальное взвешенное среднее нормализованных ответов, N/A исключаются; грубая ошибка остаётся отдельным флагом без автоматического обнуления итога.

`Appeal`: `{appeal_id,evaluation_id,criterion_id,comment,status:"open"|"resolved",created_at,decision:null|{action,reason,replacement_result,created_at}}`. Начальная revision оценки — 1; решение повышает её на 1. Снимки исходных диалога, чек-листа, результатов и итогов неизменяемы. Новая версия чек-листа не изменяет прежнюю оценку.

Повтор с тем же idempotency_key возвращает прежнюю операцию. Другое содержимое с уже использованным ключом возвращает 409. Ключи необязательны, но рекомендуются при повторной отправке UI. После сбоя сервера незавершённые задачи становятся `failed`; повторная оценка создаётся новым ключом.

UI сохраняет очередь и ключи до отправки в `sessionStorage` этой вкладки:
ID группы/диалога, чек-лист и его версию, модель/промпт, job ID и состояние.
Транскрипты и секреты туда не записываются. Потеря ответа, временный сбой,
некорректный успешный ответ и обновление вкладки позволяют продолжить очередь
с теми же ключами; подтверждённый `failed` автоматически не переоценивается.
До продолжения повторно проверяются текущие модель и версия промпта.
Это восстановление вкладки, а не распределённая очередь и не сохранение после
закрытия браузера. Источник задачи фиксируется сервером при первом принятии
POST, а не при создании очереди в браузере.

Автоматический повтор браузера ограничен тремя HTTP-попытками для GET и POST
с ключом идемпотентности. `Retry-After` до 5 секунд соблюдается; более длинная
пауза возвращает ошибку и оставляет продолжение пользователю. Каждая попытка
ограничена 30 секундами. У evaluator свой лимит в две модельные попытки,
общий тайм-аут на каждую и обработка `Retry-After` до меньшего из 30 секунд
и настроенного тайм-аута. Более длинный cooldown не вызывает ранний повтор.
Неизвестная usage/цена после обрыва остаётся неизвестной, не нулевой.

Импорт дополняет группу и обновляет совпавшие ID; он не удаляет старые диалоги,
отсутствующие в новом файле. `total` — число строк файла, `current_group_total` —
число сохранённых диалогов группы, `retained_from_previous_import` — число
оставшихся ID, которых нет в текущем файле. UI предупреждает о таком составе.
Исходные ID сохраняются, в том числе Unicode и `/`, `?`, `#`, `%`; путь к диалогу
нужно URL-кодировать. Пустые/пробельные ID и полностью пустой транскрипт
отклоняются до записи и вызова модели. Отдельные пустые реплики сохраняются,
чтобы номера исходных сообщений не сдвигались.

Аудио открывается по запросу пользователя. Резолвер читает ограниченную по
размеру HTML-страницу официального плеера и извлекает единственный аудиоисточник;
скрипты не выполняются. Разрешены два проверенных HTTPS-хоста организаторов,
перечисленных в `backend/cqa/audio.py`, включая проверку перенаправлений.
Произвольный URL в запросе не принимается. Подписанные ссылки возвращаются
только для воспроизведения с `Cache-Control: no-store`; ответы об ошибках их не
содержат. Нет записи — 404, неподдерживаемый источник/формат — 422, сбой
источника — 502, тайм-аут — 504. Историческая оценка использует свой снимок URL.
AI работает по транскрипту; прослушивание и точная временная привязка реплик
не заявляются как выполненная моделью обработка.

## Официальная сдача JSON 1.0

GET `/api/submission/export?group_id=DEV&split=dev&team_name=SetW&model_name=gpt-6.1-sol&model_mode=external_api` скачивает официальный `results.json`. `model_mode` обязателен: local / external_api / hybrid; известный провайдер проверяется на соответствие. Имя команды можно задать через CQA_TEAM_NAME.

Выбирается последний исходный результат указанной модели по каждому диалогу, рассчитанный по утверждённой полной конфигурации критериев. Изменённые через UI инструкции/веса не подменяют исходный официальный прогон. Апелляции не заменяют исходные ответы модели в этом экспорте. Отсутствующая/неполная оценка даёт status=error и score=null. DEV требует 65 записей (60 чатов и 5 звонков), PROD — 10 чатов. Исторический `/api/evaluations/export` остаётся отдельным форматом приложения.

Результат должен совпадать с текущим полным снимком исходного диалога.
Если старая задача завершилась позже новой, экспорт и сводка ищут последнюю
подходящую оценку среди истории; устаревшая запись не скрывает актуальную.

Criterion дополнительно поддерживает `allowed_values:number[]|null` для точных ступеней шкалы и `critical_answer:boolean|null` для согласования булевого ответа с флагом грубой ошибки. Официальные ступени консультации 0/5/10/15/20; ведения диалога 0/4/7/10.

Фильтр грубых ошибок в очереди относится к последней оценке текущего снимка исходного диалога. В истории и обычном экспорте он относится к действующему результату каждой оценки после апелляций. Исходный модельный флаг сохраняется в original_score.
