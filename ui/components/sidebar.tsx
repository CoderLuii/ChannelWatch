"use client"

import { useState, useEffect } from "react"
import { Button } from "@/components/base/button"
import { cn } from "@/lib/utils"
import {
  Home,
  Settings,
  HeartPulse,
  Info,
  Menu,
  X,
  ChevronRight
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
                  <img 
                    src="/images/channelwatch-logo.png" 
                    alt="ChannelWatch Logo" 
                    className="h-7 w-auto" 
                  />
                </div>
              </div>
              {/* Close button for mobile inline with header */}
              <button
                onClick={() => setIsOpen(false)}
                className="p-1 rounded-full hover:bg-muted/20"
                aria-label="Close sidebar"
              >
                <X className="h-5 w-5" />
              </button>
            </>
          ) : (
            <>
              {isCollapsed ? (
                <img 
                  src="/images/channelwatch-logo.png" 
                  alt="ChannelWatch Logo" 
                  className="h-7 w-auto"
                />
              ) : (
                <>
                  <div className="rounded-md p-1">
                    <img 
                      src="/images/channelwatch-logo.png" 
                      alt="ChannelWatch Logo" 
                      className="h-7 w-auto" 
                    />
                  </div>
                  <span className="font-semibold">ChannelWatch</span>
                </>
              )}
            </>
          )}
        </div>
        
        {/* Navigation links */}
        <div className="px-3 py-2">
          <div className="space-y-1">
            <Button
              variant={activeView === "overview" ? "default" : "ghost"}
              size="sm"
              className={cn("w-full", 
                           !isMobile && isCollapsed ? "justify-center p-2" : "justify-start")}
              onClick={() => setActiveView("overview")}
            >
              <Home className={cn("h-4 w-4", !isMobile && isCollapsed ? "" : "mr-2")} />
              {(!isMobile && !isCollapsed) || isMobile ? "Dashboard" : ""}
            </Button>
            <Button
              variant={activeView === "settings" ? "default" : "ghost"}
              size="sm"
              className={cn("w-full", 
                           !isMobile && isCollapsed ? "justify-center p-2" : "justify-start")}
              onClick={() => setActiveView("settings")}
            >
              <Settings className={cn("h-4 w-4", !isMobile && isCollapsed ? "" : "mr-2")} />
              {(!isMobile && !isCollapsed) || isMobile ? "Settings" : ""}
            </Button>
            <Button
              variant={activeView === "diagnostics" ? "default" : "ghost"}
              size="sm"
              className={cn("w-full", 
                           !isMobile && isCollapsed ? "justify-center p-2" : "justify-start")}
              onClick={() => setActiveView("diagnostics")}
            >
              <HeartPulse className={cn("h-4 w-4", !isMobile && isCollapsed ? "" : "mr-2")} />
              {(!isMobile && !isCollapsed) || isMobile ? "Diagnostics" : ""}
            </Button>
            <Button
              variant={activeView === "about" ? "default" : "ghost"}
              size="sm"
              className={cn("w-full", 
                           !isMobile && isCollapsed ? "justify-center p-2" : "justify-start")}
              onClick={() => setActiveView("about")}
            >
              <Info className={cn("h-4 w-4", !isMobile && isCollapsed ? "" : "mr-2")} />
              {(!isMobile && !isCollapsed) || isMobile ? "About" : ""}
            </Button>
          </div>
        </div>

        {/* Collapse button - integrated directly into the sidebar */}
        {!isMobile && (
          <button
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="absolute bottom-4 right-4 bg-background/80 backdrop-blur-sm rounded-full shadow-md flex items-center justify-center transition-all duration-200 hover:bg-muted h-7 w-7 z-10"
            aria-label={isCollapsed ? "Expand sidebar" : "Collapse sidebar"}
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
        <div 
          className="fixed inset-0 z-30 bg-background/80 backdrop-blur-sm"
          onClick={() => setIsOpen(false)}
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


