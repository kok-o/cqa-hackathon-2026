import { useEffect, useState } from "react";
import { AlertTriangle, ArrowUpRight, ChevronRight } from "lucide-react";
import Select from "./Select";
import { errorText, q, request } from "./api";
import "./QualitySummary.css";

export type SummaryFinding = {
  conversation_id: string;
  evaluation_id: string;
  criterion_id: string;
};

type Summary = {
  total: number;
  complete: number;
  incomplete: number;
  unrated: number;
  without_points: number;
  affected: number;
  critical_unknown: number;
  critical: SummaryFinding[];
  violations: {
    criterion_id: string;
    title: string;
    applicable: number;
    dialogs: SummaryFinding[];
  }[];
};

type Props = {
  groups: string[];
  initialGroup: string;
  checklistId: string;
  version: number;
  onOpen: (group: string, finding: SummaryFinding) => void;
};

function FindingList({ findings, onOpen }: {
  findings: SummaryFinding[];
  onOpen: (finding: SummaryFinding) => void;
}) {
  return (
    <ul className="quality-findings">
      {findings.map((item) => (
        <li key={item.conversation_id}>
          <button type="button" onClick={() => onOpen(item)}>
            <span>{item.conversation_id}</span>
            <ArrowUpRight size={15} aria-hidden="true" />
          </button>
        </li>
      ))}
    </ul>
  );
}

export default function QualitySummary({ groups, initialGroup, checklistId, version, onOpen }: Props) {
  const [selected, setSelected] = useState(initialGroup);
  const [retry, setRetry] = useState(0);
  const [loaded, setLoaded] = useState<{ key: string; data?: Summary; error?: string } | null>(null);
  const group = groups.includes(selected) ? selected : (groups[0] ?? "");
  const path = `/quality-summary?group_id=${q(group)}&checklist_id=${q(checklistId)}&checklist_version=${version}`;
  const key = `${path}&attempt=${retry}`;
  const state = loaded?.key === key ? loaded : null;

  useEffect(() => {
    if (!group) return;
    const controller = new AbortController();
    request<Summary>(path, { signal: controller.signal }).then((data) => {
      if (!data || !Array.isArray(data.violations) || !Array.isArray(data.critical)) {
        throw new Error("Сервер вернул неполную сводку.");
      }
      if (!controller.signal.aborted) setLoaded({ key, data });
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setLoaded({ key, error: errorText(error) });
    });
    return () => controller.abort();
  }, [group, key, path]);

  if (!group) return <p className="quality-summary">Импортируйте набор диалогов, чтобы открыть сводку.</p>;

  const data = state?.data;
  const open = (finding: SummaryFinding) => onOpen(group, finding);
  return (
    <section className="quality-summary" aria-label="Сводка по нарушениям">
      <label className="quality-group">
        <span>Набор</span>
        <Select value={group} onChange={(event) => setSelected(event.target.value)}>
          {groups.map((item) => <option key={item} value={item}>{item}</option>)}
        </Select>
      </label>
      {state?.error ? (
        <div className="quality-state" role="alert">
          <p>{state.error}</p>
          <button type="button" className="button secondary" onClick={() => setRetry((value) => value + 1)}>Обновить</button>
        </div>
      ) : !data ? <p className="quality-state" role="status">Загружаем сводку…</p> : (
        <>
          <dl className="quality-totals">
            <div><dt>Диалогов</dt><dd>{data.total}</dd></div>
            <div><dt>Полных оценок</dt><dd>{data.complete}</dd></div>
            <div><dt>Нужна проверка</dt><dd>{data.incomplete}</dd></div>
            <div><dt>Без оценки</dt><dd>{data.unrated}</dd></div>
          </dl>
          {data.without_points > 0 ? <p className="quality-note">Без применимых баллов — {data.without_points}.</p> : null}
          {data.total === data.unrated ? <p className="quality-state">У этого набора ещё нет оценок по выбранному чек-листу.</p> : (
            <>
              <div className="quality-critical">
                <h2>{data.critical.length > 0 ? <AlertTriangle size={16} aria-hidden="true" /> : null} Грубые нарушения <strong>{data.critical.length}</strong></h2>
                {data.critical_unknown > 0 ? <p className="quality-note">Ещё проверяются на грубые нарушения — {data.critical_unknown}.</p> : null}
                {data.critical.length > 0 ? (
                  <details>
                    <summary><ChevronRight size={15} aria-hidden="true" /> Показать диалоги</summary>
                    <FindingList findings={data.critical} onOpen={open} />
                  </details>
                ) : null}
              </div>
              <div className="quality-violations">
                <h2>Частые нарушения</h2>
                <p className="quality-note">Диалогов со снижениями или грубыми нарушениями — {data.affected}. В строках — число снижений среди диалогов, где критерий применим и оценён.</p>
                {data.violations.length > 0 ? data.violations.map((item) => (
                  <details key={item.criterion_id}>
                    <summary>
                      <ChevronRight size={15} aria-hidden="true" />
                      <span>{item.title}</span>
                      <span className="quality-count" title="Из оценённых применимых диалогов">{item.dialogs.length} из {item.applicable}</span>
                    </summary>
                    <FindingList findings={item.dialogs} onOpen={open} />
                  </details>
                )) : <p className="quality-state">Подтверждённых снижений балла пока нет.</p>}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}
