import Select from "./Select";
import { createContext, useContext, useEffect, useRef, useState } from "react";
import type { Dispatch, ReactNode, SetStateAction } from "react";
import {
  ArrowUpRight,
  Bot,
  ChevronDown,
  MessageSquarePlus,
  Check,
  Gavel,
  FileDown,
  AlertTriangle,
  CheckCheck,
  X,
} from "lucide-react";
import type {
  Appeal,
  Criterion,
  CriterionResult,
  Evaluation,
} from "./contracts";
import {
  ApiError,
  downloadJson,
  errorText,
  post,
  q,
  request,
  uid,
} from "./api";
import { answerName, date, number, statusName } from "./format";
import type { EvidenceTarget } from "./Transcript";
import "./Assessment.css";

type Props = {
  evaluation: Evaluation;
  initialCriterionId?: string;
  onUpdated: (value: Evaluation) => void;
  onEvidence: (target: EvidenceTarget, animate: boolean) => void;
};
type AppealProps = Pick<Props, "evaluation" | "onUpdated"> & {
  criterion: Criterion;
  result: CriterionResult;
};
type AppealDraft = {
  adding: boolean;
  comment: string;
  busy: boolean;
  error: string;
};
type DecisionDraft = {
  reviewing: boolean;
  action: "uphold" | "replace";
  reason: string;
  replacement: CriterionResult;
  evidenceMode: "keep" | "quote" | "span";
  messageId: string;
  busy: boolean;
  error: string;
  isConflict: boolean;
  refreshing: boolean;
};
type DraftContextValue = {
  appeals: Record<string, AppealDraft>;
  decisions: Record<string, DecisionDraft>;
  setAppeals: Dispatch<SetStateAction<Record<string, AppealDraft>>>;
  setDecisions: Dispatch<SetStateAction<Record<string, DecisionDraft>>>;
};
const DraftContext = createContext<DraftContextValue | null>(null);
const emptyAppealDraft = (): AppealDraft => ({ adding: false, comment: "", busy: false, error: "" });

export function AssessmentDraftsProvider({ children }: { children: ReactNode }) {
  const [appeals, setAppeals] = useState<Record<string, AppealDraft>>({});
  const [decisions, setDecisions] = useState<Record<string, DecisionDraft>>({});
  return <DraftContext.Provider value={{ appeals, decisions, setAppeals, setDecisions }}>{children}</DraftContext.Provider>;
}

function useAppealDraft(evaluationId: string, criterionId: string) {
  const { appeals, setAppeals } = useContext(DraftContext)!;
  const key = JSON.stringify([evaluationId, criterionId]);
  const draft = appeals[key] ?? emptyAppealDraft();
  const update = (patch: Partial<AppealDraft>) => setAppeals(current => ({
    ...current,
    [key]: { ...(current[key] ?? emptyAppealDraft()), ...patch },
  }));
  return { draft, update };
}

