"use client"

import { useState, useEffect } from "react"
import { Sidebar } from "@/components/sidebar"
import { Header, HeaderContext } from "@/components/header"
import { SettingsForm } from "@/components/settings-form"
import { DiagnosticsPanel } from "@/components/diagnostics-panel"
import { AboutSection } from "@/components/about-section"
import { StatusOverview } from "@/components/status-overview"
import { useToast } from "@/hooks/use-toast"
import type { AppSettings } from "@/lib/types"
import { fetchSettings } from "@/lib/api"
import { Loader2 } from "lucide-react"

export function Dashboard() {
  const [activeView, setActiveView] = useState<string>("overview")
  const [settings, setSettings] = useState<AppSettings | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const { toast } = useToast()

  useEffect(() => {
    const loadSettings = async () => {
      try {
        setIsLoading(true)
        const data = await fetchSettings()
        setSettings(data)
      } catch (err) {
        toast({
          variant: "destructive",
          title: "Error loading settings",
          description: err instanceof Error ? err.message : "An unknown error occurred",
        })
        setSettings(null)
      } finally {
        setIsLoading(false)
      }
    }

    loadSettings()
  }, [toast])

  
  const handleSettingsSaved = () => {
  }

  const renderContent = () => {
    if (isLoading) {
      return (
        <div className="flex h-[80vh] w-full items-center justify-center">
          <div className="flex flex-col items-center gap-2">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
            <p className="text-sm text-muted-foreground">Loading ChannelWatch...</p>
          </div>
        </div>
      )
    }

    switch (activeView) {
      case "overview":
        return <StatusOverview settings={settings} onNavigate={setActiveView} />
      case "settings":
        return <SettingsForm onSettingsSaved={handleSettingsSaved} />
      case "diagnostics":
        return <DiagnosticsPanel />
      case "about":
        return <AboutSection />
      default:
        return <StatusOverview settings={settings} onNavigate={setActiveView} />
    }
  }

  return (
    <div className="flex min-h-screen">
      <HeaderContext.Provider value={{ activeView, setActiveView }}>
        <Sidebar activeView={activeView} setActiveView={setActiveView} />
        <div className="flex flex-col flex-1 overflow-hidden relative">
          <Header />
          <main className="flex-1 overflow-y-auto overflow-x-hidden p-3 pt-24 md:p-6 md:pt-24">{renderContent()}</main>
        </div>
      </HeaderContext.Provider>
    </div>
  )
}


