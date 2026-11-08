import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, ArrowUpRight, Check, LoaderCircle, Play, Search, SlidersHorizontal, Upload, X } from "lucide-react";
import type { ConversationSummary } from "./contracts";
import { conversationKey, matchesView, type QueueView } from "./ConversationQueue";
import { number, statusName, turnsLabel } from "./format";
import Select from "./Select";
import "./ConversationIndex.css";

export type ConversationIndexProps = {
  items: ConversationSummary[];
  sort: IndexSort;
  setSort: (next: IndexSort) => void;
  allItems: ConversationSummary[];
  query: string;
  setQuery: (value: string) => void;
  language: string;
  setLanguage: (value: string) => void;
  channel: string;
  setChannel: (value: string) => void;
  group: string;
  setGroup: (value: string) => void;
  view: QueueView;
  setView: (value: QueueView) => void;
  onOpen: (item: ConversationSummary) => void;
  onImport: () => void;
  importing: boolean;
  onRun: (items: ConversationSummary[]) => void;
  running: boolean;
  canRun: boolean;
  groups: string[];
};

const views = [
  ["all", "Все диалоги"],
  ["unrated", "Без оценки"],
  ["review", "На проверку"],
  ["critical", "Грубые ошибки"],
] as const;
export type IndexSort = { field: "dialogue" | "score"; ascending: boolean } | null;

function stateFor(item: ConversationSummary) {
  if (!item.latest_evaluation_id) return { text: "Без оценки", tone: "unrated" };
  if (item.latest_status === "incomplete") return { text: "Нужна проверка", tone: "review" };
  if (item.latest_status === "complete") return { text: "Оценён", tone: "complete" };
  if (item.latest_status === "not_applicable") return { text: "Не применимо", tone: "unrated" };
  return { text: statusName(item.latest_status), tone: "unrated" };
}

