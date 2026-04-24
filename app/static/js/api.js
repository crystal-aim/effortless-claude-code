// API fetch helper + auth helpers. No UI dependencies.
export async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const msg = typeof data === "object" ? (data.detail || JSON.stringify(data)) : data;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export async function requireLogin() {
  try {
    return await api("/auth/me");
  } catch (e) {
    window.location.href = "/ui/login";
    throw e;
  }
}

export async function logout() {
  try {
    await api("/auth/logout", { method: "POST" });
  } catch {}
  window.location.href = "/ui/login";
}