function useDecisionDraft(appealId: string, result: CriterionResult, evaluation: Evaluation) {
  const { decisions, setDecisions } = useContext(DraftContext)!;
  const empty = (): DecisionDraft => ({
    reviewing: false, action: "uphold", reason: "", replacement: structuredClone(result),
    evidenceMode: "keep", messageId: evaluation.conversation.transcript.find(message => message.speaker === "operator")?.message_id
      ?? evaluation.conversation.transcript[0]?.message_id ?? "",
    busy: false, error: "", isConflict: false, refreshing: false,
  });
  const draft = decisions[appealId] ?? empty();
  const update = (patch: Partial<DecisionDraft>) => setDecisions(current => ({
    ...current,
    [appealId]: { ...(current[appealId] ?? empty()), ...patch },
  }));
  const reset = () => setDecisions(current => {
    const next = { ...current };
    delete next[appealId];
    return next;
  });
  return { draft, update, reset };
}
export default function Assessment({
  evaluation,
  initialCriterionId,
  onUpdated,
  onEvidence,
}: Props) {
  const [showOriginal, setShowOriginal] = useState(false);
  const [animateRevision, setAnimateRevision] = useState(false);
  const [selection, setSelection] = useState(() => {
    const attention = evaluation.results.find(item => item.gross_error)
      ?? evaluation.results.find(item => item.status === "needs_review")
      ?? evaluation.results.find(item => evaluation.score.criteria.some(row =>
        row.criterion_id === item.criterion_id && row.normalized_score != null && row.normalized_score < 1));
    return { id: initialCriterionId ?? attention?.criterion_id ?? "", animate: false };
  });
  const [saved, setSaved] = useState(false);
  const revisionRef = useRef(evaluation.revision);
  useEffect(() => {
    if (!initialCriterionId) return;
    const frame = window.requestAnimationFrame(() => {
      const control = document.getElementById(`criterion-${initialCriterionId}`);
      control?.focus({ preventScroll: true });
      control?.scrollIntoView({ block: "nearest" });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [initialCriterionId]);
  useEffect(() => {
    if (revisionRef.current !== evaluation.revision) {
      revisionRef.current = evaluation.revision;
      setSaved(true);
      const timer = window.setTimeout(() => setSaved(false), 5000);
      return () => window.clearTimeout(timer);
    }
  }, [evaluation.revision]);
  const metadata = evaluation.model_info;
  const testRun = metadata.mode === "test" || metadata.provider === "test" || metadata.provider === "isolated-test-double";
  const reviewed = evaluation.revision > 1;
  const currentReview = reviewed && !showOriginal;
  const latestDecision = evaluation.appeals.reduce<Appeal | null>((latest, item) =>
    item.decision && (!latest?.decision || item.decision.created_at > latest.decision.created_at) ? item : latest,
  null);
  const results = showOriginal
    ? evaluation.original_results
    : evaluation.results;
  const score = showOriginal ? evaluation.original_score : evaluation.score;
  const criterion = evaluation.checklist.criteria.find(
    (item) => item.criterion_id === selection.id,
  );
  const result = results.find(
    (item) => item.criterion_id === criterion?.criterion_id,
  );
  const calculated = score.criteria.find(
    (item) => item.criterion_id === criterion?.criterion_id,
  );
  const replaced = currentReview && evaluation.appeals.some((appeal) =>
    appeal.criterion_id === criterion?.criterion_id && appeal.decision?.action === "replace",
  );
  const position = (id: string) =>
    evaluation.conversation.transcript.findIndex(
      (item) => item.message_id === id,
    ) + 1;
  const tone = (value?: CriterionResult) =>
    value?.gross_error
      ? "bad"
      : value?.status === "needs_review" || !value
        ? "warning"
        : value?.status === "not_applicable"
          ? "neutral"
          : score.criteria.find(
                (item) => item.criterion_id === value.criterion_id,
              )?.normalized_score === 1
            ? "good"
            : value?.status === "applicable"
              ? "attention"
              : "neutral";
  const orderedCriteria = [...evaluation.checklist.criteria].sort((a, b) => {
    const priority = (id: string) => {
      const item = evaluation.results.find(result => result.criterion_id === id);
      if (item?.gross_error) return 0;
      if (!item || item.status === "needs_review") return 1;
      const grade = evaluation.score.criteria.find(result => result.criterion_id === id);
      if (item.status === "applicable" && grade?.normalized_score != null && grade.normalized_score < 1) return 2;
      return 3;
    };
    return priority(a.criterion_id) - priority(b.criterion_id);
  });
  function select(id: string, animate: boolean) {
    setSelection((current) => ({ id: current.id === id ? "" : id, animate }));
    window.requestAnimationFrame(() =>
      document
        .getElementById(`criterion-${id}`)
        ?.scrollIntoView({ block: "nearest" }),
    );
  }
  function chooseResult(original: boolean, animate: boolean) {
    setAnimateRevision(animate);
    setShowOriginal(original);
  }
  const detail = criterion ? (
    <div
      className={`aq-detail${selection.animate ? " aq-detail-motion" : ""}`}
      id="assessment-criterion-detail"
      aria-labelledby={`criterion-${criterion.criterion_id}`}
    >
      <div className="aq-detail-meta">
        <span className={`aq-result-label ${tone(result)}`}>
          {result?.status === "not_applicable"
            ? "N/A · Не применимо"
            : result
              ? answerName(result)
              : "Нет результата"}
        </span>
        {calculated?.contribution != null ? (
          <span>В итог: {number(calculated.contribution, 2)} из 100</span>
        ) : null}
        {result?.gross_error ? (
          <span className="aq-danger">Грубая ошибка</span>
        ) : null}
        {replaced && <span className="badge good">Ответ проверяющего</span>}
      </div>
      {result ? (
        <>
          <p className="aq-explanation">{result.explanation}</p>
          {result.evidence.length ? (
            <div className="aq-evidence-list">
              {result.evidence.map((evidence, index) => (
                <button
                  type="button"
                  className="aq-evidence"
                  key={`${evidence.message_id}-${index}`}
                  onClick={(event) => onEvidence({
                    start: evidence.message_id,
                    quote: evidence.quote,
                    criterion: criterion.criterion_id,
                    title: criterion.title,
                  }, event.detail > 0)}
                >
                  <span className="aq-quote">«{evidence.quote}»</span>
                  <span className="aq-evidence-action">
                    К реплике №{position(evidence.message_id)}
                    <ArrowUpRight size={14} aria-hidden="true" />
                  </span>
                </button>
              ))}
            </div>
          ) : null}
          {result.checked_span ? (
            <button
              type="button"
              className="aq-span"
              onClick={(event) => onEvidence({
                start: result.checked_span!.start_message_id,
                end: result.checked_span!.end_message_id,
                criterion: criterion.criterion_id,
                title: criterion.title,
              }, event.detail > 0)}
            >
              <span>
                <strong>Проверенный участок</strong>
                <small>
                  Реплики №{position(result.checked_span.start_message_id)}–
                  {position(result.checked_span.end_message_id)}
                </small>
              </span>
              <ArrowUpRight size={15} aria-hidden="true" />
            </button>
          ) : null}
        </>
      ) : (
        <p className="aq-note">Ответ по критерию отсутствует.</p>
      )}
      <details className="aq-rule">
        <summary>
          Условие критерия
          <ChevronDown size={13} />
        </summary>
        <p>{criterion.instruction}</p>
        {criterion.allowed_values?.length ? (
          <p>Шкала: {criterion.allowed_values.join(" / ")}</p>
        ) : null}
      </details>
      {result && !showOriginal ? (
        <CriterionAppeals
          key={criterion.criterion_id}
          evaluation={evaluation}
          criterion={criterion}
          result={result}
          onUpdated={onUpdated}
        />
      ) : null}
    </div>
  ) : null;
  return (
    <section className="assessment aq-panel" aria-label="Оценка диалога" data-revision-motion={animateRevision}>
      <div className="aq-summary">
        <div className="aq-heading">
          <div className="aq-provenance">
            <span>{currentReview ? <Gavel size={15} aria-hidden="true" /> : <Bot size={16} aria-hidden="true" />}{currentReview ? "Решение проверяющего" : testRun ? "Тестовый расчёт" : "Оценка AI"}</span>
            <span className="aq-version" title={evaluation.checklist.title}>Правила запуска <strong>v{evaluation.checklist.version}</strong></span>
          </div>
          <button
          type="button"
          className="icon-button aq-download"
          aria-label="Скачать выбранную оценку"
          title="Скачать оценку в JSON"
          onClick={() =>
            downloadJson(
              evaluation,
              `cqa-${evaluation.conversation.conversation_id}.json`,
            )
          }
        >
          <FileDown size={17} aria-hidden="true" />
        </button>
        </div>
      <div className="aq-score" key={showOriginal ? "original" : "reviewed"}>
        <div className="aq-score-value">
          <span className="sr-only">{score.total == null ? "Итоговый балл не рассчитан" : showOriginal ? "Исходная оценка" : "Итоговая оценка"}</span>
          <strong>{number(score.total, 2)}</strong>
          {score.total != null && <span>/ 100</span>}
        </div>
        <span
          className={`aq-status badge ${score.status === "complete" ? "good" : "warning"}`}
        >
          {statusName(score.status)}
        </span>
      </div>
      {score.gross_error ? (
        <p className="aq-critical" role="status">
          <AlertTriangle size={16} aria-hidden="true" />
          <span><strong>Грубая ошибка</strong></span>
        </p>
      ) : null}
      {score.status !== "complete" ? (
        <p className="aq-note">
          {score.status === "incomplete"
            ? `Проверено ${number(score.coverage * 100, 0)}% критериев. Для итогового балла завершите проверку.`
            : "Нет применимых критериев с положительным весом."}
        </p>
      ) : null}
      {saved ? (
        <p className="aq-saved" role="status">
          <CheckCheck size={14} aria-hidden="true" />
          Решение сохранено, итог пересчитан
        </p>
      ) : null}
      {reviewed ? (
        <div className="aq-revision" role="group" aria-label="Версия результата">
          <button
            type="button"
            aria-pressed={showOriginal}
            onClick={(event) => chooseResult(true, event.detail > 0)}
          >
            <span>Исходная</span><strong>{number(evaluation.original_score.total, 2)}</strong>
          </button>
          <button
            type="button"
            aria-pressed={!showOriginal}
            onClick={(event) => chooseResult(false, event.detail > 0)}
          >
            <span>После пересмотра</span><strong>{number(evaluation.score.total, 2)}</strong>
          </button>
        </div>
      ) : null}
      {latestDecision?.decision && (
        <details className="aq-reason">
          <summary>Последнее решение <ChevronDown size={14} aria-hidden="true" /></summary>
          <p>{latestDecision.decision.reason}</p>
        </details>
      )}
      </div>
      <div className="aq-criteria-heading">
        <h3>Критерии <span className="aq-priority-note">Сначала замечания</span></h3>
        <span>Баллы / вес</span>
      </div>
      <div className="aq-criteria-list" aria-label="Критерии оценки">
        {orderedCriteria.map((item) => {
          const value = results.find(
            (entry) => entry.criterion_id === item.criterion_id,
          );
          const row = score.criteria.find(
            (entry) => entry.criterion_id === item.criterion_id,
          );
          const selected = criterion?.criterion_id === item.criterion_id;
          return (
            <div
              className={`aq-ledger-entry ${selected ? "aq-ledger-open" : ""}`}
              key={item.criterion_id}
            >
              <button
                type="button"
                className={`aq-criterion ${selected ? "aq-selected" : ""}`}
                id={`criterion-${item.criterion_id}`}
                aria-expanded={selected}
                aria-controls={
                  selected ? "assessment-criterion-detail" : undefined
                }
                onClick={(event) => select(item.criterion_id, event.detail > 0)}
              >
                <span className="aq-criterion-name">{item.title}</span>
                <span className={`aq-criterion-answer ${tone(value)}`}>
                  {value?.status === "not_applicable"
                    ? "N/A"
                    : value?.status === "needs_review"
                      ? "Проверка"
                      : !value
                        ? "Нет ответа"
                        : item.weight > 0 && row?.normalized_score != null
                          ? `${number(row.normalized_score * item.weight, 2)} / ${number(item.weight)}`
                          : answerName(value)}
                </span>
                <ChevronDown size={13} aria-hidden="true" />
              </button>
              {selected ? detail : null}
            </div>
          );
        })}
      </div>
      {Object.keys(score.sections).length ? (
        <details className="aq-run-details">
          <summary>
            По разделам
            <ChevronDown size={14} />
          </summary>
          <dl>
            {Object.entries(score.sections).map(([section, total]) => (
              <div key={section}>
                <dt>{section}</dt>
                <dd>{number(total, 2)}</dd>
              </div>
            ))}
          </dl>
        </details>
      ) : null}
      <details className="aq-run-details">
        <summary>
          Данные запуска
          <ChevronDown size={14} />
        </summary>
        <dl>
          <div>
            <dt>Модель</dt>
            <dd>{String(metadata.model ?? "Не указана")}</dd>
          </div>
          <div>
            <dt>Создано</dt>
            <dd>{date(evaluation.created_at)}</dd>
          </div>
          <div>
            <dt>Чек-лист</dt>
            <dd>{evaluation.checklist.title} · v{evaluation.checklist.version}</dd>
          </div>
          <div>
            <dt>Ревизия</dt>
            <dd>{evaluation.revision}</dd>
          </div>
          {metadata.elapsed_ms != null ? (
            <div>
              <dt>Длительность</dt>
              <dd>{number(Number(metadata.elapsed_ms) / 1000)} с</dd>
            </div>
          ) : null}
          {metadata.prompt_version != null ? (
            <div>
              <dt>Инструкция</dt>
              <dd>{String(metadata.prompt_version)}</dd>
            </div>
          ) : null}
          <div>
            <dt>ID запуска</dt>
            <dd>{evaluation.evaluation_id}</dd>
          </div>
        </dl>
      </details>
    </section>
  );
}

function CriterionAppeals({
  evaluation,
  criterion,
  result,
  onUpdated,
}: AppealProps) {
  const { draft, update } = useAppealDraft(evaluation.evaluation_id, criterion.criterion_id);
  const { adding, comment, busy, error } = draft;
  const setAdding = (adding: boolean) => update({ adding });
  const setComment = (comment: string) => update({ comment });
  const setBusy = (busy: boolean) => update({ busy });
  const setError = (error: string) => update({ error });
  const trigger = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const appeals = evaluation.appeals.filter(
    (item) => item.criterion_id === criterion.criterion_id,
  );
  useEffect(() => {
    if (adding) input.current?.focus();
  }, [adding]);
  function close() {
    setAdding(false);
    window.requestAnimationFrame(() =>
      (
        trigger.current ??
        document.getElementById(`criterion-${criterion.criterion_id}`)
      )?.focus(),
    );
  }
  async function add() {
    setBusy(true);
    setError("");
    try {
      await post<Appeal>(
        `/evaluations/${q(evaluation.evaluation_id)}/appeals`,
        {
          criterion_id: criterion.criterion_id,
          comment: comment.trim(),
          idempotency_key: uid(),
        },
      );
      const fresh = await request<Evaluation>(
        `/evaluations/${q(evaluation.evaluation_id)}`,
      );
      onUpdated(fresh);
      close();
      setComment("");
    } catch (error) {
      setError(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="aq-appeals">
      {appeals.map((appeal) => (
        <AppealItem
          key={appeal.appeal_id}
          appeal={appeal}
          evaluation={evaluation}
          criterion={criterion}
          result={result}
          onUpdated={onUpdated}
        />
      ))}
      {!appeals.some((item) => item.status === "open") ? (
        <button
          ref={trigger}
          type="button"
          className="aq-appeal-trigger"
          aria-expanded={adding}
          onClick={() => (adding ? close() : setAdding(true))}
        >
          <MessageSquarePlus size={16} />
          {adding ? "Закрыть апелляцию" : "Оспорить оценку"}
        </button>
      ) : null}
      {adding ? (
        <form
          className="aq-appeal-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (comment.trim() && !busy) void add();
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape" && !busy) {
              event.stopPropagation();
              close();
            }
          }}
        >
          <div className="aq-form-title">
            <h4>Подать апелляцию</h4>
            <button
              type="button"
              className="icon-button"
              aria-label="Закрыть форму апелляции"
              disabled={busy}
              onClick={close}
            >
              <X size={16} />
            </button>
          </div>
          <p>
            Исходный ответ: <strong>{answerName(result)}</strong>. Укажите,
            какой вывод требует пересмотра.
          </p>
          <label className="field">
            Почему оценку нужно пересмотреть?
            <textarea
              ref={input}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              disabled={busy}
              rows={3}
              placeholder="Сошлитесь на реплику или правило чек-листа"
              required
              maxLength={10000}
            />
          </label>
          <div className="button-row">
            <button
              type="submit"
              className="button primary small"
              disabled={busy || !comment.trim()}
            >
              {busy ? "Отправляем…" : "Отправить апелляцию"}
            </button>
            <button
              type="button"
              className="button ghost small"
              disabled={busy}
              onClick={close}
            >
              Отмена
            </button>
          </div>
        </form>
      ) : null}
      {error ? (
        <p className="error-text" role="alert">
          {error}
        </p>
      ) : null}
    </div>
  );
}

function AppealItem({
  appeal,
  evaluation,
  criterion,
  result,
  onUpdated,
}: AppealProps & { appeal: Appeal }) {
  const { draft, update } = useDecisionDraft(appeal.appeal_id, result, evaluation);
  const { reviewing } = draft;
  const setReviewing = (reviewing: boolean) => update({ reviewing });
  const trigger = useRef<HTMLButtonElement>(null);
  function close() {
    setReviewing(false);
    window.requestAnimationFrame(() =>
      (
        trigger.current ??
        document.getElementById(`criterion-${criterion.criterion_id}`)
      )?.focus(),
    );
  }
  return (
    <div className="aq-appeal-item">
      <div className="aq-appeal-heading">
        <Gavel size={15} />
        <strong>Апелляция</strong>
        <span
          className={`badge ${appeal.status === "open" ? "warning" : "good"}`}
        >
          {appeal.status === "open" ? "Ожидает решения" : "Рассмотрена"}
        </span>
      </div>
      <p>{appeal.comment}</p>
      <time>{date(appeal.created_at)}</time>
      {appeal.decision ? (
        <div className="aq-decision-summary">
          <strong>
            <Check size={15} />
            {appeal.decision.action === "uphold"
              ? "Оценка подтверждена"
              : "Оценка изменена"}
          </strong>
          <p>{appeal.decision.reason}</p>
          <span>
            Исходный итог {number(evaluation.original_score.total, 2)} → текущий{" "}
            {number(evaluation.score.total, 2)}
          </span>
        </div>
      ) : null}
      {!appeal.decision || reviewing ? (
        <button
          type="button"
          ref={trigger}
          className="text-button aq-review-trigger"
          aria-expanded={reviewing}
          onClick={() => (reviewing ? close() : setReviewing(true))}
        >
          {reviewing ? "Закрыть форму решения" : "Решение проверяющего"}
        </button>
      ) : null}
      {reviewing ? (
        <DecisionForm
          appeal={appeal}
          evaluation={evaluation}
          criterion={criterion}
          result={result}
          onUpdated={onUpdated}
          onClose={close}
        />
      ) : null}
    </div>
  );
}

function DecisionForm({
  appeal,
  evaluation,
  criterion,
  result,
  onUpdated,
  onClose,
}: AppealProps & { appeal: Appeal; onClose: () => void }) {
  const { draft, update, reset } = useDecisionDraft(appeal.appeal_id, result, evaluation);
  const { action, reason, replacement, evidenceMode, messageId, busy, error, isConflict, refreshing } = draft;
  const setAction = (action: DecisionDraft["action"]) => update({ action });
  const setReason = (reason: string) => update({ reason });
  const setReplacement = (replacement: CriterionResult) => update({ replacement });
  const setEvidenceMode = (evidenceMode: DecisionDraft["evidenceMode"]) => update({ evidenceMode });
  const setMessageId = (messageId: string) => update({ messageId });
  const setBusy = (busy: boolean) => update({ busy });
  const setError = (error: string) => update({ error });
  const setIsConflict = (isConflict: boolean) => update({ isConflict });
  const setRefreshing = (refreshing: boolean) => update({ refreshing });
  const transcript = evaluation.conversation.transcript;
  const firstField = useRef<HTMLButtonElement>(null);
  useEffect(() => firstField.current?.focus(), []);
  const message = transcript.find((item) => item.message_id === messageId);
  const grossError =
    replacement.status === "applicable" &&
    (criterion.critical_answer != null
      ? replacement.answer === criterion.critical_answer
      : replacement.gross_error);
  async function submit() {
    if (!reason.trim() || appeal.status !== "open") return;
    setBusy(true);
    setError("");
    setIsConflict(false);
    try {
      let updated: CriterionResult | null = null;
      if (action === "replace") {
        updated = {
          ...replacement,
          answer:
            replacement.status === "applicable" ? replacement.answer : null,
          gross_error: grossError,
        };
        if (evidenceMode === "quote") {
          if (!message)
            throw new Error("Выберите доступную реплику для доказательства.");
          updated.evidence = [{ message_id: messageId, quote: message.text }];
          updated.checked_span = null;
        }
        if (evidenceMode === "span") {
          if (!transcript.length)
            throw new Error("В диалоге нет реплик для проверенного участка.");
          updated.evidence = [];
          updated.checked_span = {
            start_message_id: transcript[0].message_id,
            end_message_id: transcript.at(-1)!.message_id,
          };
        }
      }
      const next = await post<Evaluation>(
        `/appeals/${q(appeal.appeal_id)}/decision`,
        {
          action,
          reason: reason.trim(),
          replacement_result: updated,
          expected_revision: evaluation.revision,
          idempotency_key: uid(),
        },
      );
      onUpdated(next);
      reset();
      onClose();
    } catch (err) {
      setError(errorText(err));
      if (err instanceof ApiError && err.status === 409) setIsConflict(true);
    } finally {
      setBusy(false);
    }
  }
  async function syncEvaluation() {
    setRefreshing(true);
    try {
      const fresh = await request<Evaluation>(
        `/evaluations/${q(evaluation.evaluation_id)}`,
      );
      onUpdated(fresh);
      setError("");
      setIsConflict(false);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setRefreshing(false);
    }
  }
  return (
    <form
      className="aq-decision-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!busy) void submit();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !busy) {
          event.stopPropagation();
          onClose();
        }
      }}
    >
      <div className="aq-form-title">
        <h4>Решение проверяющего</h4>
        <button
          type="button"
          className="icon-button"
          aria-label="Закрыть форму решения"
          disabled={busy}
          onClick={onClose}
        >
          <X size={16} />
        </button>
      </div>
      <p className="aq-original-answer">
        Исходный ответ модели:{" "}
        <strong>
          {answerName(
            evaluation.original_results.find(
              (item) => item.criterion_id === criterion.criterion_id,
            ) ?? result,
          )}
        </strong>
      </p>
      {appeal.status !== "open" ? (
        <p className="aq-note" role="status">
          Апелляция уже рассмотрена. Ваш текст сохранён в форме, его можно
          скопировать.
        </p>
      ) : null}
      <label className="field">
        Решение
        <Select
          ref={firstField}
          value={action}
          onChange={(event) =>
            setAction(event.target.value as "uphold" | "replace")
          }
        >
          <option value="uphold">Подтвердить оценку</option>
          <option value="replace">Изменить оценку</option>
        </Select>
      </label>
      {action === "replace" ? (
        <>
          <label className="field">
            Применимость
            <Select
              value={replacement.status}
              onChange={(event) => {
                const status = event.target.value as CriterionResult["status"];
                setReplacement({
                  ...replacement,
                  status,
                  gross_error: false,
                  answer:
                    status !== "applicable"
                      ? null
                      : criterion.answer_type === "boolean"
                        ? false
                        : criterion.answer_type === "scale"
                          ? (criterion.allowed_values?.[0] ??
                            criterion.scale_min)
                          : Object.keys(criterion.choices)[0],
                });
              }}
            >
              <option value="applicable">Применим</option>
              {criterion.na_allowed ? (
                <option value="not_applicable">Не применим (N/A)</option>
              ) : null}
              <option value="needs_review">Нужна проверка</option>
            </Select>
          </label>
          {replacement.status === "applicable" ? (
            <label className="field">
              Новый ответ
              {criterion.answer_type === "boolean" ? (
                <Select
                  value={String(replacement.answer ?? false)}
                  onChange={(event) =>
                    setReplacement({
                      ...replacement,
                      answer: event.target.value === "true",
                    })
                  }
                >
                  <option value="true">Да</option>
                  <option value="false">Нет</option>
                </Select>
              ) : criterion.answer_type === "scale" ? (
                criterion.allowed_values?.length ? (
                  <Select
                    value={String(
                      replacement.answer ?? criterion.allowed_values[0],
                    )}
                    onChange={(event) =>
                      setReplacement({
                        ...replacement,
                        answer: Number(event.target.value),
                      })
                    }
                  >
                    {criterion.allowed_values.map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <input
                    type="number"
                    step="any"
                    min={criterion.scale_min ?? undefined}
                    max={criterion.scale_max ?? undefined}
                    value={
                      typeof replacement.answer === "number"
                        ? replacement.answer
                        : (criterion.scale_min ?? 0)
                    }
                    onChange={(event) =>
                      setReplacement({
                        ...replacement,
                        answer: Number(event.target.value),
                      })
                    }
                  />
                )
              ) : (
                <Select
                  value={String(replacement.answer ?? "")}
                  onChange={(event) =>
                    setReplacement({
                      ...replacement,
                      answer: event.target.value,
                    })
                  }
                >
                  {Object.keys(criterion.choices).map((choice) => (
                    <option key={choice}>{choice}</option>
                  ))}
                </Select>
              )}
            </label>
          ) : null}
          <label className="field">
            Объяснение нового результата
            <textarea
              rows={3}
              value={replacement.explanation}
              onChange={(event) =>
                setReplacement({
                  ...replacement,
                  explanation: event.target.value,
                })
              }
              required
            />
          </label>
          <label className="field">
            Доказательство
            <Select
              value={evidenceMode}
              onChange={(event) =>
                setEvidenceMode(event.target.value as typeof evidenceMode)
              }
            >
              <option value="keep">Сохранить исходные доказательства</option>
              <option value="quote">Выбрать точную реплику</option>
              <option value="span">
                Проверен весь диалог (действие отсутствует)
              </option>
            </Select>
          </label>
          {evidenceMode === "quote" ? (
            <>
              <label className="field">
                Реплика
                <Select
                  value={messageId}
                  onChange={(event) => setMessageId(event.target.value)}
                >
                  {transcript.map((entry, i) => (
                    <option key={entry.message_id} value={entry.message_id}>
                      {i + 1}. {entry.speaker}: {entry.text.slice(0, 65)}
                    </option>
                  ))}
                </Select>
              </label>
              {message ? <blockquote>{message.text}</blockquote> : null}
            </>
          ) : null}
          {criterion.gross_error_rule ? (
            <label className="check-field">
              <input
                type="checkbox"
                checked={grossError}
                disabled={
                  replacement.status !== "applicable" ||
                  criterion.critical_answer != null
                }
                onChange={(event) =>
                  setReplacement({
                    ...replacement,
                    gross_error: event.target.checked,
                  })
                }
              />
              Грубая ошибка
              {criterion.critical_answer != null ? " (по ответу)" : ""}
            </label>
          ) : null}
        </>
      ) : null}
      <label className="field">
        Обоснование решения
        <textarea
          rows={3}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="Обязательно для истории пересмотра"
          required
          maxLength={10000}
        />
      </label>
      {error ? (
        <div className="aq-decision-error" role="alert">
          <p>{error}</p>
          {isConflict ? (
            <>
              <p>
                Оценка изменилась. Обновите данные и проверьте решение.
                Введённое обоснование останется в форме.
              </p>
              <button
                type="button"
                className="button secondary small"
                disabled={refreshing}
                onClick={() => void syncEvaluation()}
              >
                {refreshing ? "Обновляем…" : "Обновить данные оценки"}
              </button>
            </>
          ) : null}
        </div>
      ) : null}
      <button
        type="submit"
        className="button primary small"
        disabled={
          busy ||
          refreshing ||
          isConflict ||
          appeal.status !== "open" ||
          !reason.trim() ||
          (action === "replace" && !replacement.explanation.trim())
        }
      >
        {busy ? "Сохраняем…" : "Сохранить решение и пересчитать"}
      </button>
    </form>
  );
}
