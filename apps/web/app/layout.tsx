import type { Metadata } from "next";
import { Archivo } from "next/font/google";
import "./globals.css";
import { Shell } from "@/components/Shell";

const archivo = Archivo({ subsets: ["latin"], axes: ["wdth"], variable: "--font-archivo", display: "swap" });

export const metadata: Metadata = {
  title: "Analytics Copilot (PoC)",
  description: "Turn business questions into reviewed Tableau dashboards. Synthetic PoC data.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={archivo.variable}>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
