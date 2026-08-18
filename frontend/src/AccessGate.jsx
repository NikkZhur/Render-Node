import { useCallback, useEffect, useState } from "react";

import { bootstrapBrowserAccess, hasPairingFragment } from "./accessBootstrap";
import { accessApi } from "./api";

let pendingAccessBootstrap = null;

function beginAccessBootstrap() {
  pendingAccessBootstrap ??= bootstrapBrowserAccess(accessApi).finally(() => {
    pendingAccessBootstrap = null;
  });
  return pendingAccessBootstrap;
}

function AccessScreen({ error, onRetry }) {
  const locked = error?.code === "node_already_claimed";
  return (
    <main className="access-screen">
      <section className="access-card" aria-live="polite">
        <div className="access-brand-mark" aria-hidden="true"><span /><span /><span /></div>
        {locked ? (
          <>
            <h1>This Render Node is already paired with another browser.</h1>
            <p>
              Ask an authorized user to share a pairing link, or run<br />
              <code>render-node reset-access</code> on the node.
            </p>
          </>
        ) : error ? (
          <>
            <h1>Could not connect to Render Node.</h1>
            <p>{error.message}</p>
          </>
        ) : (
          <>
            <div className="access-spinner" aria-hidden="true" />
            <h1>Connecting to Render Node…</h1>
            <p>Establishing a protected browser session.</p>
          </>
        )}
        {error && <button type="button" onClick={onRetry}>Retry</button>}
      </section>
    </main>
  );
}

export default function AccessGate({ children, bypass }) {
  const [state, setState] = useState(() => bypass
    ? { status: "ready", error: null }
    : { status: "connecting", error: null });

  const retry = useCallback(() => {
    setState({ status: "connecting", error: null });
    beginAccessBootstrap().then(
      () => setState({ status: "ready", error: null }),
      (error) => setState({ status: "error", error }),
    );
  }, []);

  useEffect(() => {
    if (bypass) return undefined;
    let active = true;
    beginAccessBootstrap().then(
      () => { if (active) setState({ status: "ready", error: null }); },
      (error) => { if (active) setState({ status: "error", error }); },
    );
    return () => { active = false; };
  }, [bypass]);

  useEffect(() => {
    if (bypass) return undefined;
    const handlePairingNavigation = () => {
      if (hasPairingFragment()) retry();
    };
    window.addEventListener("hashchange", handlePairingNavigation);
    return () => window.removeEventListener("hashchange", handlePairingNavigation);
  }, [bypass, retry]);

  if (state.status === "ready") return children;
  return <AccessScreen error={state.error} onRetry={retry} />;
}
