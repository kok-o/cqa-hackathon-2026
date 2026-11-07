import {
  ArrowLeft,
  Bot,
  Headphones,
  MessageSquare,
  Settings2,
  UserRound,
} from "lucide-react";
import type { Conversation, Evaluation, Speaker } from "./contracts";
const names: Record<Speaker, string> = {
  operator: "Оператор",
  client: "Клиент",
  bot: "Бот",
  system: "Система",
};
type Props = {
  source: Conversation | null;
  evaluation: Evaluation | null;
  highlight: { start: string; end?: string; criterion?: string } | null;
  loading: boolean;
  onReturn: () => void;
};
export default function Transcript({
  source,
  evaluation,
  highlight,
  loading,
  onReturn,
}: Props) {
  const start =
    source?.transcript.findIndex((m) => m.message_id === highlight?.start) ??
    -1;
  const end = highlight?.end
    ? (source?.transcript.findIndex((m) => m.message_id === highlight.end) ??
      start)
    : start;
  return (
    <section
      id="transcript-panel"
      className="transcript-pane"
      aria-label="Транскрипт"
      aria-busy={loading}
    >
      <header className="panel-heading">
        <div>
          <span className="eyebrow">Исходный разговор</span>
          <h2>
            Транскрипт{" "}
            <span className="subtle">
              {source?.transcript.length ?? 0} реплик
            </span>
          </h2>
        </div>
        <span className="badge neutral">
          {source?.language || "—"} ·{" "}
          {source?.channel === "voice call" ? "Звонок" : "Чат"}
        </span>
      </header>
      {evaluation && (
        <div className="snapshot-label">
          <span className="status-dot" /> Снимок на момент оценки · версия
          правил {evaluation.checklist.version}
        </div>
      )}
      {highlight && (
        <div className="evidence-return">
          <span>
            {highlight.end
              ? `Проверенный участок: ${start + 1}–${end + 1}`
              : `Доказательство · реплика ${start + 1}`}
          </span>
          {highlight.criterion && (
            <button className="text-button" onClick={onReturn}>
              <ArrowLeft size={14} /> К критерию
            </button>
          )}
        </div>
      )}
      {source?.audio_url && (
        <div className="audio-player">
          <Headphones size={18} />
          <audio
            controls
            preload="none"
            src={source.audio_url}
            aria-label="Запись звонка"
          />
        </div>
      )}
      {source?.channel === "voice call" && !source.audio_url && (
        <div className="no-audio-banner">
          <Headphones size={16} />
          <span>Запись недоступна. Для разбора доступен транскрипт.</span>
        </div>
      )}
      <div className="transcript-content">
        {loading ? (
          <div
            className="transcript-skeleton"
            aria-label="Загрузка транскрипта"
          >
            {[1, 2, 3, 4].map((i) => (
              <div key={i}>
                <span />
                <p />
              </div>
            ))}
          </div>
        ) : source ? (
          source.transcript.map((message, i) => {
            const active =
              start >= 0 &&
              i >= Math.min(start, end) &&
              i <= Math.max(start, end);
            const isStart = active && i === start,
              isEnd = active && i === end;
            return (
              <article
                key={message.message_id}
                id={`message-${message.message_id}`}
                tabIndex={-1}
                className={`message ${message.speaker} ${active ? "highlighted" : ""} ${isStart ? "range-start" : ""} ${isEnd ? "range-end" : ""}`}
                data-range-boundary={
                  isStart ? "start" : isEnd ? "end" : undefined
                }
                aria-label={`Реплика ${i + 1}, ${names[message.speaker]}`}
              >
                <div className="message-meta">
                  <span className="message-position">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="speaker-avatar">
                    {message.speaker === "operator" ? (
                      <Headphones size={14} />
                    ) : message.speaker === "client" ? (
                      <UserRound size={14} />
                    ) : message.speaker === "bot" ? (
                      <Bot size={14} />
                    ) : (
                      <Settings2 size={14} />
                    )}
                  </span>
                  <strong>{names[message.speaker]}</strong>
                  <time title={message.timestamp}>
                    {message.timestamp.includes("T")
                      ? message.timestamp.split("T")[1]?.slice(0, 8)
                      : message.timestamp}
                  </time>
                </div>
                <p>{message.text}</p>
              </article>
            );
          })
        ) : (
          <div className="small-empty">
            <MessageSquare size={28} />
            <p>Выберите диалог в очереди.</p>
          </div>
        )}
      </div>
      <footer className="transcript-footer">
        <span>
          {source?.channel === "voice call"
            ? "Транскрипт звонка"
            : "Текстовый диалог"}
        </span>
        <span>Исходные роли и время</span>
      </footer>
    </section>
  );
}
