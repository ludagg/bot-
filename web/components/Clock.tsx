"use client";
import { useEffect, useState } from "react";

/** Horloge « First detected » : calculée depuis l'horodatage immuable. */
export default function Clock({ since }: { since: string }) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const start = new Date(since).getTime();
  let label = "…";
  if (now !== null) {
    const s = Math.max(0, Math.floor((now - start) / 1000));
    const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
    label = (d ? `${d}d ` : "") + `${h}h ${m}m ago`;
  }
  return (
    <div className="clock" aria-live="off">
      <span className="clock-label">First detected</span>
      <span className="clock-value">{label}</span>
      <time className="clock-abs" dateTime={since}>{new Date(since).toUTCString()}</time>
    </div>
  );
}
