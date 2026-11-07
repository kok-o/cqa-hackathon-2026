export class ApiError extends Error {
  constructor(message: string, public status: number, public retryAfterMs?: number) { super(message); }
}

export const isTransientError = (error: unknown) => error instanceof ApiError && (error.status === 0 || error.status === 408 || error.status === 429 || error.status >= 500);

async function attempt<T>(path: string, options?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  options?.signal?.addEventListener('abort', abort, { once: true });
  if (options?.signal?.aborted) controller.abort();
  const timeout = setTimeout(abort, 30000);
  let response: Response;
  let text: string;
  try {
    response = await fetch(`/api${path}`, { ...options, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...options?.headers } });
    text = await response.text();
  } catch (error) {
    if (options?.signal?.aborted) throw error;
    throw new ApiError('Нет ответа от сервера. Проверьте соединение и доступность приложения.', 0);
  } finally {
    clearTimeout(timeout);
    options?.signal?.removeEventListener('abort', abort);
  }
  const retryAfter = response.headers.get('Retry-After');
  const delay = retryAfter == null ? undefined : /^\d+(\.\d+)?$/.test(retryAfter.trim()) ? Number(retryAfter) * 1000 : Math.max(0, Date.parse(retryAfter) - Date.now());
  const retryAfterMs = Number.isFinite(delay) ? delay : undefined;
  let data: unknown;
  try { data = text ? JSON.parse(text) : null; } catch { throw new ApiError('Сервер вернул неожиданный ответ. Проверьте, что API запущен.', response.ok ? 0 : response.status, retryAfterMs); }
  if (!response.ok) {
    const body = data as { detail?: unknown; errors?: Array<{ field?: string; message?: string }> } | null;
    const detail = body?.detail;
    const message = typeof detail === 'string' ? detail : Array.isArray(detail)
      ? detail.filter(item => item && typeof item === 'object').map((item: { loc?: unknown; msg?: unknown }) => `${Array.isArray(item.loc) ? item.loc.slice(1).join('.') : ''}: ${typeof item.msg === 'string' ? item.msg : 'Некорректное значение'}`).join('; ')
      : 'Не удалось выполнить запрос. Повторите попытку.';
    const fields = Array.isArray(body?.errors) ? body.errors.slice(0, 5).filter(item => item && typeof item === 'object').map(item => `${typeof item.field === 'string' ? item.field : ''}: ${typeof item.message === 'string' ? item.message : 'Некорректное значение'}`).join('; ') : '';
    throw new ApiError(fields ? `${message}. ${fields}` : message || 'Некорректные данные запроса.', response.status, retryAfterMs);
  }
  return data as T;
}

export async function request<T>(path: string, options?: RequestInit, retrySafe = (options?.method ?? 'GET') === 'GET'): Promise<T> {
  for (let retry = 0; ; retry++) {
    try { return await attempt<T>(path, options); }
    catch (error) {
      if (!retrySafe || retry >= 2 || options?.signal?.aborted || !isTransientError(error)) throw error;
      const delay = (error as ApiError).retryAfterMs ?? 500 * (retry + 1);
      if (delay > 5000) throw error;
      await new Promise(resolve => setTimeout(resolve, delay));
    }
  }
}

export const post = <T>(path: string, body: unknown) => {
  const keyed = typeof body === 'object' && body !== null && 'idempotency_key' in body && typeof body.idempotency_key === 'string' && body.idempotency_key.length > 0;
  return request<T>(path, { method: 'POST', body: JSON.stringify(body) }, keyed);
};
export const errorText = (error: unknown) => error instanceof Error ? error.message : 'Не удалось выполнить действие.';
export const uid = () => crypto.randomUUID();
export const q = (value: string) => encodeURIComponent(value);
export function downloadJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' }));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
