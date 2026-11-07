import { useRef, useState, type KeyboardEvent } from "react";
import {
  Plus,
  Save,
  Trash2,
  Copy,
  FileDown,
  Check,
  ChevronRight,
  SlidersHorizontal,
  FileText,
  ShieldCheck,
  AlertCircle,
} from "lucide-react";
import type { ChecklistVersion, Criterion } from "./contracts";
import { downloadJson, errorText, post, q, uid } from "./api";
import "./ChecklistEditor.css";

type Props = {
  checklist: ChecklistVersion;
  onSaved: (value: ChecklistVersion) => void;
};
const answerNames = { boolean: "Да / нет", scale: "Шкала", choice: "Варианты" };
const freshCriterion = (): Criterion => ({
  criterion_id: `criterion-${uid().slice(0, 8)}`,
  title: "",
  instruction: "",
  weight: 1,
  answer_type: "boolean",
  na_allowed: false,
  requires_audio: false,
  scale_min: null,
  scale_max: null,
  allowed_values: null,
  higher_is_better: true,
  positive_answer: true,
  choices: {},
  section_id: null,
  gross_error_rule: null,
  critical_answer: null,
});

export default function ChecklistEditor({ checklist, onSaved }: Props) {
  const [draft, setDraft] = useState<ChecklistVersion>(() =>
    structuredClone(checklist),
  );
  const [selectedId, setSelectedId] = useState(
    checklist.criteria[0]?.criterion_id ?? "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [dirty, setDirty] = useState(false);
  const [choiceText, setChoiceText] = useState<Record<string, string>>({});
  const [scaleText, setScaleText] = useState<Record<string, string>>({});
  const titleRef = useRef<HTMLInputElement>(null);
  const formRef = useRef<HTMLDivElement>(null);
  const selectedIndex = Math.max(
    0,
    draft.criteria.findIndex((item) => item.criterion_id === selectedId),
  );
  const criterion = draft.criteria[selectedIndex];
  const weight = draft.criteria.reduce((sum, item) => sum + item.weight, 0);
  const markChanged = () => {
    setDirty(true);
    setNotice("");
  };
  const change = (values: Partial<Criterion>) => {
    markChanged();
    setDraft((current) => ({
      ...current,
      criteria: current.criteria.map((item) =>
        item.criterion_id === criterion.criterion_id
          ? { ...item, ...values }
          : item,
      ),
    }));
  };

  function preparedCriteria() {
    return draft.criteria.map((item) => {
      const fail = (message: string): never => {
        setSelectedId(item.criterion_id);
        throw new Error(
          `«${item.title.trim() || "Новый критерий"}»: ${message}`,
        );
      };
      if (!item.title.trim() || !item.instruction.trim())
        fail("заполните название и инструкцию.");
      if (!Number.isFinite(item.weight) || item.weight < 0)
        fail("вес должен быть неотрицательным числом.");
      const clean = {
        ...item,
        title: item.title.trim(),
        instruction: item.instruction.trim(),
      };
      if (item.answer_type === "scale") {
        if (
          item.scale_min == null ||
          item.scale_max == null ||
          !Number.isFinite(item.scale_min) ||
          !Number.isFinite(item.scale_max) ||
          item.scale_min >= item.scale_max
        )
          fail("максимум шкалы должен быть больше минимума.");
        const text = scaleText[item.criterion_id];
        const values =
          text == null
            ? (item.allowed_values ?? null)
            : text.trim()
              ? text
                  .split(/[\s,;]+/)
                  .filter(Boolean)
                  .map(Number)
              : null;
        if (
          values != null &&
          (!values.length ||
            values.some(
              (value) =>
                !Number.isFinite(value) ||
                value < item.scale_min! ||
                value > item.scale_max!,
            ) ||
            new Set(values).size !== values.length)
        )
          fail("укажите неповторяющиеся ступени в пределах шкалы.");
        return { ...clean, allowed_values: values };
      }
      if (item.answer_type !== "choice") return clean;
      const text =
        choiceText[item.criterion_id] ??
        Object.entries(item.choices)
          .map(([label, score]) => `${label} = ${score}`)
          .join("\n");
      const entries = text
        .split("\n")
        .filter((line) => line.trim())
        .map((line) => {
          const separator = line.lastIndexOf("=");
          if (separator < 1)
            fail("укажите каждый вариант в формате «ответ = балл от 0 до 1».");
          const label = line.slice(0, separator).trim();
          const scoreText = line.slice(separator + 1).trim();
          const score = Number(scoreText.replace(",", "."));
          if (
            !label ||
            !scoreText ||
            !Number.isFinite(score) ||
            score < 0 ||
            score > 1
          )
            fail("баллы вариантов должны быть числами от 0 до 1.");
          return [label, score] as const;
        });
      if (!entries.length) fail("добавьте хотя бы один вариант ответа.");
      if (new Set(entries.map(([label]) => label)).size !== entries.length)
        fail("названия вариантов не должны повторяться.");
      return { ...clean, choices: Object.fromEntries(entries) };
    });
  }

  async function save(asNew: boolean) {
    setError("");
    setNotice("");
    try {
      if (!draft.title.trim()) throw new Error("Укажите название чек-листа.");
      const criteria = preparedCriteria();
      setBusy(true);
      const body = {
        title: draft.title.trim(),
        provenance: draft.provenance,
        criteria,
      };
      const saved = asNew
        ? await post<ChecklistVersion>("/checklists", {
            ...body,
            checklist_id: `checklist-${uid().slice(0, 8)}`,
            version: 1,
          })
        : await post<ChecklistVersion>(
            `/checklists/${q(checklist.checklist_id)}/versions`,
            body,
          );
      setDirty(false);
      setNotice(
        `Сохранена версия ${saved.version}. Она готова к новой оценке.`,
      );
      onSaved(saved);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  function addCriterion() {
    const next = freshCriterion();
    setDraft((current) => ({
      ...current,
      criteria: [...current.criteria, next],
    }));
    setSelectedId(next.criterion_id);
    markChanged();
    requestAnimationFrame(() => {
      formRef.current?.scrollIntoView({ block: "nearest" });
      titleRef.current?.focus({ preventScroll: true });
    });
  }

  function deleteCriterion() {
    const next = draft.criteria.filter(
      (item) => item.criterion_id !== criterion.criterion_id,
    );
    setDraft((current) => ({ ...current, criteria: next }));
    const selected = next[Math.min(selectedIndex, next.length - 1)];
    setSelectedId(selected.criterion_id);
    markChanged();
    requestAnimationFrame(() =>
      document
        .getElementById(`editor-select-${selected.criterion_id}`)
        ?.focus(),
    );
  }

  function navigateCriteria(
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) {
    let next = index;
    if (event.key === "ArrowDown")
      next = Math.min(draft.criteria.length - 1, index + 1);
    else if (event.key === "ArrowUp") next = Math.max(0, index - 1);
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = draft.criteria.length - 1;
    else return;
    event.preventDefault();
    setSelectedId(draft.criteria[next].criterion_id);
    document
      .getElementById(`editor-select-${draft.criteria[next].criterion_id}`)
      ?.focus();
  }

  function setType(type: Criterion["answer_type"]) {
    setScaleText((current) => {
      const next = { ...current };
      delete next[criterion.criterion_id];
      return next;
    });
    setChoiceText((current) => {
      const next = { ...current };
      delete next[criterion.criterion_id];
      return next;
    });
    change({
      answer_type: type,
      scale_min: type === "scale" ? (criterion.scale_min ?? 0) : null,
      scale_max: type === "scale" ? (criterion.scale_max ?? 2) : null,
      allowed_values:
        type === "scale" ? (criterion.allowed_values ?? null) : null,
      critical_answer:
        type === "boolean" ? (criterion.critical_answer ?? null) : null,
      choices:
        type === "choice"
          ? Object.keys(criterion.choices).length
            ? criterion.choices
            : { Нет: 0, Частично: 0.5, Да: 1 }
          : {},
    });
  }

  return (
    <section
      className="editor-workspace editor-v2"
      aria-labelledby="editor-heading"
      aria-busy={busy}
    >
      <header className="editor-heading-row">
        <div>
          <span className="editor-eyebrow">Конфигурация оценки</span>
          <h2 id="editor-heading">Редактор чек-листа</h2>
          <p>Настройте правила, по которым модель проверяет каждый диалог.</p>
        </div>
        <button
          className="button secondary"
          disabled={busy}
          onClick={() => {
            try {
              downloadJson(
                { ...draft, criteria: preparedCriteria() },
                "cqa-checklist.json",
              );
            } catch (err) {
              setError(errorText(err));
            }
          }}
        >
          <FileDown size={16} /> Скачать
        </button>
      </header>
      {error ? (
        <div className="notice error editor-feedback" role="alert">
          <AlertCircle size={18} />
          <span>{error}</span>
        </div>
      ) : null}
      {notice ? (
        <div className="notice success editor-feedback" role="status">
          <Check size={18} />
          {notice}
        </div>
      ) : null}
      <div className="editor-meta">
        <label className="field editor-name">
          Название
          <input
            value={draft.title}
            onChange={(event) => {
              setDraft({ ...draft, title: event.target.value });
              markChanged();
            }}
            maxLength={200}
            disabled={busy}
          />
        </label>
        <label className="field">
          Источник
          <select
            value={draft.provenance}
            onChange={(event) => {
              setDraft({
                ...draft,
                provenance: event.target
                  .value as ChecklistVersion["provenance"],
              });
              markChanged();
            }}
            disabled={busy}
          >
            <option value="synthetic_example">Учебный чек-лист</option>
            <option value="organizer">Правила организаторов</option>
          </select>
        </label>
        <div className="editor-version">
          <span>Исходная версия</span>
          <strong>v{checklist.version}</strong>
        </div>
      </div>
      <div className="editor-body">
        <aside className="editor-index" aria-label="Критерии чек-листа">
          <div className="editor-index-heading">
            <strong>
              Критерии <span>{draft.criteria.length}</span>
            </strong>
            <span>
              Вес{" "}
              {new Intl.NumberFormat("ru-RU", {
                maximumFractionDigits: 2,
              }).format(weight)}
            </span>
          </div>
          <div className="editor-criterion-list">
            {draft.criteria.map((item, index) => (
              <button
                type="button"
                key={item.criterion_id}
                id={`editor-select-${item.criterion_id}`}
                className={`editor-criterion-button ${item.criterion_id === criterion.criterion_id ? "is-selected" : ""}`}
                aria-current={
                  item.criterion_id === criterion.criterion_id
                    ? "true"
                    : undefined
                }
                aria-controls="editor-detail"
                onClick={() => setSelectedId(item.criterion_id)}
                onKeyDown={(event) => navigateCriteria(event, index)}
              >
                <span className="editor-order">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <span className="editor-criterion-label">
                  <strong>{item.title || "Новый критерий"}</strong>
                  <small>
                    {answerNames[item.answer_type]}
                    {item.na_allowed ? " · N/A" : ""}
                  </small>
                </span>
                <span className="editor-weight">{item.weight}</span>
                <ChevronRight size={14} aria-hidden="true" />
              </button>
            ))}
          </div>
          <button
            className="button secondary editor-add"
            onClick={addCriterion}
            disabled={busy}
          >
            <Plus size={16} /> Добавить критерий
          </button>
          <div className="editor-history-note">
            <ShieldCheck size={17} />
            <p>У сохранённой оценки остаются её правила и исходная версия.</p>
          </div>
        </aside>
        <div
          className="editor-detail"
          id="editor-detail"
          ref={formRef}
          aria-labelledby="editor-selected-heading"
        >
          <header className="editor-detail-heading">
            <div>
              <span className="editor-eyebrow">
                Критерий {String(selectedIndex + 1).padStart(2, "0")} из{" "}
                {draft.criteria.length}
              </span>
              <h3 id="editor-selected-heading">
                {criterion.title || "Новый критерий"}
              </h3>
            </div>
            <button
              className="icon-button danger-text"
              aria-label={`Удалить критерий ${criterion.title || selectedIndex + 1}`}
              disabled={draft.criteria.length === 1 || busy}
              title={
                draft.criteria.length === 1
                  ? "В чек-листе должен остаться хотя бы один критерий"
                  : "Удалить критерий"
              }
              onClick={deleteCriterion}
            >
              <Trash2 size={17} />
            </button>
          </header>
          <fieldset className="editor-fieldset" disabled={busy}>
            <legend>
              <FileText size={16} /> Название и инструкция
            </legend>
            <div className="editor-field-grid">
              <label className="field editor-wide">
                Название критерия
                <input
                  ref={titleRef}
                  value={criterion.title}
                  onChange={(event) => change({ title: event.target.value })}
                  placeholder="Например, понятность следующего действия"
                  maxLength={200}
                  required
                />
              </label>
              <label className="field editor-wide">
                Инструкция для оценки
                <textarea
                  rows={5}
                  value={criterion.instruction}
                  onChange={(event) =>
                    change({ instruction: event.target.value })
                  }
                  placeholder="Что проверять и какие условия соответствуют каждому ответу"
                  required
                />
                <span className="form-help">
                  Опишите наблюдаемое действие и условия оценки. Эти инструкции
                  получит модель.
                </span>
              </label>
              <label className="field editor-wide">
                Раздел
                <input
                  value={criterion.section_id ?? ""}
                  onChange={(event) =>
                    change({
                      section_id: event.target.value.trim()
                        ? event.target.value
                        : null,
                    })
                  }
                  placeholder="Необязательно"
                />
              </label>
            </div>
          </fieldset>
          <fieldset className="editor-fieldset" disabled={busy}>
            <legend>
              <SlidersHorizontal size={16} /> Вес и шкала
            </legend>
            <div className="editor-field-grid">
              <label className="field">
                Тип ответа
                <select
                  value={criterion.answer_type}
                  onChange={(event) =>
                    setType(event.target.value as Criterion["answer_type"])
                  }
                >
                  <option value="boolean">Да / нет</option>
                  <option value="scale">Числовая шкала</option>
                  <option value="choice">Варианты ответа</option>
                </select>
              </label>
              <label className="field">
                Вес
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={criterion.weight}
                  onChange={(event) =>
                    change({ weight: Number(event.target.value) })
                  }
                />
                <span className="form-help">
                  Вес 0 не влияет на итоговый балл.
                </span>
              </label>
              {criterion.answer_type === "boolean" ? (
                <label className="field editor-wide">
                  Положительный ответ
                  <select
                    value={String(criterion.positive_answer)}
                    onChange={(event) =>
                      change({ positive_answer: event.target.value === "true" })
                    }
                  >
                    <option value="true">Да</option>
                    <option value="false">Нет</option>
                  </select>
                </label>
              ) : null}
              {criterion.answer_type === "scale" ? (
                <>
                  <label className="field">
                    Минимум
                    <input
                      type="number"
                      step="any"
                      value={criterion.scale_min ?? 0}
                      onChange={(event) =>
                        change({ scale_min: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label className="field">
                    Максимум
                    <input
                      type="number"
                      step="any"
                      value={criterion.scale_max ?? 2}
                      onChange={(event) =>
                        change({ scale_max: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label className="field editor-wide">
                    Направление шкалы
                    <select
                      value={String(criterion.higher_is_better)}
                      onChange={(event) =>
                        change({
                          higher_is_better: event.target.value === "true",
                        })
                      }
                    >
                      <option value="true">Больше — лучше</option>
                      <option value="false">Меньше — лучше</option>
                    </select>
                  </label>
                  <label className="field editor-wide">
                    Допустимые ступени
                    <input
                      value={
                        scaleText[criterion.criterion_id] ??
                        criterion.allowed_values?.join(", ") ??
                        ""
                      }
                      onChange={(event) => {
                        setScaleText((current) => ({
                          ...current,
                          [criterion.criterion_id]: event.target.value,
                        }));
                        markChanged();
                      }}
                      placeholder="Например: 0, 4, 7, 10"
                    />
                    <span className="form-help">
                      Через запятую или пробел; дробные числа — с точкой. Без
                      ступеней доступно любое число в пределах шкалы.
                    </span>
                  </label>
                </>
              ) : null}
              {criterion.answer_type === "choice" ? (
                <label className="field editor-wide">
                  Варианты и нормализованные баллы
                  <textarea
                    rows={4}
                    value={
                      choiceText[criterion.criterion_id] ??
                      Object.entries(criterion.choices)
                        .map(([label, score]) => `${label} = ${score}`)
                        .join("\n")
                    }
                    onChange={(event) => {
                      setChoiceText((current) => ({
                        ...current,
                        [criterion.criterion_id]: event.target.value,
                      }));
                      markChanged();
                    }}
                  />
                  <span className="form-help">
                    По одному варианту на строку: «Частично = 0.5». Баллы от 0
                    до 1.
                  </span>
                </label>
              ) : null}
            </div>
          </fieldset>
          <fieldset className="editor-fieldset" disabled={busy}>
            <legend>
              <ShieldCheck size={16} /> Применимость и нарушения
            </legend>
            <div className="editor-field-grid">
              <label className="editor-toggle">
                <input
                  type="checkbox"
                  checked={criterion.na_allowed}
                  onChange={(event) =>
                    change({ na_allowed: event.target.checked })
                  }
                />
                <span>
                  <strong>Допускается N/A</strong>
                  <small>Условие неприменимости задайте в инструкции.</small>
                </span>
              </label>
              <label className="editor-toggle">
                <input
                  type="checkbox"
                  checked={criterion.requires_audio}
                  onChange={(event) =>
                    change({ requires_audio: event.target.checked })
                  }
                />
                <span>
                  <strong>Требуется аудио</strong>
                  <small>Для проверки нужен анализ записи.</small>
                </span>
              </label>
              {criterion.requires_audio ? (
                <p className="editor-inline-note editor-wide">
                  Текстовый оценщик оставит этот критерий со статусом «Нужна
                  проверка»: наличие плеера не означает, что модель прослушала
                  запись.
                </p>
              ) : null}
              <label className="field editor-wide">
                Условие грубой ошибки
                <textarea
                  rows={3}
                  value={criterion.gross_error_rule ?? ""}
                  onChange={(event) => {
                    const rule = event.target.value.trim()
                      ? event.target.value
                      : null;
                    change({
                      gross_error_rule: rule,
                      critical_answer: rule
                        ? (criterion.critical_answer ?? null)
                        : null,
                    });
                  }}
                  placeholder="Какое нарушение нужно отмечать отдельно"
                />
                <span className="form-help">
                  Грубая ошибка отмечается отдельным флагом и не обнуляет итог
                  автоматически.
                </span>
              </label>
              {criterion.answer_type === "boolean" &&
              criterion.gross_error_rule ? (
                <label className="field editor-wide">
                  Ответ, означающий грубую ошибку
                  <select
                    value={
                      criterion.critical_answer == null
                        ? ""
                        : String(criterion.critical_answer)
                    }
                    onChange={(event) =>
                      change({
                        critical_answer:
                          event.target.value === ""
                            ? null
                            : event.target.value === "true",
                      })
                    }
                  >
                    <option value="">Определяется отдельно</option>
                    <option value="true">Да означает нарушение</option>
                    <option value="false">Нет означает нарушение</option>
                  </select>
                </label>
              ) : null}
            </div>
          </fieldset>
        </div>
      </div>
      <footer className="editor-save-bar">
        <div className="editor-save-status">
          <span className={`editor-draft-dot ${dirty ? "is-dirty" : ""}`} />
          <span>
            <strong>{dirty ? "Есть изменения" : "Версия сохранена"}</strong>
            <small>
              {dirty
                ? `Сохранение создаст новую версию после v${checklist.version}`
                : `Чек-лист v${checklist.version} · ${draft.criteria.length} критериев`}
            </small>
          </span>
        </div>
        <div className="editor-save-actions">
          <button
            className="button secondary"
            onClick={() => void save(true)}
            disabled={busy}
          >
            <Copy size={16} /> Сохранить как новый
          </button>
          <button
            className="button primary"
            onClick={() => void save(false)}
            disabled={busy}
          >
            <Save size={16} /> {busy ? "Сохраняем…" : "Сохранить новую версию"}
          </button>
        </div>
      </footer>
    </section>
  );
}
