import type { Metadata } from "next";
import { JetBrains_Mono, Syncopate } from "next/font/google";
import "./globals.css";

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

const syncopate = Syncopate({
  subsets: ["latin"],
  variable: "--font-display",
  weight: ["400", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Universal Agent - Neural Operations Center",
  description: "AGI-era universal agent interface with real-time monitoring and work product visualization",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className={`${jetbrainsMono.variable} ${syncopate.variable} font-mono`}>
        {children}
      </body>
    </html>
  );
}
