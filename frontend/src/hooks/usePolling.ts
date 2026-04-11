import { useEffect, useRef, useState, useCallback } from "react";

/**
 * Poll a function at `interval` ms while `enabled` is true.
 * Returns the latest result.
 */
export function usePolling<T>(
  fn: () => Promise<T>,
  interval: number,
  enabled: boolean
): { data: T | null; loading: boolean } {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    const tick = async () => {
      setLoading(true);
      try {
        const result = await fnRef.current();
        if (!cancelled) setData(result);
      } catch {
        // ignore
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    tick();
    const id = setInterval(tick, interval);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [interval, enabled]);

  return { data, loading };
}

/**
 * Simple drag-and-drop file handler.
 */
export function useDragDrop(onFiles: (files: File[]) => void) {
  const [dragOver, setDragOver] = useState(false);

  const handlers = {
    onDragOver: useCallback((e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(true);
    }, []),
    onDragLeave: useCallback(() => setDragOver(false), []),
    onDrop: useCallback(
      (e: React.DragEvent) => {
        e.preventDefault();
        setDragOver(false);
        const files = Array.from(e.dataTransfer.files);
        if (files.length) onFiles(files);
      },
      [onFiles]
    ),
  };

  return { dragOver, handlers };
}
