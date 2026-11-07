import { ApiError } from './api';
import type { Appeal, ChecklistVersion, Conversation, Criterion, CriterionResult, Evaluation, EvaluationJob, ProviderInfo, Score } from './contracts';

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string';
const id = (value: unknown): value is string => text(value) && value.trim().length > 0;
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const positiveInteger = (value: unknown): value is number => finite(value) && Number.isInteger(value) && value >= 1;
const nullableText = (value: unknown) => value === null || text(value);
const nullableNumber = (value: unknown) => value === null || finite(value);
const timestamp = (value: unknown): value is string => id(value) && Number.isFinite(Date.parse(value));
const member = (value: unknown, options: readonly string[]) => text(value) && options.includes(value);
const unique = (values: string[]) => new Set(values).size === values.length;
const range = (value: unknown, min: number, max: number): value is number => finite(value) && value >= min && value <= max;
const resultStatuses = ['applicable', 'not_applicable', 'needs_review'];

function protocolError(): never {
  // Never include untrusted response fields in a displayed error. Treat a bad
  // success envelope like a lost response so the existing job key is retained.
  throw new ApiError('Сервер вернул неполные или некорректные данные оценки. Продолжите сохранённую очередь после восстановления связи.', 0);
}

export function assertJob(value: unknown): asserts value is EvaluationJob {
  if (!object(value) || !id(value.job_id) || !member(value.status, ['queued', 'running', 'completed', 'failed'])
    || !nullableText(value.error) || !(value.evaluation_id === null || id(value.evaluation_id))) protocolError();
  if (value.status === 'completed' ? !id(value.evaluation_id) || value.error !== null : value.evaluation_id !== null) protocolError();
}

export function assertProviderInfo(value: unknown): asserts value is ProviderInfo {
  if (!object(value) || typeof value.provider_configured !== 'boolean' || !id(value.provider) || !id(value.mode)
    || !id(value.prompt_version) || !(value.model === null || id(value.model))
    || value.provider_configured && !id(value.model) || value.team_name !== undefined && !text(value.team_name)) {
    throw new ApiError('Сервер вернул некорректные настройки модели. Повторите запрос после восстановления связи.', 0);
  }
}

export function assertEvaluationHistory(value: unknown): asserts value is Evaluation[] {
  if (!Array.isArray(value)) protocolError();
  value.forEach(assertEvaluation);
}

function conversation(value: unknown): value is Conversation {
  if (!object(value) || !id(value.conversation_id) || !id(value.group_id)
    || !member(value.channel, ['agent chat', 'voice call']) || !member(value.language, ['RU', 'KZ', 'EN'])
    || !nullableText(value.audio_url) || !Array.isArray(value.transcript) || !value.transcript.length) return false;
  return value.transcript.every(message => object(message) && id(message.message_id) && id(message.timestamp)
    && member(message.speaker, ['client', 'operator', 'bot', 'system']) && text(message.text))
    && unique(value.transcript.map(message => message.message_id));
}

function criterion(value: unknown): value is Criterion {
  if (!object(value) || !id(value.criterion_id) || !id(value.title) || !id(value.instruction)
    || !finite(value.weight) || value.weight < 0 || !member(value.answer_type, ['boolean', 'scale', 'choice'])
    || !['na_allowed', 'requires_audio', 'higher_is_better', 'positive_answer'].every(key => typeof value[key] === 'boolean')
    || !nullableNumber(value.scale_min) || !nullableNumber(value.scale_max) || !nullableText(value.section_id)
    || !nullableText(value.gross_error_rule) || !object(value.choices) || !Object.values(value.choices).every(score => range(score, 0, 1))) return false;
  if (value.allowed_values != null && (!Array.isArray(value.allowed_values) || !value.allowed_values.length || !value.allowed_values.every(finite))) return false;
  if (value.critical_answer != null && typeof value.critical_answer !== 'boolean') return false;
  if (value.answer_type === 'scale' && (!finite(value.scale_min) || !finite(value.scale_max) || value.scale_min >= value.scale_max)) return false;
  return value.answer_type !== 'choice' || Object.keys(value.choices).length > 0;
}

