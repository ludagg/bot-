import Link from "next/link";
import AutoRefresh from "@/components/AutoRefresh";
import { ago, getCards } from "@/lib/api";

export const dynamic = "force-dynamic"; // jamais pré-rendu au build : l API peut être injoignable
export const revalidate = 60;

export default async function Home() {
  const cards = await getCards();
  return (
    <>
      <AutoRefresh />
      <h1 className="hero">SOMETHING IS HAPPENING</h1>
      <p className="sub">Behavior changes detected across independent communities. A signal, not a prediction.</p>
      {cards === null ? (
        <p className="empty">The data service is unreachable right now. Retrying automatically.</p>
      ) : cards.length === 0 ? (
        <p className="empty">Nothing unusual right now. Detections appear here as soon as several independent communities move together.</p>
      ) : (
        <div className="cards">
          {cards.map((c) => (
            <Link key={c.id} href={`/d/${c.id}`} className={`card ${c.level}`}>
              <div className="name">{c.entity}</div>
              <div className="pct">+{c.change_pct.toLocaleString("en-US")}%</div>
              <div className="meta">
                <span>{c.communities.join(" · ")}</span>
                <span>detected {ago(c.age_seconds)}</span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </>
  );
}
