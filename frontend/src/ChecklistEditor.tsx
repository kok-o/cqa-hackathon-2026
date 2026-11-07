import { useState } from 'react';
import { Plus, Save, Trash2, Copy, FileDown } from 'lucide-react';
import type { ChecklistVersion, Criterion } from './contracts';
import { downloadJson, errorText, post, q, uid } from './api';

type Props = { checklist: ChecklistVersion; onSaved: (value: ChecklistVersion) => void };
const freshCriterion = (): Criterion => ({ criterion_id: `criterion-${uid().slice(0, 8)}`, title: '', instruction: '', weight: 1, answer_type: 'boolean', na_allowed: false, requires_audio: false, scale_min: null, scale_max: null, higher_is_better: true, positive_answer: true, choices: {}, section_id: null, gross_error_rule: null });

export default function ChecklistEditor({ checklist, onSaved }: Props) {
  const [draft, setDraft] = useState<ChecklistVersion>(() => structuredClone(checklist));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [choiceText, setChoiceText] = useState<Record<string, string>>({});
  const change = (index: number, values: Partial<Criterion>) => setDraft(current => ({ ...current, criteria: current.criteria.map((criterion, i) => i === index ? { ...criterion, ...values } : criterion) }));

  async function save(asNew: boolean) {
    setError(''); setNotice('');
    const criteria = draft.criteria.map(criterion => {
      if (criterion.answer_type !== 'choice') return criterion;
      const text = choiceText[criterion.criterion_id];
      if (text == null) return criterion;
      const entries = text.split('\n').filter(line => line.trim()).map(line => {
        const separator = line.lastIndexOf('=');
        if (separator < 1) throw new Error('Варианты указываются по одному в строке: ответ = балл от 0 до 1.');
        const label = line.slice(0, separator).trim();
        const score = Number(line.slice(separator + 1).trim().replace(',', '.'));
        if (!label || !Number.isFinite(score) || score < 0 || score > 1) throw new Error('Баллы вариантов должны быть числами от 0 до 1.');
        return [label, score] as const;
      });
      if (new Set(entries.map(([label]) => label)).size !== entries.length) throw new Error('Названия вариантов не должны повторяться.');
      return { ...criterion, choices: Object.fromEntries(entries) };
    });
    if (!draft.title.trim() || !criteria.length || criteria.some(criterion => !criterion.title.trim() || !criterion.instruction.trim())) {
      setError('Заполните название чек-листа, названия и инструкции всех критериев.'); return;
    }
    setBusy(true);
    try {
      const body = { title: draft.title.trim(), provenance: draft.provenance, criteria };
      const saved = asNew
        ? await post<ChecklistVersion>('/checklists', { ...body, checklist_id: `checklist-${uid().slice(0, 8)}`, version: 1 })
        : await post<ChecklistVersion>(`/checklists/${q(checklist.checklist_id)}/versions`, body);
      setNotice(`Сохранена версия ${saved.version}. Теперь её можно применить к диалогам.`);
      onSaved(saved);
    } catch (error) { setError(errorText(error)); } finally { setBusy(false); }
  }

  function submit(asNew: boolean) { void save(asNew).catch(error => setError(errorText(error))); }

  return <section className="editor-workspace" aria-labelledby="editor-heading">
    <header className="workspace-title"><div><h2 id="editor-heading">Редактор чек-листа</h2><p>Изменения сохраняются в новой версии. Прошлые оценки остаются неизменными.</p></div><button className="button ghost" onClick={() => downloadJson(draft, 'cqa-checklist.json')}><FileDown size={17} /> Скачать</button></header>
    {error ? <div className="notice error" role="alert">{error}</div> : null}
    {notice ? <div className="notice success" role="status">{notice}</div> : null}
    <div className="checklist-meta form-grid">
      <label className="field span-2">Название<input value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })} maxLength={200} /></label>
      <label className="field">Источник<select value={draft.provenance} onChange={event => setDraft({ ...draft, provenance: event.target.value as ChecklistVersion['provenance'] })}><option value="synthetic_example">Демонстрационные правила</option><option value="organizer">Правила организаторов</option></select></label>
      <p className="form-help span-3">Отмечайте правила организаторов только после переноса официального чек-листа. Текущая формула — взвешенное среднее; флаг грубой ошибки не задаёт штраф автоматически.</p>
    </div>
    <div className="criteria-editor-list">
      {draft.criteria.map((criterion, index) => <section className="criterion-editor" key={criterion.criterion_id}>
        <header><div className="criterion-number">{index + 1}</div><h2>{criterion.title || 'Новый критерий'}</h2><button className="icon-button danger-text" aria-label={`Удалить критерий ${criterion.title || index + 1}`} disabled={draft.criteria.length === 1 || busy} onClick={() => setDraft(current => ({ ...current, criteria: current.criteria.filter((_, i) => i !== index) }))}><Trash2 size={17} /></button></header>
        <div className="form-grid">
          <label className="field span-2">Название критерия<input value={criterion.title} onChange={event => change(index, { title: event.target.value })} placeholder="Например, понятность следующего действия" required /></label>
          <label className="field">Вес<input type="number" min="0" step="0.1" value={criterion.weight} onChange={event => change(index, { weight: Number(event.target.value) })} /></label>
          <label className="field span-3">Инструкция для оценки<textarea rows={3} value={criterion.instruction} onChange={event => change(index, { instruction: event.target.value })} placeholder="Что проверять, какие ответы допустимы и когда критерий не применим" required /></label>
          <label className="field">Тип ответа<select value={criterion.answer_type} onChange={event => {
            const type = event.target.value as Criterion['answer_type'];
            change(index, { answer_type: type, scale_min: type === 'scale' ? criterion.scale_min ?? 0 : null, scale_max: type === 'scale' ? criterion.scale_max ?? 2 : null, choices: type === 'choice' ? Object.keys(criterion.choices).length ? criterion.choices : { 'Нет': 0, 'Частично': 0.5, 'Да': 1 } : {} });
          }}><option value="boolean">Да / нет</option><option value="scale">Числовая шкала</option><option value="choice">Варианты ответа</option></select></label>
          <label className="field">Раздел<input value={criterion.section_id ?? ''} onChange={event => change(index, { section_id: event.target.value.trim() || null })} placeholder="Необязательно" /></label>
          {criterion.answer_type === 'boolean' ? <label className="field">Положительный ответ<select value={String(criterion.positive_answer)} onChange={event => change(index, { positive_answer: event.target.value === 'true' })}><option value="true">Да</option><option value="false">Нет</option></select></label> : null}
          {criterion.answer_type === 'scale' ? <><label className="field">Минимум<input type="number" step="any" value={criterion.scale_min ?? 0} onChange={event => change(index, { scale_min: Number(event.target.value) })} /></label><label className="field">Максимум<input type="number" step="any" value={criterion.scale_max ?? 2} onChange={event => change(index, { scale_max: Number(event.target.value) })} /></label><label className="field">Направление шкалы<select value={String(criterion.higher_is_better)} onChange={event => change(index, { higher_is_better: event.target.value === 'true' })}><option value="true">Больше — лучше</option><option value="false">Меньше — лучше</option></select></label></> : null}
          {criterion.answer_type === 'choice' ? <label className="field span-3">Варианты и нормализованные баллы<textarea rows={3} value={choiceText[criterion.criterion_id] ?? Object.entries(criterion.choices).map(([label, score]) => `${label} = ${score}`).join('\n')} onChange={event => setChoiceText(current => ({ ...current, [criterion.criterion_id]: event.target.value }))} /><span className="form-help">Одна строка на вариант: «Частично = 0.5». Баллы от 0 до 1.</span></label> : null}
          <label className="check-field"><input type="checkbox" checked={criterion.na_allowed} onChange={event => change(index, { na_allowed: event.target.checked })} /> Допускается N/A</label>
          <label className="check-field"><input type="checkbox" checked={criterion.requires_audio} onChange={event => change(index, { requires_audio: event.target.checked })} /> Требуется аудио</label>
          <label className="field span-3">Условие грубой ошибки<textarea rows={2} value={criterion.gross_error_rule ?? ''} onChange={event => change(index, { gross_error_rule: event.target.value || null })} placeholder="Необязательно. Опишите, какое нарушение нужно отмечать отдельно." /></label>
          {criterion.requires_audio ? <p className="form-help span-3">Текстовый оценщик не проверяет интонацию и дикцию: критерий будет отмечен как требующий проверки.</p> : null}
        </div>
      </section>)}
    </div>
    <button className="button secondary add-criterion" onClick={() => setDraft(current => ({ ...current, criteria: [...current.criteria, freshCriterion()] }))} disabled={busy}><Plus size={17} /> Добавить критерий</button>
    <footer className="editor-footer"><span>{draft.criteria.length} критериев • исходная версия {checklist.version}</span><div className="button-row"><button className="button secondary" onClick={() => submit(true)} disabled={busy}><Copy size={16} /> Сохранить как новый</button><button className="button primary" onClick={() => submit(false)} disabled={busy}><Save size={16} /> {busy ? 'Сохраняем…' : 'Сохранить новую версию'}</button></div></footer>
  </section>;
}
