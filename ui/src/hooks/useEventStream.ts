import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { keysFor } from "../lib/invalidation";
import type { RunEvent } from "../types";

/** Subscribe to /api/stream and refresh the affected queries (batched every 250 ms). */
export function useEventStream(): boolean {
  const client = useQueryClient();
  const [live, setLive] = useState(false);
  useEffect(() => {
    const source = new EventSource("/api/stream");
    const pending = new Set<string>();
    let timer: number | undefined;
    const flush = () => {
      timer = undefined;
      for (const key of pending) void client.invalidateQueries({ queryKey: JSON.parse(key) });
      pending.clear();
    };
    source.onopen = () => setLive(true);
    source.onerror = () => setLive(false);
    source.onmessage = (message) => {
      const event = JSON.parse(message.data) as RunEvent;
      for (const key of keysFor(event)) pending.add(JSON.stringify(key));
      timer ??= window.setTimeout(flush, 250);
    };
    return () => {
      source.close();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [client]);
  return live;
}
