import Select from "./Select";
import "./ConversationQueue.css";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  Headphones,
  ListFilter,
  LoaderCircle,
  MessageSquare,
  Play,
  Search,
  Upload,
  X,
} from "lucide-react";
import type { ConversationSummary } from "./contracts";
import { number, statusName, turnsLabel } from "./format";

export const conversationKey = (value: {
  group_id: string;
  conversation_id: string;
}) => JSON.stringify([value.group_id, value.conversation_id]);

export type QueueView = "all" | "unrated" | "review" | "critical" | "clean";
const quickViews = [
  ["all", "Все"],
  ["unrated", "Без оценки"],
  ["review", "На проверку"],
  ["critical", "Грубые ошибки"],
] as const;

export function matchesView(item: ConversationSummary, view: QueueView) {
  switch (view) {
    case "unrated": return item.latest_evaluation_id == null;
    case "review": return item.latest_status === "incomplete";
    case "critical": return item.latest_gross_error === true;
    case "clean": return item.latest_gross_error === false;
    default: return true;
  }
}

type Props = {
  resizeHandle?: ReactNode;
  items: ConversationSummary[];
  scopedItems: ConversationSummary[];
  total: number;
  selectedKey: string;
  groups: string[];
  query: string;
  language: string;
  channel: string;
  group: string;
  view: QueueView;
  filtersOpen: boolean;
  drawerOpen: boolean;
  isMobile: boolean;
  running: boolean;
  pending: boolean;
  importing: boolean;
  configured: boolean;
  canRun: boolean;
  onQuery: (value: string) => void;
  onLanguage: (value: string) => void;
  onChannel: (value: string) => void;
  onGroup: (value: string) => void;
  onView: (value: QueueView) => void;
  onFilters: () => void;
  onSelect: (item: ConversationSummary) => void;
  onReset: () => void;
  onImport: () => void;
  onRun: () => void;
  onClose: () => void;
};

