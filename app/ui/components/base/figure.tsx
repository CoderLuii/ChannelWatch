import * as React from "react"

import { cn } from "@/lib/utils"

/** A number with its unit stepped down, so "116 GB" reads as 116, then GB. */
export function Figure({ value, className, ...props }: { value: string | number } & React.HTMLAttributes<HTMLSpanElement>) {
  const parts = String(value).split(/(\d[\d.,]*)/).filter(Boolean)
  return (
    <span className={cn("tabular-nums", className)} {...props}>
      {parts.map((part, index) =>
        /^\d/.test(part) ? (
          <React.Fragment key={index}>{part}</React.Fragment>
        ) : (
          <span key={index} className="ml-0.5 text-sm font-medium text-muted-foreground">{part}</span>
        ),
      )}
    </span>
  )
}
