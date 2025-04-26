"use client"

import { useState, useContext, createContext } from "react"
import { ModeToggle } from "@/components/mode-toggle"
import { Button } from "@/components/base/button"
import { signalContainerRestart } from "@/lib/api"
import { useToast } from "@/hooks/use-toast"
import { 
  Menu, 
  RefreshCw, 
  Power,
  X, 
  Home, 
  Settings, 
  HeartPulse, 
  Info 
} from "lucide-react"
import { cn } from "@/lib/utils"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/base/tooltip"


export const HeaderContext = createContext<{
  activeView: string;
  setActiveView: (view: string) => void;
}>({
  activeView: "overview",
  setActiveView: () => {}
});

export function Header() {
  const [isRestarting, setIsRestarting] = useState(false)
  const { toast } = useToast()
  const { activeView, setActiveView } = useContext(HeaderContext)
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false)

  const handleRestart = async () => {
    try {
      if (confirm("Are you sure you want to restart the ChannelWatch service?")) {
        setIsRestarting(true)
        const result = await signalContainerRestart()
        toast({
          title: "System Restart Initiated",
          description: "ChannelWatch services are restarting. This process may take up to 15 seconds to complete.",
          variant: "default",
          className: "bg-blue-50 dark:bg-blue-900 border-blue-200 dark:border-blue-800",
        })
        
        setTimeout(() => {
          setIsRestarting(false)
        }, 10000)
      }
    } catch (error) {
      console.error("Error during restart:", error)
      toast({
        variant: "destructive",
        title: "Restart Failed",
        description: "Unable to restart ChannelWatch services. Please check system logs for more information.",
      })
      setIsRestarting(false)
    }
  }

  const toggleMobileSidebar = () => {
    setIsMobileSidebarOpen(!isMobileSidebarOpen);
  };

  const handleNavClick = (view: string) => {
    setActiveView(view);
    setIsMobileSidebarOpen(false);
  };

  return (
    <>
      <header className="fixed top-0 left-0 right-0 z-40 border-b bg-background/95 backdrop-blur-lg supports-[backdrop-filter]:bg-background/60 w-full">
        <div className="flex h-16 items-center justify-between px-4 md:px-6 w-full">
          <div className="flex items-center gap-2 md:gap-4">
            {/* Direct mobile menu button */}
            <Button 
              variant="ghost" 
              size="icon" 
              className="md:hidden"
              onClick={toggleMobileSidebar}
            >
              <Menu className="h-5 w-5" />
              <span className="sr-only">Toggle menu</span>
            </Button>
          </div>
          
          {/* Mobile title - centered with logo */}
          <div className="absolute left-1/2 transform -translate-x-1/2 flex items-center gap-2 md:hidden">
            <img 
              src="/images/channelwatch-logo.png" 
              alt="ChannelWatch Logo" 
              className="h-6 w-auto" 
            />
            <span className="text-lg font-semibold">ChannelWatch</span>
          </div>

          <div className="flex items-center gap-2">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="hidden md:flex items-center gap-1"
                    onClick={handleRestart}
                    disabled={isRestarting}
                  >
                    <Power className={`h-4 w-4 ${isRestarting ? "animate-spin" : ""}`} />
                    Restart
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>Restart application</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>

            {/* Mobile restart button */}
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="outline"
                    size="sm"
                    className="md:hidden flex items-center gap-1"
                    onClick={handleRestart}
                    disabled={isRestarting}
                  >
                    <Power className={`h-4 w-4 ${isRestarting ? "animate-spin" : ""}`} />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>
                  <p>Restart</p>
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>

            <ModeToggle />
          </div>
        </div>
      </header>

      {/* Mobile sidebar - completely separated from header */}
      {isMobileSidebarOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          {/* Backdrop */}
          <div 
            className="absolute inset-0 bg-black/50" 
            onClick={toggleMobileSidebar}
            aria-hidden="true"
          ></div>
          
          {/* Sidebar container */}
          <div className="absolute inset-y-0 left-0 w-64 bg-slate-900 shadow-xl overflow-auto">
            {/* Header */}
            <div className="flex justify-between items-center p-4 border-b border-slate-800">
              <div className="flex items-center gap-2">
                <img 
                  src="/images/channelwatch-logo.png" 
                  alt="ChannelWatch Logo" 
                  className="h-6 w-auto" 
                />
                <span className="text-white font-semibold">ChannelWatch</span>
              </div>
              <button
                className="p-1 rounded-full hover:bg-slate-800"
                onClick={toggleMobileSidebar}
              >
                <X className="h-5 w-5 text-white" />
              </button>
            </div>
            
            {/* Navigation */}
            <nav className="p-4 space-y-2">
              <Button
                variant={activeView === "overview" ? "default" : "ghost"}
                size="sm"
                className="w-full justify-start text-white"
                onClick={() => handleNavClick("overview")}
              >
                <Home className="h-4 w-4 mr-2" />
                Dashboard
              </Button>
              <Button
                variant={activeView === "settings" ? "default" : "ghost"}
                size="sm"
                className="w-full justify-start text-white"
                onClick={() => handleNavClick("settings")}
              >
                <Settings className="h-4 w-4 mr-2" />
                Settings
              </Button>
              <Button
                variant={activeView === "diagnostics" ? "default" : "ghost"}
                size="sm"
                className="w-full justify-start text-white"
                onClick={() => handleNavClick("diagnostics")}
              >
                <HeartPulse className="h-4 w-4 mr-2" />
                Diagnostics
              </Button>
              <Button
                variant={activeView === "about" ? "default" : "ghost"}
                size="sm"
                className="w-full justify-start text-white"
                onClick={() => handleNavClick("about")}
              >
                <Info className="h-4 w-4 mr-2" />
                About
              </Button>
            </nav>
          </div>
        </div>
      )}
    </>
  )
}


