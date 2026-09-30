"use client";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Rafraîchit les données serveur toutes les 60 secondes. */
export default function AutoRefresh() {
  const router = useRouter();
  useEffect(() => {
    const t = setInterval(() => router.refresh(), 60_000);
    return () => clearInterval(t);
  }, [router]);
  return null;
}
