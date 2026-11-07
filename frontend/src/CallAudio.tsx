import { useEffect, useRef, useState } from 'react';
import { Headphones, LoaderCircle, RefreshCw } from 'lucide-react';
import { errorText, request } from './api';

type Props = { groupId: string; conversationId: string; hasAudio: boolean; evaluationId?: string };

export default function CallAudio({ groupId, conversationId, hasAudio, evaluationId }: Props) {
  const [url, setUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  async function load() {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setLoading(true); setError(''); setUrl('');
    try {
      const params = new URLSearchParams({ group_id: groupId, conversation_id: conversationId });
      if (evaluationId) params.set('evaluation_id', evaluationId);
      const result = await request<{ audio_url: string }>(`/audio-source?${params}`, { signal: current.signal });
      if (!current.signal.aborted) setUrl(result.audio_url);
    } catch (reason) {
      if (!current.signal.aborted) setError(errorText(reason));
    } finally {
      if (!current.signal.aborted) setLoading(false);
    }
  }

  if (!hasAudio && !url && !error) return null;

  return <section className="call-audio" aria-label="Аудиозапись звонка">
    <div className="audio-player"><Headphones size={16} />
      {url ? <audio key={url} controls preload="metadata" src={url} aria-label="Запись звонка" onError={() => { setError('Запись не воспроизводится. Обновите ссылку; если ошибка повторится, проверьте доступ к записи у организаторов.'); setUrl(''); }} />
        : hasAudio ? <button className="button secondary small" disabled={loading} onClick={() => void load()}>{loading ? <span className="spin" aria-hidden="true"><LoaderCircle size={15} /></span> : error ? <RefreshCw size={15} aria-hidden="true" /> : null}{loading ? 'Подключаем запись…' : error ? 'Обновить ссылку на запись' : 'Открыть запись звонка'}</button>
          : <span className="no-audio-banner">Аудиозапись не предоставлена.</span>}
    </div>
    {error ? <p className="audio-error" role="alert">{error}</p> : null}
  </section>;
}
