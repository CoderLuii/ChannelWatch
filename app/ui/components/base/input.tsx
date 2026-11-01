import * as React from "react"

import { cn } from "@/lib/utils"

const Input = React.forwardRef<HTMLInputElement, React.ComponentProps<"input">>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "flex min-h-11 w-full rounded-control border border-input bg-well px-3.5 py-2 text-base ring-offset-background transition-[border-color,box-shadow] placeholder:text-muted-foreground focus-visible:border-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 focus-visible:ring-offset-0 disabled:cursor-not-allowed disabled:bg-muted disabled:opacity-60",
          className
        )}
        ref={ref}
        {...props}
      />
    )
  }
)
Input.displayName = "Input"

export { Input }

