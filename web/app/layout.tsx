import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Product Investigator",
  description:
    "Type a product idea, get an evidence-backed discovery report where every sentence cites a verbatim span in a fetched page.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <nav className="site-nav" aria-label="Main navigation">
            <Link className="brand" href="/"><span className="brand-mark">pi</span>Product Investigator</Link>
            <Link className="nav-link" href="/new">New investigation</Link>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}
