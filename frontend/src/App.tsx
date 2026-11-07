import Select from "./Select";
import QueueResize, { savedQueueWidth } from "./QueueResize";
import type { CSSProperties } from "react";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  AlertCircle,
  ArrowDownToLine,
  CheckCheck,
  ClipboardList,
  Headphones,
  List,
  LoaderCircle,
  MessageSquare,
  Play,
  RefreshCw,
  Settings2,
  Upload,
  X,
} from "lucide-react";
import type {
  ChecklistVersion,
  Conversation,
  ConversationSummary,
  Evaluation,
  EvaluationJob,
  ProviderInfo,
} from "./contracts";
import { ApiError, errorText, isTransientError, post, q, request, uid } from "./api";
import { readQueue, saveQueue, type SavedQueue } from "./evaluationQueue";
import { assertEvaluation, assertEvaluationHistory, assertJob, assertProviderInfo } from "./evaluationProtocol";
import { date, number } from "./format";
import Assessment, { AssessmentDraftsProvider } from "./Assessment";
import ChecklistEditor from "./ChecklistEditor";
import ConversationQueue, { conversationKey, matchesView, type QueueView } from "./ConversationQueue";
import Transcript, { type EvidenceTarget } from "./Transcript";
import Modal from "./Modal";
import SubmissionDialog from "./SubmissionDialog";
import freedomShield from "./freedom-shield.svg";

const checklistKey = (value: ChecklistVersion) =>
  JSON.stringify([value.checklist_id, value.version]);
