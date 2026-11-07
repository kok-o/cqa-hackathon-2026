import { useState } from 'react';
import { ChevronDown, ExternalLink, MessageSquarePlus, Check, Gavel, FileDown, AlertTriangle } from 'lucide-react';
import type { Appeal, Criterion, CriterionResult, Evaluation } from './contracts';
import { ApiError, downloadJson, errorText, post, q, request, uid } from './api';
import { answerName, date, number, statusName } from './format';

type Props = { evaluation: Evaluation; onUpdated: (value: Evaluation) => void; onEvidence: (id: string) => void };

export default function Assessment({ evaluation, onUpdated, onEvidence }: Props) {
  const metadata = evaluation.model_info;
  const [showOriginal, setShowOriginal] = useState(false);
  const results = showOriginal ? evaluation.original_results : evaluation.results;
  const score = showOriginal ? evaluation.original_score : evaluation.score;
  return <section className="assessment" aria-labelledby="assessment-title">
    <header className="panel-heading"><div><h2 id="assessment-title">Оценка качества</h2><span className="subtle">Чек-лист v{evaluation.checklist.version} • Ревизия {evaluation.revision}</span></div><button className="icon-button" aria-label="Скачать выбранную оценку" onClick={() => downloadJson(evaluation, `cqa-${evaluation.conversation.conversation_id}.json`)}><FileDown size={18} /></button></header>
    <div className={`score-summary ${score.status}`}><div><span className="score-label">Итог</span><strong>{number(score.total)}{score.total != null ? <small> / 100</small> : null}</strong></div><div className="score-description"><span className={`badge ${score.status === 'complete' ? 'good' : 'warning'}`}>{statusName(score.status)}</span><span className="subtle">Проверено {number(score.coverage * 100, 0)}% критериев</span></div></div>
    {score.status === 'incomplete' ? <p className="inline-note">Итог появится, когда все критерии будут оценены. Неопределённость не исключается из расчёта как N/A.</p> : null}
    {score.status === 'not_applicable' ? <p className="inline-note">Нет применимых критериев с положительным весом. Итоговый балл не рассчитывается.</p> : null}
    {score.gross_error ? <div className="notice error compact"><AlertTriangle size={16} /> Отмечена грубая ошибка. Флаг учитывается отдельно от итогового балла.</div> : null}
    {evaluation.revision > 1 ? <div className="revision-switch"><button className={!showOriginal ? 'active' : ''} onClick={() => setShowOriginal(false)}>После пересмотра</button><button className={showOriginal ? 'active' : ''} onClick={() => setShowOriginal(true)}>Исходная оценка {number(evaluation.original_score.total)}</button></div> : null}
    <p className="provenance-note">{evaluation.checklist.provenance === 'synthetic_example' ? 'Демонстрационный чек-лист.' : 'Чек-лист организаторов; сохранена версия правил этого запуска.'} Итог рассчитан по весам выбранной версии с перераспределением N/A.</p>
    <div className="result-list">
      {evaluation.checklist.criteria.map((criterion, index) => {
        const result = results.find(item => item.criterion_id === criterion.criterion_id);
        const calculated = score.criteria.find(item => item.criterion_id === criterion.criterion_id);
        return result ? <details className="result-item" key={`${criterion.criterion_id}-${showOriginal}`} open={index === 0}>
          <summary><span className="result-number">{index + 1}</span><span className="result-title">{criterion.title}<span className="subtle">Вес {number(criterion.weight)}{calculated?.contribution != null ? ` • вклад ${number(calculated.contribution)}` : ''}</span></span><span className={`answer-badge ${result.status === 'applicable' ? (calculated?.normalized_score === 1 ? 'good' : calculated?.normalized_score === 0 ? 'bad' : 'neutral') : 'warning'}`}>{answerName(result)}</span><ChevronDown size={15} className="disclosure-icon" /></summary>
          <div className="result-body"><p>{result.explanation}</p>{result.gross_error ? <span className="badge bad">Грубая ошибка</span> : null}
            {result.evidence.length ? <div className="evidence-list">{result.evidence.map((evidence, i) => <button className="evidence-link" key={`${evidence.message_id}-${i}`} onClick={() => onEvidence(evidence.message_id)}><span>«{evidence.quote}»</span><ExternalLink size={15} /><small>Открыть реплику</small></button>)}</div> : null}
            {result.checked_span ? <button className="text-button" onClick={() => onEvidence(result.checked_span!.start_message_id)}>Проверенный участок: {result.checked_span.start_message_id.split(':').at(-1)}–{result.checked_span.end_message_id.split(':').at(-1)}<ExternalLink size={14} /></button> : null}
            {!showOriginal ? <CriterionAppeals evaluation={evaluation} criterion={criterion} result={result} onUpdated={onUpdated} /> : null}
          </div>
        </details> : null;
      })}
    </div>
    {Object.keys(score.sections).length ? <div className="section-totals"><h3>По разделам</h3>{Object.entries(score.sections).map(([section, total]) => <div key={section}><span>{section}</span><strong>{number(total)}</strong></div>)}</div> : null}
    <details className="run-details"><summary>Данные запуска<ChevronDown size={15} /></summary><dl><div><dt>Модель</dt><dd>{String(metadata.model ?? 'Не указана')}</dd></div><div><dt>Создано</dt><dd>{date(evaluation.created_at)}</dd></div>{metadata.elapsed_ms != null ? <div><dt>Длительность</dt><dd>{number(Number(metadata.elapsed_ms) / 1000)} с</dd></div> : null}{metadata.prompt_version != null ? <div><dt>Инструкция</dt><dd>{String(metadata.prompt_version)}</dd></div> : null}<div><dt>Идентификатор</dt><dd className="break-all">{evaluation.evaluation_id}</dd></div></dl></details>
  </section>;
}

