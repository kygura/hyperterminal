import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { BottomTicker } from "@/components/shell/BottomTicker";
import { TopNav } from "@/components/shell/TopNav";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const jetBrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Hypertrade",
  description: "Signal viewer and portfolio branching terminal scaffold.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${inter.variable} ${jetBrainsMono.variable} bg-[--bg-body] text-[--text-primary] antialiased`}
      >
        <TopNav />
        <main className="mx-auto min-h-screen max-w-[1600px] px-4 pb-12 pt-14 sm:px-6">
          {children}
        </main>
        <BottomTicker />
      </body>
    </html>
  );
}
