import type { Series } from "@/lib/api";

const COLORS = ["#ff4d4f", "#ffa940", "#40a9ff", "#73d13d", "#b37feb", "#36cfc9"];

/** Courbes horaires par communauté (SVG côté serveur), avec repère « first detected ». */
export default function Chart({ data }: { data: Series }) {
  const W = 640, H = 220, P = 28;
  const names = Object.keys(data.series);
  const n = data.hours.length;
  const max = Math.max(1, ...names.flatMap((k) => data.series[k]));
  const x = (i: number) => P + (i / Math.max(1, n - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - (v / max) * (H - 2 * P);
  const detIdx = data.hours.findIndex((h) => new Date(h) >= new Date(data.first_detected_at));
  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Mentions per hour by community">
        <line x1={P} y1={H - P} x2={W - P} y2={H - P} stroke="var(--line)" />
        <text x={P} y={12} className="axis">{max} /h</text>
        {detIdx >= 0 && (
          <g>
            <line x1={x(detIdx)} x2={x(detIdx)} y1={P - 8} y2={H - P} stroke="var(--red)" strokeDasharray="4 3" />
            <text x={x(detIdx) + 4} y={P} className="axis">first detected</text>
          </g>
        )}
        {names.map((k, j) => (
          <polyline key={k} fill="none" stroke={COLORS[j % COLORS.length]} strokeWidth="2" strokeLinejoin="round"
            points={data.series[k].map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")} />
        ))}
      </svg>
      <figcaption className="legend">
        {names.map((k, j) => (
          <span key={k}><i style={{ background: COLORS[j % COLORS.length] }} />{k}</span>
        ))}
      </figcaption>
    </figure>
  );
}
