import { useEffect, useRef, useState } from "react";
import { AlertCircle, Download, FileJson, LoaderCircle } from "lucide-react";
import { downloadJson, errorText, request } from "./api";
import type { ProviderInfo, SubmissionFile } from "./contracts";
import Modal from "./Modal";
import Select from "./Select";
import "./SubmissionDialog.css";

type Split = "dev" | "prod";
type Form = {
  team: string;
  group: string;
  split: Split;
  model: string;
  mode: SubmissionFile["model"]["mode"];
};
type Props = {
  config: ProviderInfo | null;
  group: string;
  groupSizes: Map<string, number>;
  onClose: () => void;
  onDownload: (message: string, hasErrors: boolean) => void;
};
const splitFor = (group: string): Split => group.toUpperCase() === "DEV" ? "dev" : "prod";

export default function SubmissionDialog({ config, group, groupSizes, onClose, onDownload }: Props) {
  const [form, setForm] = useState<Form>(() => ({
    team: config?.team_name?.trim() || "SetW",
    group,
    split: splitFor(group),
    model: config?.model ?? "",
    mode: config?.provider === "local" ? "local" : "external_api",
  }));
  const [preview, setPreview] = useState<SubmissionFile | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const previewHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (preview) previewHeading.current?.focus();
  }, [preview]);

  const groupCount = groupSizes.get(form.group) ?? 0;
  const failed = preview?.results.filter((item) => item.status === "error") ?? [];
  const ok = (preview?.results.length ?? 0) - failed.length;

  function update(patch: Partial<Form>) {
    setForm((current) => ({ ...current, ...patch }));
    setPreview(null);
    setError("");
  }

  async function prepare() {
    if (!form.team.trim() || !form.group || !form.model.trim() || controller.current) return;
    const current = new AbortController();
    controller.current = current;
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({
        group_id: form.group,
        model_name: form.model.trim(),
        split: form.split,
        team_name: form.team.trim(),
        model_mode: form.mode,
      });
      const result = await request<SubmissionFile>(`/submission/export?${params}`, { signal: current.signal });
      if (result.schema_version !== "1.0" || !Array.isArray(result.results))
        throw new Error("Сервер вернул файл в неожиданном формате. Повторите подготовку.");
      if (!current.signal.aborted) setPreview(result);
    } catch (reason) {
      if (!current.signal.aborted) setError(errorText(reason));
    } finally {
      controller.current = null;
      if (!current.signal.aborted) setLoading(false);
    }
  }

  function download() {
    if (!preview) return;
    downloadJson(preview, "results.json");
    onDownload(`Файл results.json скачан. Набор ${form.group}, успешных оценок ${ok}, ошибок ${failed.length}. Путь для сдачи — results/${form.split}/results.json.`, failed.length > 0);
  }

  return (
    <Modal title="Файл для сдачи" onClose={onClose}>
      <form
        className="submission-form"
        autoComplete="off"
        aria-busy={loading}
        onSubmit={(event) => {
          event.preventDefault();
          if (preview) download();
          else void prepare();
        }}
      >
        <p className="submission-intro">
          Все диалоги выбранного набора и исходные оценки модели по официальному чек-листу. Формат организаторов JSON 1.0.
        </p>
        {!preview && <div className="submission-fields">
          <label className="field">
            Команда
            <input name="team_name" value={form.team} required disabled={loading} onChange={(event) => update({ team: event.target.value })} />
          </label>
          <label className="field">
            Набор для сдачи
            <Select name="group_id" value={form.group} required disabled={loading} onChange={(event) => update({ group: event.target.value, split: splitFor(event.target.value) })}>
              {[...groupSizes.keys()].map((value) => <option key={value}>{value}</option>)}
            </Select>
          </label>
          <label className="field">
            Раздел сдачи
            <Select name="split" value={form.split} disabled={loading} onChange={(event) => update({ split: event.target.value as Split })}>
              <option value="dev">DEV — 60 чатов и 5 звонков</option>
              <option value="prod">PROD — 10 чатов</option>
            </Select>
          </label>
          <label className="field">
            Режим модели
            <Select name="model_mode" value={form.mode} disabled={loading} onChange={(event) => update({ mode: event.target.value as Form["mode"] })}>
              <option value="external_api">Внешний API</option>
              <option value="local">Локальная модель</option>
              <option value="hybrid">Гибридный</option>
            </Select>
          </label>
          <label className="field submission-model">
            Модель для сдачи
            <input name="model_name" value={form.model} required disabled={loading} spellCheck={false} onChange={(event) => update({ model: event.target.value })} />
          </label>
        </div>}
        {error && <p className="notice error" role="alert"><AlertCircle size={17} aria-hidden="true" />{error}</p>}
        {preview ? (
          <section className="submission-preview" aria-labelledby="submission-preview-heading">
            <div className="submission-preview-title">
              <h3 ref={previewHeading} id="submission-preview-heading" tabIndex={-1}>Файл готов к скачиванию</h3>
              <button type="button" className="text-button" onClick={() => setPreview(null)}>Изменить параметры</button>
            </div>
            <dl className="submission-file-details">
              <div><dt>Команда</dt><dd>{preview.team_name}</dd></div>
              <div><dt>Набор</dt><dd>{form.group}</dd></div>
              <div><dt>Модель</dt><dd>{preview.model.name}</dd></div>
              <div><dt>Путь для сдачи</dt><dd><code>results/{form.split}/results.json</code></dd></div>
            </dl>
            <dl className="submission-totals">
              <div><dt>Записей</dt><dd>{preview.results.length}</dd></div>
              <div className="good"><dt>Успешных</dt><dd>{ok}</dd></div>
              <div className={failed.length ? "bad" : undefined}><dt>С ошибкой</dt><dd>{failed.length}</dd></div>
            </dl>
            {failed.length > 0 && (
              <details className="submission-errors">
                <summary>Посмотреть ошибки ({failed.length})</summary>
                <ul>
                  {failed.map((item) => <li key={item.conversation_id}><strong translate="no">{item.conversation_id}</strong> {item.error}</li>)}
                </ul>
              </details>
            )}
          </section>
        ) : (
          <p className="submission-path"><FileJson size={16} aria-hidden="true" /> <span>{groupCount} записей в наборе <code>results/{form.split}/results.json</code></span></p>
        )}
        <footer className="button-row submission-actions">
          <button className="button primary" type="submit" disabled={loading}>
            {loading ? <span className="spin" aria-hidden="true"><LoaderCircle size={16} /></span>
              : preview ? <Download size={16} aria-hidden="true" /> : <FileJson size={16} aria-hidden="true" />}
            {loading ? "Формируем файл…" : preview ? "Скачать results.json" : "Подготовить файл"}
          </button>
          <button className="button ghost" type="button" onClick={onClose}>Закрыть</button>
        </footer>
      </form>
    </Modal>
  );
}
