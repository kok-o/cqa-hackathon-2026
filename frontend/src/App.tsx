import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCheck,
  ClipboardList,
  Download,
  FileJson,
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
  SubmissionFile,
} from "./contracts";
import { downloadJson, errorText, post, q, request, uid } from "./api";
import { date, number } from "./format";
import Assessment from "./Assessment";
import ChecklistEditor from "./ChecklistEditor";
import ConversationQueue from "./ConversationQueue";
import Transcript from "./Transcript";
import Modal from "./Modal";

const conversationKey = (value: {
  group_id: string;
  conversation_id: string;
}) => JSON.stringify([value.group_id, value.conversation_id]);
const checklistKey = (value: ChecklistVersion) =>
  JSON.stringify([value.checklist_id, value.version]);
type Batch = {
  total: number;
  done: number;
  failed: number;
  label: string;
  stopped: boolean;
};
type SubmissionForm = {
  team: string;
  group: string;
  split: "dev" | "prod";
  model: string;
  mode: SubmissionFile["model"]["mode"];
};

export default function App() {
  const [tab, setTab] = useState<"review" | "checklist">("review");
  const [config, setConfig] = useState<ProviderInfo | null>(null);
  const [showSetup, setShowSetup] = useState(false);
  const [submission, setSubmission] = useState<SubmissionForm | null>(null);
  const [submissionError, setSubmissionError] = useState("");
  const [exportingSubmission, setExportingSubmission] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [checklists, setChecklists] = useState<ChecklistVersion[]>([]);
  const [selectedKey, setSelectedKey] = useState("");
  const [selectedChecklistKey, setSelectedChecklistKey] = useState("");
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [evaluationId, setEvaluationId] = useState("");
  const [highlight, setHighlight] = useState<{
    start: string;
    end?: string;
    criterion?: string;
  } | null>(null);
  const [mobilePanel, setMobilePanel] = useState<
    "queue" | "transcript" | "assessment"
  >("queue");
  const [queueOpen, setQueueOpen] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [jobStatus, setJobStatus] = useState<EvaluationJob["status"] | null>(
    null,
  );
  const queueButton = useRef<HTMLButtonElement>(null);
  const [query, setQuery] = useState("");
  const [language, setLanguage] = useState("");
  const [channel, setChannel] = useState("");
  const [group, setGroup] = useState("");
  const [grossError, setGrossError] = useState("");
  const [initialLoading, setInitialLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [running, setRunning] = useState(false);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
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
  const groups = useMemo(
    () => [...new Set(conversations.map((item) => item.group_id))],
    [conversations],
  );
  const filtered = useMemo(
    () =>
      conversations.filter(
        (item) =>
          (!language || item.language === language) &&
          (!channel || item.channel === channel) &&
          (!group || item.group_id === group) &&
          (!grossError ||
            item.latest_gross_error === (grossError === "true")) &&
          (!query ||
            `${item.conversation_id} ${item.preview}`
              .toLocaleLowerCase()
              .includes(query.toLocaleLowerCase())),
      ),
    [conversations, language, channel, group, grossError, query],
  );
  const submissionCount = submission
    ? conversations.filter((item) => item.group_id === submission.group).length
    : 0;

  function openSubmission() {
    const chosenGroup = group || selectedSummary?.group_id || groups[0] || "";
    setSubmission({
      team: config?.team_name ?? "",
      group: chosenGroup,
      split: chosenGroup.toUpperCase() === "DEV" ? "dev" : "prod",
      model: config?.model ?? "",
      mode: config?.provider === "local" ? "local" : "external_api",
    });
    setSubmissionError("");
  }

  async function exportSubmission() {
    if (
      !submission ||
      !submission.team.trim() ||
      !submission.group ||
      !submission.model.trim()
    )
      return;
    setExportingSubmission(true);
    setSubmissionError("");
    setNotice("");
    try {
      const params = new URLSearchParams({
        group_id: submission.group,
        model_name: submission.model.trim(),
        split: submission.split,
        team_name: submission.team.trim(),
        model_mode: submission.mode,
      });
      const document = await request<SubmissionFile>(
        `/submission/export?${params}`,
      );
      downloadJson(document, "results.json");
      const ok = document.results.filter((item) => item.status === "ok").length;
      const errors = document.results.filter(
        (item) => item.status === "error",
      ).length;
      setNotice(
        `results.json сформирован: ${submission.group} / ${submission.split.toUpperCase()} — ${document.results.length} записей, успешных оценок: ${ok}, записей с ошибкой: ${errors}. Сохраните файл в results/${submission.split}/.`,
      );
      setSubmission(null);
    } catch (error) {
      setSubmissionError(errorText(error));
    } finally {
      setExportingSubmission(false);
    }
  }

  async function loadOverview() {
    const [list, rules, provider] = await Promise.all([
      request<ConversationSummary[]>("/conversations"),
      request<ChecklistVersion[]>("/checklists"),
      request<ProviderInfo>("/config"),
    ]);
    if (!mountedRef.current) return;
    setConversations(list);
    setChecklists(rules);
    setConfig(provider);
    setSelectedKey((current) =>
      list.some((item) => conversationKey(item) === current)
        ? current
        : list[0]
          ? conversationKey(list[0])
          : "",
    );
    setSelectedChecklistKey((current) =>
      rules.some((item) => checklistKey(item) === current)
        ? current
        : rules[0]
          ? checklistKey(rules[0])
          : "",
    );
  }

  async function refreshConversations() {
    const list = await request<ConversationSummary[]>("/conversations");
    if (mountedRef.current) setConversations(list);
  }

  useEffect(() => {
    mountedRef.current = true;
    void loadOverview()
      .catch((error) => setError(errorText(error)))
      .finally(() => setInitialLoading(false));
    return () => {
      mountedRef.current = false;
      stopRef.current = true;
    };
  }, []);

  useEffect(() => {
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
      request<Conversation>(
        `/conversations/${q(conversationId)}?group_id=${q(groupId)}`,
        { signal: controller.signal },
      ),
      request<Evaluation[]>(
        `/evaluations?group_id=${q(groupId)}&conversation_id=${q(conversationId)}`,
        { signal: controller.signal },
      ),
    ])
      .then(([value, history]) => {
        if (!controller.signal.aborted) {
          setConversation(value);
          setEvaluations((current) => {
            const merged = new Map(
              history.map((item) => [item.evaluation_id, item]),
            );
            current
              .filter(
                (item) => conversationKey(item.conversation) === selectedKey,
              )
              .forEach((item) => {
                if (
                  (merged.get(item.evaluation_id)?.revision ?? 0) <=
                  item.revision
                )
                  merged.set(item.evaluation_id, item);
              });
            return [...merged.values()].sort((a, b) =>
              b.created_at.localeCompare(a.created_at),
            );
          });
          setEvaluationId(
            (current) => current || history[0]?.evaluation_id || "",
          );
        }
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorText(error));
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [selectedKey]);

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
      }>("/datasets/import", value);
      const list = await request<ConversationSummary[]>("/conversations");
      setConversations(list);
      setGroup(result.group_id);
      setLanguage("");
      setChannel("");
      setGrossError("");
      setQuery("");
      const first = list.find((item) => item.group_id === result.group_id);
      if (first) {
        const nextKey = conversationKey(first);
        if (nextKey === selectedRef.current) {
          const [current, history] = await Promise.all([
            request<Conversation>(
              `/conversations/${q(first.conversation_id)}?group_id=${q(first.group_id)}`,
            ),
            request<Evaluation[]>(
              `/evaluations?group_id=${q(first.group_id)}&conversation_id=${q(first.conversation_id)}`,
            ),
          ]);
          if (selectedRef.current === nextKey) {
            setConversation(current);
            setEvaluations(history);
            setEvaluationId("");
          }
        } else setSelectedKey(nextKey);
      }
      setTab("review");
      setMobilePanel("queue");
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
    if (!selectedChecklist || !targets.length) return;
    if (!config?.provider_configured) {
      setShowSetup(true);
      return;
    }
    setRunning(true);
    setError("");
    setNotice("");
    setJobErrors([]);
    stopRef.current = false;
    const rules = selectedChecklist;
    let done = 0,
      failed = 0;
    setBatch({
      total: targets.length,
      done,
      failed,
      label: "",
      stopped: false,
    });
    for (const target of targets) {
      if (stopRef.current || !mountedRef.current) break;
      setBatch({
        total: targets.length,
        done,
        failed,
        label: target.conversation_id,
        stopped: false,
      });
      try {
        let job = await post<EvaluationJob>("/evaluation-jobs", {
          conversation_id: target.conversation_id,
          group_id: target.group_id,
          checklist_id: rules.checklist_id,
          checklist_version: rules.version,
          idempotency_key: uid(),
        });
        setJobStatus(job.status);
        while (job.status === "queued" || job.status === "running") {
          await new Promise((resolve) => setTimeout(resolve, 1200));
          if (!mountedRef.current) return;
          job = await request<EvaluationJob>(
            `/evaluation-jobs/${q(job.job_id)}`,
          );
          setJobStatus(job.status);
        }
        if (job.status === "failed" || !job.evaluation_id)
          throw new Error(
            job.error || "Оценка не завершена. Повторите запуск.",
          );
        const completed = await request<Evaluation>(
          `/evaluations/${q(job.evaluation_id)}`,
        );
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
      done++;
      setBatch({
        total: targets.length,
        done,
        failed,
        label: "",
        stopped: stopRef.current,
      });
      await refreshConversations().catch(() => undefined);
    }
    if (mountedRef.current) {
      setRunning(false);
      setBatch({
        total: targets.length,
        done,
        failed,
        label: "",
        stopped: stopRef.current,
      });
    }
  }

  function onEvaluationUpdated(value: Evaluation) {
    setEvaluations((current) =>
      current.map((item) => {
        if (
          item.evaluation_id !== value.evaluation_id ||
          item.revision > value.revision
        )
          return item;
        if (item.revision === value.revision) {
          const appeals = new Map(
            item.appeals.map((appeal) => [appeal.appeal_id, appeal]),
          );
          value.appeals.forEach((appeal) =>
            appeals.set(appeal.appeal_id, appeal),
          );
          return { ...value, appeals: [...appeals.values()] };
        }
        return value;
      }),
    );
    void refreshConversations().catch((error) => setError(errorText(error)));
  }

  function focusElement(id: string) {
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        const element = document.getElementById(id);
        element?.scrollIntoView({
          behavior: window.matchMedia("(prefers-reduced-motion: reduce)")
            .matches
            ? "instant"
            : "smooth",
          block: "center",
        });
        element?.focus({ preventScroll: true });
      }),
    );
  }
  function openEvidence(
    messageId: string,
    endMessageId?: string,
    criterionId?: string,
  ) {
    setHighlight({
      start: messageId,
      end: endMessageId,
      criterion: criterionId,
    });
    setMobilePanel("transcript");
    focusElement(`message-${messageId}`);
  }
  function returnToCriterion() {
    if (!highlight?.criterion) return;
    setMobilePanel("assessment");
    focusElement(`criterion-${highlight.criterion}`);
  }
  function closeQueue() {
    setQueueOpen(false);
    queueButton.current?.focus();
  }
  useEffect(() => {
    if (!queueOpen && !filtersOpen) return;
    const escape = (event: KeyboardEvent) => {
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
      if (queueOpen) {
        setQueueOpen(false);
        queueButton.current?.focus();
      }
      if (filtersOpen) {
        setFiltersOpen(false);
        document.querySelector<HTMLButtonElement>(".filter-toggle")?.focus();
      }
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
    setGrossError("");
  }

  async function savedChecklist(value: ChecklistVersion) {
    setChecklists((current) => [
      value,
      ...current.filter((item) => checklistKey(item) !== checklistKey(value)),
    ]);
    setSelectedChecklistKey(checklistKey(value));
    setNotice(
      `Чек-лист сохранён: версия ${value.version}. Выберите диалог, чтобы применить новые правила.`,
    );
  }

  return (
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
            setTab("review");
          }}
          aria-label="CQA — рабочая область"
        >
          <span className="brand-word">cqa<span aria-hidden="true" className="brand-signal" /></span>
        </a>
        <nav className="main-nav" aria-label="Разделы приложения">
          <button
            className={tab === "review" ? "active" : ""}
            onClick={() => setTab("review")}
            aria-current={tab === "review" ? "page" : undefined}
          >
            Диалоги
          </button>
          <button
            className={tab === "checklist" ? "active" : ""}
            onClick={() => setTab("checklist")}
            aria-current={tab === "checklist" ? "page" : undefined}
          >
            Чек-листы
          </button>
        </nav>
        <div className="header-actions">
          <button
            className={`provider-button ${config?.provider_configured ? "connected" : ""}`}
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
            <span className="status-dot" />
            <span>
              {config?.mode === "test"
                ? "Тест"
                : config?.provider_configured
                  ? "Модель"
                  : "Нет модели"}
            </span>
            <Settings2 size={15} />
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
          onClose={() => setShowSetup(false)}
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
            <button
              className="button secondary"
              onClick={() =>
                void loadOverview().catch((error) => setError(errorText(error)))
              }
            >
              <RefreshCw size={15} /> Обновить настройки
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
          <div className="notice success" role="status">
            <CheckCheck size={17} />
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
      </div>
      <main id="main-content">
        <div className="workspace-toolbar">
          <h1 className="page-title">{tab === "review" ? "Диалоги" : "Чек-листы"}</h1>
          <label className="checklist-select">
            <ClipboardList size={17} />
            <span className="sr-only">Версия чек-листа</span>
            <select
              value={selectedChecklist ? checklistKey(selectedChecklist) : ""}
              onChange={(event) => setSelectedChecklistKey(event.target.value)}
              disabled={!checklists.length}
            >
              {checklists.map((item) => (
                <option value={checklistKey(item)} key={checklistKey(item)}>
                  {item.title} — v{item.version}
                </option>
              ))}
            </select>
          </label>
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
        {submission ? (
          <Modal title="Файл для сдачи" onClose={() => setSubmission(null)}>
            <form
              id="submission-form"
              className="checklist-meta form-grid"
              aria-labelledby="submission-heading"
              onSubmit={(event) => {
                event.preventDefault();
                void exportSubmission();
              }}
            >
              <div className="span-3">
                <h3 id="submission-heading">Официальный формат JSON 1.0</h3>
                <p className="form-help">
                  JSON 1.0 организаторов. Весь выбранный набор:{" "}
                  {submissionCount} диалогов; фильтры списка не применяются.
                  Используются исходные оценки модели по официальному чек-листу.
                </p>
              </div>
              <label className="field">
                Команда
                <input
                  value={submission.team}
                  required
                  disabled={exportingSubmission}
                  onChange={(event) =>
                    setSubmission({ ...submission, team: event.target.value })
                  }
                />
              </label>
              <label className="field">
                Набор для сдачи
                <select
                  value={submission.group}
                  required
                  disabled={exportingSubmission}
                  onChange={(event) =>
                    setSubmission({
                      ...submission,
                      group: event.target.value,
                      split:
                        event.target.value.toUpperCase() === "DEV"
                          ? "dev"
                          : "prod",
                    })
                  }
                >
                  {groups.map((value) => (
                    <option key={value}>{value}</option>
                  ))}
                </select>
              </label>
              <label className="field">
                Раздел сдачи
                <select
                  value={submission.split}
                  disabled={exportingSubmission}
                  onChange={(event) =>
                    setSubmission({
                      ...submission,
                      split: event.target.value as SubmissionForm["split"],
                    })
                  }
                >
                  <option value="dev">DEV — 65 диалогов</option>
                  <option value="prod">PROD — 10 чатов</option>
                </select>
              </label>
              <label className="field span-2">
                Модель для сдачи
                <input
                  value={submission.model}
                  required
                  disabled={exportingSubmission}
                  onChange={(event) =>
                    setSubmission({ ...submission, model: event.target.value })
                  }
                />
              </label>
              <label className="field">
                Режим модели
                <select
                  value={submission.mode}
                  disabled={exportingSubmission}
                  onChange={(event) =>
                    setSubmission({
                      ...submission,
                      mode: event.target.value as SubmissionForm["mode"],
                    })
                  }
                >
                  <option value="external_api">Внешний API</option>
                  <option value="local">Локальная модель</option>
                  <option value="hybrid">Гибридный</option>
                </select>
              </label>
              {submissionError ? (
                <p className="notice error span-3" role="alert">
                  {submissionError}
                </p>
              ) : null}
              <p className="form-help span-3">
                Назначение: results/{submission.split}/results.json. Проверка
                формата файла не подтверждает точность оценок относительно
                Ground Truth.
              </p>
              <div className="button-row span-3">
                <button
                  className="button primary small"
                  type="submit"
                  disabled={
                    exportingSubmission ||
                    !submission.team.trim() ||
                    !submission.group ||
                    !submission.model.trim()
                  }
                >
                  {exportingSubmission ? (
                    <LoaderCircle size={15} className="spin" />
                  ) : (
                    <Download size={15} />
                  )}
                  {exportingSubmission ? "Формируем…" : "Скачать results.json"}
                </button>
                <button
                  className="button ghost small"
                  type="button"
                  disabled={exportingSubmission}
                  onClick={() => setSubmission(null)}
                >
                  Закрыть
                </button>
              </div>
            </form>
          </Modal>
        ) : null}

        {batch && (
          <div className="batch-banner" role="status">
            <LoaderCircle size={18} className={running ? "spin" : ""} />
            <div>
              <strong>
                {running
                  ? `${jobStatus === "queued" ? "В очереди" : "Анализ"}: ${batch.label}`
                  : batch.stopped
                    ? "Очередь остановлена"
                    : "Обработка завершена"}
              </strong>
              <span>
                Обработано {batch.done} из {batch.total}
                {batch.failed ? ` · ошибок ${batch.failed}` : ""}
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
              disabled={running}
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
          <div className="large-empty">
            <LoaderCircle className="spin" size={30} />
            <h2>Открываем рабочую область</h2>
          </div>
        ) : tab === "checklist" ? (
          selectedChecklist ? (
            <ChecklistEditor
              key={checklistKey(selectedChecklist)}
              checklist={selectedChecklist}
              onSaved={(value) => void savedChecklist(value)}
            />
          ) : (
            <div className="large-empty">
              <h2>Чек-лист не найден</h2>
              <p>Проверьте доступность API и повторите загрузку.</p>
              <button
                className="button secondary"
                onClick={() =>
                  void loadOverview().catch((error) =>
                    setError(errorText(error)),
                  )
                }
              >
                Обновить
              </button>
            </div>
          )
        ) : conversations.length === 0 ? (
          <section className="onboarding">
            <h2>Загрузите диалоги</h2>
            <p>JSON с транскриптами на русском, казахском или английском.</p>
            <div className="button-row">
              <button className="button primary" onClick={() => inputRef.current?.click()} disabled={importing}><Upload size={17} /> Импорт JSON</button>
              <button className="button secondary" onClick={() => void importDataset({ source: "dev" })} disabled={importing}>Открыть DEV</button>
            </div>
            <button className="text-button" onClick={() => void importDataset({ source: "demo" })} disabled={importing}>Открыть 3 учебных диалога</button>
          </section>
        ) : (
          <>
            <div className="review-context">
              <button
                ref={queueButton}
                className="button secondary small tablet-queue-button"
                onClick={() => {
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
              <div className="conversation-context">
                <strong>
                  {selectedSummary?.conversation_id || "Выберите диалог"}
                </strong>
                <span>{selectedSummary?.group_id}</span>
                <span>{selectedSummary?.language}</span>
                <span>
                  {selectedSummary?.channel === "voice call" ? "Звонок" : "Чат"}
                </span>
              </div>
              <button
                className="button secondary small submission-button"
                onClick={openSubmission}
              >
                <FileJson size={15} /> Файл для сдачи
              </button>
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
                      <span className="tab-result-dot" />
                    )}
                  </button>
                ),
              )}
            </nav>
            <div
              className={`review-workspace mobile-${mobilePanel} ${queueOpen ? "queue-open" : ""}`}
            >
              {queueOpen && (
                <button
                  className="queue-backdrop"
                  aria-label="Закрыть список диалогов"
                  onClick={closeQueue}
                />
              )}
              <ConversationQueue
                items={filtered}
                total={conversations.length}
                selectedKey={selectedKey}
                groups={groups}
                query={query}
                language={language}
                channel={channel}
                group={group}
                grossError={grossError}
                filtersOpen={filtersOpen}
                running={running}
                importing={importing}
                configured={!!config?.provider_configured}
                onQuery={setQuery}
                onLanguage={setLanguage}
                onChannel={setChannel}
                onGroup={setGroup}
                onGrossError={setGrossError}
                onFilters={() => setFiltersOpen((value) => !value)}
                onSelect={(item) => {
                  setSelectedKey(conversationKey(item));
                  setMobilePanel("transcript");
                  setQueueOpen(false);
                }}
                onReset={resetFilters}
                onImport={() => inputRef.current?.click()}
                onRun={() => void run(filtered)}
                onDemo={() => void importDataset({ source: "demo" })}
                onSubmission={openSubmission}
                onClose={closeQueue}
              />
              <Transcript
                source={source}
                evaluation={evaluation}
                highlight={highlight}
                loading={detailLoading}
                onReturn={returnToCriterion}
              />
              <aside
                id="assessment-panel"
                className="assessment-pane"
                aria-label="Результаты оценки"
              >
                <div className="assessment-actions">
                  <h2>Оценка</h2>
                  <button
                    className="button primary small"
                    disabled={
                      !selectedSummary ||
                      running ||
                      detailLoading ||
                      !selectedChecklist
                    }
                    onClick={() =>
                      selectedSummary && void run([selectedSummary])
                    }
                  >
                    {running ? (
                      <LoaderCircle size={14} className="spin" />
                    ) : (
                      <Play size={14} />
                    )}
                    {running
                      ? "Анализируем…"
                      : evaluation
                        ? "Оценить снова"
                        : "Оценить диалог"}
                  </button>
                </div>
                {evaluations.length > 0 && (
                  <label className="history-select">
                    Запуск
                    <select
                      aria-label="История оценок"
                      value={evaluationId}
                      onChange={(event) => setEvaluationId(event.target.value)}
                    >
                      {evaluations.map((item) => (
                        <option
                          key={item.evaluation_id}
                          value={item.evaluation_id}
                        >
                          {date(item.created_at)} · v{item.checklist.version} ·{" "}
                          {number(item.score.total, 2)}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {evaluation ? (
                  <Assessment
                    key={evaluation.evaluation_id}
                    evaluation={evaluation}
                    onUpdated={onEvaluationUpdated}
                    onEvidence={openEvidence}
                  />
                ) : (
                  <div className="assessment-empty">
                    <h2>Диалог ещё не оценён</h2>
                    <p>{selectedChecklist?.criteria.length ?? 0} критериев, версия {selectedChecklist?.version}.</p>
                    {selectedChecklist?.provenance === "synthetic_example" && <span className="badge warning">Учебный чек-лист</span>}
                    {!config?.provider_configured && <button className="connection-hint" onClick={() => setShowSetup(true)}><AlertCircle size={16} /> Подключить модель</button>}
                    <button className="text-button" onClick={() => setTab("checklist")}>Изменить чек-лист</button>
                  </div>
                )}
              </aside>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