function CriterionAppeals({ evaluation, criterion, result, onUpdated }: Omit<Props, 'onEvidence'> & { criterion: Criterion; result: CriterionResult }) {
  const [adding, setAdding] = useState(false);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const appeals = evaluation.appeals.filter(item => item.criterion_id === criterion.criterion_id);
  async function add() {
    setBusy(true); setError('');
    try {
      await post<Appeal>(`/evaluations/${q(evaluation.evaluation_id)}/appeals`, { criterion_id: criterion.criterion_id, comment: comment.trim(), idempotency_key: uid() });
      const fresh = await request<Evaluation>(`/evaluations/${q(evaluation.evaluation_id)}`);
      onUpdated(fresh); setAdding(false); setComment('');
    } catch (error) { setError(errorText(error)); } finally { setBusy(false); }
  }
  return <div className="criterion-appeals">
    {appeals.map(appeal => <AppealItem key={appeal.appeal_id} appeal={appeal} evaluation={evaluation} criterion={criterion} result={result} onUpdated={onUpdated} />)}
    {!adding && !appeals.some(item => item.status === 'open') ? <button className="text-button" onClick={() => setAdding(true)}><MessageSquarePlus size={15} /> Подать апелляцию</button> : null}
    {adding ? <div className="appeal-form"><label className="field">Почему оценку нужно пересмотреть?<textarea value={comment} onChange={event => setComment(event.target.value)} rows={3} placeholder="Укажите спорный вывод и основание для пересмотра" autoFocus /></label><div className="button-row"><button className="button primary small" onClick={() => void add()} disabled={busy || !comment.trim()}>{busy ? 'Отправляем…' : 'Отправить апелляцию'}</button><button className="button ghost small" disabled={busy} onClick={() => setAdding(false)}>Отмена</button></div></div> : null}
    {error ? <p className="error-text" role="alert">{error}</p> : null}
  </div>;
}

