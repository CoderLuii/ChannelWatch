"use client"

import { useCallback, useEffect, useState } from "react"
import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { fetchNotificationLog } from "@/lib/api"
import type { NotificationDeliveryItem } from "@/lib/api"
import { t } from "@/lib/i18n"
import { useDvrSelection } from "@/lib/dvr-selection-context"
import { AlertCircle, Bell, CheckCircle, ChevronLeft, ChevronRight, Loader2, RefreshCw, XCircle } from "lucide-react"

const PAGE_SIZE = 50
const STATUS_OPTIONS = ["sent", "retry", "failed", "circuit_open"]
const CHANNEL_OPTIONS = ["apprise", "webhook"]

function eventTypeLabel(type?: string): string {
  switch (type) {
    case "watching_channel":
    case "stream_started":
      return t("type.liveTV")
    case "watching_vod":
    case "vod_playback":
      return t("type.vod")
    case "recording_started":
    case "recording_completed":
    case "recording_event":
      return t("type.recording")
    case "disk_alert":
      return t("type.diskAlert")
    default:
      return type?.replaceAll("_", " ") || "—"
  }
}

function formatTimestamp(ts: string): string {
  try {
    return new Date(ts).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    })
  } catch {
    return ts
  }
}

function StatusBadge({ status }: { status: string }) {
  if (status === "sent") {
    return (
      <Badge className="bg-emerald-500/20 text-emerald-800 dark:text-emerald-300 border-0 gap-1">
        <CheckCircle className="h-3 w-3" />
        {t("notificationLog.statusSent")}
      </Badge>
    )
  }
  if (status === "retry") {
    return (
      <Badge className="bg-blue-500/20 text-blue-700 dark:text-blue-400 border-0 gap-1">
        <RefreshCw className="h-3 w-3" />
        {t("notificationLog.statusRetry")}
      </Badge>
    )
  }
  if (status === "circuit_open") {
    return (
      <Badge className="bg-amber-500/20 text-amber-700 dark:text-amber-400 border-0 gap-1">
        <AlertCircle className="h-3 w-3" />
        {t("notificationLog.statusCircuitOpen")}
      </Badge>
    )
  }
  return (
    <Badge className="bg-red-500/20 text-red-700 dark:text-red-400 border-0 gap-1">
      <XCircle className="h-3 w-3" />
      {t("notificationLog.statusFailed")}
    </Badge>
  )
}

function toIsoIfSet(localDate: string): string | undefined {
  if (!localDate) return undefined
  try {
    return new Date(localDate).toISOString()
  } catch {
    return undefined
  }
}

export function buildNotificationLogOptions({
  selectedDvr,
  channel,
  status,
  since,
  until,
  offset,
}: {
  selectedDvr?: string
  channel?: string
  status?: string
  since?: string
  until?: string
  offset: number
}) {
  return {
    dvr_id: selectedDvr && selectedDvr !== "all" ? selectedDvr : undefined,
    channel: channel || undefined,
    status: status || undefined,
    since: toIsoIfSet(since || ""),
    until: toIsoIfSet(until || ""),
    offset,
    limit: PAGE_SIZE,
  }
}

