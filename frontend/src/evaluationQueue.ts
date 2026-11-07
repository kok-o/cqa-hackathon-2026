// Per-tab recovery stores identities, never transcripts or provider credentials.
export type QueueItem = { group_id: string; conversation_id: string; key: string; job_id?: string; done: boolean; failed: boolean };
export type SavedQueue = { version: 1; checklist_id: string; checklist_version: number; model: string; prompt_version: string; items: QueueItem[] };
const storageKey = 'cqa.pending-evaluations.v1';
const nonblank = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;

export function readQueue(): SavedQueue | null {
  try {
    const value = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null');
    if (!value || value.version !== 1 || !nonblank(value.checklist_id) || !Number.isInteger(value.checklist_version) || value.checklist_version < 1 || !nonblank(value.model) || typeof value.prompt_version !== 'string' || !Array.isArray(value.items) || !value.items.length || value.items.length > 10000) return null;
    if (!value.items.every((item: QueueItem) => item && nonblank(item.group_id) && nonblank(item.conversation_id) && nonblank(item.key) && typeof item.done === 'boolean' && typeof item.failed === 'boolean' && (item.job_id === undefined || nonblank(item.job_id)))) return null;
    if (new Set(value.items.map((item: QueueItem) => item.key)).size !== value.items.length) return null;
    return value.items.some((item: QueueItem) => !item.done) ? value : null;
  } catch { return null; }
}

export function saveQueue(value: SavedQueue | null): boolean {
  try {
    if (value?.items.some(item => !item.done)) sessionStorage.setItem(storageKey, JSON.stringify(value));
    else sessionStorage.removeItem(storageKey);
    return true;
  } catch { return false; }
}