const mobileViewport = "(max-width: 700px)";
const isMobileViewport = () => window.matchMedia(mobileViewport).matches;
function subscribeToMobileViewport(callback: () => void) {
  const viewport = window.matchMedia(mobileViewport);
  viewport.addEventListener("change", callback);
  return () => viewport.removeEventListener("change", callback);
}
const matchingEvaluation = (history: Evaluation[], source: Conversation) => history.find(item => {
  const saved = item.conversation;
  return conversationKey(saved) === conversationKey(source) && saved.language === source.language && saved.channel === source.channel && saved.audio_url === source.audio_url && saved.transcript.length === source.transcript.length && saved.transcript.every((message, index) => {
    const current = source.transcript[index];
    return message.message_id === current.message_id && message.speaker === current.speaker && message.timestamp === current.timestamp && message.text === current.text;
  });
})?.evaluation_id ?? '';
const mergeHistory = (fetched: Evaluation[], current: Evaluation[], source: Conversation) => {
  const merged = new Map(fetched.map(item => [item.evaluation_id, item]));
  for (const item of current) {
    if (conversationKey(item.conversation) !== conversationKey(source)) continue;
    const existing = merged.get(item.evaluation_id);
    if (!existing || item.revision > existing.revision) merged.set(item.evaluation_id, item);
    else if (item.revision === existing.revision) {
      const appeals = new Map(existing.appeals.map(appeal => [appeal.appeal_id, appeal]));
      item.appeals.forEach(appeal => appeals.set(appeal.appeal_id, appeal));
      merged.set(item.evaluation_id, { ...item, appeals: [...appeals.values()] });
    }
  }
  return [...merged.values()].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
};
type Batch = {
  total: number;
  done: number;
  failed: number;
  label: string;
  stopped: boolean;
};
export default function App() {
  const isMobile = useSyncExternalStore(subscribeToMobileViewport, isMobileViewport);
  const [tab, setTab] = useState<"review" | "checklist">("review");
  const [config, setConfig] = useState<ProviderInfo | null>(null);
  const [showSetup, setShowSetup] = useState(false);
  const [setupLoading, setSetupLoading] = useState(false);
  const [setupError, setSetupError] = useState("");
  const [submissionGroup, setSubmissionGroup] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [checklists, setChecklists] = useState<ChecklistVersion[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [selectedChecklistKey, setSelectedChecklistKey] = useState("");
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [evaluationId, setEvaluationId] = useState("");
  const [highlight, setHighlight] = useState<EvidenceTarget | null>(null);
  const [mobilePanel, setMobilePanel] = useState<
    "queue" | "transcript" | "assessment"
  >("queue");
  const [queueOpen, setQueueOpen] = useState(false);
  const [drawerMotion, setDrawerMotion] = useState(false);
  const [queueWidth, setQueueWidth] = useState(savedQueueWidth);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [jobStatus, setJobStatus] = useState<EvaluationJob["status"] | null>(
    null,
  );
  const queueButton = useRef<HTMLButtonElement>(null);
  const [query, setQuery] = useState("");
  const [language, setLanguage] = useState("");
  const [channel, setChannel] = useState("");
  const [group, setGroup] = useState("");
  const [queueView, setQueueView] = useState<QueueView>("all");
  const [initialLoading, setInitialLoading] = useState(true);
  const [overviewFailed, setOverviewFailed] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [detailAttempt, setDetailAttempt] = useState(0);
  const [importing, setImporting] = useState(false);
  const [running, setRunning] = useState(false);
  const [pendingQueue, setPendingQueue] = useState<SavedQueue | null>(readQueue);
  const executingRef = useRef(false);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [noticeKind, setNoticeKind] = useState<'success' | 'warning'>('success');
  const [importWarning, setImportWarning] = useState('');
  const [jobErrors, setJobErrors] = useState<
    Array<{ key: string; label: string; error: string }>
  >([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const selectedRef = useRef(selectedKey);
  const stopRef = useRef(false);
  const mountedRef = useRef(true);
  selectedRef.current = selectedKey;

  const selectedChecklist =
    checklists.find((item) => checklistKey(item) === selectedChecklistKey) ??
    checklists[0];
  const selectedSummary = conversations.find(
    (item) => conversationKey(item) === selectedKey,
  );
  const evaluation =
    evaluations.find((item) => item.evaluation_id === evaluationId) ?? null;
  const source = evaluation?.conversation ?? conversation;
  const groupSizes = useMemo(() => {
    const sizes = new Map<string, number>();
    for (const item of conversations) sizes.set(item.group_id, (sizes.get(item.group_id) ?? 0) + 1);
    return sizes;
  }, [conversations]);
  const groups = useMemo(() => [...groupSizes.keys()], [groupSizes]);
  const scopedItems = useMemo(() => {
    const search = query.trim().toLocaleLowerCase();
    return conversations.filter((item) =>
      (!language || item.language === language) &&
      (!channel || item.channel === channel) &&
      (!group || item.group_id === group) &&
      (!search || `${item.conversation_id} ${item.preview}`.toLocaleLowerCase().includes(search)),
    );
  }, [conversations, language, channel, group, query]);
  const filtered = useMemo(() => scopedItems.filter((item) => matchesView(item, queueView)), [scopedItems, queueView]);
  const selectionOutsideQueue = selectedSummary && !filtered.some((item) => conversationKey(item) === selectedKey);

  async function refreshProvider() {
    setSetupLoading(true);
    setSetupError("");
    try {
      const provider = await request<ProviderInfo>("/config");
      assertProviderInfo(provider);
      setConfig(provider);
    } catch (error) {
      setSetupError(errorText(error));
    } finally {
      setSetupLoading(false);
    }
  }

  function openSubmission() {
    setSubmissionGroup(group || selectedSummary?.group_id || groups[0] || "");
  }

  function onSubmissionDownload(message: string, hasErrors: boolean) {
    setSubmissionGroup(null);
    setNoticeKind(hasErrors ? "warning" : "success");
    setNotice(message);
  }

  async function loadOverview(signal?: AbortSignal) {
    const [list, rules, provider] = await Promise.all([
      request<ConversationSummary[]>("/conversations", { signal }),
      request<ChecklistVersion[]>("/checklists", { signal }),
      request<ProviderInfo>("/config", { signal }),
    ]);
    if (!mountedRef.current || signal?.aborted) return;
    assertProviderInfo(provider);
    setConversations(list); setChecklists(rules); setConfig(provider);
    setSelectedKey(current => list.some(item => conversationKey(item) === current) ? current : list[0] ? conversationKey(list[0]) : '');
    setSelectedChecklistKey(current => rules.some(item => checklistKey(item) === current) ? current : rules[0] ? checklistKey(rules[0]) : '');
  }

  async function reloadOverview(signal?: AbortSignal) {
    setInitialLoading(true);
    setOverviewFailed(false);
    setError("");
    try {
      await loadOverview(signal);
    } catch (error) {
      if (signal?.aborted) return;
      setOverviewFailed(true);
      setError(errorText(error));
    } finally {
      if (!signal?.aborted) setInitialLoading(false);
    }
  }

  useEffect(() => {
    if (!running) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [running]);

  async function refreshConversations() {
    const list = await request<ConversationSummary[]>("/conversations");
    if (mountedRef.current) setConversations(list);
  }

  useEffect(() => {
    const controller = new AbortController();
    mountedRef.current = true;
    void reloadOverview(controller.signal);
    return () => {
      controller.abort();
      mountedRef.current = false;
      stopRef.current = true;
    };
  }, []);

  useEffect(() => {
    setDetailError("");
    if (!selectedKey) {
      setConversation(null);
      setEvaluations([]);
      setEvaluationId("");
      return;
    }
    const controller = new AbortController();
    const [groupId, conversationId] = JSON.parse(selectedKey) as [
      string,
      string,
    ];
    setDetailLoading(true);
    setConversation(null);
    setEvaluations([]);
    setEvaluationId("");
    setHighlight(null);
    Promise.all([
      request<Conversation>(`/conversations/${q(conversationId)}?group_id=${q(groupId)}`, { signal: controller.signal }),
      request<Evaluation[]>(`/evaluations?group_id=${q(groupId)}&conversation_id=${q(conversationId)}`, { signal: controller.signal }),
    ]).then(([value, history]) => {
      if (!controller.signal.aborted) {
        assertEvaluationHistory(history);
        setConversation(value);
        setEvaluations(current => mergeHistory(history, current, value));
        setEvaluationId(current => current || matchingEvaluation(history, value));
      }
    }).catch(error => { if (!controller.signal.aborted) setDetailError(errorText(error)); }).finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selectedKey, detailAttempt]);

  async function importDataset(value: unknown) {
    setImporting(true);
    setError("");
    setNotice("");
    try {
      const result = await post<{
        group_id: string;
        imported: number;
        updated: number;
        unchanged: number;
        total: number;
        current_group_total?: number;
        retained_from_previous_import?: number;
      }>("/datasets/import", value);
      const list = await request<ConversationSummary[]>("/conversations");
      setConversations(list);
      setGroup(result.group_id);
      setLanguage("");
      setChannel("");
      setQueueView("all");
      setQuery("");
      const first = list.find((item) => item.group_id === result.group_id);
      if (first) {
        const nextKey = conversationKey(first);
        if (nextKey === selectedRef.current) {
          const [current, history] = await Promise.all([request<Conversation>(`/conversations/${q(first.conversation_id)}?group_id=${q(first.group_id)}`), request<Evaluation[]>(`/evaluations?group_id=${q(first.group_id)}&conversation_id=${q(first.conversation_id)}`)]);
          if (selectedRef.current === nextKey) { assertEvaluationHistory(history); setConversation(current); setEvaluations(existing => mergeHistory(history, existing, current)); setEvaluationId(matchingEvaluation(history, current)); }
        } else setSelectedKey(nextKey);
      }
      setTab("review");
      setMobilePanel("queue");
      setNoticeKind('success');
      setImportWarning(result.retained_from_previous_import ? `Набор также содержит ${result.retained_from_previous_import} диалогов из предыдущих импортов. Всего: ${result.current_group_total}.` : '');
      setNotice(
        `Набор ${result.group_id}: добавлено ${result.imported}, обновлено ${result.updated}, без изменений ${result.unchanged}.`,
      );
    } catch (error) {
      setError(errorText(error));
    } finally {
      setImporting(false);
    }
  }

  async function readFile(file: File | undefined) {
    if (!file) return;
    try {
      if (file.size > 25 * 1024 * 1024)
        throw new Error(
          "Файл больше 25 МБ. Разделите набор на несколько файлов.",
        );
      const dataset: unknown = JSON.parse(await file.text());
      await importDataset({ dataset });
    } catch (error) {
      setError(
        error instanceof SyntaxError
          ? "Файл не содержит корректный JSON. Используйте исходный экспорт транскриптов."
          : errorText(error),
      );
    } finally {
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  async function run(targets: ConversationSummary[]) {
    if (!selectedChecklist || !targets.length || pendingQueue || importing || executingRef.current) return;
    if (!config?.provider_configured) { setShowSetup(true); return; }
    const queue: SavedQueue = { version: 1, checklist_id: selectedChecklist.checklist_id, checklist_version: selectedChecklist.version, model: config.model ?? '', prompt_version: config.prompt_version ?? '', items: targets.map(target => ({ group_id: target.group_id, conversation_id: target.conversation_id, key: uid(), done: false, failed: false })) };
    await executeQueue(queue);
  }

  async function executeQueue(queue: SavedQueue) {
    if (executingRef.current) return;
    executingRef.current = true;
    setRunning(true);
    setError("");
    let currentConfig: ProviderInfo;
    try { currentConfig = await request<ProviderInfo>('/config'); assertProviderInfo(currentConfig); setConfig(currentConfig); }
    catch (error) { executingRef.current = false; setRunning(false); setError(errorText(error)); return; }
    if (!currentConfig.provider_configured || currentConfig.model !== queue.model || (currentConfig.prompt_version ?? '') !== queue.prompt_version) {
      executingRef.current = false;
      setRunning(false);
      setError('Настройки модели изменились. Проверьте сохранённые результаты и уберите старую очередь перед новым запуском.'); return;
    }
    setNotice(''); setJobErrors([]); stopRef.current = false;
    setSelectedChecklistKey(JSON.stringify([queue.checklist_id, queue.checklist_version]));
    setPendingQueue(queue);
    if (!saveQueue(queue)) setError('Браузер не сохранил очередь. Не обновляйте вкладку до завершения обработки.');
    let done = queue.items.filter(item => item.done).length, failed = queue.items.filter(item => item.failed).length;
    setBatch({ total: queue.items.length, done, failed, label: '', stopped: false });
    for (const target of queue.items) {
      if (target.done) continue;
      if (stopRef.current || !mountedRef.current) break;
      setJobStatus(null);
      setBatch({ total: queue.items.length, done, failed, label: target.conversation_id, stopped: false });
      try {
        let job = target.job_id ? await request<EvaluationJob>(`/evaluation-jobs/${q(target.job_id)}`)
          : await post<EvaluationJob>('/evaluation-jobs', { conversation_id: target.conversation_id, group_id: target.group_id, checklist_id: queue.checklist_id, checklist_version: queue.checklist_version, idempotency_key: target.key });
        assertJob(job);
        if (target.job_id && job.job_id !== target.job_id) throw new ApiError('Сервер вернул другую задачу. Продолжите сохранённую очередь после восстановления связи.', 0);
        setJobStatus(job.status);
        target.job_id = job.job_id;
        saveQueue(queue);
        while (job.status === 'queued' || job.status === 'running') {
          await new Promise(resolve => setTimeout(resolve, 1200));
          if (!mountedRef.current) { executingRef.current = false; return; }
          const nextJob: EvaluationJob = await request<EvaluationJob>(`/evaluation-jobs/${q(target.job_id)}`);
          assertJob(nextJob);
          if (nextJob.job_id !== target.job_id) throw new ApiError('Сервер вернул другую задачу. Продолжите сохранённую очередь после восстановления связи.', 0);
          job = nextJob;
          setJobStatus(job.status);
        }
        if (job.status === 'failed' || !job.evaluation_id) throw new Error(job.error || 'Оценка не завершена. Повторите запуск.');
        const completed = await request<Evaluation>(`/evaluations/${q(job.evaluation_id)}`);
        assertEvaluation(completed);
        if (completed.evaluation_id !== job.evaluation_id || conversationKey(completed.conversation) !== conversationKey(target)
          || completed.checklist.checklist_id !== queue.checklist_id || completed.checklist.version !== queue.checklist_version) throw new ApiError('Сервер вернул оценку другого диалога или версии правил. Проверьте историю перед продолжением очереди.', 0);
        if (selectedRef.current === conversationKey(target)) {
          setEvaluations((current) => [
            completed,
            ...current.filter(
              (item) => item.evaluation_id !== completed.evaluation_id,
            ),
          ]);
          setEvaluationId(completed.evaluation_id);
          setMobilePanel("assessment");
        }
      } catch (error) {
        if (isTransientError(error)) {
          stopRef.current = true;
          setError('Связь с сервером прервалась. Очередь сохранена: продолжение проверит уже созданную задачу без повторного запуска модели.');
          break;
        }
        target.failed = true;
        failed++;
        setJobErrors((current) => [
          ...current,
          {
            key: conversationKey(target),
            label: target.conversation_id,
            error: errorText(error),
          },
        ]);
      }
      target.done = true;
      saveQueue(queue);
      done++;
      setBatch({ total: queue.items.length, done, failed, label: '', stopped: stopRef.current });
      await refreshConversations().catch(() => undefined);
    }
    executingRef.current = false;
    const remaining = queue.items.some(item => !item.done) ? queue : null;
    saveQueue(remaining);
    if (mountedRef.current) { setPendingQueue(remaining); setRunning(false); setBatch({ total: queue.items.length, done, failed, label: '', stopped: !!remaining && stopRef.current }); }
  }

  function onEvaluationUpdated(value: Evaluation) {
    assertEvaluation(value);
    setEvaluations(current => current.map(item => {
      if (item.evaluation_id !== value.evaluation_id || item.revision > value.revision) return item;
      if (item.revision === value.revision) {
        const appeals = new Map(item.appeals.map(appeal => [appeal.appeal_id, appeal]));
        value.appeals.forEach(appeal => appeals.set(appeal.appeal_id, appeal));
        return { ...value, appeals: [...appeals.values()] };
      }
      return value;
    }));
    void refreshConversations().catch(error => setError(errorText(error)));
  }

  function focusElement(id: string, animate = false) {
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        const element = document.getElementById(id);
        if (!element) return;
        const panel = element.closest(".transcript-content, .assessment-pane");
        const nearby = panel && Math.abs(element.getBoundingClientRect().top - panel.getBoundingClientRect().top) < panel.clientHeight;
        const smooth = animate && nearby && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        element.scrollIntoView({
          behavior: smooth ? "smooth" : "instant",
          block: "center",
        });
        element.focus({ preventScroll: true });
      }),
    );
  }
  function openEvidence(target: EvidenceTarget, animate: boolean) {
    setHighlight(target);
    setMobilePanel("transcript");
    focusElement(`message-${target.start}`, animate);
  }
  function returnToCriterion(animate: boolean) {
    if (!highlight) return;
    setMobilePanel("assessment");
    focusElement(`criterion-${highlight.criterion}`, animate);
  }
  function closeQueue() {
    setQueueOpen(false);
    setFiltersOpen(false);
    queueButton.current?.focus();
  }
  useEffect(() => {
    if (!queueOpen && !filtersOpen) return;
    const escape = (event: KeyboardEvent) => {
      if (event.defaultPrevented || document.querySelector("dialog[open], .select-menu[data-state=open]")) return;
      if (event.key === "Tab" && queueOpen) {
        const controls = [
          ...document.querySelectorAll<HTMLElement>(
            "#queue-panel button:not(:disabled), #queue-panel input, #queue-panel select",
          ),
        ].filter((element) => element.getClientRects().length > 0);
        const first = controls[0],
          last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
      if (event.key !== "Escape") return;
      if (filtersOpen) {
        setFiltersOpen(false);
        document.querySelector<HTMLButtonElement>(".filter-toggle")?.focus();
      } else if (queueOpen) closeQueue();
    };
    const adjacent = [
      document.getElementById("transcript-panel"),
      document.getElementById("assessment-panel"),
    ];
    if (queueOpen)
      adjacent.forEach((element) => element?.setAttribute("inert", ""));
    const viewport = window.matchMedia(
      "(min-width: 701px) and (max-width: 1199px)",
    );
    const resized = () => {
      if (!viewport.matches) setQueueOpen(false);
    };
    viewport.addEventListener("change", resized);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("keydown", escape);
      viewport.removeEventListener("change", resized);
      adjacent.forEach((element) => element?.removeAttribute("inert"));
    };
  }, [queueOpen, filtersOpen]);
  useEffect(() => {
    setHighlight(null);
  }, [evaluationId]);
  function resetFilters() {
    setQuery("");
    setLanguage("");
    setChannel("");
    setGroup("");
    setQueueView("all");
  }

  function openTab(value: typeof tab) {
    setQueueOpen(false);
    setFiltersOpen(false);
    setTab(value);
  }

  function savedChecklist(value: ChecklistVersion, baseKey: string) {
    setChecklists((current) => [
      value,
      ...current.filter((item) => checklistKey(item) !== checklistKey(value)),
    ]);
    setSelectedChecklistKey(current => current === baseKey ? checklistKey(value) : current);
    setNoticeKind("success");
    setNotice(
      `Чек-лист «${value.title}» v${value.version} сохранён. Выберите эту версию для следующей оценки.`,
    );
  }

  const workspace = (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Перейти к рабочей области
      </a>
      <header className="app-header">
        <a
          className="brand"
          href="#"
          onClick={(event) => {
            event.preventDefault();
            openTab("review");
          }}
          aria-label="CQA — рабочая область"
        >
          <span className="brand-mark"><img src={freedomShield} alt="" width={23} height={27} /></span>
          <span className="brand-copy" translate="no"><strong>CQA</strong><span>Freedom Broker</span></span>
        </a>
        <nav className="main-nav" aria-label="Разделы приложения">
          <button
            className={tab === "review" ? "active" : ""}
            onClick={() => openTab("review")}
            aria-current={tab === "review" ? "page" : undefined}
          >
            Диалоги
          </button>
          <button
            className={tab === "checklist" ? "active" : ""}
            onClick={() => openTab("checklist")}
            aria-current={tab === "checklist" ? "page" : undefined}
          >
            Чек-листы
          </button>
        </nav>
        <div className="header-actions">
          <span className="team-name" translate="no">SetW</span>
          <button
            className={`provider-button ${config?.provider_configured ? "connected" : ""}`}
            data-mode={config?.mode}
            onClick={() => setShowSetup(true)}
            aria-haspopup="dialog"
            aria-label={
              config?.mode === "test"
                ? "Тестовый режим — настройки модели"
                : config?.provider_configured
                  ? "Модель подключена — настройки модели"
                  : "Модель не подключена — настройки модели"
            }
          >
            <span className="status-dot" aria-hidden="true" />
            <span>
              {config?.mode === "test"
                ? "Тест"
                : config?.provider_configured
                  ? "Модель"
                  : "Нет модели"}
            </span>
            <Settings2 size={15} aria-hidden="true" />
          </button>
        </div>
      </header>
      {showSetup && (
        <Modal
          title={
            config?.mode === "test"
              ? "Тестовый режим интерфейса"
              : "Подключение модели"
          }
          onClose={() => {
            setSetupError("");
            setShowSetup(false);
          }}
        >
          <div className="setup-content">
            <div
              className={`setup-status ${config?.provider_configured ? "connected" : ""}`}
            >
              <span className="status-dot" />
              {config?.mode === "test"
                ? "Изолированная проверка UI"
                : config?.provider_configured
                  ? config.model
                  : "Подключение не настроено"}
            </div>
            <p>
              {config?.mode === "test"
                ? "Ответы эмулятора проверяют работу интерфейса. Это не реальная AI-оценка и не эталон качества."
                : config?.provider_configured
                  ? `Диалоги передаются настроенному провайдеру ${config.provider} при запуске оценки.`
                  : "Импорт, просмотр и настройка правил доступны. Для оценки укажите подключение на сервере."}
            </p>
            {!config?.provider_configured && (
              <p className="setup-code">
                В локальном <code>.env</code> укажите <code>LLM_BASE_URL</code>,{" "}
                <code>LLM_MODEL</code> и при необходимости{" "}
                <code>LLM_API_KEY</code>. Затем перезапустите сервер.
              </p>
            )}
            {setupError && <p className="notice error" role="alert">{setupError}</p>}
            <button
              className="button secondary"
              onClick={() => void refreshProvider()}
              disabled={setupLoading}
            >
              {setupLoading ? <span className="spin" aria-hidden="true"><LoaderCircle size={15} /></span> : <RefreshCw size={15} aria-hidden="true" />}
              {setupLoading ? "Проверяем подключение…" : "Обновить настройки"}
            </button>
          </div>
        </Modal>
      )}
      <div className="global-messages" aria-live="polite">
        {error && (
          <div className="notice error" role="alert">
            <AlertCircle size={18} />
            <span>{error}</span>
            <button
              className="icon-button"
              aria-label="Закрыть сообщение об ошибке"
              onClick={() => setError("")}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {notice && (
          <div className={`notice ${noticeKind}`} role="status">
            {noticeKind === "warning" ? <AlertCircle size={17} aria-hidden="true" /> : <CheckCheck size={17} aria-hidden="true" />}
            <span>{notice}</span>
            <button
              className="icon-button"
              aria-label="Закрыть уведомление"
              onClick={() => setNotice("")}
            >
              <X size={16} />
            </button>
          </div>
        )}
        {importWarning && <div className="notice warning" role="status"><AlertCircle size={17} aria-hidden="true" /><span>{importWarning}</span><button className="icon-button" aria-label="Закрыть предупреждение о составе набора" onClick={() => setImportWarning('')}><X size={16} aria-hidden="true" /></button></div>}
      </div>
      <main id="main-content" tabIndex={-1}>
        <div className="workspace-toolbar">
          <h1 className="page-title">
            {tab === "review" ? "Диалоги" : "Чек-листы"}
          </h1>
          <div className="toolbar-actions">
            <label className="checklist-select">
              <span className="checklist-label">{tab === "review" ? "Чек-лист для запуска" : "Версия для редактирования"}</span>
              <Select
                aria-label="Версия чек-листа"
                value={selectedChecklist ? checklistKey(selectedChecklist) : ""}
                onChange={(event) => setSelectedChecklistKey(event.target.value)}
                disabled={!checklists.length}
                selectedLabel={selectedChecklist ? (
                  <span className="version-selection">
                    <ClipboardList size={15} aria-hidden="true" />
                    <span title={selectedChecklist.title}>{selectedChecklist.title}</span>
                    <strong>v{selectedChecklist.version}</strong>
                  </span>
                ) : initialLoading ? "Загружаем…" : "Чек-лист недоступен"}
              >
                {checklists.map((item) => (
                  <option value={checklistKey(item)} key={checklistKey(item)}>
                    {item.title} — v{item.version}
                  </option>
                ))}
              </Select>
            </label>
            {tab === "review" && conversations.length > 0 && (
              <button className="button secondary submission-button" onClick={openSubmission} aria-haspopup="dialog">
                <ArrowDownToLine size={16} aria-hidden="true" /> <span>Файл для сдачи</span>
              </button>
            )}
          </div>
        </div>
        <input
          type="file"
          accept="application/json,.json"
          className="sr-only"
          ref={inputRef}
          onChange={(event) => void readFile(event.target.files?.[0])}
          aria-label="Выбрать JSON с диалогами"
          tabIndex={-1}
        />
        {submissionGroup !== null && (
          <SubmissionDialog
            config={config}
            group={submissionGroup}
            groupSizes={groupSizes}
            onClose={() => setSubmissionGroup(null)}
            onDownload={onSubmissionDownload}
          />
        )}

        {batch && (
          <div className={`batch-banner${!running && batch.failed ? " batch-warning" : ""}`} role="status">
            <span className={running ? "spin" : undefined} aria-hidden="true">
              {running ? <LoaderCircle size={18} /> : batch.failed ? <AlertCircle size={18} /> : <CheckCheck size={18} />}
            </span>
            <div>
              <strong>
                {running
                  ? batch.label
                    ? `${jobStatus === "queued" ? "В очереди" : jobStatus === "running" ? "Анализ" : "Обработка"}: ${batch.label}`
                    : "Обновляем очередь…"
                  : batch.stopped
                    ? "Очередь остановлена"
                    : "Обработка завершена"}
              </strong>
              <span>
                Обработано {batch.done} из {batch.total}
                {batch.failed ? ` · успешно ${batch.done - batch.failed}, ошибок ${batch.failed}` : ""}
              </span>
            </div>
            <progress
              value={batch.done}
              max={batch.total}
              aria-label="Прогресс оценки диалогов"
            />
            {running ? (
              <button
                className="button secondary small"
                onClick={() => {
                  stopRef.current = true;
                  setBatch((current) =>
                    current ? { ...current, stopped: true } : current,
                  );
                }}
                disabled={batch.stopped}
              >
                {batch.stopped
                  ? "После текущего диалога"
                  : "Остановить очередь"}
              </button>
            ) : (
              <button
                className="icon-button"
                aria-label="Скрыть прогресс"
                onClick={() => {
                  setBatch(null);
                  setJobErrors([]);
                }}
              >
                <X size={17} />
              </button>
            )}
          </div>
        )}
        {pendingQueue && !running && (
          <section className="queue-recovery notice warning" aria-label="Незавершённая очередь">
            <div>
              <strong>Осталось обработать: {pendingQueue.items.filter(item => !item.done).length}</strong>
              <p>Продолжение проверит уже запущенные оценки и обработает оставшиеся диалоги.</p>
            </div>
            <div className="button-row">
              <button className="button secondary small" disabled={!config?.provider_configured} onClick={() => void executeQueue(pendingQueue)}>Продолжить очередь</button>
              <button className="button ghost small" onClick={() => { saveQueue(null); setPendingQueue(null); }}>Убрать очередь</button>
            </div>
          </section>
        )}
        {jobErrors.length > 0 && (
          <details className="job-errors">
            <summary>
              {jobErrors.length} ошибок обработки. Открыть подробности
            </summary>
            {jobErrors.map((item, index) => (
              <p key={index}>
                <strong>{item.label}:</strong> {item.error}
              </p>
            ))}
            <button
              className="button secondary small"
              disabled={running || !!pendingQueue}
              onClick={() =>
                void run(
                  conversations.filter((item) =>
                    jobErrors.some(
                      (error) => error.key === conversationKey(item),
                    ),
                  ),
                )
              }
            >
              Повторить с ошибками
            </button>
          </details>
        )}
        {initialLoading ? (
          <div className="large-empty" role="status">
            <span className="spin" aria-hidden="true"><LoaderCircle size={26} /></span>
            <h2>Открываем рабочую область…</h2>
          </div>
        ) : overviewFailed ? (
          <div className="large-empty">
            <AlertCircle size={26} aria-hidden="true" />
            <h2>Рабочая область недоступна</h2>
            <p>Проверьте, запущен ли сервер, и повторите загрузку.</p>
            <button className="button primary" onClick={() => void reloadOverview()}><RefreshCw size={16} aria-hidden="true" /> Повторить загрузку</button>
          </div>
        ) : tab === "checklist" ? (
          !selectedChecklist && (
            <div className="large-empty">
              <h2>Чек-лист не найден</h2>
              <p>Проверьте доступность API и повторите загрузку.</p>
              <button
                className="button secondary"
                onClick={() => void reloadOverview()}
              >
                Обновить
              </button>
            </div>
          )
        ) : conversations.length === 0 ? (
          <section className="onboarding">
            <div className="onboarding-import">
              <span className="onboarding-icon"><Upload size={24} aria-hidden="true" /></span>
              <h2>Загрузите диалоги</h2>
              <p>Импортируйте JSON с транскриптами чатов или звонков на русском, казахском или английском. Один файл до 25&nbsp;МБ.</p>
              <div className="button-row">
                <button
                  className="button primary"
                  onClick={() => inputRef.current?.click()}
                  disabled={importing}
                >
                  {importing ? <span className="spin" aria-hidden="true"><LoaderCircle size={17} /></span> : <Upload size={17} aria-hidden="true" />}
                  {importing ? "Импортируем…" : "Импорт JSON"}
                </button>
                <button
                  className="button secondary"
                  onClick={() => void importDataset({ source: "dev" })}
                  disabled={importing}
                >
                  Открыть DEV
                </button>
              </div>
              <button
                className="text-button"
                onClick={() => void importDataset({ source: "demo" })}
                disabled={importing}
              >
                Открыть пример диалогов
              </button>
            </div>
            <div className="onboarding-steps">
              <h3>Как проходит проверка</h3>
              <ol>
                <li><strong>Выберите правила</strong><span>Укажите версию чек-листа для запуска.</span></li>
                <li><strong>Запустите оценку</strong><span>Оцените один диалог или всю выборку.</span></li>
                <li><strong>Проверьте выводы</strong><span>Откройте цитаты из диалога и при необходимости подайте апелляцию.</span></li>
              </ol>
            </div>
          </section>
        ) : (
          <div
            className={`review-workspace mobile-${mobilePanel} ${queueOpen ? "queue-open" : ""}`}
            data-drawer-motion={drawerMotion}
            style={{ "--queue-width": `${queueWidth}px` } as CSSProperties}
          >
            <div className="review-context">
              <button
                ref={queueButton}
                className="button secondary small tablet-queue-button"
                onClick={(event) => {
                  setDrawerMotion(event.detail > 0);
                  setQueueOpen(true);
                  requestAnimationFrame(() =>
                    document
                      .querySelector<HTMLInputElement>(".queue-search input")
                      ?.focus(),
                  );
                }}
                aria-expanded={queueOpen}
                aria-controls="queue-panel"
              >
                <List size={16} /> Очередь
              </button>
              {selectedSummary && (
                <div className="conversation-context">
                  <span className="context-icon" aria-hidden="true">
                    {selectedSummary.channel === "voice call" ? <Headphones size={19} /> : <MessageSquare size={19} />}
                  </span>
                  <div className="context-copy">
                    <h2 id="conversation-title" tabIndex={-1} translate="no">{selectedSummary.conversation_id}</h2>
                    <div className="context-tags">
                      <span>{selectedSummary.channel === "voice call" ? "Звонок" : "Чат"}</span>
                      <span translate="no">{selectedSummary.group_id}</span>
                      <span translate="no">{selectedSummary.language}</span>
                      {selectionOutsideQueue && <span className="outside-queue">Вне текущей выборки</span>}
                    </div>
                  </div>
                </div>
              )}
            </div>
            <nav
              className="mobile-panel-tabs"
              role="tablist"
              aria-label="Панели разбора"
            >
              {(["queue", "transcript", "assessment"] as const).map(
                (panel, index) => (
                  <button
                    key={panel}
                    id={`tab-${panel}`}
                    role="tab"
                    aria-selected={mobilePanel === panel}
                    aria-controls={
                      panel === "queue" ? "queue-panel" : `${panel}-panel`
                    }
                    tabIndex={mobilePanel === panel ? 0 : -1}
                    onKeyDown={(event) => {
                      if (
                        ["ArrowRight", "ArrowLeft", "Home", "End"].includes(
                          event.key,
                        )
                      ) {
                        event.preventDefault();
                        const next =
                          event.key === "Home"
                            ? "queue"
                            : event.key === "End"
                              ? "assessment"
                              : (
                                  ["queue", "transcript", "assessment"] as const
                                )[
                                  (index +
                                    (event.key === "ArrowRight" ? 1 : 2)) %
                                    3
                                ];
                        setMobilePanel(next);
                        document.getElementById(`tab-${next}`)?.focus();
                      }
                    }}
                    onClick={() => setMobilePanel(panel)}
                  >
                    {index === 0 ? (
                      <List size={16} />
                    ) : index === 1 ? (
                      <MessageSquare size={16} />
                    ) : (
                      <ClipboardList size={16} />
                    )}
                    {["Очередь", "Диалог", "Оценка"][index]}
                    {panel === "assessment" && evaluation && (
                      <span className="tab-result-dot" aria-hidden="true" />
                    )}
                  </button>
                ),
              )}
            </nav>
              {queueOpen && (
                <button
                  className="queue-backdrop"
                  aria-label="Закрыть список диалогов"
                tabIndex={-1}
                  onClick={closeQueue}
                />
              )}
              <ConversationQueue
                resizeHandle={<QueueResize onChange={setQueueWidth} />}
                items={filtered}
                scopedItems={scopedItems}
                total={conversations.length}
                selectedKey={selectedKey}
                groups={groups}
                query={query}
                language={language}
                channel={channel}
                group={group}
                view={queueView}
                filtersOpen={filtersOpen}
                drawerOpen={queueOpen}
                isMobile={isMobile}
                running={running}
                pending={!!pendingQueue}
                importing={importing}
                configured={!!config?.provider_configured}
                canRun={!!selectedChecklist}
                onQuery={setQuery}
                onLanguage={setLanguage}
                onChannel={setChannel}
                onGroup={setGroup}
                onView={setQueueView}
                onFilters={() => {
                  setFiltersOpen(!filtersOpen);
                  if (filtersOpen) document.querySelector<HTMLButtonElement>(".filter-toggle")?.focus();
                }}
                onSelect={(item) => {
                  setSelectedKey(conversationKey(item));
                  setMobilePanel("transcript");
                  setQueueOpen(false);
                  setFiltersOpen(false);
                  if (queueOpen || isMobile)
                    requestAnimationFrame(() => document.getElementById("conversation-title")?.focus({ preventScroll: true }));
                }}
                onReset={resetFilters}
                onImport={() => inputRef.current?.click()}
                onRun={() => void run(filtered)}
                onClose={closeQueue}
              />
              <Transcript
                key={JSON.stringify([selectedKey, evaluationId])}
                source={source}
                evaluation={evaluation}
                highlight={highlight}
                loading={detailLoading}
                error={detailError}
                isMobile={isMobile}
                onRetry={() => setDetailAttempt((current) => current + 1)}
                onReturn={returnToCriterion}
              />
              <aside
                id="assessment-panel"
                className="assessment-pane"
                role={isMobile ? "tabpanel" : undefined}
                aria-labelledby={isMobile ? "tab-assessment" : undefined}
                aria-label={isMobile ? undefined : "Результаты оценки"}
              >
                <div className="assessment-actions">
                  <h2>Оценка</h2>
                  <button
                    className="button primary small"
                    disabled={
                      !selectedSummary ||
                      running ||
                      importing ||
                      !!pendingQueue ||
                      detailLoading ||
                      !!detailError ||
                      !selectedChecklist
                    }
                    onClick={() =>
                      selectedSummary && void run([selectedSummary])
                    }
                  >
                    {running ? (
                      <span className="spin" aria-hidden="true"><LoaderCircle size={14} /></span>
                    ) : !config?.provider_configured ? (
                      <Settings2 size={14} aria-hidden="true" />
                    ) : (
                      <Play size={14} aria-hidden="true" />
                    )}
                    {running
                      ? "Обрабатываем…"
                      : !config?.provider_configured
                        ? "Подключить модель"
                      : evaluation
                        ? "Оценить снова"
                        : "Оценить диалог"}
                  </button>
                </div>
                {evaluations.length > 0 && (
                  <label className="history-select">
                    <span>История запусков</span>
                    <Select
                      aria-label="История оценок"
                      value={evaluationId}
                      onChange={(event) => setEvaluationId(event.target.value)}
                    >
                      <option value="">Текущий диалог без оценки</option>
                      {evaluations.map((item) => (
                        <option
                          key={item.evaluation_id}
                          value={item.evaluation_id}
                        >
                          {date(item.created_at)} · v{item.checklist.version} ·{" "}
                          {number(item.score.total, 2)}
                        </option>
                      ))}
                    </Select>
                  </label>
                )}
                {evaluation ? (
                  <Assessment
                    key={evaluation.evaluation_id}
                    evaluation={evaluation}
                    onUpdated={onEvaluationUpdated}
                    onEvidence={openEvidence}
                  />
                ) : detailLoading ? (
                  <div className="assessment-empty" role="status">
                    <span className="spin" aria-hidden="true"><LoaderCircle size={22} /></span>
                    <p>Загружаем оценки…</p>
                  </div>
                ) : detailError ? (
                  <div className="assessment-empty">
                    <h3>Оценки недоступны</h3>
                    <button className="text-button" onClick={() => setDetailAttempt((current) => current + 1)}>Повторить загрузку</button>
                  </div>
                ) : (
                  <div className="assessment-empty">
                    <span className="assessment-empty-icon"><ClipboardList size={22} aria-hidden="true" /></span>
                    <h3>{evaluations.length ? "Открыт текущий диалог" : "Диалог ещё не оценён"}</h3>
                    <p>
                      {selectedChecklist
                        ? `Для следующего запуска выбрана версия v${selectedChecklist.version} (${selectedChecklist.criteria.length} критериев).`
                        : "Выберите чек-лист для оценки."}
                    </p>
                    <button
                      className="text-button"
                      onClick={() => openTab("checklist")}
                    >
                      Изменить чек-лист
                    </button>
                  </div>
                )}
              </aside>
          </div>
        )}
        {selectedChecklist && (
          <ChecklistEditor
            visible={tab === "checklist" && !initialLoading && !overviewFailed}
            checklist={selectedChecklist}
            versionKey={checklistKey(selectedChecklist)}
            onSaved={savedChecklist}
          />
        )}
      </main>
    </div>
  );
  return <AssessmentDraftsProvider>{workspace}</AssessmentDraftsProvider>;
}
