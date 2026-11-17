// When deployed, VITE_API_BASE is the address of the backend. In development it is empty and Vite forwards /api.
const BASE = import.meta.env.VITE_API_BASE || "";

async function request(path, options) {
  const res = await fetch(BASE + path, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // A list of validation problems (status 422) becomes one readable sentence
    const detail = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join("; ") : body.detail;
    const error = new Error(detail || `Request failed (${res.status})`);
    error.status = res.status; // lets a page show a plain message for 501, 429, 422 ...
    throw error;
  }
  return res.json();
}

export const getOverview = () => request("/api/overview");
export const getProduct = (id) => request(`/api/product/${id}`);
export const runAnalysis = (id, market) => request(`/api/analyze/${id}/${market}`, { method: "POST" });
export const getHealth = () => request("/api/health");
// Custom refit of one product with the chosen options. `signal` lets the page cancel a running "Refit all".
// `curves` also asks for the custom profit curve of each market (only the single-product result draws it).
export const runRefit = (id, options, signal, curves = false) =>
  request(`/api/refit/${id}${curves ? "?curves=true" : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
    signal,
  });
