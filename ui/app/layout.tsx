import type React from "react"
import { ThemeProvider } from "@/components/theme-provider"
import { Toaster } from "@/components/base/toaster"
import "@/app/globals.css"
import { Mona_Sans as FontSans } from "next/font/google"
import { cn } from "@/lib/utils"
import { Suspense } from 'react'
import { Metadata } from 'next'

const fontSans = FontSans({
  subsets: ["latin"],
  variable: "--font-sans",
})

export const metadata: Metadata = {
  title: "ChannelWatch",
  description: "Real-time monitoring and alerts for your Channels DVR",
  generator: 'CoderLuii',
  icons: {
    icon: '/favicon.png',
    apple: '/favicon.png'
  },
  openGraph: {
    title: "ChannelWatch",
    description: "Real-time monitoring and alerts for your Channels DVR",
    siteName: "ChannelWatch",
    images: [{
      url: '/og-image.png',
      width: 1200,
      height: 630,
      alt: 'ChannelWatch'
    }],
    locale: 'en_US',
    type: 'website',
  }
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
        <link rel="icon" href="/favicon.png" />
        <link rel="apple-touch-icon" href="/favicon.png" />
        
        <meta property="og:type" content="website" />
        <meta property="og:title" content="ChannelWatch" />
        <meta property="og:description" content="Real-time monitoring and alerts for your Channels DVR" />
        <meta property="og:image" content="https://demo.luiverse.com/og-image.png" />
        <meta property="og:image:width" content="1200" />
        <meta property="og:image:height" content="630" />
        <meta property="og:image:alt" content="ChannelWatch" />
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

import './globals.css'
