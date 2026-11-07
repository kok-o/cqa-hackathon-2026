import type { CriterionResult } from './contracts';

export const number = (value: number | null | undefined, digits = 1) => value == null ? '—' : new Intl.NumberFormat('ru-RU', { maximumFractionDigits: digits }).format(value);
export const date = (value: string) => new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
export const statusName = (status: string | null) => ({ applicable: 'Оценено', not_applicable: 'Не применимо', needs_review: 'Нужна проверка', complete: 'Готово', incomplete: 'Не завершено', completed: 'Готово', running: 'Анализ', queued: 'В очереди', failed: 'Ошибка' }[status ?? ''] ?? 'Не оценено');
export const answerName = (result: CriterionResult) => result.status !== 'applicable' ? statusName(result.status) : typeof result.answer === 'boolean' ? (result.answer ? 'Да' : 'Нет') : String(result.answer ?? '—');
