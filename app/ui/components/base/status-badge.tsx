import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

export type StatusTone = "success" | "warning" | "danger" | "info" | "neutral"

const statusBadgeVariants = cva(
  "inline-flex min-h-5 max-w-full items-center gap-1.5 rounded-thumb px-1.5 py-0.5 text-2xs font-medium leading-4 tabular-nums",
  {
    variants: {
      variant: {
        success: "bg-success-bg text-success-fg",
        warning: "bg-warning-bg text-warning-fg",
        danger: "bg-danger-bg text-danger-fg",
        info: "bg-info-bg text-info-fg",
        neutral: "bg-neutral-bg text-neutral-fg",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  }
)

const statusDotClass: Record<StatusTone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-danger",
  info: "bg-info",
  neutral: "bg-neutral",
}

export interface StatusBadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof statusBadgeVariants> {
  dot?: boolean
}

function StatusBadge({ className, variant, dot = false, children, ...props }: StatusBadgeProps) {
  const tone: StatusTone = variant ?? "neutral"
  return (
    <span data-status={tone} className={cn(statusBadgeVariants({ variant }), className)} {...props}>
      {dot ? <span aria-hidden="true" className={cn("size-2 shrink-0 rounded-full", statusDotClass[tone])} /> : null}
      {children}
    </span>
  )
}

export { StatusBadge, statusBadgeVariants, statusDotClass }
