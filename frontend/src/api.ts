import type { SolveRequest, SolveResponse } from "./types";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export async function solveInstance(
  req: SolveRequest,
  signal?: AbortSignal,
): Promise<SolveResponse> {
  const res = await fetch(`${API_BASE}/api/solve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
    signal,
  });
  // 422 carries the itemized validation payload; other failures are
  // transport-level and surfaced as a thrown error.
  const data = await res.json();
  return data as SolveResponse;
}

export async function fetchHealth(): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/health`);
    return res.ok;
  } catch {
    return false;
  }
}
