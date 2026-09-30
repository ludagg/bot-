import type { Metadata } from "next";
import { notFound } from "next/navigation";
import AutoRefresh from "@/components/AutoRefresh";
import Chart from "@/components/Chart";
import Clock from "@/components/Clock";
import ShareButton from "@/components/ShareButton";
import { getDetail, getSeries } from "@/lib/api";

export const revalidate = 60;
type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { id } = await params;
  const d = await getDetail(id);
  if (!d) return { title: "Detection not found" };
  const title = `${d.entity}: +${d.change_pct}% — SOMETHING IS HAPPENING`;
  const description = d.headline ?? `Activity around ${d.entity} is moving across ${d.communities.length} independent communities.`;
  return { title, description, openGraph: { title, description }, twitter: { title, description, card: "summary_large_image" } };
}

export default async function Investigate({ params }: Props) {
  const { id } = await params;
  const [d, series] = await Promise.all([getDetail(id), getSeries(id)]);
  if (!d) notFound();
  const ex = d.explanation;
  return (
    <>
      <AutoRefresh />
      <p className="sub">{d.level === "red" ? "High-confidence detection" : "Detection"} · {d.communities.join(" · ")}</p>
      <h1 className="hero" style={{ fontSize: "clamp(26px,7vw,40px)" }}>{d.entity} <span style={{ color: "var(--red)" }}>+{d.change_pct}%</span></h1>
      <Clock since={d.first_detected_at} />
      <ShareButton title={`${d.entity} — SOMETHING IS HAPPENING`} />

      {ex ? (
        <>
          <h2>What seems to be happening</h2>
          <p><strong>{ex.headline}</strong></p>
          <p>{ex.summary}</p>
          {ex.hypotheses.length > 0 && (<ul>{ex.hypotheses.map((h) => <li key={h}>{h}</li>)}</ul>)}
          {ex.timeline.length > 0 && (
            <>
              <h2>Timeline</h2>
              <ol className="timeline">
                {ex.timeline.map((p, i) => (
                  <li key={i}>
                    <time dateTime={p.time}>{new Date(p.time).toUTCString()}</time>
                    {p.event}{" "}
                    <a href={p.source_url} target="_blank" rel="noopener noreferrer nofollow">source ↗</a>
                  </li>
                ))}
              </ol>
            </>
          )}
          {ex.confidence_note && <p className="note">Possible false positive: {ex.confidence_note}</p>}
        </>
      ) : (
        <p className="note">Analysis in progress. The detection above is already timestamped and will not change.</p>
      )}

      {series && (<><h2>Mentions per hour, by community</h2><Chart data={series} /></>)}

      <h2>Publication record</h2>
      {d.log.map((l, i) => (
        <p key={i} className="hash">
          {String(l.snapshot.kind)} · {new Date(l.published_at).toUTCString()}<br />sha256 {l.content_hash}
        </p>
      ))}
      <p className="note">A signal, not a prediction. Automated analysis; hypotheses are not facts.</p>
    </>
  );
}
