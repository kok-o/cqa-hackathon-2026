# API приложения CQA

Все пути относительные. Ошибки возвращают `{ "detail": "понятное сообщение" }`, некорректные данные — HTTP 422, отсутствующий объект — 404, конфликт версии или повторного ключа — 409. Ключи модели и подписанные URL конфигурации не возвращаются. JSON-файл импорта имеет исходную структуру `DatasetExport` из `backend/cqa/models.py`.

| Метод | Путь | Формат |
|---|---|---|
| GET | `/health` | `{status, phase, version}` |
| GET | `/api/config` | `{provider_configured, provider, model, mode, prompt_version}` |
| POST | `/api/datasets/import` | `{dataset: DatasetExport}` либо `{source: "dev"}` / `{source: "demo"}`; результат `{group_id, imported, updated, unchanged, total}` |
| GET | `/api/conversations` | Массив `{conversation_id,group_id,channel,language,message_count,preview,latest_evaluation_id,latest_score,latest_status}`; фильтры `group_id`, `language`, `channel` |
| GET | `/api/conversations/{id}?group_id=DEV` | Полный `Conversation` |
| GET | `/api/checklists` | Массив всех `ChecklistVersion`, новые версии первыми |
| POST | `/api/checklists` | `ChecklistVersion`; сервер устанавливает `version=1` |
| POST | `/api/checklists/{id}/versions` | `{title,provenance,criteria}`; сервер увеличивает версию |
| POST | `/api/evaluation-jobs` | `{conversation_id,group_id,checklist_id,checklist_version,idempotency_key?}`; HTTP 202, объект задачи |
| GET | `/api/evaluation-jobs/{id}` | `{job_id,status,error,evaluation_id}`; status: `queued/running/completed/failed` |
| GET | `/api/evaluations` | Массив полных оценок; фильтры `group_id`, `conversation_id` |
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

`Score`: `{total:number|null,status:"complete"|"incomplete"|"not_applicable",coverage:number,earned_weight:number,applicable_weight:number,criteria:[{criterion_id,status,normalized_score:number|null,weight,contribution:number|null}],sections:Record<string,number|null>,gross_error:boolean}`. Coverage — доля критериев без `needs_review` от 0 до 1. Contribution — вклад в итоговую шкалу 0–100, доступен только при полном результате. При `needs_review` итог отсутствует. Все N/A или нулевые применимые веса также не создают фиктивный итог. Формула предварительная: глобальное взвешенное среднее нормализованных ответов, N/A исключаются; грубая ошибка остаётся отдельным флагом без невыданного организаторами штрафа.

`Appeal`: `{appeal_id,evaluation_id,criterion_id,comment,status:"open"|"resolved",created_at,decision:null|{action,reason,replacement_result,created_at}}`. Начальная revision оценки — 1; решение повышает её на 1. Снимки исходных диалога, чек-листа, результатов и итогов неизменяемы. Новая версия чек-листа не изменяет прежнюю оценку.

Повтор с тем же idempotency_key возвращает прежнюю операцию. Другое содержимое с уже использованным ключом возвращает 409. Ключи необязательны, но рекомендуются при повторной отправке UI. После сбоя сервера незавершённые задачи становятся `failed`; повторная оценка создаётся новым ключом.
