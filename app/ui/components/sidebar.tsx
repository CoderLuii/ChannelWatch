"use client"

import { useEffect, useState, type ComponentType } from "react"
import {
  Bell,
  ChevronRight,
  HeartPulse,
  History,
  Home,
  Info,
  LifeBuoy,
  Settings,
  X,
} from "lucide-react"

import { Button } from "@/components/base/button"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/base/tooltip"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"

interface SidebarProps {
  activeView: string
  setActiveView: (view: string) => void
  isMobile?: boolean
}

interface NavigationItem {
  view: string
  labelKey: string
  icon: ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" | "false" }>
  matches?: (activeView: string) => boolean
}

const primaryNavigation: NavigationItem[] = [
  { view: "overview", labelKey: "nav.dashboard", icon: Home },
  { view: "watch-history", labelKey: "nav.watchHistory", icon: History },
  { view: "settings", labelKey: "nav.settings", icon: Settings, matches: (view) => view.startsWith("settings") },
  { view: "notification-log", labelKey: "nav.notificationLog", icon: Bell },
  { view: "diagnostics", labelKey: "nav.diagnostics", icon: HeartPulse },
]

const utilityNavigation: NavigationItem[] = [
  { view: "help-feedback", labelKey: "nav.helpFeedback", icon: LifeBuoy },
  { view: "about", labelKey: "nav.about", icon: Info },
]

export function Sidebar({ activeView, setActiveView, isMobile: propIsMobile }: SidebarProps) {
  const [isMobile, setIsMobile] = useState(propIsMobile || false)
  const [isOpen, setIsOpen] = useState(false)
  const [isCollapsed, setIsCollapsed] = useState(true)

  useEffect(() => {
    if (propIsMobile !== undefined) {
      setIsMobile(propIsMobile)
      return
    }
    const checkIsMobile = () => setIsMobile(window.innerWidth < 768)
    checkIsMobile()
    window.addEventListener("resize", checkIsMobile)
    return () => window.removeEventListener("resize", checkIsMobile)
  }, [propIsMobile])

  useEffect(() => {
    const handler = () => setIsOpen((current) => !current)
    window.addEventListener("toggle-mobile-sidebar", handler)
    return () => window.removeEventListener("toggle-mobile-sidebar", handler)
  }, [])

  useEffect(() => {
    if (isMobile) setIsOpen(false)
  }, [activeView, isMobile])

  useEffect(() => {
    if (!isMobile || !isOpen) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsOpen(false)
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [isMobile, isOpen])

  const renderNavigationItem = (item: NavigationItem) => {
    const selected = item.matches ? item.matches(activeView) : activeView === item.view
    const label = t(item.labelKey)
    const Icon = item.icon
    const button = (
      <Button
        key={item.view}
        type="button"
        variant={selected ? "default" : "ghost"}
        size="sm"
        aria-label={label}
        aria-current={selected ? "page" : undefined}
        className={cn(
          "min-h-11 w-full",
          !isMobile && isCollapsed ? "justify-center px-2" : "justify-start",
        )}
        onClick={() => setActiveView(item.view)}
      >
        <Icon className={cn("h-4 w-4 shrink-0", !isMobile && isCollapsed ? "" : "mr-2")} aria-hidden="true" />
        {(!isMobile && !isCollapsed) || isMobile ? label : null}
      </Button>
    )
    if (isMobile || !isCollapsed) return button
    return (
      <Tooltip key={item.view}>
        <TooltipTrigger asChild>{button}</TooltipTrigger>
        <TooltipContent side="right">{label}</TooltipContent>
      </Tooltip>
    )
  }

  const collapseLabel = isCollapsed ? t("sidebar.expandSidebar") : t("sidebar.collapseSidebar")
  const collapseButton = (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className={cn("min-h-11 w-full", isCollapsed ? "justify-center px-2" : "justify-start")}
      onClick={() => setIsCollapsed((current) => !current)}
      aria-label={collapseLabel}
      aria-expanded={!isCollapsed}
    >
      <ChevronRight
        className={cn(
          "h-4 w-4 transition-transform motion-reduce:transition-none",
          isCollapsed ? "" : "mr-2 rotate-180",
        )}
        aria-hidden="true"
      />
      {!isCollapsed ? collapseLabel : null}
    </Button>
  )

  return (
    <TooltipProvider delayDuration={300}>
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-64 flex-col border-r border-border bg-background",
          isOpen ? "translate-x-0" : "-translate-x-full md:translate-x-0",
          !isMobile && isCollapsed ? "md:w-[60px]" : "md:w-64",
          "transition-all duration-200 ease-in-out motion-reduce:transition-none",
        )}
        role={isMobile ? "dialog" : undefined}
        aria-modal={isMobile ? true : undefined}
        aria-label={isMobile ? t("sidebar.navigation") : undefined}
        aria-hidden={isMobile && !isOpen ? true : undefined}
        inert={isMobile && !isOpen}
      >
        <div className={cn(
          "flex min-h-14 shrink-0 items-center border-b p-2",
          !isMobile && isCollapsed ? "justify-center" : "justify-between gap-2 px-4",
        )}>
          <div className="flex min-w-0 items-center gap-2">
            {/* eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable */}
            <img src="/images/channelwatch-logo.png" alt={t("sidebar.logoAlt")} className="h-7 w-auto shrink-0" />
            {isMobile || !isCollapsed ? <span className="truncate font-semibold">{t("sidebar.brandName")}</span> : null}
          </div>
          {isMobile ? (
            <Button type="button" variant="ghost" size="icon" className="min-h-11 min-w-11" onClick={() => setIsOpen(false)} aria-label={t("sidebar.closeSidebar")}>
              <X className="h-5 w-5" aria-hidden="true" />
            </Button>
          ) : null}
        </div>

        <nav className="flex-1 overflow-y-auto px-3 py-2" aria-label={t("sidebar.navigation")}>
          <div className="space-y-1">{primaryNavigation.map(renderNavigationItem)}</div>
        </nav>

        <nav className="shrink-0 border-t border-border px-3 py-2" aria-label={t("sidebar.utilityNavigation")}>
          <div className="space-y-1">{utilityNavigation.map(renderNavigationItem)}</div>
        </nav>

        {!isMobile ? (
          <div className="shrink-0 border-t border-border p-2">
            {isCollapsed ? (
              <Tooltip>
                <TooltipTrigger asChild>{collapseButton}</TooltipTrigger>
                <TooltipContent side="right">{collapseLabel}</TooltipContent>
              </Tooltip>
            ) : collapseButton}
          </div>
        ) : null}
      </aside>

      {isMobile && isOpen ? (
        <button
          type="button"
          className="fixed inset-0 z-30 bg-background/80 backdrop-blur-sm"
          onClick={() => setIsOpen(false)}
          aria-label={t("sidebar.closeSidebar")}
        />
      ) : null}

      {!isMobile ? <div className={cn("transition-all duration-200 ease-in-out motion-reduce:transition-none", isCollapsed ? "ml-[60px]" : "ml-64")} /> : null}
    </TooltipProvider>
  )
}
