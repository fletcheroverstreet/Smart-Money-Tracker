import type { Metadata, Viewport } from "next";
import { Barlow_Condensed, IBM_Plex_Mono, IBM_Plex_Sans } from "next/font/google";

import { QuickLook } from "@/components/drawer/QuickLook";
import { CommandPaletteProvider } from "@/components/search/CommandPalette";
import { AppShell } from "@/components/shell/AppShell";
import { Providers } from "@/components/shell/Providers";
import "./globals.css";

// Self-hosted at build time by next/font: no requests to Google from visitors.
const barlow = Barlow_Condensed({ subsets: ["latin"], weight: ["500", "600"], variable: "--font-barlow" });
const plexSans = IBM_Plex_Sans({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-plex-sans" });
const plexMono = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400"], variable: "--font-plex-mono" });

export const metadata: Metadata = {
  title: { default: "Smart Money Tracker", template: "%s · Smart Money Tracker" },
  description:
    "What insiders, members of Congress and institutions buy and sell, from official public disclosures. Every number links to its filing.",
};

export const viewport: Viewport = {
  themeColor: "#ffffff",
  colorScheme: "light",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${barlow.variable} ${plexSans.variable} ${plexMono.variable}`}>
      <body>
        <Providers>
          <CommandPaletteProvider>
            <AppShell>{children}</AppShell>
            <QuickLook />
          </CommandPaletteProvider>
        </Providers>
      </body>
    </html>
  );
}
