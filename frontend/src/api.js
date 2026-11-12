// When deployed, VITE_API_BASE is the address of the backend. In development it is empty and Vite forwards /api.
const BASE = import.meta.env.VITE_API_BASE || "";

async function request(path, options) {
  const res = await fetch(BASE + path, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export const getOverview = () => request("/api/overview");
export const getProduct = (id) => request(`/api/product/${id}`);
export const runAnalysis = (id, market) => request(`/api/analyze/${id}/${market}`, { method: "POST" });
