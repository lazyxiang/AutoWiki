import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AutoWiki",
  description: "AI-powered wiki generator for GitHub repositories",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="font-sans">{children}</body>
    </html>
  );
}
