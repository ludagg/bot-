export const metadata = { title: "About" };

export default function About() {
  return (
    <>
      <h1 className="hero" style={{ fontSize: 32 }}>How it works</h1>
      <p className="note"><strong>A signal, not a prediction.</strong> SIH reports that something changed. It does not say what will happen next.</p>
      <h2>Method</h2>
      <p>Every 15 minutes we collect titles, links and metrics from Hacker News, GitHub and thousands of tech feeds, grouped into independent communities.</p>
      <p>For each keyword, repository or domain we count mentions per hour and community, then compare the last 6 hours to a 14-day baseline using a robust score (median and MAD). A community "moves" when the score is at least 4, with a minimum of 5 mentions and at least 3× the expected level.</p>
      <p>A detection needs at least <strong>two independent communities</strong> moving together (orange); three or more is red. Fifty blogs from the same ecosystem count as one signal.</p>
      <p>The "First detected" time is recorded once and never changes. Published detections are stored in an append-only log with a hash.</p>
      <p>After detection, a language model writes the explanation from the collected data. It never decides whether a signal exists. Its timeline only cites real source links; its hypotheses are hypotheses, not facts.</p>
      <h2>Limits</h2>
      <p>Bots, republications and scheduled events can look like anomalies. Each page lists what could explain a false positive.</p>
    </>
  );
}