export function NotificationLog() {
  const { selectedDvr } = useDvrSelection()

  const [items, setItems] = useState<NotificationDeliveryItem[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [filterChannel, setFilterChannel] = useState("")
  const [filterStatus, setFilterStatus] = useState("")
  const [filterSince, setFilterSince] = useState("")
  const [filterUntil, setFilterUntil] = useState("")

  const currentPage = Math.floor(offset / PAGE_SIZE) + 1
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const load = useCallback(
    async (off: number) => {
      setIsLoading(true)
      setError(null)
      try {
        const resp = await fetchNotificationLog(buildNotificationLogOptions({
          selectedDvr,
          channel: filterChannel,
          status: filterStatus,
          since: filterSince,
          until: filterUntil,
          offset: off,
        }))
        setItems(resp.items)
        setTotal(resp.total)
        setOffset(off)
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error")
      } finally {
        setIsLoading(false)
      }
    },
    [selectedDvr, filterChannel, filterStatus, filterSince, filterUntil],
  )

  useEffect(() => {
    setOffset(0)
    load(0)
  }, [load])

  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + PAGE_SIZE, total)

  return (
    <div className="space-y-5">
      <Card className="border-border/70 shadow-sm">
        <CardHeader className="px-4 pb-4 pt-5 sm:px-5">
          <div className="flex min-w-0 items-start gap-2">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-blue-500/10"><Bell className="h-5 w-5 text-primary" /></span>
            <CardTitle className="min-w-0 break-words text-[22px] font-semibold leading-tight tracking-tight [overflow-wrap:anywhere] sm:text-2xl"><h1>{t("notificationLog.title")}</h1></CardTitle>
          </div>
          <CardDescription>{t("notificationLog.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-5 grid gap-3 min-[360px]:grid-cols-2 lg:grid-cols-4">
            <Select value={filterChannel} onValueChange={(v) => setFilterChannel(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-11 w-full rounded-xl" aria-label={t("notificationLog.aria.channelFilter")}>
                <SelectValue placeholder={t("notificationLog.filterChannel")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{t("notificationLog.filterChannel")}</SelectItem>
                {CHANNEL_OPTIONS.map((c) => (
                  <SelectItem key={c} value={c}>
                    {c.charAt(0).toUpperCase() + c.slice(1)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={filterStatus} onValueChange={(v) => setFilterStatus(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-11 w-full rounded-xl" aria-label={t("notificationLog.aria.statusFilter")}>
                <SelectValue placeholder={t("notificationLog.filterStatus")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{t("notificationLog.filterStatus")}</SelectItem>
                <SelectItem value="sent">{t("notificationLog.statusSent")}</SelectItem>
                <SelectItem value="retry">{t("notificationLog.statusRetry")}</SelectItem>
                <SelectItem value="failed">{t("notificationLog.statusFailed")}</SelectItem>
                <SelectItem value="circuit_open">{t("notificationLog.statusCircuitOpen")}</SelectItem>
              </SelectContent>
            </Select>

            <div className="flex min-w-0 items-center gap-2 min-[360px]:col-span-2 sm:col-span-1">
              <label htmlFor="notification-log-since" className="whitespace-nowrap text-[13px] text-muted-foreground">{t("notificationLog.filterSince")}</label>
              <Input
                id="notification-log-since"
                type="date"
                className="h-11 min-w-0 w-full rounded-xl text-sm"
                value={filterSince}
                onChange={(e) => setFilterSince(e.target.value)}
                aria-label={t("notificationLog.aria.since")}
              />
            </div>

            <div className="flex min-w-0 items-center gap-2 min-[360px]:col-span-2 sm:col-span-1">
              <label htmlFor="notification-log-until" className="whitespace-nowrap text-[13px] text-muted-foreground">{t("notificationLog.filterUntil")}</label>
              <Input
                id="notification-log-until"
                type="date"
                className="h-11 min-w-0 w-full rounded-xl text-sm"
                value={filterUntil}
                onChange={(e) => setFilterUntil(e.target.value)}
                aria-label={t("notificationLog.aria.until")}
              />
            </div>
          </div>

          {isLoading && (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              <span className="ml-2 text-sm text-muted-foreground">{t("notificationLog.loading")}</span>
            </div>
          )}

          {!isLoading && error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
              <p className="font-medium">{t("notificationLog.errorTitle")}</p>
              <p className="mt-1 opacity-80">{error}</p>
              <Button variant="outline" size="sm" className="mt-3" onClick={() => load(offset)}>
                {t("notificationLog.tryAgain")}
              </Button>
            </div>
          )}

          {!isLoading && !error && items.length === 0 && (
            <div className="py-12 text-center text-muted-foreground">
              <Bell className="mx-auto mb-2 h-8 w-8 opacity-30" />
              <p className="text-sm font-medium">{t("notificationLog.empty")}</p>
              <p className="mt-1 text-xs">{t("notificationLog.emptyHint")}</p>
            </div>
          )}

          {!isLoading && !error && items.length > 0 && (
            <>
              <div className="text-xs text-muted-foreground mb-2">
                {t("notificationLog.showing")
                  .replace("{from}", String(from))
                  .replace("{to}", String(to))
                  .replace("{total}", String(total))}
              </div>
              <div className="space-y-3 lg:hidden" role="list" aria-label={t("notificationLog.tableCaption")}>
                {items.map((item) => (
                  <article key={item.id} role="listitem" className="rounded-xl border bg-background p-3 shadow-sm">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="break-words font-mono text-[13px] font-medium">{item.channel || item.provider_type}</p>
                        <p className="mt-1 text-[13px] leading-5 text-muted-foreground">{formatTimestamp(item.sent_at)}</p>
                      </div>
                      <StatusBadge status={item.status} />
                    </div>
                    <dl className="mt-3 grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-2 border-t pt-3 text-[13px] leading-5">
                      <dt className="text-muted-foreground">{t("notificationLog.colEventType")}</dt>
                      <dd className="max-w-[55vw] break-words text-right">{eventTypeLabel(item.event_type)}</dd>
                      <dt className="text-muted-foreground">{t("notificationLog.colRetries")}</dt>
                      <dd className="text-right tabular-nums">{item.retry_count}</dd>
                      <dt className="text-muted-foreground">{t("notificationLog.colPayload")}</dt>
                      <dd className="text-right tabular-nums">
                        {item.payload_size > 0
                          ? t("notificationLog.bytes").replace("{n}", String(item.payload_size))
                          : "—"}
                      </dd>
                      {item.error ? (
                        <>
                          <dt className="text-muted-foreground">{t("notificationLog.colError")}</dt>
                          <dd className="max-w-[55vw] break-words text-right text-muted-foreground">{item.error}</dd>
                        </>
                      ) : null}
                    </dl>
                  </article>
                ))}
              </div>
              <div
                className="hidden overflow-x-auto rounded-xl border lg:block"
                role="region"
                aria-label={t("notificationLog.tableCaption")}
                tabIndex={0}
              >
                <table className="w-full text-sm">
                  <caption className="sr-only">{t("notificationLog.tableCaption")}</caption>
                  <thead>
                    <tr className="border-b bg-muted/30 text-xs text-muted-foreground">
                      <th scope="col" className="px-3 py-2 text-left">{t("notificationLog.colSentAt")}</th>
                      <th scope="col" className="px-3 py-2 text-left">{t("notificationLog.colChannel")}</th>
                      <th scope="col" className="px-3 py-2 text-left">{t("notificationLog.colEventType")}</th>
                      <th scope="col" className="px-3 py-2 text-left">{t("notificationLog.colStatus")}</th>
                      <th scope="col" className="px-3 py-2 text-right">{t("notificationLog.colRetries")}</th>
                      <th scope="col" className="px-3 py-2 text-right">{t("notificationLog.colPayload")}</th>
                      <th scope="col" className="px-3 py-2 text-left">{t("notificationLog.colError")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => (
                      <tr key={item.id} className="border-b last:border-0 hover:bg-muted/20">
                        <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                          {formatTimestamp(item.sent_at)}
                        </td>
                        <td className="px-3 py-2 font-mono text-xs">{item.channel || item.provider_type}</td>
                        <td className="px-3 py-2 text-xs">{eventTypeLabel(item.event_type)}</td>
                        <td className="px-3 py-2">
                          <StatusBadge status={item.status} />
                        </td>
                        <td className="px-3 py-2 text-right tabular-nums">{item.retry_count}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-xs text-muted-foreground">
                          {item.payload_size > 0
                            ? t("notificationLog.bytes").replace("{n}", String(item.payload_size))
                            : "—"}
                        </td>
                        <td className="max-w-xs whitespace-normal break-words px-3 py-2 text-xs text-muted-foreground [overflow-wrap:anywhere]" title={item.error ?? ""}>
                          {item.error || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="mt-3 flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    Page {currentPage} of {totalPages}
                  </span>
                  <div className="flex gap-1">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={offset === 0}
                      onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={offset + PAGE_SIZE >= total}
                      onClick={() => load(offset + PAGE_SIZE)}
                    >
                      <ChevronRight className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
