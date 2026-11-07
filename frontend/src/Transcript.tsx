import CallAudio from "./CallAudio";
import { UserRound } from "lucide-react";

import { ArrowLeft, Headphones, MessageSquare } from "lucide-react";
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
        <h2>
          Разговор{" "}
          <span className="subtle">
            {source?.transcript.length ?? 0} реплик
          </span>
        </h2>
        {evaluation && (
          <span
            className="source-version"
            title={`Сохранённый снимок диалога из оценки по чек-листу v${evaluation.checklist.version}`}
          >
            Снимок оценки
          </span>
        )}
      </header>
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
      {source?.channel === 'voice call' && <CallAudio key={`${source.group_id}:${source.conversation_id}:${evaluation?.evaluation_id ?? ''}`} groupId={source.group_id} conversationId={source.conversation_id} evaluationId={evaluation?.evaluation_id} hasAudio={Boolean(source.audio_url)} />}
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
                  <span className="speaker-mark" aria-hidden="true">{message.speaker === "operator" ? <Headphones size={17} /> : <UserRound size={17} />}</span>
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
    </section>
  );
}
