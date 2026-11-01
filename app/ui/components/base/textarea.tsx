"use client"

import * as React from "react"

import { cn } from "@/lib/utils"

const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => {
  return (
    <textarea
      ref={ref}
      className={cn(
        "flex min-h-[112px] w-full rounded-control border border-input bg-well px-3.5 py-3 text-base leading-relaxed ring-offset-background transition-[border-color,box-shadow] placeholder:text-muted-foreground focus-visible:border-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:ring-offset-0 disabled:cursor-not-allowed disabled:bg-muted disabled:opacity-60",
        className,
      )}
      {...props}
    />
  )
})
Textarea.displayName = "Textarea"

export { Textarea }
