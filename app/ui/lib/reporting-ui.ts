import type { ReportMode } from "@/lib/api"

export const supportCodeDownloadFilename = "channelwatch-support-code.txt"

export function reportSubmitLabelKey(mode: ReportMode): string {
  if (mode === "live") return "supportReport.review.submit"
  if (mode === "email-test") return "supportReport.review.sendTest"
  return "supportReport.review.preview"
}

export function isPrivateDeliveryFailure(result: {
  status?: string | null
  private_delivery_status?: string | null
}): boolean {
  return (
    result.status === "completed_with_private_delivery_failure" ||
    result.private_delivery_status === "failed"
  )
}

interface SelectableCodeField {
  focus(): void
  select(): void
}

export async function copySupportCode(
  supportCode: string,
  field: SelectableCodeField | null,
): Promise<"copied" | "manual"> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(supportCode)
      return "copied"
    }
  } catch {
    // Continue to the selection-based fallback below.
  }

  field?.focus()
  field?.select()
  try {
    if (document.execCommand?.("copy")) return "copied"
  } catch {
    // The selected field remains available for manual copying.
  }
  return "manual"
}
