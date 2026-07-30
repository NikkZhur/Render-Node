import { useEffect, useState } from "react";
import { eventsApi, isMockMode, presentMetrics } from "./api";

const MAX_LOG_LINES = 500;

const resync = (queryClient) => {
  queryClient.invalidateQueries({ queryKey: ["jobs"] });
  queryClient.invalidateQueries({ queryKey: ["versions"] });
  queryClient.invalidateQueries({ queryKey: ["official-versions"] });
  queryClient.invalidateQueries({ queryKey: ["metrics"] });
  queryClient.invalidateQueries({ queryKey: ["frames"] });
  queryClient.invalidateQueries({ queryKey: ["artifacts"] });
  queryClient.invalidateQueries({ queryKey: ["log-tail"] });
};

export function useRenderEvents(queryClient) {
  const [logsByJob, setLogsByJob] = useState({});
  const [connectionState, setConnectionState] = useState(isMockMode ? "mock" : "connecting");

  useEffect(() => {
    if (isMockMode) return undefined;
    let closed = false;
    let retryTimer;
    let retryDelay = 500;
    let socket;

    const connect = () => {
      if (closed) return;
      setConnectionState("connecting");
      socket = new WebSocket(eventsApi.url());
      socket.addEventListener("open", () => {
        retryDelay = 500;
        setConnectionState("connected");
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
          resync(queryClient);
          return;
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
      socket.addEventListener("close", () => {
        if (closed) return;
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
  }, [queryClient]);

  return { connectionState, logsByJob };
}
