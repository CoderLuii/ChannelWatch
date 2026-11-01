"use client"

import * as React from "react"
import { Check, ChevronDown } from "lucide-react"

import { Button } from "@/components/base/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/base/dialog"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"

/** Phone filters: a short row of pills, each opening one shared sheet of plain rows. */
export function FilterPills({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div data-testid="filter-pills" className={cn("flex flex-wrap gap-2 md:hidden", className)} {...props} />
}

export function FilterPill({ label, active = false, className, ...props }: { label: string; active?: boolean } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      className={cn(
        "press-row inline-flex min-h-11 max-w-full items-center gap-1 rounded-control border border-input px-3 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active ? "bg-selected text-foreground" : "text-muted-foreground",
        className,
      )}
      {...props}
    >
      <span className="min-w-0 truncate">{label}</span>
      <ChevronDown className="size-3.5 shrink-0" aria-hidden="true" />
    </button>
  )
}

export function FilterSheet({
  open,
  onOpenChange,
  title = t("filters.title"),
  clearLabel,
  onClear,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title?: string
  clearLabel?: string
  onClear?: () => void
  children: React.ReactNode
}) {
  const returnFocus = React.useRef<HTMLElement | null>(null)
  React.useEffect(() => {
    if (!open) return
    const wide = window.matchMedia("(min-width: 768px)")
    const closeWhenWide = () => {
      if (wide.matches) onOpenChange(false)
    }
    wide.addEventListener("change", closeWhenWide)
    return () => wide.removeEventListener("change", closeWhenWide)
  }, [open, onOpenChange])
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        aria-describedby={undefined}
        data-testid="filter-sheet"
        className="flex max-h-[85dvh] flex-col gap-0 overflow-hidden p-0 max-sm:pb-0 max-sm:pt-5 md:hidden"
        // Focusing a row while the sheet is still below the screen would scroll the list to reveal it.
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          returnFocus.current = document.activeElement as HTMLElement | null
          ;(event.currentTarget as HTMLElement).focus({ preventScroll: true })
        }}
        // Opened by state rather than a Radix trigger, so nothing else knows where focus came from.
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          returnFocus.current?.focus({ preventScroll: true })
        }}
      >
        <DialogHeader className="px-4 pb-3 pt-4 max-sm:pt-2">
          <DialogTitle className="pr-10">{title}</DialogTitle>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto overscroll-contain px-4 pb-4">{children}</div>
        <div className="flex items-center justify-between gap-2 border-t border-hairline px-4 pb-[max(12px,env(safe-area-inset-bottom))] pt-3">
          {onClear && clearLabel ? (
            <Button type="button" variant="ghost" size="sm" className="min-h-11" onClick={onClear}>{clearLabel}</Button>
          ) : <span />}
          <Button type="button" className="min-h-11 flex-1" onClick={() => onOpenChange(false)}>{t("filters.showResults")}</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function FilterGroup({ title, children, plain = false }: { title: string; children: React.ReactNode; plain?: boolean }) {
  return (
    <section className="min-w-0">
      <h3 className="pb-1.5 text-xs font-medium text-muted-foreground">{title}</h3>
      {plain ? children : <div className="divide-y divide-hairline overflow-hidden rounded-control bg-fill">{children}</div>}
    </section>
  )
}

export function FilterRow({ label, selected, count, onSelect }: { label: string; selected: boolean; count?: number; onSelect: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className="press-row flex min-h-11 w-full items-center gap-2 px-3 py-2 text-left text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
    >
      <Check className={cn("size-4 shrink-0", selected ? "opacity-100" : "opacity-0")} aria-hidden="true" />
      <span className="line-clamp-2 min-w-0 flex-1 break-words">{label}</span>
      {count !== undefined ? <span className="shrink-0 text-xs tabular-nums text-muted-foreground">{count}</span> : null}
    </button>
  )
}
