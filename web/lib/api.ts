const API = process.env.API_URL ?? "http://localhost:8000";

export type Card = {
  id: number; entity: string; kind: string; level: "red" | "orange"; change_pct: number;
  communities: string[]; confidence: number; status: string; first_detected_at: string;
  age_seconds: number; headline: string | null; content_hash: string | null;
};
export type Explanation = {
  headline: string; summary: string; hypotheses: string[]; confidence_note: string;
  timeline: { time: string; event: string; source_url: string }[];
};
export type Detail = Card & {
  explanation: Explanation | null;
  log: { channel: string; snapshot: Record<string, unknown>; content_hash: string; published_at: string }[];
};
export type Series = { hours: string[]; first_detected_at: string; series: Record<string, number[]> };
export type LogEntry = {
  id: number; detection_id: number; snapshot: Record<string, any>; content_hash: string; published_at: string;
};

async function get<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${API}${path}`, { next: { revalidate: 60 } });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

export const getCards = () => get<Card[]>("/api/detections?status=open&limit=20");
export const getDetail = (id: string) => get<Detail>(`/api/detections/${id}`);
export const getSeries = (id: string) => get<Series>(`/api/detections/${id}/series`);
export const getLog = () => get<LogEntry[]>("/api/log?limit=200");

export function ago(seconds: number): string {
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${Math.max(m, 1)} min ago`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h}h ${m % 60}m ago` : `${Math.floor(h / 24)} d ago`;
}
