import * as React from "react"

import { statusDotClass, type StatusTone } from "@/components/base/status-badge"
import { cn } from "@/lib/utils"

/**
 * The chip family. Four shapes, one job each:
 * - StatusText: a healthy or plain fact, a dot and a muted word.
 * - Tag: a neutral label.
 * - Count: a neutral number.
 * - StatusBadge (status-badge.tsx): a tinted word for warning and danger.
 */
export interface StatusTextProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: StatusTone
  /** Rows that already lead with their own dot show the word alone. */
  dot?: boolean
}

export function StatusText({ tone = "success", dot = true, className, children, ...props }: StatusTextProps) {
  return (
    <span data-status={tone} className={cn("inline-flex items-center gap-1.5 text-xs text-muted-foreground", className)} {...props}>
      {dot ? <span aria-hidden="true" className={cn("size-2 shrink-0 rounded-full", statusDotClass[tone])} /> : null}
      {children}
    </span>
  )
}

export const tagClassName = "inline-flex h-5 items-center rounded-thumb bg-fill px-1.5 text-2xs text-muted-foreground"

export function Tag({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn(tagClassName, className)} {...props} />
}

export function Count({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return <span className={cn(tagClassName, "rounded-full tabular-nums", className)} {...props} />
}
