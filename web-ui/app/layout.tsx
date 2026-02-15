import type { Metadata } from "next";
import { Syncopate } from "next/font/google";
import "./globals.css";

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

export const viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body className={`${syncopate.variable} font-mono`}>
        {children}
      </body>
    </html>
  );
}
