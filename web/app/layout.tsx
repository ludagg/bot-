import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

const SITE = process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000";

export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: { default: "SIH — Something Is Happening", template: "%s · SIH" },
  description: "Detects behavior changes across the internet and timestamps the first detection. A signal, not a prediction.",
  openGraph: { siteName: "SIH", type: "website" },
  twitter: { card: "summary_large_image" },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="wrap">
          <header className="top">
            <Link className="brand" href="/">SIH</Link>
            <nav>
              <Link href="/">Live</Link>
              <Link href="/log">Log</Link>
              <Link href="/about">About</Link>
            </nav>
          </header>
          <main>{children}</main>
          <footer>
            A signal, not a prediction. Detections are automated and may be wrong. Links point to original sources; content is not republished.{" "}
            <Link href="/about">Method</Link> · <Link href="/legal">Legal</Link>
          </footer>
        </div>
      </body>
    </html>
  );
}
