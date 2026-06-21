"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/base/button"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"
import {
  Home,
  History,
  Settings,
  HeartPulse,
  Info,
  Menu,
  X,
  ChevronRight,
  Bell,
} from "lucide-react"

interface SidebarProps {
  activeView: string
  setActiveView: (view: string) => void
  isMobile?: boolean
}

export function Sidebar({ activeView, setActiveView, isMobile: propIsMobile }: SidebarProps) {
  const [isMobile, setIsMobile] = useState(propIsMobile || false)
  const [isOpen, setIsOpen] = useState(false)
  const [isCollapsed, setIsCollapsed] = useState(true)

  // Check if we're on mobile and listen for window resize
  useEffect(() => {
    // If isMobile prop is provided, use it directly
    if (propIsMobile !== undefined) {
      setIsMobile(propIsMobile)
      return
    }

    const checkIsMobile = () => {
      setIsMobile(window.innerWidth < 768)
    }

    // Check initially
    checkIsMobile()

    // Add resize listener
    window.addEventListener("resize", checkIsMobile)

    // Clean up
    return () => window.removeEventListener("resize", checkIsMobile)
  }, [propIsMobile])

  // Listen for mobile sidebar toggle from header
  useEffect(() => {
    const handler = () => setIsOpen(prev => !prev)
    window.addEventListener("toggle-mobile-sidebar", handler)
    return () => window.removeEventListener("toggle-mobile-sidebar", handler)
  }, [])

  // Close sidebar when view changes on mobile
  useEffect(() => {
    if (isMobile) {
      setIsOpen(false)
    }
  }, [activeView, isMobile])

  return (
    <>
      {/* Mobile menu button */}
      {isMobile && (
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setIsOpen(!isOpen)}
          className="fixed top-4 left-4 z-50 md:hidden"
        >
          {isOpen ? (
            <X className="h-5 w-5" />
          ) : (
            <Menu className="h-5 w-5" />
          )}
        </Button>
      )}

      {/* Simple fixed sidebar with integrated collapse button */}
      <div
        className={cn(
          "fixed inset-y-0 left-0 z-50 border-r border-border bg-background backdrop-blur-lg",
          // Mobile sidebar visibility
          isMobile && !isOpen ? "-translate-x-full" : "translate-x-0",
          // Desktop sidebar width - collapsed or expanded
          !isMobile && isCollapsed ? "w-[60px]" : "w-64",
          "transition-all duration-200 ease-in-out"
        )}
        role={isMobile ? "dialog" : undefined}
        aria-modal={isMobile ? true : undefined}
        aria-label={isMobile ? t("sidebar.navigation") : undefined}
      >
        {/* Logo section */}
        <div className={cn(
          "flex items-center border-b p-2",
          !isMobile && isCollapsed ? "justify-center" : "justify-start gap-2 px-4",
          isMobile && "justify-between pt-4"
        )}>
          {isMobile ? (
            <>
              <div className="flex items-center">
                <div className="rounded-md p-1">
                  {/* eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable */}
                  <img
                    src="/images/channelwatch-logo.png"
                    alt={t("sidebar.logoAlt")}
                    className="h-7 w-auto"
                  />
                </div>
              </div>
              {/* Close button for mobile inline with header */}
              <button
                onClick={() => setIsOpen(false)}
                className="p-1 rounded-full hover:bg-muted/20"
                aria-label={t("sidebar.closeSidebar")}
              >
                <X className="h-5 w-5" />
              </button>
            </>
          ) : (
            <>
              {isCollapsed ? (
                // eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable
                <img
                  src="/images/channelwatch-logo.png"
                  alt={t("sidebar.logoAlt")}
                  className="h-7 w-auto"
                />
              ) : (
                <>
                  <div className="rounded-md p-1">
                    {/* eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable */}
                    <img
                      src="/images/channelwatch-logo.png"
                      alt={t("sidebar.logoAlt")}
                      className="h-7 w-auto"
                    />
                  </div>
                  <span className="font-semibold">{t("sidebar.brandName")}</span>
                </>
              )}
            </>
          )}
        </div>

        {/* Navigation links */}
        <nav className="px-3 py-2" aria-label={t("sidebar.navigation")}>
          <div className="space-y-1">
            <Button
              variant={activeView === "overview" ? "default" : "ghost"}
              size="sm"
              aria-label={t("nav.dashboard")}
              aria-current={activeView === "overview" ? "page" : undefined}
              className={cn("w-full",
                           !isMobile && isCollapsed ? "justify-center p-2" : "justify-start")}
              onClick={() => setActiveView("overview")}
            >
              <Home className={cn("h-4 w-4", !isMobile && isCollapsed ? "" : "mr-2")} />
              {(!isMobile && !isCollapsed) || isMobile ? t("nav.dashboard") : ""}
            </Button>
            <Button
              variant={activeView === "watch-history" ? "default" : "ghost"}
              size="sm"
              aria-label={t("nav.watchHistory")}
              aria-current={activeView === "watch-history" ? "page" : undefined}
              className={cn("w-full",
                           !isMobile && isCollapsed ? "justify-center p-2" : "justify-start")}
              onClick={() => setActiveView("watch-history")}
            >
              <History className={cn("h-4 w-4", !isMobile && isCollapsed ? "" : "mr-2")} />
              {(!isMobile && !isCollapsed) || isMobile ? t("nav.watchHistory") : ""}
            </Button>
            <Button
              variant={activeView.startsWith("settings") ? "default" : "ghost"}
              size="sm"
              aria-label={t("nav.settings")}
              aria-current={activeView.startsWith("settings") ? "page" : undefined}
              className={cn("w-full",
                           !isMobile && isCollapsed ? "justify-center p-2" : "justify-start")}
              onClick={() => setActiveView("settings")}
            >
              <Settings className={cn("h-4 w-4", !isMobile && isCollapsed ? "" : "mr-2")} />
              {(!isMobile && !isCollapsed) || isMobile ? t("nav.settings") : ""}
            </Button>
            <Button
              variant={activeView === "notification-log" ? "default" : "ghost"}
              size="sm"
              aria-label={t("nav.notificationLog")}
              aria-current={activeView === "notification-log" ? "page" : undefined}
              className={cn("w-full",
                           !isMobile && isCollapsed ? "justify-center p-2" : "justify-start")}
              onClick={() => setActiveView("notification-log")}
            >
              <Bell className={cn("h-4 w-4", !isMobile && isCollapsed ? "" : "mr-2")} />
              {(!isMobile && !isCollapsed) || isMobile ? t("nav.notificationLog") : ""}
            </Button>
            <Button
              variant={activeView === "diagnostics" ? "default" : "ghost"}
              size="sm"
              aria-label={t("nav.diagnostics")}
              aria-current={activeView === "diagnostics" ? "page" : undefined}
              className={cn("w-full",
                           !isMobile && isCollapsed ? "justify-center p-2" : "justify-start")}
              onClick={() => setActiveView("diagnostics")}
            >
              <HeartPulse className={cn("h-4 w-4", !isMobile && isCollapsed ? "" : "mr-2")} />
              {(!isMobile && !isCollapsed) || isMobile ? t("nav.diagnostics") : ""}
            </Button>
            <Button
              variant={activeView === "about" ? "default" : "ghost"}
              size="sm"
              aria-label={t("nav.about")}
              aria-current={activeView === "about" ? "page" : undefined}
              className={cn("w-full",
                           !isMobile && isCollapsed ? "justify-center p-2" : "justify-start")}
              onClick={() => setActiveView("about")}
            >
              <Info className={cn("h-4 w-4", !isMobile && isCollapsed ? "" : "mr-2")} />
              {(!isMobile && !isCollapsed) || isMobile ? t("nav.about") : ""}
            </Button>
          </div>
        </nav>

        {/* Collapse button - integrated directly into the sidebar */}
        {!isMobile && (
          <button
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="absolute bottom-4 right-4 bg-background/80 backdrop-blur-sm rounded-full shadow-md flex items-center justify-center transition-all duration-200 hover:bg-muted h-7 w-7 z-10"
            aria-label={isCollapsed ? t("sidebar.expandSidebar") : t("sidebar.collapseSidebar")}
          >
            <ChevronRight
              className={cn("h-4 w-4 text-muted-foreground transition-transform",
                        isCollapsed ? "rotate-180" : "")}
            />
          </button>
        )}
      </div>

      {/* Overlay to close sidebar when clicking outside on mobile */}
      {isMobile && isOpen && (
        <button
          type="button"
          className="fixed inset-0 z-30 bg-background/80 backdrop-blur-sm"
          onClick={() => setIsOpen(false)}
          aria-label={t("sidebar.closeSidebar")}
        />
      )}

      {/* Push main content to the right */}
      {!isMobile && (
        <div
          className={cn(
            "transition-all duration-200 ease-in-out",
            isCollapsed ? "ml-[60px]" : "ml-64"
          )}
        />
      )}
    </>
  )
}


