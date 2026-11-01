'use client'

import * as React from 'react'
import {
  ThemeProvider as NextThemesProvider,
  useTheme,
} from 'next-themes'

type ThemeProviderProps = React.ComponentProps<typeof NextThemesProvider>

/** The app switches theme with a class, so the media-query metas alone can disagree with it. */
function ThemeColorSync() {
  const { resolvedTheme } = useTheme()
  React.useEffect(() => {
    if (!resolvedTheme) return
    const color = resolvedTheme === 'dark' ? '#0f1115' : '#eff0f3'
    document.querySelectorAll('meta[name="theme-color"]').forEach((meta) => meta.setAttribute('content', color))
  }, [resolvedTheme])
  return null
}

export function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  return (
    <NextThemesProvider {...props}>
      <ThemeColorSync />
      {children}
    </NextThemesProvider>
  )
}

