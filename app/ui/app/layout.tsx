import type React from "react"
import { ThemeProvider } from "@/components/theme-provider"
import { Toaster } from "@/components/base/toaster"
import "@/app/globals.css"
import { Mona_Sans as FontSans } from "next/font/google"
import { cn } from "@/lib/utils"
import { Suspense } from "react"
import type { Metadata } from "next"

const fontSans = FontSans({
  subsets: ["latin"],
  variable: "--font-sans",
})

const metadataBaseUrl = process.env.NEXT_PUBLIC_APP_URL ?? "http://localhost:8501"

export const metadata: Metadata = {
  title: "ChannelWatch",
  description: "Real-time monitoring and alerts for your Channels DVR",
  metadataBase: new URL(metadataBaseUrl),
  generator: "CoderLuii",
  icons: {
    icon: "/favicon.png",
    apple: "/favicon.png",
  },
  openGraph: {
    title: "ChannelWatch",
    description: "Real-time monitoring and alerts for your Channels DVR",
    siteName: "ChannelWatch",
    images: [{
      url: "/og-image.png",
      width: 1200,
      height: 630,
      alt: "ChannelWatch",
    }],
    locale: "en_US",
    type: "website",
  },
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      </head>
      <body className={cn("min-h-screen bg-background font-sans antialiased", fontSans.variable)}>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem disableTransitionOnChange>
          <Suspense fallback={<div>Loading page...</div>}>
            {children}
          </Suspense>
          <Toaster />
          <Suspense fallback={null} />
        </ThemeProvider>
      </body>
    </html>
  )
}
