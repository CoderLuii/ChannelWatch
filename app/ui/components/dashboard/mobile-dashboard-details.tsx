"use client"

import { useState, type ReactNode } from "react"
import { AlertTriangle, ChevronDown, CircleAlert } from "lucide-react"
import { Button } from "@/components/base/button"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"

type SummaryStatus = {
  tone: "warning" | "critical"
  label: string
}

export function MobileDashboardDetails({ id, title, summary, summaryStatus, children, className, hasError = false, integratedCard = false }: { id: string; title: string; summary?: string; summaryStatus?: SummaryStatus; children: ReactNode; className?: string; hasError?: boolean; integratedCard?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const open = expanded
  const displaySummary = hasError ? t("uptime.unavailable") : summary
  const SummaryStatusIcon = summaryStatus?.tone === "critical" ? CircleAlert : AlertTriangle
  return (
    <div className={cn("min-w-0 rounded-[20px] border border-border/70 bg-card self-start xl:contents", className, integratedCard && open && "self-stretch")}>
      <Button type="button" variant="ghost" className="h-auto min-h-11 w-full justify-between gap-3 whitespace-normal rounded-[20px] px-4 py-3 text-left xl:hidden" aria-expanded={open} aria-controls={id} onClick={() => setExpanded(!open)}>
        <span className="min-w-0">
          <span className="block text-[17px] font-semibold">{title}</span>
          {displaySummary || summaryStatus ? (
            <span className={cn("mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[13px] font-normal", hasError ? "text-red-500" : "text-muted-foreground")}>
              {!hasError && summaryStatus ? (
                <span className={cn("inline-flex items-center gap-1 font-semibold", summaryStatus.tone === "critical" ? "text-red-700 dark:text-red-300" : "text-amber-700 dark:text-amber-300")}>
                  <SummaryStatusIcon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                  {summaryStatus.label}
                </span>
              ) : null}
              {displaySummary ? <span>{displaySummary}</span> : null}
            </span>
          ) : null}
        </span>
        <ChevronDown className={cn("h-5 w-5 shrink-0 transition-transform motion-reduce:transition-none", open && "rotate-180")} aria-hidden="true" />
      </Button>
      <div id={id} className={cn("xl:contents", !open && "hidden", integratedCard && "max-xl:[&>div]:rounded-none max-xl:[&>div]:border-0 max-xl:[&>div]:bg-transparent max-xl:[&>div]:shadow-none max-xl:[&>div>div:first-child]:hidden max-xl:[&>div>div:last-child]:pb-5")}>
        {children}
      </div>
    </div>
  )
}