export default function ConversationIndex(p: ConversationIndexProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const { sort, setSort } = p;
  const [filtersOpen, setFiltersOpen] = useState(false);
  const selectAllRef = useRef<HTMLInputElement>(null);
  const query = p.query.trim().toLocaleLowerCase();
  const scoped = useMemo(() => p.allItems.filter(item =>
    (!p.language || item.language === p.language) &&
    (!p.channel || item.channel === p.channel) &&
    (!p.group || item.group_id === p.group) &&
    (!query || `${item.conversation_id} ${item.preview}`.toLocaleLowerCase().includes(query)),
  ), [p.allItems, p.language, p.channel, p.group, query]);
  const rows = p.items;
  const selectedItems = rows.filter(item => selected.has(conversationKey(item)));
  const allSelected = rows.length > 0 && selectedItems.length === rows.length;
  const activeFilters = [p.language, p.channel, p.group].filter(Boolean).length;

  useEffect(() => {
    // A batch always describes the visible set, including after filters change.
    setSelected(new Set());
  }, [p.query, p.language, p.channel, p.group, p.view]);
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = selectedItems.length > 0 && !allSelected;
  }, [selectedItems.length, allSelected]);

  function toggleItem(item: ConversationSummary) {
    const key = conversationKey(item);
    setSelected(current => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }
  function reset() {
    p.setQuery(""); p.setLanguage(""); p.setChannel(""); p.setGroup(""); p.setView("all");
  }
  function toggleSort(field: "dialogue" | "score") {
    setSort(sort?.field === field
      ? sort.ascending ? { field, ascending: false } : null
      : { field, ascending: true });
  }
  function sortIcon(field: "dialogue" | "score") {
    return sort?.field !== field ? <ArrowUpDown size={13} aria-hidden="true" />
      : sort.ascending ? <ArrowUp size={13} aria-hidden="true" /> : <ArrowDown size={13} aria-hidden="true" />;
  }

  return <section className="conversation-index" aria-label="Список диалогов">
    <div className="ci-topline">
      <div className="ci-views" role="group" aria-label="Отбор по состоянию оценки">
        {views.map(([value, label]) => <button type="button" key={value}
          aria-pressed={p.view === value} onClick={() => p.setView(value)}>
          {label}<span>{scoped.filter(item => matchesView(item, value)).length}</span>
        </button>)}
        {p.view === "clean" && <button type="button" aria-pressed="true" onClick={() => p.setView("all")}>Без грубых ошибок <X size={13} aria-hidden="true" /></button>}
      </div>
      <button type="button" className="button primary ci-import" disabled={p.importing || p.running} onClick={p.onImport}>
        {p.importing ? <span className="spin"><LoaderCircle size={16} aria-hidden="true" /></span> : <Upload size={16} aria-hidden="true" />}
        {p.importing ? "Импортируем…" : "Импорт JSON"}
      </button>
    </div>

    <div className="ci-toolbar">
      <label className="ci-search">
        <Search size={17} aria-hidden="true" />
        <input type="search" aria-label="Поиск диалога" placeholder="Найти по тексту или ID" autoComplete="off" value={p.query} onChange={event => p.setQuery(event.target.value)} />
      </label>
      <button type="button" className={`button secondary ci-filter-button${activeFilters ? " is-filtered" : ""}`} aria-expanded={filtersOpen} aria-controls="index-filters" onClick={() => setFiltersOpen(value => !value)}>
        <SlidersHorizontal size={15} aria-hidden="true" /> Фильтры{activeFilters ? ` (${activeFilters})` : ""}
      </button>
      <span className="ci-record-count" role="status">{rows.length} из {p.allItems.length}</span>
    </div>
    {filtersOpen && <div className="ci-filters" id="index-filters">
      <label><span>Язык</span><Select value={p.language} onChange={event => p.setLanguage(event.target.value)}><option value="">Все языки</option><option>RU</option><option>KZ</option><option>EN</option></Select></label>
      <label><span>Канал</span><Select value={p.channel} onChange={event => p.setChannel(event.target.value)}><option value="">Все каналы</option><option value="agent chat">Чаты</option><option value="voice call">Звонки</option></Select></label>
      <label><span>Набор</span><Select value={p.group} onChange={event => p.setGroup(event.target.value)}><option value="">Все наборы</option>{p.groups.map(group => <option key={group}>{group}</option>)}</Select></label>
      <button type="button" className="button ghost" onClick={reset} disabled={!activeFilters && !p.query && p.view === "all"}>Сбросить</button>
    </div>}

    {selectedItems.length > 0 && <div className="ci-selection" role="region" aria-label="Действия с выбранными диалогами">
      <span><Check size={16} aria-hidden="true" /> Выбрано <strong>{selectedItems.length}</strong></span>
      <button type="button" className="button primary small" disabled={!p.canRun || p.running || p.importing} onClick={() => p.onRun(selectedItems)}>
        <Play size={14} aria-hidden="true" /> Оценить выбранные
      </button>
      <button type="button" className="ci-clear-selection" onClick={() => setSelected(new Set())}>Снять выбор</button>
    </div>}

    {rows.length > 0 ? <div className="ci-table-scroll">
      <table className="ci-table">
        <caption className="sr-only">Диалоги и результаты последних оценок</caption>
        <thead><tr>
          <th className="ci-checkbox-cell"><label className="ci-check"><input ref={selectAllRef} type="checkbox" aria-label={`Выбрать все диалоги в выборке (${rows.length})`} checked={allSelected} onChange={() => setSelected(allSelected ? new Set() : new Set(rows.map(conversationKey)))} /></label></th>
          <th className="ci-dialogue-cell" aria-sort={sort?.field === "dialogue" ? sort.ascending ? "ascending" : "descending" : "none"}><button type="button" onClick={() => toggleSort("dialogue")}>Диалог {sortIcon("dialogue")}</button></th>
          <th className="ci-channel-cell">Канал</th>
          <th className="ci-language-cell">Язык</th>
          <th className="ci-state-cell">Оценивание</th>
          <th className="ci-score-cell" aria-sort={sort?.field === "score" ? sort.ascending ? "ascending" : "descending" : "none"}><button type="button" onClick={() => toggleSort("score")}>Балл {sortIcon("score")}</button></th>
          <th className="ci-critical-cell">Грубая ошибка</th>
          <th className="ci-open-cell"><span className="sr-only">Открыть</span></th>
        </tr></thead>
        <tbody>{rows.map(item => {
          const key = conversationKey(item);
          const state = stateFor(item);
          const isSelected = selected.has(key);
          return <tr key={key} className={isSelected ? "is-selected" : undefined}>
            <td className="ci-checkbox-cell"><label className="ci-check"><input type="checkbox" aria-label={`Выбрать ${item.conversation_id} из ${item.group_id}`} checked={isSelected} onChange={() => toggleItem(item)} /></label></td>
            <td className="ci-dialogue-cell"><button type="button" className="ci-open-dialogue" onClick={() => p.onOpen(item)} aria-label={`Открыть диалог ${item.conversation_id} из ${item.group_id}`}>
              <span className="ci-preview" lang={item.preview.trim() ? { RU: "ru", KZ: "kk", EN: "en" }[item.language] : "ru"}>{item.preview.trim() || (item.channel === "voice call" ? "Транскрипт звонка" : "Транскрипт чата")}</span>
              <span className="ci-dialogue-meta"><span translate="no">{item.conversation_id}</span><span translate="no">{item.group_id}</span><span>{turnsLabel(item.message_count)}</span></span>
            </button></td>
            <td className="ci-channel-cell">{item.channel === "voice call" ? "Звонок" : "Чат"}</td>
            <td className="ci-language-cell" translate="no">{item.language}</td>
            <td className="ci-state-cell"><span className={`ci-state ci-state-${state.tone}`}>{state.text}</span></td>
            <td className="ci-score-cell"><span className="ci-score">{item.latest_score == null ? <span className="ci-no-score" aria-label="Оценка отсутствует">—</span> : number(item.latest_score, 2)}</span></td>
            <td className="ci-critical-cell">{item.latest_gross_error === true ? <span className="ci-critical-yes">Есть<span className="ci-critical-description">грубая ошибка</span></span> : item.latest_gross_error === false ? <span className="ci-critical-no">Нет</span> : <span className="ci-critical-unknown" aria-label="Нет результата проверки грубых ошибок">—</span>}</td>
            <td className="ci-open-cell"><ArrowUpRight size={16} aria-hidden="true" /></td>
          </tr>;
        })}</tbody>
      </table>
    </div> : <div className="ci-empty"><h2>Диалоги не найдены</h2><p>Попробуйте другой запрос или измените фильтры.</p><button type="button" className="button secondary" onClick={reset}>Сбросить фильтры</button></div>}
    <footer className="ci-footer"><span>{p.group || "Все наборы"}</span><span>Откройте диалог, чтобы проверить оценку и доказательства</span></footer>
  </section>;
}
