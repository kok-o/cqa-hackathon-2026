import { useEffect, useRef, useState } from "react";

export const DEFAULT_QUEUE_WIDTH = 300;
const MIN_WIDTH = 240;
const MAX_WIDTH = 560;
const STORAGE_KEY = "cqa.queue-width";

export function savedQueueWidth() {
  try {
    const width = Number(localStorage.getItem(STORAGE_KEY));
    return width >= MIN_WIDTH && width <= MAX_WIDTH ? width : DEFAULT_QUEUE_WIDTH;
  } catch {
    return DEFAULT_QUEUE_WIDTH;
  }
}

function rememberWidth(width: number) {
  try {
    localStorage.setItem(STORAGE_KEY, String(width));
  } catch {
    // Resizing still works if browser storage is unavailable.
  }
}

export default function QueueResize({ onChange }: { onChange: (width: number) => void }) {
  const handle = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; x: number; start: number; width: number } | null>(null);
  const [bounds, setBounds] = useState({ width: DEFAULT_QUEUE_WIDTH, max: MAX_WIDTH });
  const [resizing, setResizing] = useState(false);
  const clamp = (width: number) => Math.round(Math.max(MIN_WIDTH, Math.min(bounds.max, width)));

  useEffect(() => {
    const sidebar = handle.current?.closest<HTMLElement>(".conversation-sidebar");
    const workspace = sidebar?.closest<HTMLElement>(".review-workspace");
    const assessment = workspace?.querySelector<HTMLElement>(".assessment-pane");
    if (!sidebar || !workspace || !assessment) return;
    const observer = new ResizeObserver(() => {
      setBounds({
        width: Math.round(sidebar.getBoundingClientRect().width),
        max: Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, workspace.clientWidth - assessment.offsetWidth - 320)),
      });
    });
    observer.observe(workspace);
    observer.observe(sidebar);
    return () => observer.disconnect();
  }, []);

  function cancel() {
    if (!drag.current) return;
    onChange(drag.current.start);
    drag.current = null;
    setResizing(false);
  }

  return (
    <div
      ref={handle}
      className="queue-resize-handle"
      role="separator"
      aria-label="Ширина очереди"
      aria-orientation="vertical"
      aria-valuemin={MIN_WIDTH}
      aria-valuemax={bounds.max}
      aria-valuenow={Math.min(bounds.max, Math.max(MIN_WIDTH, bounds.width))}
      aria-valuetext={`${bounds.width} пикселей`}
      aria-controls="queue-panel"
      title="Потяните для изменения ширины. Двойной щелчок — сброс."
      tabIndex={0}
      data-resizing={resizing}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.focus();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { id: event.pointerId, x: event.clientX, start: bounds.width, width: bounds.width };
        setResizing(true);
      }}
      onPointerMove={(event) => {
        if (!drag.current || drag.current.id !== event.pointerId) return;
        const width = clamp(drag.current.start + event.clientX - drag.current.x);
        drag.current.width = width;
        onChange(width);
      }}
      onPointerUp={(event) => {
        if (!drag.current || drag.current.id !== event.pointerId) return;
        rememberWidth(drag.current.width);
        drag.current = null;
        setResizing(false);
        event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onPointerCancel={cancel}
      onLostPointerCapture={cancel}
      onDoubleClick={() => {
        const width = clamp(DEFAULT_QUEUE_WIDTH);
        onChange(width);
        rememberWidth(width);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape" && drag.current) {
          event.preventDefault();
          const id = drag.current.id;
          cancel();
          event.currentTarget.releasePointerCapture(id);
          return;
        }
        const step = event.shiftKey ? 48 : 16;
        const next = event.key === "ArrowLeft" ? bounds.width - step
          : event.key === "ArrowRight" ? bounds.width + step
          : event.key === "Home" ? MIN_WIDTH
          : event.key === "End" ? bounds.max : null;
        if (next == null) return;
        event.preventDefault();
        const width = clamp(next);
        onChange(width);
        rememberWidth(width);
      }}
    />
  );
}
