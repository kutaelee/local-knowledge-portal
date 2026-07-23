import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Local Knowledge Portal",
  description: "A local-first, provenance-preserving knowledge workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <body><Providers>{children}</Providers></body>
    </html>
  );
}
