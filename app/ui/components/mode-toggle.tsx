"use client"

import { Moon, Sun } from "lucide-react"
import { useTheme } from "next-themes"

import { Button } from "@/components/base/button"
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from "@/components/base/dropdown-menu"
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/base/tooltip"
import { t } from "@/lib/i18n"

export function ModeToggle() {
  const { theme, setTheme } = useTheme()

  return (
    <DropdownMenu>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="size-11 min-h-11 min-w-11 text-muted-foreground">
                <Sun className="h-[1.2rem] w-[1.2rem] dark:hidden" />
                <Moon className="hidden h-[1.2rem] w-[1.2rem] dark:block" />
                <span className="sr-only">{t("theme.label")}</span>
              </Button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent>
            <p>{t("theme.label")}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup value={theme} onValueChange={setTheme}>
          <DropdownMenuRadioItem value="light">{t("theme.light")}</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">{t("theme.dark")}</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system">{t("theme.system")}</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
