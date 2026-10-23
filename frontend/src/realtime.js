import { useEffect, useState } from "react";
import { accessApi, eventsApi, presentMetrics } from "./api";
import { mergeRenderProgress } from "./renderProgress";
import { BROWSER_WEBSOCKET_PROTOCOL } from "./requestPolicy";

const MAX_LOG_LINES = 500;

const resync = (queryClient) => {
  queryClient.invalidateQueries({ queryKey: ["jobs"] });
  queryClient.invalidateQueries({ queryKey: ["versions"] });
  queryClient.invalidateQueries({ queryKey: ["official-versions"] });
  queryClient.invalidateQueries({ queryKey: ["devices"] });
  queryClient.invalidateQueries({ queryKey: ["metrics"] });
  queryClient.invalidateQueries({ queryKey: ["frames"] });
  queryClient.invalidateQueries({ queryKey: ["artifacts"] });
  queryClient.invalidateQueries({ queryKey: ["log-tail"] });
};

export function useRenderEvents(queryClient, enabled = true) {
  const [logsByJob, setLogsByJob] = useState({});
  const [progressByJob, setProgressByJob] = useState({});
  const [connectionState, setConnectionState] = useState(enabled ? "connecting" : "mock");

  useEffect(() => {
    if (!enabled) return undefined;
    let closed = false;
    let retryTimer;
    let retryDelay = 500;
    let socket;

    const connect = () => {
      if (closed) return;
      setConnectionState("connecting");
      socket = new WebSocket(eventsApi.url(), BROWSER_WEBSOCKET_PROTOCOL);
      socket.addEventListener("open", () => {
        retryDelay = 500;
        setConnectionState("connected");
        setProgressByJob({});
        socket.send(JSON.stringify({ action: "subscribe", job_ids: null }));
        resync(queryClient);
      });
      socket.addEventListener("message", (message) => {
        let event;
        try {
          event = JSON.parse(message.data);
        } catch {
          return;
        }
        if (event.type === "render.log" && event.job_id && typeof event.line === "string") {
          setLogsByJob((current) => ({
            ...current,
            [event.job_id]: [...(current[event.job_id] ?? []), event.line].slice(-MAX_LOG_LINES),
          }));
          return;
        }
        if (event.type === "system.metrics_updated" && event.metrics) {
          queryClient.setQueryData(["metrics"], presentMetrics(event.metrics));
          return;
        }
        if (event.type === "resync.required") {
          setProgressByJob({});
          resync(queryClient);
          return;
        }
        if (event.type === "render.progress" && event.job_id) {
          setProgressByJob((current) => {
            return {
              ...current,
              [event.job_id]: mergeRenderProgress(current[event.job_id], event),
            };
          });
        }
        if (event.type === "job.status_changed" && event.job_id) {
          setProgressByJob((current) => {
            if (!(event.job_id in current)) return current;
            const next = { ...current };
            delete next[event.job_id];
            return next;
          });
        }
        if (event.type?.startsWith("job.") || event.type === "render.progress") {
          queryClient.invalidateQueries({ queryKey: ["jobs"] });
        }
        if (event.type?.startsWith("render.") && event.type !== "render.progress") {
          queryClient.invalidateQueries({ queryKey: ["frames", event.job_id] });
          queryClient.invalidateQueries({ queryKey: ["artifacts", event.job_id] });
          queryClient.invalidateQueries({ queryKey: ["log-tail", event.job_id] });
        }
        if (event.type?.startsWith("blender.operation_")) {
          queryClient.invalidateQueries({ queryKey: ["versions"] });
          queryClient.invalidateQueries({ queryKey: ["official-versions"] });
        }
      });
      socket.addEventListener("close", async (event) => {
        if (closed) return;
        if (event.code === 4401) {
          try {
            await accessApi.claim();
          } catch {
            setConnectionState("blocked");
            return;
          }
        }
        setConnectionState("reconnecting");
        retryTimer = window.setTimeout(connect, retryDelay);
        retryDelay = Math.min(retryDelay * 2, 10_000);
      });
      socket.addEventListener("error", () => socket.close());
    };

    connect();
    return () => {
      closed = true;
      window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [enabled, queryClient]);

  return { connectionState, logsByJob, progressByJob };
}
