"use client"

import * as React from "react"
import * as DialogPrimitive from "@radix-ui/react-dialog"
import { X } from "lucide-react"

import { useSheetDrag } from "@/hooks/use-sheet-drag"
import { cn } from "@/lib/utils"

const Dialog = DialogPrimitive.Root

const DialogTrigger = DialogPrimitive.Trigger

const DialogPortal = DialogPrimitive.Portal

const DialogClose = DialogPrimitive.Close

const DialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      "mobile-sheet-overlay fixed inset-0 z-50 bg-scrim",
      className
    )}
    {...props}
  />
))
DialogOverlay.displayName = DialogPrimitive.Overlay.displayName

/**
 * The grabber, and the top edge of a phone sheet that always drags. Lives inside the sheet,
 * so it only exists while the sheet is open. The body joins in once it is scrolled to the top.
 */
export function SheetHandle({ onDismiss, dismissGuard, onDismissBlocked, maxWidth, className, children }: {
  onDismiss: () => void
  dismissGuard?: () => boolean
  onDismissBlocked?: () => void
  maxWidth?: string
  className?: string
  children?: React.ReactNode
}) {
  const anchorRef = React.useRef<HTMLDivElement | null>(null)
  useSheetDrag({ anchorRef, onDismiss, dismissGuard, onBlocked: onDismissBlocked, maxWidth })
  return (
    <div ref={anchorRef} data-sheet-drag="" data-testid="sheet-handle" className={cn("touch-none select-none", className)}>
      <span aria-hidden="true" className="mx-auto mt-2 block h-1 w-9 rounded-full bg-muted-foreground/35" />
      {children}
    </div>
  )
}

type DialogContentProps = React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & {
  /** True while a swipe down would lose work. The sheet springs back instead of closing. */
  dismissGuard?: () => boolean
  onDismissBlocked?: () => void
}

const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  DialogContentProps
>(({ className, children, dismissGuard, onDismissBlocked, ...props }, ref) => {
  const closeRef = React.useRef<HTMLButtonElement | null>(null)
  return (
  <DialogPortal>
    <DialogOverlay />
    <DialogPrimitive.Content
      ref={ref}
      className={cn(
        "fixed left-[50%] top-[50%] z-50 grid grid-cols-1 max-h-[calc(100dvh-2rem)] min-w-0 w-[calc(100%-2rem)] max-w-lg grid-cols-[minmax(0,1fr)] translate-x-[-50%] translate-y-[-50%] gap-4 overflow-x-hidden overflow-y-auto rounded-group border border-hairline-strong bg-raised p-4 shadow-3 dialog-sheet sm:transition-none sm:duration-[var(--motion-base)] sm:ease-out sm:data-[state=open]:animate-in sm:data-[state=closed]:animate-out sm:data-[state=closed]:fade-out-0 sm:data-[state=open]:fade-in-0 sm:data-[state=closed]:zoom-out-[0.96] sm:data-[state=open]:zoom-in-[0.96] sm:p-6",
        "max-sm:inset-x-0 max-sm:bottom-0 max-sm:left-0 max-sm:top-auto max-sm:w-full max-sm:max-w-none max-sm:max-h-[calc(100dvh-env(safe-area-inset-top)-0.75rem)] max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-b-none max-sm:rounded-t-sheet max-sm:border-x-0 max-sm:border-b-0 max-sm:pt-7 max-sm:pb-[max(1.25rem,env(safe-area-inset-bottom))] max-sm:overscroll-contain",
        className
      )}
      {...props}
    >
      <SheetHandle
        onDismiss={() => closeRef.current?.click()}
        dismissGuard={dismissGuard}
        onDismissBlocked={onDismissBlocked}
        className="absolute inset-x-0 top-0 h-6 sm:hidden"
      />
      {children}
      <DialogPrimitive.Close ref={closeRef} className="absolute right-3 top-3 flex h-11 w-11 items-center justify-center rounded-control text-muted-foreground ring-offset-background transition-colors press-row hover:bg-fill hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none">
        <X className="h-4 w-4" />
        <span className="sr-only">Close</span>
      </DialogPrimitive.Close>
    </DialogPrimitive.Content>
  </DialogPortal>
  )
})
DialogContent.displayName = DialogPrimitive.Content.displayName

const DialogHeader = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn(
      "flex flex-col space-y-1.5 text-left",
      className
    )}
    {...props}
  />
)
DialogHeader.displayName = "DialogHeader"

const DialogFooter = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => (
  <div
    className={cn(
      "flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2",
      className
    )}
    {...props}
  />
)
DialogFooter.displayName = "DialogFooter"

const DialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Title
    ref={ref}
    className={cn(
      "text-base font-semibold leading-snug tracking-[-0.012em]",
      className
    )}
    {...props}
  />
))
DialogTitle.displayName = DialogPrimitive.Title.displayName

const DialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Description
    ref={ref}
    className={cn("text-xs leading-relaxed text-muted-foreground", className)}
    {...props}
  />
))
DialogDescription.displayName = DialogPrimitive.Description.displayName

export {
  Dialog,
  DialogPortal,
  DialogOverlay,
  DialogClose,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
}

