export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  const text = await response.text();
  let data: unknown;
  try { data = text ? JSON.parse(text) : null; } catch { throw new ApiError('Сервер вернул неожиданный ответ. Проверьте, что API запущен.', response.status); }
  if (!response.ok) {
    const body = data as { detail?: unknown; errors?: Array<{ field?: string; message?: string }> } | null;
    const detail = body?.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.map((item: { loc?: string[]; msg?: string }) => `${item.loc?.slice(1).join('.') ?? ''}: ${item.msg ?? 'Некорректное значение'}`).join('; ')
      : 'Не удалось выполнить запрос. Повторите попытку.';
    const fields = Array.isArray(body?.errors) ? body.errors.slice(0, 5).map(item => `${item.field ?? ''}: ${item.message ?? 'Некорректное значение'}`).join('; ') : '';
    throw new ApiError(fields ? `${message}. ${fields}` : message, response.status);
  }
  return data as T;
}

export const post = <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body: JSON.stringify(body) });
export const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить действие.';
export const uid = () => crypto.randomUUID();
export const q = (value: string) => encodeURIComponent(value);
export function downloadJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