function checklist(value: unknown): value is ChecklistVersion {
  return object(value) && id(value.checklist_id) && positiveInteger(value.version) && id(value.title)
    && member(value.provenance, ['synthetic_example', 'organizer']) && Array.isArray(value.criteria)
    && value.criteria.length > 0 && value.criteria.every(criterion) && unique(value.criteria.map(item => item.criterion_id));
}

function result(value: unknown): value is CriterionResult {
  if (!object(value) || !id(value.criterion_id) || !member(value.status, resultStatuses) || !id(value.explanation)
    || typeof value.gross_error !== 'boolean' || !Array.isArray(value.evidence)
    || !value.evidence.every(item => object(item) && id(item.message_id) && text(item.quote) && item.quote.length > 0)
    || !(value.checked_span === null || object(value.checked_span) && id(value.checked_span.start_message_id) && id(value.checked_span.end_message_id))) return false;
  return value.status === 'applicable'
    ? (typeof value.answer === 'boolean' || text(value.answer) || finite(value.answer)) && (value.evidence.length > 0 || value.checked_span !== null)
    : value.answer === null;
}

function score(value: unknown): value is Score {
  if (!object(value) || !member(value.status, ['complete', 'incomplete', 'not_applicable'])
    || !(value.total === null || range(value.total, 0, 100)) || !range(value.coverage, 0, 1)
    || !finite(value.earned_weight) || value.earned_weight < 0 || !finite(value.applicable_weight) || value.applicable_weight < 0
    || typeof value.gross_error !== 'boolean' || !object(value.sections)
    || !Object.values(value.sections).every(total => total === null || range(total, 0, 100))
    || !Array.isArray(value.criteria) || !value.criteria.every(item => object(item) && id(item.criterion_id)
      && member(item.status, resultStatuses) && (item.normalized_score === null || range(item.normalized_score, 0, 1))
      && finite(item.weight) && item.weight >= 0 && (item.contribution === null || range(item.contribution, 0, 100)))) return false;
  return value.status === 'complete' ? value.total !== null : value.total === null;
}

function appeal(value: unknown): value is Appeal {
  if (!object(value) || !id(value.appeal_id) || !id(value.evaluation_id) || !id(value.criterion_id)
    || !id(value.comment) || !timestamp(value.created_at) || !member(value.status, ['open', 'resolved'])) return false;
  const decision = value.decision;
  if (value.status === 'open') return decision === null;
  return object(decision) && member(decision.action, ['uphold', 'replace']) && id(decision.reason) && timestamp(decision.created_at)
    && (decision.action === 'uphold' ? decision.replacement_result === null : result(decision.replacement_result));
}

export function assertEvaluation(value: unknown): asserts value is Evaluation {
  if (!object(value) || !id(value.evaluation_id) || !positiveInteger(value.revision) || !timestamp(value.created_at)
    || !conversation(value.conversation) || !checklist(value.checklist) || !score(value.score) || !score(value.original_score)
    || !Array.isArray(value.results) || !value.results.every(result) || !Array.isArray(value.original_results) || !value.original_results.every(result)
    || !Array.isArray(value.appeals) || !value.appeals.every(appeal) || !object(value.model_info)) protocolError();
  const ids = new Set(value.checklist.criteria.map(item => item.criterion_id));
  for (const rows of [value.results, value.original_results, value.score.criteria, value.original_score.criteria]) {
    if (rows.length !== ids.size || !unique(rows.map(item => item.criterion_id)) || !rows.every(item => ids.has(item.criterion_id))) protocolError();
  }
  if (!unique(value.appeals.map(item => item.appeal_id)) || !value.appeals.every(item => item.evaluation_id === value.evaluation_id && ids.has(item.criterion_id))) protocolError();
  const metadata = value.model_info;
  if (metadata.model != null && !text(metadata.model) || metadata.prompt_version != null && !text(metadata.prompt_version)
    || metadata.elapsed_ms != null && (!finite(metadata.elapsed_ms) || metadata.elapsed_ms < 0)) protocolError();
}
