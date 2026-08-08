import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AI Product Investigator",
  description:
    "Type a product idea, get an evidence-backed discovery report where every sentence cites a verbatim span in a fetched page.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
