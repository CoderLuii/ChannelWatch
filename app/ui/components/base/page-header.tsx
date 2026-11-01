import * as React from "react"

import { cn } from "@/lib/utils"

export const pageTitleClassName = "text-[min(1.5rem,32px)] font-semibold leading-tight tracking-tight break-words sm:text-title md:text-display"

interface PageHeaderProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "title"> {
  title: React.ReactNode
  description?: React.ReactNode
  actions?: React.ReactNode
}

export function PageHeader({ title, description, actions, className, ...props }: PageHeaderProps) {
  return (
    <div className={cn("flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between", className)} {...props}>
      <div className="min-w-0 space-y-1">
        <h1 className={pageTitleClassName}>{title}</h1>
        {description ? <p className="text-sm leading-6 text-muted-foreground [overflow-wrap:anywhere] sm:text-base">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  )
}
