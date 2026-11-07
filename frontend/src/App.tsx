import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, ArrowUpRight, CheckCheck, ClipboardList, Download, FileJson, Headphones, ListFilter, LoaderCircle, MessageSquare, Play, RefreshCw, Search, Settings2, ShieldCheck, Upload, X } from 'lucide-react';
import type { ChecklistVersion, Conversation, ConversationSummary, Evaluation, EvaluationJob, ProviderInfo, Speaker, SubmissionFile } from './contracts';
import { downloadJson, errorText, post, q, request, uid } from './api';
import { date, number, statusName } from './format';
import Assessment from './Assessment';
import ChecklistEditor from './ChecklistEditor';

const conversationKey = (value: { group_id: string; conversation_id: string }) => JSON.stringify([value.group_id, value.conversation_id]);
const checklistKey = (value: ChecklistVersion) => JSON.stringify([value.checklist_id, value.version]);
const speakerNames: Record<Speaker, string> = { operator: 'Оператор', client: 'Клиент', bot: 'Бот', system: 'Система' };
type Batch = { total: number; done: number; failed: number; label: string; stopped: boolean };
type SubmissionForm = { team: string; group: string; split: 'dev' | 'prod'; model: string; mode: SubmissionFile['model']['mode'] };

export default function App() {
  const [tab, setTab] = useState<'review' | 'checklist'>('review');
  const [config, setConfig] = useState<ProviderInfo | null>(null);
  const [showSetup, setShowSetup] = useState(false);
  const [submission, setSubmission] = useState<SubmissionForm | null>(null);
  const [submissionError, setSubmissionError] = useState('');
  const [exportingSubmission, setExportingSubmission] = useState(false);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [checklists, setChecklists] = useState<ChecklistVersion[]>([]);
  const [selectedKey, setSelectedKey] = useState('');
  const [selectedChecklistKey, setSelectedChecklistKey] = useState('');
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [evaluations, setEvaluations] = useState<Evaluation[]>([]);
  const [evaluationId, setEvaluationId] = useState('');
  const [highlight, setHighlight] = useState('');
  const [query, setQuery] = useState('');
  const [language, setLanguage] = useState('');
  const [channel, setChannel] = useState('');
  const [group, setGroup] = useState('');
  const [grossError, setGrossError] = useState('');
  const [initialLoading, setInitialLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [importing, setImporting] = useState(false);
  const [running, setRunning] = useState(false);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [jobErrors, setJobErrors] = useState<Array<{ label: string; error: string }>>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const selectedRef = useRef(selectedKey);
  const stopRef = useRef(false);
  const mountedRef = useRef(true);
  selectedRef.current = selectedKey;

  const selectedChecklist = checklists.find(item => checklistKey(item) === selectedChecklistKey) ?? checklists[0];
  const selectedSummary = conversations.find(item => conversationKey(item) === selectedKey);
  const evaluation = evaluations.find(item => item.evaluation_id === evaluationId) ?? null;
  const source = evaluation?.conversation ?? conversation;
  const groups = useMemo(() => [...new Set(conversations.map(item => item.group_id))], [conversations]);
  const filtered = useMemo(() => conversations.filter(item => (!language || item.language === language) && (!channel || item.channel === channel) && (!group || item.group_id === group) && (!grossError || item.latest_gross_error === (grossError === 'true')) && (!query || `${item.conversation_id} ${item.preview}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()))), [conversations, language, channel, group, grossError, query]);
  const completedCount = conversations.filter(item => item.latest_score != null).length;
  const submissionCount = submission ? conversations.filter(item => item.group_id === submission.group).length : 0;
  const exportParams = new URLSearchParams();
  if (group) exportParams.set('group_id', group);
  if (grossError) exportParams.set('gross_error', grossError);

  function openSubmission() {
    const chosenGroup = group || selectedSummary?.group_id || groups[0] || '';
    setSubmission({ team: config?.team_name ?? '', group: chosenGroup, split: chosenGroup.toUpperCase() === 'DEV' ? 'dev' : 'prod', model: config?.model ?? '', mode: config?.provider === 'local' ? 'local' : 'external_api' });
    setSubmissionError('');
  }

  async function exportSubmission() {
    if (!submission || !submission.team.trim() || !submission.group || !submission.model.trim()) return;
    setExportingSubmission(true); setSubmissionError(''); setNotice('');
    try {
      const params = new URLSearchParams({ group_id: submission.group, model_name: submission.model.trim(), split: submission.split, team_name: submission.team.trim(), model_mode: submission.mode });
      const document = await request<SubmissionFile>(`/submission/export?${params}`);
      downloadJson(document, 'results.json');
      const ok = document.results.filter(item => item.status === 'ok').length;
      const errors = document.results.filter(item => item.status === 'error').length;
      setNotice(`results.json сформирован: ${submission.group} / ${submission.split.toUpperCase()} — ${document.results.length} записей, успешных оценок: ${ok}, записей с ошибкой: ${errors}. Сохраните файл в results/${submission.split}/.`);
    } catch (error) { setSubmissionError(errorText(error)); } finally { setExportingSubmission(false); }
  }

  async function loadOverview() {
    const [list, rules, provider] = await Promise.all([request<ConversationSummary[]>('/conversations'), request<ChecklistVersion[]>('/checklists'), request<ProviderInfo>('/config')]);
    if (!mountedRef.current) return;
    setConversations(list); setChecklists(rules); setConfig(provider);
    setSelectedKey(current => list.some(item => conversationKey(item) === current) ? current : list[0] ? conversationKey(list[0]) : '');
    setSelectedChecklistKey(current => rules.some(item => checklistKey(item) === current) ? current : rules[0] ? checklistKey(rules[0]) : '');
  }

  async function refreshConversations() {
    const list = await request<ConversationSummary[]>('/conversations');
    if (mountedRef.current) setConversations(list);
  }

  useEffect(() => {
    mountedRef.current = true;
    void loadOverview().catch(error => setError(errorText(error))).finally(() => setInitialLoading(false));
    return () => { mountedRef.current = false; stopRef.current = true; };
  }, []);

  useEffect(() => {
    if (!selectedKey) { setConversation(null); setEvaluations([]); setEvaluationId(''); return; }
    const controller = new AbortController();
    const [groupId, conversationId] = JSON.parse(selectedKey) as [string, string];
    setDetailLoading(true); setConversation(null); setEvaluations([]); setEvaluationId(''); setHighlight('');
    Promise.all([
      request<Conversation>(`/conversations/${q(conversationId)}?group_id=${q(groupId)}`, { signal: controller.signal }),
      request<Evaluation[]>(`/evaluations?group_id=${q(groupId)}&conversation_id=${q(conversationId)}`, { signal: controller.signal }),
    ]).then(([value, history]) => {
      if (!controller.signal.aborted) { setConversation(value); setEvaluations(history); setEvaluationId(history[0]?.evaluation_id ?? ''); }
    }).catch(error => { if (!controller.signal.aborted) setError(errorText(error)); }).finally(() => { if (!controller.signal.aborted) setDetailLoading(false); });
    return () => controller.abort();
  }, [selectedKey]);

  async function importDataset(value: unknown) {
    setImporting(true); setError(''); setNotice('');
    try {
      const result = await post<{ group_id: string; imported: number; updated: number; unchanged: number; total: number }>('/datasets/import', value);
      const list = await request<ConversationSummary[]>('/conversations');
      setConversations(list); setGroup(result.group_id); setLanguage(''); setChannel(''); setGrossError(''); setQuery('');
      const first = list.find(item => item.group_id === result.group_id);
      if (first) {
        const nextKey = conversationKey(first);
        if (nextKey === selectedRef.current) {
          const [current, history] = await Promise.all([request<Conversation>(`/conversations/${q(first.conversation_id)}?group_id=${q(first.group_id)}`), request<Evaluation[]>(`/evaluations?group_id=${q(first.group_id)}&conversation_id=${q(first.conversation_id)}`)]);
          if (selectedRef.current === nextKey) { setConversation(current); setEvaluations(history); setEvaluationId(''); }
        } else setSelectedKey(nextKey);
      }
      setTab('review'); setNotice(`Набор ${result.group_id}: добавлено ${result.imported}, обновлено ${result.updated}, без изменений ${result.unchanged}.`);
    } catch (error) { setError(errorText(error)); } finally { setImporting(false); }
  }

  async function readFile(file: File | undefined) {
    if (!file) return;
    try {
      if (file.size > 25 * 1024 * 1024) throw new Error('Файл больше 25 МБ. Разделите набор на несколько файлов.');
      const dataset: unknown = JSON.parse(await file.text());
      await importDataset({ dataset });
    } catch (error) { setError(error instanceof SyntaxError ? 'Файл не содержит корректный JSON. Используйте исходный экспорт транскриптов.' : errorText(error)); }
    finally { if (inputRef.current) inputRef.current.value = ''; }
  }

  async function run(targets: ConversationSummary[]) {
    if (!selectedChecklist || !targets.length) return;
    if (!config?.provider_configured) { setShowSetup(true); return; }
    setRunning(true); setError(''); setJobErrors([]); stopRef.current = false;
    const rules = selectedChecklist;
    let done = 0, failed = 0;
    setBatch({ total: targets.length, done, failed, label: '', stopped: false });
    for (const target of targets) {
      if (stopRef.current || !mountedRef.current) break;
      setBatch({ total: targets.length, done, failed, label: target.conversation_id, stopped: false });
      try {
        let job = await post<EvaluationJob>('/evaluation-jobs', { conversation_id: target.conversation_id, group_id: target.group_id, checklist_id: rules.checklist_id, checklist_version: rules.version, idempotency_key: uid() });
        while (job.status === 'queued' || job.status === 'running') {
          await new Promise(resolve => setTimeout(resolve, 1200));
          if (!mountedRef.current) return;
          job = await request<EvaluationJob>(`/evaluation-jobs/${q(job.job_id)}`);
        }
        if (job.status === 'failed' || !job.evaluation_id) throw new Error(job.error || 'Оценка не завершена. Повторите запуск.');
        const completed = await request<Evaluation>(`/evaluations/${q(job.evaluation_id)}`);
        if (selectedRef.current === conversationKey(target)) {
          setEvaluations(current => [completed, ...current.filter(item => item.evaluation_id !== completed.evaluation_id)]);
          setEvaluationId(completed.evaluation_id);
        }
      } catch (error) {
        failed++;
        setJobErrors(current => [...current, { label: target.conversation_id, error: errorText(error) }]);
      }
      done++;
      setBatch({ total: targets.length, done, failed, label: '', stopped: stopRef.current });
      await refreshConversations().catch(() => undefined);
    }
    if (mountedRef.current) { setRunning(false); setBatch({ total: targets.length, done, failed, label: '', stopped: stopRef.current }); }
  }

  function onEvaluationUpdated(value: Evaluation) {
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

  function openEvidence(messageId: string) {
    setHighlight(messageId);
    const element = document.getElementById(`message-${messageId}`);
    element?.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'center' });
    element?.focus({ preventScroll: true });
  }

  async function savedChecklist(value: ChecklistVersion) {
    setChecklists(current => [value, ...current.filter(item => checklistKey(item) !== checklistKey(value))]);
    setSelectedChecklistKey(checklistKey(value));
    setNotice(`Чек-лист сохранён: версия ${value.version}. Выберите диалог, чтобы применить новые правила.`);
  }

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Перейти к рабочей области</a>
    <header className="app-header">
      <a className="brand" href="#" onClick={event => { event.preventDefault(); setTab('review'); }} aria-label="CQA — рабочая область"><span className="brand-mark"><ShieldCheck size={23} strokeWidth={1.8} /></span><span><strong>CQA</strong><small>Качество диалогов</small></span></a>
      <nav className="main-nav" aria-label="Разделы приложения"><button className={tab === 'review' ? 'active' : ''} onClick={() => setTab('review')} aria-current={tab === 'review' ? 'page' : undefined}><MessageSquare size={17} /> Диалоги</button><button className={tab === 'checklist' ? 'active' : ''} onClick={() => setTab('checklist')} aria-current={tab === 'checklist' ? 'page' : undefined}><ClipboardList size={17} /> Чек-лист</button></nav>
      <div className="header-actions">
        {config?.mode === 'test' ? <span className="test-mode-badge" title="Режим эмуляции UI с синтетическими ответами">Эмулятор UI (Тест)</span> : null}
        <button className={`provider-button ${config?.provider_configured ? 'connected' : ''}`} onClick={() => setShowSetup(value => !value)} aria-expanded={showSetup}><span className="status-dot" />{config?.provider_configured ? (config.mode === 'test' ? 'Тестовый эмулятор' : config.model || 'Модель настроена') : 'Модель не подключена'}<Settings2 size={16} /></button>
      </div>
    </header>

    {showSetup ? <section className="setup-panel"><div><h2>{config?.provider_configured ? (config.mode === 'test' ? 'Тестовый режим интерфейса' : 'Подключение к модели') : 'Подключите модель для AI-оценки'}</h2><p>{config?.mode === 'test' ? 'Активен автономный эмулятор ответов для проверки интерфейса, переходов к цитатам и апелляций. Это не реальная AI-оценка диалогов.' : config?.provider_configured ? `Используется ${config.provider}, модель ${config.model}. Диалоги передаются настроенному провайдеру при запуске оценки.` : 'Импорт, просмотр и настройка чек-листа уже доступны. Для настоящей оценки укажите подключение на сервере.'}</p>{!config?.provider_configured ? <p className="setup-code">В файле <code>.env</code>: <code>LLM_BASE_URL</code>, <code>LLM_MODEL</code> и <code>LLM_API_KEY</code>, если провайдер требует ключ. Затем перезапустите сервер.</p> : null}</div><div className="button-row"><button className="button secondary small" onClick={() => void loadOverview().catch(error => setError(errorText(error)))}><RefreshCw size={15} /> Обновить настройки</button><button className="icon-button" aria-label="Закрыть настройки подключения" onClick={() => setShowSetup(false)}><X size={18} /></button></div></section> : null}

    <div className="global-messages" aria-live="polite">
      {error ? <div className="notice error" role="alert"><AlertCircle size={18} /><span>{error}</span><button className="icon-button" aria-label="Закрыть сообщение об ошибке" onClick={() => setError('')}><X size={16} /></button></div> : null}
      {notice ? <div className="notice success" role="status"><CheckCheck size={17} /><span>{notice}</span><button className="icon-button" aria-label="Закрыть уведомление" onClick={() => setNotice('')}><X size={16} /></button></div> : null}
    </div>

    <main id="main-content">
      <div className="workspace-toolbar">
        <div className="toolbar-title"><h1>{tab === 'review' ? 'Рабочая область' : 'Правила оценки'}</h1><span className="subtle">{conversations.length} диалогов • {completedCount} с итоговой оценкой</span></div>
        <label className="checklist-select"><ClipboardList size={16} /><span className="sr-only">Версия чек-листа</span><select value={selectedChecklist ? checklistKey(selectedChecklist) : ''} onChange={event => setSelectedChecklistKey(event.target.value)} disabled={!checklists.length}>{checklists.map(item => <option value={checklistKey(item)} key={checklistKey(item)}>{item.title} — v{item.version}</option>)}</select></label>
        <input type="file" accept="application/json,.json" className="sr-only" ref={inputRef} onChange={event => void readFile(event.target.files?.[0])} aria-label="Выбрать JSON с диалогами" tabIndex={-1} />
        <button className="button primary" disabled={importing || running} onClick={() => inputRef.current?.click()}>{importing ? <LoaderCircle size={17} className="spin" /> : <Upload size={17} />} Импорт JSON</button>
        <button className="button secondary" disabled={initialLoading || !groups.length || exportingSubmission} onClick={openSubmission} aria-expanded={submission != null} aria-controls="submission-form"><FileJson size={17} /> Файл для сдачи</button>
      </div>

      {submission ? <form id="submission-form" className="checklist-meta form-grid" aria-labelledby="submission-heading" onSubmit={event => { event.preventDefault(); void exportSubmission(); }}>
        <div className="span-3"><h2 id="submission-heading">Файл для сдачи</h2><p className="form-help">JSON 1.0 организаторов. Весь выбранный набор: {submissionCount} диалогов; фильтры списка не применяются. Используются исходные оценки модели по официальному чек-листу.</p></div>
        <label className="field">Команда<input value={submission.team} required disabled={exportingSubmission} onChange={event => setSubmission({ ...submission, team: event.target.value })} /></label>
        <label className="field">Набор для сдачи<select value={submission.group} required disabled={exportingSubmission} onChange={event => setSubmission({ ...submission, group: event.target.value, split: event.target.value.toUpperCase() === 'DEV' ? 'dev' : 'prod' })}>{groups.map(value => <option key={value}>{value}</option>)}</select></label>
        <label className="field">Раздел сдачи<select value={submission.split} disabled={exportingSubmission} onChange={event => setSubmission({ ...submission, split: event.target.value as SubmissionForm['split'] })}><option value="dev">DEV — 65 диалогов</option><option value="prod">PROD — 10 чатов</option></select></label>
        <label className="field span-2">Модель для сдачи<input value={submission.model} required disabled={exportingSubmission} onChange={event => setSubmission({ ...submission, model: event.target.value })} /></label>
        <label className="field">Режим модели<select value={submission.mode} disabled={exportingSubmission} onChange={event => setSubmission({ ...submission, mode: event.target.value as SubmissionForm['mode'] })}><option value="external_api">Внешний API</option><option value="local">Локальная модель</option><option value="hybrid">Гибридный</option></select></label>
        {submissionError ? <p className="notice error span-3" role="alert">{submissionError}</p> : null}
        <p className="form-help span-3">Назначение: results/{submission.split}/results.json. Проверка формата файла не подтверждает точность оценок относительно Ground Truth.</p>
        <div className="button-row span-3"><button className="button primary small" type="submit" disabled={exportingSubmission || !submission.team.trim() || !submission.group || !submission.model.trim()}>{exportingSubmission ? <LoaderCircle size={15} className="spin" /> : <Download size={15} />}{exportingSubmission ? 'Формируем…' : 'Скачать results.json'}</button><button className="button ghost small" type="button" disabled={exportingSubmission} onClick={() => setSubmission(null)}>Закрыть</button></div>
      </form> : null}

      {batch ? <div className="batch-banner" role="status"><div><strong>{running ? `Анализ: ${batch.label}` : batch.stopped ? 'Очередь остановлена' : 'Обработка завершена'}</strong><span>Обработано {batch.done} из {batch.total}{batch.failed ? ` • ошибок ${batch.failed}` : ''}</span></div><progress value={batch.done} max={batch.total} aria-label="Прогресс оценки диалогов" />{running ? <button className="button secondary small" onClick={() => { stopRef.current = true; setBatch(current => current ? { ...current, stopped: true } : current); }} disabled={batch.stopped}>{batch.stopped ? 'После текущего диалога' : 'Остановить очередь'}</button> : <button className="icon-button" aria-label="Скрыть прогресс" onClick={() => { setBatch(null); setJobErrors([]); }}><X size={17} /></button>}</div> : null}
      {jobErrors.length ? <details className="job-errors"><summary>{jobErrors.length} ошибок обработки. Открыть подробности</summary>{jobErrors.map((item, index) => <p key={index}><strong>{item.label}:</strong> {item.error}</p>)}</details> : null}

      {initialLoading ? <div className="large-empty"><LoaderCircle className="spin" size={30} /><h2>Открываем рабочую область</h2></div> : tab === 'checklist' ? (selectedChecklist ? <ChecklistEditor key={checklistKey(selectedChecklist)} checklist={selectedChecklist} onSaved={value => void savedChecklist(value)} /> : <div className="large-empty"><h2>Чек-лист не найден</h2><p>Проверьте доступность API и повторите загрузку.</p><button className="button secondary" onClick={() => void loadOverview().catch(error => setError(errorText(error)))}>Обновить</button></div>) : conversations.length === 0 ? <section className="onboarding"><div className="onboarding-symbol"><MessageSquare size={38} strokeWidth={1.5} /></div><h2>Каждая оценка начинается с диалога</h2><p>Загрузите JSON с транскриптами, выберите чек-лист и проверьте выводы модели по исходным репликам.</p><div className="button-row"><button className="button primary" onClick={() => inputRef.current?.click()} disabled={importing}><Upload size={17} /> Загрузить JSON</button><button className="button secondary" onClick={() => void importDataset({ source: 'dev' })} disabled={importing}><FileJson size={17} /> Открыть DEV</button></div><button className="text-button" onClick={() => void importDataset({ source: 'demo' })} disabled={importing}>Открыть 3 учебных диалога<ArrowUpRight size={15} /></button><div className="onboarding-note"><ShieldCheck size={18} /><span>Учебные диалоги не содержат готовых AI-оценок. Для анализа потребуется подключить модель.</span></div></section> : <div className="review-workspace">
        <aside className="conversation-sidebar" aria-label="Список диалогов">
          <div className="sidebar-heading"><h2>Диалоги <span>{filtered.length}</span></h2><ListFilter size={17} /></div>
          <label className="search-field"><Search size={16} /><input type="search" placeholder="ID или начало диалога" value={query} onChange={event => setQuery(event.target.value)} aria-label="Поиск диалога" /></label>
          <div className="filters"><label><span className="sr-only">Язык</span><select value={language} onChange={event => setLanguage(event.target.value)}><option value="">Все языки</option><option>RU</option><option>KZ</option><option>EN</option></select></label><label><span className="sr-only">Канал</span><select value={channel} onChange={event => setChannel(event.target.value)}><option value="">Все каналы</option><option value="agent chat">Чаты</option><option value="voice call">Звонки</option></select></label><label className="full-width"><span className="sr-only">Набор</span><select value={group} onChange={event => setGroup(event.target.value)}><option value="">Все наборы</option>{groups.map(value => <option key={value}>{value}</option>)}</select></label><label className="full-width"><span className="sr-only">Грубые ошибки в последней оценке</span><select value={grossError} onChange={event => setGrossError(event.target.value)} title="По последней оценке текущего диалога, с учётом апелляций"><option value="">Грубые ошибки: все</option><option value="true">С грубой ошибкой</option><option value="false">Без грубых ошибок</option></select></label></div>
          <div className="conversation-list">{filtered.map(item => <button key={conversationKey(item)} className={`conversation-item ${conversationKey(item) === selectedKey ? 'selected' : ''}`} onClick={() => setSelectedKey(conversationKey(item))} aria-current={conversationKey(item) === selectedKey ? 'true' : undefined}><span className="conversation-item-top"><span>{item.channel === 'voice call' ? <Headphones size={15} /> : <MessageSquare size={15} />}<strong>{item.conversation_id}</strong></span><span className="language-tag">{item.language}</span></span><span className="conversation-preview">{item.preview || 'Текст диалога'}</span><span className="conversation-item-bottom"><span>{item.message_count} реплик</span><span className={item.latest_score != null ? 'list-score' : ''}>{item.latest_score != null ? `${number(item.latest_score)} / 100` : statusName(item.latest_status)}</span></span></button>)}{!filtered.length ? <div className="small-empty"><Search size={24} /><strong>Ничего не найдено</strong><p>Измените запрос или фильтры.</p><button className="text-button" onClick={() => { setQuery(''); setLanguage(''); setChannel(''); setGroup(''); setGrossError(''); }}>Сбросить фильтры</button></div> : null}</div>
          <footer className="sidebar-footer"><button className="button secondary full-width" disabled={running || !filtered.length || !config?.provider_configured} onClick={() => void run(filtered)}><Play size={15} /> Оценить выборку ({filtered.length})</button><div className="sidebar-links"><button className="text-button" disabled={importing || running} onClick={() => void importDataset({ source: 'dev' })}>Загрузить DEV</button><a className="text-button" href={`/api/evaluations/export${exportParams.size ? `?${exportParams}` : ''}`} title="Экспорт истории: признак грубой ошибки проверяется у каждой оценки" download><Download size={13} /> Экспорт</a></div></footer>
        </aside>

        <section className="transcript-pane" aria-labelledby="transcript-heading">
          <header className="panel-heading"><div><h2 id="transcript-heading">{selectedSummary?.conversation_id ?? 'Выберите диалог'}</h2><span className="subtle">{selectedSummary?.group_id}{selectedSummary ? ` • ${selectedSummary.language} • ${selectedSummary.channel === 'voice call' ? 'Звонок' : 'Чат'}` : ''}</span></div>{selectedSummary ? <button className="button primary small" disabled={running || !config?.provider_configured || !selectedChecklist || detailLoading} onClick={() => void run([selectedSummary])}>{running ? <LoaderCircle size={15} className="spin" /> : <Play size={15} />} Оценить</button> : null}</header>
          {!config?.provider_configured ? <button className="connection-hint" onClick={() => setShowSetup(true)}><AlertCircle size={16} /><span>Подключите модель, чтобы запустить оценку</span><ArrowUpRight size={15} /></button> : null}
          {evaluations.length ? <label className="history-select">История<select value={evaluationId} onChange={event => { setEvaluationId(event.target.value); setHighlight(''); }}><option value="">Текущий диалог без оценки</option>{evaluations.map(item => <option key={item.evaluation_id} value={item.evaluation_id}>{String(item.model_info.model ?? 'Модель')} — {date(item.created_at)} — v{item.checklist.version} — {number(item.score.total)} баллов{item.revision > 1 ? `, пересмотр ${item.revision - 1}` : ''}</option>)}</select></label> : null}
          {evaluation ? <div className="snapshot-label">Снимок диалога на момент этой оценки</div> : null}
          {source?.audio_url ? <div className="audio-player"><Headphones size={16} /><audio controls preload="none" src={source.audio_url} aria-label="Запись звонка" /><span className="sr-only">Аудио воспроизводится с начала; точные смещения реплик не предоставлены.</span></div> : source?.channel === 'voice call' ? <div className="no-audio-banner"><Headphones size={15} /><span>Аудиозапись звонка не предоставлена (аудиофайл отсутствует в данных).</span></div> : null}
          <div className="transcript-content">{detailLoading ? <div className="small-empty"><LoaderCircle size={24} className="spin" /><p>Загружаем диалог…</p></div> : source ? source.transcript.map((message, index) => <article className={`message ${message.speaker} ${highlight === message.message_id ? 'highlighted' : ''}`} id={`message-${message.message_id}`} key={message.message_id} tabIndex={-1}><div className="message-meta"><span className="speaker-avatar">{message.speaker === 'operator' ? <Headphones size={14} /> : message.speaker === 'client' ? <MessageSquare size={14} /> : <Settings2 size={14} />}</span><strong>{speakerNames[message.speaker]}</strong><span className="message-position">#{index + 1}</span><time title={message.timestamp}>{message.timestamp.includes('T') ? message.timestamp.split('T')[1]?.slice(0, 8) : message.timestamp}</time></div><p>{message.text}</p></article>) : <div className="small-empty"><MessageSquare size={28} /><p>Выберите диалог в списке слева.</p></div>}</div>
        </section>

        <aside className="assessment-pane" aria-label="Результаты оценки">{evaluation ? <Assessment key={evaluation.evaluation_id} evaluation={evaluation} onUpdated={onEvaluationUpdated} onEvidence={openEvidence} /> : <div className="assessment-empty"><ClipboardList size={32} strokeWidth={1.4} /><h2>Оценка с доказательствами</h2><p>Здесь появятся ответы по каждому критерию, обоснования и ссылки на исходные реплики.</p><div className="selected-rules"><strong>Выбранный чек-лист</strong><span>{selectedChecklist?.title ?? 'Не выбран'}</span>{selectedChecklist ? <span className="badge neutral">Версия {selectedChecklist.version} • {selectedChecklist.criteria.length} критерия</span> : null}</div>{selectedChecklist?.provenance === 'synthetic_example' ? <p className="provenance-note">Это демонстрационные правила. Официальный чек-лист ещё не предоставлен.</p> : null}<button className="text-button" onClick={() => setTab('checklist')}>Настроить критерии<ArrowUpRight size={15} /></button></div>}</aside>
      </div>}
    </main>
    <footer className="app-footer"><span>CQA • Freedom Broker Hackathon</span><span>Транскрипт → оценка → доказательство → пересмотр</span></footer>
  </div>;
}
