import { AlertCircle, AlertTriangle, ChevronRight, Loader2 } from "lucide-react"

import { Button } from "@/components/base/button"
import type { HealthVerdict, HealthVerdictActionKind } from "@/lib/health-verdict"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"

const toneStyles = {
  // Healthy stays quiet: a dot and a line of text, no box. Same padding, so the height holds.
  healthy: { wrapper: "border-transparent bg-transparent shadow-none px-0", icon: "", Icon: null },
  warning: { wrapper: "border-warning-border bg-warning-bg px-3.5", icon: "text-warning-fg", Icon: AlertTriangle },
  danger: { wrapper: "border-danger-border bg-danger-bg px-3.5", icon: "text-danger-fg", Icon: AlertCircle },
  pending: { wrapper: "border-hairline bg-card px-3.5", icon: "text-muted-foreground animate-spin motion-reduce:animate-none", Icon: Loader2 },
} as const

export function HealthVerdictLine({ verdict, onAction, className }: { verdict: HealthVerdict; onAction?: (kind: HealthVerdictActionKind) => void; className?: string }) {
  const style = toneStyles[verdict.tone]
  const Icon = style.Icon
  const action = onAction ? verdict.action : undefined
  return (
    <div
      data-testid="health-verdict"
      data-tone={verdict.tone}
      role="status"
      aria-live="polite"
      className={cn("flex min-h-[62px] min-w-0 flex-wrap items-start gap-x-2.5 gap-y-1 rounded-group border py-2.5 sm:min-h-11 sm:items-center", style.wrapper, className)}
    >
      {Icon ? (
        <Icon className={cn("mt-[2px] h-[18px] w-[18px] shrink-0 sm:mt-0", style.icon)} aria-hidden="true" />
      ) : (
        <span aria-hidden="true" className="mt-1.5 size-2 shrink-0 rounded-full bg-success sm:mt-0" />
      )}
      <p className="min-w-0 flex-1 basis-[9rem] break-words text-sm leading-5">
        <span className="block font-semibold text-foreground sm:inline">
          {verdict.headline}
          {verdict.more ? <span className="whitespace-nowrap font-normal text-muted-foreground">{t("status.banner.more", { count: verdict.more })}</span> : null}
        </span>
        {verdict.details.length > 0 ? (
          <span className="block text-muted-foreground sm:inline">
            <span className="sr-only">, </span>
            <span aria-hidden="true" className="hidden px-1.5 sm:inline">·</span>
            {verdict.details.join(" · ")}
          </span>
        ) : null}
      </p>
      {action ? (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          data-testid="health-verdict-action"
          className="-my-2.5 ml-auto min-h-11 shrink-0 gap-1 px-2 text-xs font-medium sm:px-3"
          onClick={() => onAction?.(action.kind)}
        >
          {action.label}
          <ChevronRight className="size-4 text-muted-foreground" aria-hidden="true" />
        </Button>
      ) : null}
    </div>
  )
}