function AppealItem({ appeal, evaluation, criterion, result, onUpdated }: Omit<Props, 'onEvidence'> & { appeal: Appeal; criterion: Criterion; result: CriterionResult }) {
  const [reviewing, setReviewing] = useState(false);
  return <div className="appeal-item"><div className="appeal-heading"><Gavel size={15} /><strong>Апелляция</strong><span className={`badge ${appeal.status === 'open' ? 'warning' : 'good'}`}>{appeal.status === 'open' ? 'Ожидает решения' : 'Рассмотрена'}</span></div><p>{appeal.comment}</p><time className="subtle">{date(appeal.created_at)}</time>
    {appeal.decision ? <div className="decision-summary"><strong><Check size={15} /> {appeal.decision.action === 'uphold' ? 'Оценка подтверждена' : 'Оценка изменена'}</strong><p>{appeal.decision.reason}</p></div> : <><button className="text-button" onClick={() => setReviewing(value => !value)}>{reviewing ? 'Закрыть форму решения' : 'Решение проверяющего'}</button>{reviewing ? <DecisionForm appeal={appeal} evaluation={evaluation} criterion={criterion} result={result} onUpdated={onUpdated} onClose={() => setReviewing(false)} /> : null}</>}
  </div>;
}

function DecisionForm({ appeal, evaluation, criterion, result, onUpdated, onClose }: Omit<Props, 'onEvidence'> & { appeal: Appeal; criterion: Criterion; result: CriterionResult; onClose: () => void }) {
  const [action, setAction] = useState<'uphold' | 'replace'>('uphold');
  const [reason, setReason] = useState('');
  const [replacement, setReplacement] = useState<CriterionResult>(() => structuredClone(result));
  const [evidenceMode, setEvidenceMode] = useState<'keep' | 'quote' | 'span'>('keep');
  const [messageId, setMessageId] = useState(evaluation.conversation.transcript.find(message => message.speaker === 'operator')?.message_id ?? evaluation.conversation.transcript[0].message_id);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [isConflict, setIsConflict] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const message = evaluation.conversation.transcript.find(item => item.message_id === messageId)!;
  const grossError = replacement.status === 'applicable' && (criterion.critical_answer != null ? replacement.answer === criterion.critical_answer : replacement.gross_error);
  async function submit() {
    setBusy(true); setError(''); setIsConflict(false);
    try {
      let updated: CriterionResult | null = null;
      if (action === 'replace') {
        updated = { ...replacement, answer: replacement.status === 'applicable' ? replacement.answer : null, gross_error: grossError };
        if (evidenceMode === 'quote') { updated.evidence = [{ message_id: messageId, quote: message.text }]; updated.checked_span = null; }
        if (evidenceMode === 'span') { updated.evidence = []; updated.checked_span = { start_message_id: evaluation.conversation.transcript[0].message_id, end_message_id: evaluation.conversation.transcript.at(-1)!.message_id }; }
      }
      const next = await post<Evaluation>(`/appeals/${q(appeal.appeal_id)}/decision`, { action, reason: reason.trim(), replacement_result: updated, expected_revision: evaluation.revision, idempotency_key: uid() });
      onUpdated(next); onClose();
    } catch (err) {
      const msg = errorText(err);
      setError(msg);
      if ((err instanceof ApiError && err.status === 409) || msg.toLowerCase().includes('ревизия') || msg.toLowerCase().includes('конфликт')) {
        setIsConflict(true);
      }
    } finally { setBusy(false); }
  }

  async function syncEvaluation() {
    setRefreshing(true);
    try {
      const fresh = await request<Evaluation>(`/evaluations/${q(evaluation.evaluation_id)}`);
      onUpdated(fresh);
      setError('');
      setIsConflict(false);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setRefreshing(false);
    }
  }

  return <div className="decision-form">
    <label className="field">Решение<select value={action} onChange={event => setAction(event.target.value as 'uphold' | 'replace')}><option value="uphold">Подтвердить оценку</option><option value="replace">Изменить оценку</option></select></label>
    {action === 'replace' ? <><label className="field">Применимость<select value={replacement.status} onChange={event => { const status = event.target.value as CriterionResult['status']; setReplacement({ ...replacement, status, gross_error: false, answer: status !== 'applicable' ? null : criterion.answer_type === 'boolean' ? false : criterion.answer_type === 'scale' ? criterion.allowed_values?.[0] ?? criterion.scale_min : Object.keys(criterion.choices)[0] }); }}><option value="applicable">Применим</option>{criterion.na_allowed ? <option value="not_applicable">Не применим (N/A)</option> : null}<option value="needs_review">Нужна проверка</option></select></label>
      {replacement.status === 'applicable' ? <label className="field">Новый ответ{criterion.answer_type === 'boolean' ? <select value={String(replacement.answer ?? false)} onChange={event => setReplacement({ ...replacement, answer: event.target.value === 'true' })}><option value="true">Да</option><option value="false">Нет</option></select> : criterion.answer_type === 'scale' ? criterion.allowed_values?.length ? <select value={String(replacement.answer ?? criterion.allowed_values[0])} onChange={event => setReplacement({ ...replacement, answer: Number(event.target.value) })}>{criterion.allowed_values.map(value => <option key={value} value={value}>{value}</option>)}</select> : <input type="number" step="any" min={criterion.scale_min ?? undefined} max={criterion.scale_max ?? undefined} value={typeof replacement.answer === 'number' ? replacement.answer : criterion.scale_min ?? 0} onChange={event => setReplacement({ ...replacement, answer: Number(event.target.value) })} /> : <select value={String(replacement.answer ?? '')} onChange={event => setReplacement({ ...replacement, answer: event.target.value })}>{Object.keys(criterion.choices).map(choice => <option key={choice}>{choice}</option>)}</select>}</label> : null}
      <label className="field">Объяснение нового результата<textarea rows={3} value={replacement.explanation} onChange={event => setReplacement({ ...replacement, explanation: event.target.value })} /></label>
      <label className="field">Доказательство<select value={evidenceMode} onChange={event => setEvidenceMode(event.target.value as typeof evidenceMode)}><option value="keep">Сохранить исходные доказательства</option><option value="quote">Выбрать точную реплику</option><option value="span">Проверен весь диалог (действие отсутствует)</option></select></label>
      {evidenceMode === 'quote' ? <><label className="field">Реплика<select value={messageId} onChange={event => setMessageId(event.target.value)}>{evaluation.conversation.transcript.map((message, i) => <option key={message.message_id} value={message.message_id}>{i + 1}. {message.speaker}: {message.text.slice(0, 65)}</option>)}</select></label><blockquote>{message.text}</blockquote></> : null}
      {criterion.gross_error_rule ? <label className="check-field"><input type="checkbox" checked={grossError} disabled={replacement.status !== 'applicable' || criterion.critical_answer != null} onChange={event => setReplacement({ ...replacement, gross_error: event.target.checked })} /> Грубая ошибка{criterion.critical_answer != null ? ' (по ответу)' : ''}</label> : null}
    </> : null}
    <label className="field">Обоснование решения<textarea rows={2} value={reason} onChange={event => setReason(event.target.value)} placeholder="Обязательно для истории пересмотра" /></label>
    {error ? <div className="decision-error-block" role="alert"><p className="error-text">{error}</p>{isConflict ? <button type="button" className="button secondary small" disabled={refreshing} onClick={() => void syncEvaluation()}>{refreshing ? 'Обновляем…' : 'Обновить данные оценки'}</button> : null}</div> : null}
    <button className="button primary small" disabled={busy || !reason.trim() || (action === 'replace' && !replacement.explanation.trim())} onClick={() => void submit()}>{busy ? 'Сохраняем…' : 'Сохранить решение и пересчитать'}</button>
  </div>;
}
