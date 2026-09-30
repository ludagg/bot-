"use client";
import { useState } from "react";

export default function ShareButton({ title }: { title: string }) {
  const [done, setDone] = useState(false);
  async function share() {
    const url = window.location.href;
    if (navigator.share) {
      try { await navigator.share({ title, url }); return; } catch { /* annulé */ }
    }
    await navigator.clipboard?.writeText(url);
    setDone(true);
    setTimeout(() => setDone(false), 2000);
  }
  return <button className="btn" onClick={share}>{done ? "Link copied" : "Share"}</button>;
}
