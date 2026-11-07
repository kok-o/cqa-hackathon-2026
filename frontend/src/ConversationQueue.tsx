import {
  Headphones,
  ListFilter,
  MessageSquare,
  Play,
  Search,
  Upload,
  X,
} from "lucide-react";
import type { ConversationSummary } from "./contracts";
import { number, statusName } from "./format";

export const conversationKey = (value: {
  group_id: string;
  conversation_id: string;
}) => JSON.stringify([value.group_id, value.conversation_id]);
type Props = {
  items: ConversationSummary[];
  total: number;
  selectedKey: string;
  groups: string[];
  query: string;
  language: string;
  channel: string;
  group: string;
  grossError: string;
  filtersOpen: boolean;
  running: boolean;
  importing: boolean;
  configured: boolean;
  onQuery: (value: string) => void;
  onLanguage: (value: string) => void;
  onChannel: (value: string) => void;
  onGroup: (value: string) => void;
  onGrossError: (value: string) => void;
  onFilters: () => void;
  onSelect: (item: ConversationSummary) => void;
  onReset: () => void;
  onImport: () => void;
  onRun: () => void;
  onDemo: () => void;
  onSubmission: () => void;
  onClose: () => void;
};
export default function ConversationQueue(p: Props) {
  const filterCount = [p.language, p.channel, p.group, p.grossError].filter(
    Boolean,
  ).length;
  return (
    <aside
      id="queue-panel"
      className="conversation-sidebar"
      aria-label="Список диалогов"
    >
      <div className="sidebar-heading">
        <div>
          <h2>Очередь <span>{p.items.length === p.total ? p.total : `${p.items.length} / ${p.total}`}</span></h2>
        </div>
        <button className="icon-button queue-import" onClick={p.onImport} disabled={p.importing || p.running} aria-label="Импорт JSON" title="Импорт JSON"><Upload size={17} /></button>
        <button
          className="icon-button queue-close"
          onClick={p.onClose}
          aria-label="Закрыть очередь"
        >
          <X size={18} />
        </button>
      </div>
      <div className="queue-search">
        <label className="search-field">
          <Search size={16} />
          <input
            type="search"
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
          aria-expanded={p.filtersOpen}
          aria-controls="queue-filters"
        >
          <ListFilter size={18} />
          {filterCount > 0 && (
            <span className="filter-count">{filterCount}</span>
          )}
        </button>
      </div>
      {p.filtersOpen && (
        <div id="queue-filters" className="filters">
          <label className="field">
            Язык
            <select
              value={p.language}
              onChange={(e) => p.onLanguage(e.target.value)}
            >
              <option value="">Все языки</option>
              <option>RU</option>
              <option>KZ</option>
              <option>EN</option>
            </select>
          </label>
          <label className="field">
            Канал
            <select
              value={p.channel}
              onChange={(e) => p.onChannel(e.target.value)}
            >
              <option value="">Все каналы</option>
              <option value="agent chat">Чаты</option>
              <option value="voice call">Звонки</option>
            </select>
          </label>
          <label className="field full-width">
            Набор
            <select value={p.group} onChange={(e) => p.onGroup(e.target.value)}>
              <option value="">Все наборы</option>
              {p.groups.map((value) => (
                <option key={value}>{value}</option>
              ))}
            </select>
          </label>
          <label className="field full-width">
            Грубые ошибки в последней оценке
            <select
              value={p.grossError}
              onChange={(e) => p.onGrossError(e.target.value)}
            >
              <option value="">Все результаты</option>
              <option value="true">С грубой ошибкой</option>
              <option value="false">Без грубых ошибок</option>
            </select>
          </label>
          {filterCount > 0 && (
            <button className="text-button full-width" onClick={p.onReset}>
              Сбросить фильтры
            </button>
          )}
        </div>
      )}
      {p.group && <div className="queue-caption"><span>{p.group}</span></div>}
      <div className="conversation-list">
        {p.items.map((item) => (
          <button
            key={conversationKey(item)}
            className={`conversation-item ${conversationKey(item) === p.selectedKey ? "selected" : ""}`}
            onClick={() => p.onSelect(item)}
            aria-current={
              conversationKey(item) === p.selectedKey ? "true" : undefined
            }
          >
            <span className="conversation-item-top">
              <strong>{item.conversation_id}</strong>
              <span className="language-tag">{item.language}</span>
            </span>
            <span className="conversation-preview">
              {item.preview || "Текст диалога"}
            </span>
            <span className="conversation-item-bottom">
              <span>
                {item.channel === "voice call" ? (
                  <Headphones size={12} />
                ) : (
                  <MessageSquare size={12} />
                )}
                {item.message_count} реплик
              </span>
              <span
                className={
                  item.latest_score != null ? "list-score" : "list-status"
                }
              >
                {item.latest_gross_error ? (
                  <span className="critical-dot" title="Грубая ошибка" />
                ) : null}
                {item.latest_score != null ? (
                  <>
                    {number(item.latest_score, 2)}
                  </>
                ) : (
                  statusName(item.latest_status)
                )}
              </span>

            </span>
          </button>
        ))}
        {!p.items.length && (
          <div className="small-empty">
            <Search size={24} />
            <strong>Ничего не найдено</strong>

            <button className="text-button" onClick={p.onReset}>
              Сбросить фильтры
            </button>
          </div>
        )}
      </div>
      <footer className="sidebar-footer">
        <button
          className="button primary full-width"
          disabled={p.running || !p.items.length || !p.configured}
          onClick={p.onRun}
        >
          <Play size={15} /> Оценить выборку{" "}
          <span className="button-count">{p.items.length}</span>
        </button>
        <button
          className="text-button demo-link"
          disabled={p.importing}
          onClick={p.onDemo}
        >
          Учебный набор
        </button>
      </footer>
    </aside>
  );
}
