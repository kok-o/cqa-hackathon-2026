import { useLayoutEffect, useState } from "react";
import { ArrowLeft, Bot, ChevronDown, Link2, MessageSquare, RefreshCw } from "lucide-react";
import CallAudio from "./CallAudio";
import type { Conversation, Evaluation, Message, Speaker } from "./contracts";
import { turnsLabel } from "./format";
import "./Transcript.css";

const names: Record<Speaker, string> = {
  operator: "Оператор",
  client: "Клиент",
  bot: "Бот",
  system: "Система",
};
const locales = { RU: "ru", KZ: "kk", EN: "en" };

export type EvidenceTarget = {
  start: string;
  end?: string;
  quote?: string;
  criterion: string;
  title: string;
};
type Props = {
  source: Conversation | null;
  evaluation: Evaluation | null;
  highlight: EvidenceTarget | null;
  loading: boolean;
  isMobile: boolean;
  error: string;
  onRetry: () => void;
  onReturn: (animate: boolean) => void;
};

function MessageText({ text, quote }: { text: string; quote?: string }) {
  const offset = quote ? text.indexOf(quote) : -1;
  if (offset < 0 || !quote) return <p>{text}</p>;
  return (
    <p>{text.slice(0, offset)}<mark>{text.slice(offset, offset + quote.length)}</mark>{text.slice(offset + quote.length)}</p>
  );
}

export default function Transcript({ source, evaluation, highlight, loading, isMobile, error, onRetry, onReturn }: Props) {
  const [introOpen, setIntroOpen] = useState(false);
  const messages = source?.transcript;
  const start = messages?.findIndex((item) => item.message_id === highlight?.start) ?? -1;
  const endIndex = highlight?.end ? messages?.findIndex((item) => item.message_id === highlight.end) ?? -1 : start;
  const end = endIndex < 0 ? start : endIndex;
  const firstOperator = messages?.findIndex((item) => item.speaker === "operator") ?? -1;
  const introCount = source?.channel === "agent chat" && firstOperator > 0 &&
    messages?.slice(0, firstOperator).some((item) => item.speaker === "bot")
    ? firstOperator : 0;

  useLayoutEffect(() => {
    if (start >= 0 && Math.min(start, end) < introCount) setIntroOpen(true);
  }, [highlight, start, end, introCount]);

  function renderMessage(message: Message, index: number) {
    const active = start >= 0 && index >= Math.min(start, end) && index <= Math.max(start, end);
    const isStart = active && index === start;
    const isEnd = active && index === end;
    const timestamp = message.timestamp.includes("T")
      ? message.timestamp.split("T")[1]?.slice(0, 8) : message.timestamp;
    return (
      <article
        key={message.message_id}
        id={`message-${message.message_id}`}
        tabIndex={-1}
        className={`message ${message.speaker}${active ? " highlighted" : ""}${isStart ? " range-start" : ""}${isEnd ? " range-end" : ""}`}
        data-range-boundary={isStart ? "start" : isEnd ? "end" : undefined}
        aria-label={`Реплика ${index + 1}, ${names[message.speaker]}`}
      >
        <span className="message-position" aria-hidden="true">{index + 1}</span>
        <div className="message-body">
          <div className="message-meta">
            <strong>{names[message.speaker]}</strong>
            {timestamp && <time title={message.timestamp}>{timestamp}</time>}
          </div>
          <div lang={source ? locales[source.language] : undefined}>
            <MessageText text={message.text} quote={isStart ? highlight?.quote : undefined} />
          </div>
        </div>
      </article>
    );
  }

  return (
    <section
      id="transcript-panel"
      className="transcript-pane"
      role={isMobile ? "tabpanel" : undefined}
      aria-labelledby={isMobile ? "tab-transcript" : undefined}
      aria-label={isMobile ? undefined : "Транскрипт"}
      aria-busy={loading}
    >
      <header className="panel-heading">
        <h2>Разговор {messages && <span className="subtle">{turnsLabel(messages.length)}</span>}</h2>
        <div className="transcript-tags">
          {source?.channel === "voice call" && !source.audio_url && <span className="source-version">Без записи</span>}
          {evaluation && (
            <span className="source-version" title={`Сохранённый транскрипт запуска по чек-листу v${evaluation.checklist.version}`}>
              Текст на момент оценки
            </span>
          )}
        </div>
      </header>
      {highlight && start >= 0 && (
        <div className="evidence-return">
          <Link2 size={17} aria-hidden="true" />
          <div>
            <strong>{highlight.end ? `Реплики ${start + 1}–${end + 1}` : `Реплика ${start + 1}`}</strong>
            <span title={highlight.title}>{highlight.title}</span>
          </div>
          <button className="text-button" onClick={(event) => onReturn(event.detail > 0)}>
            <ArrowLeft size={15} aria-hidden="true" /> К критерию
          </button>
        </div>
      )}
      {source?.channel === "voice call" && (
        <CallAudio
          key={source.audio_url}
          groupId={source.group_id}
          conversationId={source.conversation_id}
          evaluationId={evaluation?.evaluation_id}
          hasAudio={Boolean(source.audio_url)}
        />
      )}
      <div className="transcript-content" tabIndex={0} aria-label="Текст разговора">
        {loading ? (
          <div className="transcript-skeleton" role="status">
            <span className="sr-only">Загружаем транскрипт…</span>
            {[1, 2, 3, 4].map((i) => <div key={i} aria-hidden="true"><span /><p /></div>)}
          </div>
        ) : error ? (
          <div className="small-empty" role="alert">
            <strong>Транскрипт недоступен</strong>
            <p>{error}</p>
            <button className="button secondary" onClick={onRetry}><RefreshCw size={15} aria-hidden="true" /> Повторить загрузку</button>
          </div>
        ) : messages ? (
          <>
            {introCount > 0 && (
              <div className="transcript-intro">
                <button
                  className="transcript-intro-toggle"
                  type="button"
                  aria-controls="transcript-intro-messages"
                  aria-expanded={introOpen}
                  onClick={() => setIntroOpen((open) => !open)}
                >
                  <Bot size={17} aria-hidden="true" />
                  <span>До подключения оператора <small>{turnsLabel(introCount)}</small></span>
                  <ChevronDown size={16} aria-hidden="true" />
                </button>
                <div id="transcript-intro-messages" hidden={!introOpen}>
                  {introOpen && messages.slice(0, introCount).map(renderMessage)}
                </div>
              </div>
            )}
            {messages.slice(introCount).map((message, index) => renderMessage(message, index + introCount))}
          </>
        ) : (
          <div className="small-empty">
            <MessageSquare size={26} aria-hidden="true" />
            <p>Выберите диалог в очереди.</p>
          </div>
        )}
      </div>
    </section>
  );
}
