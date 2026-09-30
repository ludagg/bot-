import Link from "next/link";
import { getLog } from "@/lib/api";

export const dynamic = "force-dynamic"; // jamais pré-rendu au build : l API peut être injoignable
export const revalidate = 60;
export const metadata = { title: "Log" };

export default async function LogPage() {
  const log = await getLog();
  return (
    <>
      <h1 className="hero" style={{ fontSize: 32 }}>Public log</h1>
      <p className="sub">Append-only. Each entry is frozen with its original timestamp and a SHA-256 hash; later updates are new lines, never edits.</p>
      {!log || log.length === 0 ? (
        <p className="empty">No published detections yet.</p>
      ) : (
        <table className="log">
          <thead><tr><th>Published</th><th>Entity</th><th>Type</th></tr></thead>
          <tbody>
            {log.map((e) => (
              <tr key={e.id}>
                <td>{new Date(e.published_at).toUTCString().slice(5, 22)}<div className="hash">{e.content_hash.slice(0, 16)}…</div></td>
                <td><Link href={`/d/${e.detection_id}`}>{e.snapshot.entity?.name}</Link><div className="hash">first detected {String(e.snapshot.first_detected_at).slice(0, 16).replace("T", " ")} UTC</div></td>
                <td>{e.snapshot.kind}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
