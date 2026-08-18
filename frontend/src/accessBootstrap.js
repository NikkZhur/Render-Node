export function hasPairingFragment(location = window.location) {
  if (!location.hash) return false;
  return new URLSearchParams(location.hash.slice(1)).has("pair");
}

export function takePairingToken(location = window.location, history = window.history) {
  if (!location.hash) return null;
  const values = new URLSearchParams(location.hash.slice(1)).getAll("pair");
  if (values.length === 0) return null;

  history.replaceState(history.state, "", `${location.pathname}${location.search}`);
  return values.length === 1 ? values[0] : "";
}

export async function bootstrapBrowserAccess(accessApi, browser = window) {
  const token = takePairingToken(browser.location, browser.history);
  if (token !== null) return accessApi.pair(token);
  return accessApi.claim();
}

export function createPairingUrl(token, origin = window.location.origin) {
  const url = new URL("/", origin);
  url.hash = `pair=${encodeURIComponent(token)}`;
  return url.toString();
}