export default function ConversationQueue(p: Props) {
  const filterCount = [p.language, p.channel, p.group, p.view !== "all"].filter(Boolean).length;
  const viewLabel = p.view === "clean" ? "Без грубых ошибок"
    : quickViews.find(([value]) => value === p.view)?.[1];
  const selectionLabel = [p.group, p.view !== "all" ? viewLabel : ""].filter(Boolean).join(" · ");
  const blocked = p.pending ? "Сначала продолжите сохранённую очередь"
    : !p.configured ? "Подключите модель для оценки"
    : !p.canRun ? "Выберите чек-лист для запуска"
    : "";

  return (
    <aside
      id="queue-panel"
      className="conversation-sidebar queue-neutral"
      role={p.isMobile ? "tabpanel" : p.drawerOpen ? "dialog" : undefined}
      aria-labelledby={p.isMobile ? "tab-queue" : undefined}
      aria-modal={!p.isMobile && p.drawerOpen || undefined}
      aria-label={p.isMobile ? undefined : "Список диалогов"}
    >
      <header className="sidebar-heading">
        <h2>Очередь <span role="status">{p.items.length === p.total ? p.total : `${p.items.length} / ${p.total}`}</span></h2>
        <button
          className="icon-button queue-import"
          aria-label={p.importing ? "Импортируем диалоги" : "Импорт JSON"}
          title="Импорт JSON"
          onClick={p.onImport}
          disabled={p.importing || p.running}
        >
          {p.importing ? <span className="spin" aria-hidden="true"><LoaderCircle size={15} /></span> : <Upload size={15} aria-hidden="true" />}
        </button>
        <button className="icon-button queue-close" onClick={p.onClose} aria-label="Закрыть очередь">
          <X size={19} aria-hidden="true" />
        </button>
      </header>
      <div className="queue-controls">
        <div className="queue-search">
          <label className="search-field">
            <Search size={16} aria-hidden="true" />
            <input
              type="search"
              name="conversation-search"
              autoComplete="off"
              placeholder="Найти диалог…"
              value={p.query}
              onChange={(e) => p.onQuery(e.target.value)}
              aria-label="Поиск диалога"
            />
          </label>
          <button
            className={`icon-button filter-toggle ${p.filtersOpen || filterCount ? "active" : ""}`}
            onClick={p.onFilters}
            aria-label={`Фильтры${filterCount ? `: ${filterCount}` : ""}`}
            title="Фильтры"
            aria-expanded={p.filtersOpen}
            aria-controls={p.filtersOpen ? "queue-filters" : undefined}
          >
            <ListFilter size={18} aria-hidden="true" />
            {filterCount > 0 && <span className="filter-count" aria-hidden="true">{filterCount}</span>}
          </button>
          {p.filtersOpen && <div id="queue-filters" className="filters">
            <fieldset className="queue-state-filter">
              <legend>Состояние оценки</legend>
              <div className="queue-views" role="group" aria-label="Отбор по состоянию оценки">
                {quickViews.map(([value, label]) => (
                  <button key={value} type="button" className={`queue-view ${value}`} aria-pressed={p.view === value} onClick={() => p.onView(value)}>
                    <span>{label}</span>
                    <strong>{p.scopedItems.filter((item) => matchesView(item, value)).length}</strong>
                  </button>
                ))}
              </div>
            </fieldset>
            <label className="field">
              Язык
              <Select menuClassName="queue-select-menu" value={p.language} onChange={(e) => p.onLanguage(e.target.value)}>
                <option value="">Все языки</option>
                <option>RU</option>
                <option>KZ</option>
                <option>EN</option>
              </Select>
            </label>
            <label className="field">
              Канал
              <Select menuClassName="queue-select-menu" value={p.channel} onChange={(e) => p.onChannel(e.target.value)}>
                <option value="">Все каналы</option>
                <option value="agent chat">Чаты</option>
                <option value="voice call">Звонки</option>
              </Select>
            </label>
            <label className="field">
              Набор
              <Select menuClassName="queue-select-menu" value={p.group} onChange={(e) => p.onGroup(e.target.value)}>
                <option value="">Все наборы</option>
                {p.groups.map((value) => <option key={value}>{value}</option>)}
              </Select>
            </label>
            <label className="field">
              Грубые ошибки в последней оценке
              <Select
                menuClassName="queue-select-menu"
                value={p.view === "critical" ? "true" : p.view === "clean" ? "false" : ""}
                onChange={(e) => p.onView(e.target.value === "true" ? "critical" : e.target.value === "false" ? "clean" : "all")}
              >
                <option value="">Любой результат</option>
                <option value="true">С грубой ошибкой</option>
                <option value="false">Без грубых ошибок</option>
              </Select>
            </label>
            <button className="button primary" onClick={p.onFilters}>Показать диалоги ({p.items.length})</button>
            {(filterCount > 0 || p.view !== "all" || p.query) && (
              <button className="text-button" onClick={p.onReset}>Сбросить фильтры</button>
            )}
          </div>}
        </div>
      </div>
      {selectionLabel && <div className="queue-caption">{selectionLabel}</div>}
      <div className="conversation-list">
        {p.items.map((item) => {
          const key = conversationKey(item);
          const selected = key === p.selectedKey;
          const call = item.channel === "voice call";
          return (
            <button
              key={key}
              className={`conversation-item ${selected ? "selected" : ""}`}
              onClick={() => p.onSelect(item)}
              aria-current={selected ? "true" : undefined}
            >
              <span className="conversation-item-top">
                <span className="channel-icon" title={call ? "Звонок" : "Чат"}>
                  {call ? <Headphones size={15} aria-hidden="true" /> : <MessageSquare size={15} aria-hidden="true" />}
                  <span className="sr-only">{call ? "Звонок" : "Чат"}</span>
                </span>
                <strong title={item.conversation_id} translate="no">{item.conversation_id}</strong>
                <span className="language-tag" translate="no">{item.language}</span>
              </span>
              <span className="conversation-preview">
                {item.preview.trim() || (call ? "Транскрипт звонка" : "Транскрипт чата")}
              </span>
              <span className="conversation-item-bottom">
                <span>{turnsLabel(item.message_count)}</span>
                {item.latest_gross_error && (
                  <span className="queue-critical"><AlertTriangle size={12} aria-hidden="true" /> Грубая ошибка</span>
                )}
                {item.latest_score != null ? (
                  <span className="list-score">{number(item.latest_score, 2)}</span>
                ) : (
                  <span className={`list-status ${item.latest_status === "incomplete" ? "review" : ""}`}>
                    {item.latest_status === "incomplete" ? "Нужна проверка" : statusName(item.latest_status)}
                  </span>
                )}
              </span>
            </button>
          );
        })}
        {!p.items.length && (
          <div className="small-empty">
            <Search size={24} aria-hidden="true" />
            <strong>Ничего не найдено</strong>
            <button className="text-button" onClick={p.onReset}>Сбросить фильтры</button>
          </div>
        )}
      </div>
      <footer className="sidebar-footer">
        {blocked && <p id="queue-run-status" className="sr-only">{blocked}</p>}
        <button
          className="button primary full-width"
          disabled={p.running || p.importing || !!blocked || !p.items.length}
          onClick={p.onRun}
          title={blocked || undefined}
          aria-describedby={blocked ? "queue-run-status" : undefined}
        >
          <Play size={15} aria-hidden="true" /> Оценить выборку <span className="button-count">{p.items.length}</span>
        </button>
      </footer>
      {p.resizeHandle}
    </aside>
  );
}
