"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { StatusText } from "@/components/base/badge"
import { StatusBadge } from "@/components/base/status-badge"
import { Button } from "@/components/base/button"
import { Card, CardContent } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { PageHeader } from "@/components/base/page-header"
import { FilterGroup, FilterPill, FilterPills, FilterRow, FilterSheet } from "@/components/base/filter-sheet"
import { describeDeliveryError, describeError } from "@/lib/error-catalog"
import { SESSION_RESTORED_EVENT, fetchNotificationLog, loadFailureHeadline } from "@/lib/api"
import type { NotificationDeliveryItem } from "@/lib/api"
import { t } from "@/lib/i18n"
import { healthEventPresentation } from "@/lib/activity-presentation"
import { useDvrSelection } from "@/lib/dvr-selection-context"
import { formatBytes, formatCount } from "@/lib/utils"
import { AlertCircle, Bell, Calendar, ChevronLeft, ChevronRight, Loader2, RefreshCw, XCircle } from "lucide-react"

const PAGE_SIZE = 25
const STATUS_OPTIONS = ["sent", "retry", "failed", "circuit_open"]
const CHANNEL_OPTIONS = ["apprise", "webhook"]

function eventTypeLabel(type?: string): string {
  const health = healthEventPresentation(type)
  if (health) return t(health.labelKey)
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
      return type?.replaceAll("_", " ") || "–"
  }
}

function channelFilterLabel(channel: string): string {
  return t(channel === "webhook" ? "notificationLog.channelWebhook" : "notificationLog.channelApprise")
}

// A provider's first destination is "pushover", its second "pushover:2".
const DESTINATION_NAMES: Record<string, string> = {
  pushover: "provider.pushover.name",
  discord: "provider.discord.name",
  telegram: "provider.telegram.name",
  email: "provider.email.name",
  slack: "provider.slack.name",
  gotify: "provider.gotify.name",
  matrix: "provider.matrix.name",
  custom: "provider.custom.name",
  webhook: "routing.webhook",
}

export function destinationLabel(item: Pick<NotificationDeliveryItem, "channel" | "channel_id">): string {
  const nameKey = DESTINATION_NAMES[(item.channel_id || "").split(":")[0] ?? ""] ?? DESTINATION_NAMES[item.channel]
  return nameKey ? t(nameKey) : t("notificationLog.channelApprise")
}

function formatTimestamp(ts: string): string {
  try {
    const date = new Date(ts)
    if (!Number.isFinite(date.getTime()) || date.getTime() <= 0) return "–"
    return date.toLocaleString(undefined, {
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

function DeliveryStatus({ status }: { status: string }) {
  if (status === "sent") {
    return <StatusText className="shrink-0 whitespace-nowrap">{t("notificationLog.statusSent")}</StatusText>
  }
  if (status === "retry") {
    return (
      <StatusBadge variant="info" className="shrink-0 whitespace-nowrap">
        <RefreshCw className="h-3 w-3" />
        {t("notificationLog.statusRetry")}
      </StatusBadge>
    )
  }
  if (status === "circuit_open") {
    return (
      <StatusBadge variant="warning" className="shrink-0 whitespace-nowrap">
        <AlertCircle className="h-3 w-3" />
        {t("notificationLog.statusCircuitOpen")}
      </StatusBadge>
    )
  }
  return (
    <StatusBadge variant="danger" className="shrink-0 whitespace-nowrap">
      <XCircle className="h-3 w-3" />
      {t("notificationLog.statusFailed")}
    </StatusBadge>
  )
}

// <input type="date"> yields YYYY-MM-DD. Parse it as a local calendar day:
// "since" starts at local midnight and "until" includes the whole day.
function toIsoIfSet(localDate: string, edge: "start" | "end" = "start"): string | undefined {
  if (!localDate) return undefined
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(localDate)
  const date = match
    ? edge === "end"
      ? new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 23, 59, 59, 999)
      : new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 0, 0, 0, 0)
    : new Date(localDate)
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString()
}

/** The phone row's one line of facts. Zero retries and an empty payload say nothing, so they stay out. */
export function deliveryRowFacts(item: Pick<NotificationDeliveryItem, "event_type" | "payload_size" | "retry_count">): string {
  const retries = item.retry_count === 1
    ? t("notificationLog.retryOne")
    : t("notificationLog.retryMany", { n: formatCount(item.retry_count) })
  return [
    eventTypeLabel(item.event_type),
    item.payload_size > 0 ? formatBytes(item.payload_size) : null,
    item.retry_count > 0 ? retries : null,
  ].filter(Boolean).join(" · ")
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
    since: toIsoIfSet(since || "", "start"),
    until: toIsoIfSet(until || "", "end"),
    offset,
    limit: PAGE_SIZE,
  }
}

export function NotificationLog({ onNavigate }: { onNavigate?: (view: string) => void }) {
  const { selectedDvr } = useDvrSelection()
  const [filterSheetOpen, setFilterSheetOpen] = useState(false)
  const [filterSheetUsed, setFilterSheetUsed] = useState(false)
  const openFilterSheet = () => {
    setFilterSheetUsed(true)
    setFilterSheetOpen(true)
  }

  const [items, setItems] = useState<NotificationDeliveryItem[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [errorHeadline, setErrorHeadline] = useState("")

  const [filterChannel, setFilterChannel] = useState("")
  const [filterStatus, setFilterStatus] = useState("")
  const [filterSince, setFilterSince] = useState("")
  const [filterUntil, setFilterUntil] = useState("")
  const hasActiveFilter = Boolean(filterChannel || filterStatus || filterSince || filterUntil)

  const currentPage = Math.floor(offset / PAGE_SIZE) + 1
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const requestSeqRef = useRef(0)
  const pageTopRef = useRef<HTMLDivElement | null>(null)

  const load = useCallback(
    async (off: number) => {
      const requestId = ++requestSeqRef.current
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
        if (requestId !== requestSeqRef.current) return
        setItems(resp.items)
        setTotal(resp.total)
        setOffset(off)
      } catch (err) {
        if (requestId !== requestSeqRef.current) return
        setError(describeError(err, t("common.unknownError")).message)
        setErrorHeadline(loadFailureHeadline(err, t("notificationLog.thing")))
      } finally {
        if (requestId === requestSeqRef.current) setIsLoading(false)
      }
    },
    [selectedDvr, filterChannel, filterStatus, filterSince, filterUntil],
  )

  useEffect(() => {
    setOffset(0)
    load(0)
  }, [load])

  useEffect(() => {
    if (typeof window === "undefined") return
    const reload = () => void load(offset)
    window.addEventListener(SESSION_RESTORED_EVENT, reload)
    return () => window.removeEventListener(SESSION_RESTORED_EVENT, reload)
  }, [load, offset])

  // A new page starts at its first row, and focus stays with the list while the pager is replaced.
  const turnPage = (nextOffset: number) => {
    void load(nextOffset)
    pageTopRef.current?.scrollIntoView({ block: "start" })
    pageTopRef.current?.focus({ preventScroll: true })
  }

  const from = total === 0 ? 0 : offset + 1
  const to = Math.min(offset + PAGE_SIZE, total)
  // Nothing to filter yet means no filters to look at.
  const showFilters = hasActiveFilter || total > 0
  const channelLabel = filterChannel ? channelFilterLabel(filterChannel) : t("notificationLog.filterChannel")
  const statusOptions = [
    ["sent", t("notificationLog.statusSent")],
    ["retry", t("notificationLog.statusRetry")],
    ["failed", t("notificationLog.statusFailed")],
    ["circuit_open", t("notificationLog.statusCircuitOpen")],
  ] as const
  const statusLabel = statusOptions.find(([value]) => value === filterStatus)?.[1] ?? t("notificationLog.filterStatus")
  const dateRangeLabel = filterSince || filterUntil ? [filterSince, filterUntil].filter(Boolean).join(" – ") : t("filters.anyDate")
  const clearFilters = () => {
    setFilterChannel("")
    setFilterStatus("")
    setFilterSince("")
    setFilterUntil("")
  }
  const dateFields = ([
    ["notification-log-since", "notificationLog.filterSince", "notificationLog.aria.since", filterSince, setFilterSince],
    ["notification-log-until", "notificationLog.filterUntil", "notificationLog.aria.until", filterUntil, setFilterUntil],
  ] as const).map(([id, labelKey, ariaKey, value, setValue]) => (
    <div key={id} data-testid={`${id}-row`} className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 col-span-full sm:col-span-1">
      <label htmlFor={id} className="shrink-0 basis-[3rem] whitespace-nowrap text-xs text-muted-foreground">{t(labelKey)}</label>
      <div className="relative min-w-0 flex-1 basis-[11rem]">
        <Input
          id={id}
          type="date"
          className="date-input h-11 w-full min-w-0 pr-10 text-left text-sm"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          aria-label={t(ariaKey)}
        />
        <Calendar className="pointer-events-none absolute right-3 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
      </div>
    </div>
  ))

  return (
    <div className="space-y-5">
      <PageHeader title={t("notificationLog.title")} description={t("notificationLog.description")} />
      <div ref={pageTopRef} tabIndex={-1} role="group" aria-label={t("watchHistory.pageOf", { page: formatCount(currentPage), total: formatCount(totalPages) })} className="scroll-mt-[calc(var(--app-header-height,4rem)+0.5rem)] focus:outline-none" />
      <Card>
        <CardContent className="px-4 pt-4">
          {showFilters ? (
            <FilterPills className="mb-4">
              <FilterPill label={channelLabel} active={Boolean(filterChannel)} onClick={openFilterSheet} />
              <FilterPill label={statusLabel} active={Boolean(filterStatus)} onClick={openFilterSheet} />
              <FilterPill label={dateRangeLabel} active={Boolean(filterSince || filterUntil)} onClick={openFilterSheet} />
            </FilterPills>
          ) : null}
          {filterSheetUsed ? (
          <FilterSheet open={filterSheetOpen} onOpenChange={setFilterSheetOpen} clearLabel={t("activity.clearFilters")} onClear={hasActiveFilter ? clearFilters : undefined}>
            <FilterGroup title={t("notificationLog.colChannel")}>
              <FilterRow label={t("notificationLog.filterChannel")} selected={!filterChannel} onSelect={() => setFilterChannel("")} />
              {CHANNEL_OPTIONS.map((c) => (
                <FilterRow key={c} label={channelFilterLabel(c)} selected={filterChannel === c} onSelect={() => setFilterChannel(c)} />
              ))}
            </FilterGroup>
            <FilterGroup title={t("notificationLog.colStatus")}>
              <FilterRow label={t("notificationLog.filterStatus")} selected={!filterStatus} onSelect={() => setFilterStatus("")} />
              {statusOptions.map(([value, label]) => (
                <FilterRow key={value} label={label} selected={filterStatus === value} onSelect={() => setFilterStatus(value)} />
              ))}
            </FilterGroup>
            <FilterGroup title={t("filters.dates")} plain>
              <div className="space-y-3">{filterSheetOpen ? dateFields : null}</div>
            </FilterGroup>
          </FilterSheet>
          ) : null}
          <div className={`mb-5 grid grid-cols-[repeat(auto-fit,minmax(min(100%,9rem),1fr))] gap-3 max-md:hidden lg:grid-cols-4 ${showFilters ? "" : "hidden"}`}>
            <Select value={filterChannel} onValueChange={(v) => setFilterChannel(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-11 w-full" aria-label={t("notificationLog.aria.channelFilter")}>
                <SelectValue placeholder={t("notificationLog.filterChannel")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="_all">{t("notificationLog.filterChannel")}</SelectItem>
                {CHANNEL_OPTIONS.map((c) => (
                  <SelectItem key={c} value={c}>
                    {channelFilterLabel(c)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select value={filterStatus} onValueChange={(v) => setFilterStatus(v === "_all" ? "" : v)}>
              <SelectTrigger className="h-11 w-full" aria-label={t("notificationLog.aria.statusFilter")}>
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

            {filterSheetOpen ? null : dateFields}
          </div>

          {isLoading && (
            <div className="loading-delayed flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              <span className="ml-2 text-sm text-muted-foreground">{t("notificationLog.loading")}</span>
            </div>
          )}

          {!isLoading && error && (
            <div className="rounded-control border border-danger-border bg-danger-bg p-4 text-sm text-danger-fg">
              <p className="font-medium">{errorHeadline}</p>
              {error === errorHeadline ? null : (
                <details className="mt-1 text-xs opacity-80">
                  <summary className="cursor-pointer">{t("notificationLog.errorDetail")}</summary>
                  <p className="mt-1 [overflow-wrap:anywhere]">{error}</p>
                </details>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="outline" size="sm" className="min-h-11" onClick={() => load(offset)}>
                  {t("notificationLog.tryAgain")}
                </Button>
                {onNavigate ? <Button variant="ghost" size="sm" className="min-h-11" onClick={() => onNavigate("diagnostics")}>{t("diagnostics.open")}</Button> : null}
              </div>
            </div>
          )}

          {!isLoading && !error && items.length === 0 && (
            <div className="py-12 text-center text-muted-foreground">
              <Bell className="mx-auto mb-2 h-8 w-8 opacity-30" />
              <p className="text-sm font-medium">{t(hasActiveFilter ? "notificationLog.emptyFiltered" : "notificationLog.empty")}</p>
              <p className="mt-1 text-xs">{t(hasActiveFilter ? "notificationLog.emptyFilteredHint" : "notificationLog.emptyHint")}</p>
              {!hasActiveFilter && onNavigate ? (
                <Button variant="outline" size="sm" className="mt-3 min-h-11" onClick={() => onNavigate("settings:notifications")}>{t("settings.goToNotifications")}</Button>
              ) : null}
              {!hasActiveFilter && onNavigate ? (
                <Button variant="ghost" size="sm" className="ml-2 mt-3 min-h-11" onClick={() => onNavigate("diagnostics")}>{t("diagnostics.open")}</Button>
              ) : null}
            </div>
          )}

          {!isLoading && !error && items.length > 0 && (
            <>
              <div className="text-xs text-muted-foreground mb-2">
                {t("notificationLog.showing")
                  .replace("{from}", formatCount(from))
                  .replace("{to}", formatCount(to))
                  .replace("{total}", formatCount(total))}
              </div>
              <div className="space-y-3 lg:hidden" role="list" aria-label={t("notificationLog.tableCaption")}>
                {items.map((item) => (
                  <article key={item.id} role="listitem" className="rounded-control border border-hairline p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="break-words text-xs font-medium">{destinationLabel(item)}</p>
                        <p className="mt-1 text-xs leading-5 text-muted-foreground">{formatTimestamp(item.sent_at)}</p>
                        <p className="break-words text-xs leading-5 text-muted-foreground">{deliveryRowFacts(item)}</p>
                      </div>
                      <DeliveryStatus status={item.status} />
                    </div>
                    {item.error ? (
                      <div className="mt-2 text-xs leading-5 text-muted-foreground [overflow-wrap:anywhere]">
                        {describeDeliveryError(item.error)}
                        <details className="group/details mt-1">
                          <summary className="press-row inline-flex min-h-11 cursor-pointer items-center gap-1 rounded-thumb pr-1">
                            <ChevronRight className="size-4 shrink-0 transition-transform duration-[var(--motion-base)] ease-out group-open/details:rotate-90 motion-reduce:transition-none" aria-hidden="true" />
                            {t("notificationLog.errorDetail")}
                          </summary>
                          <p className="font-mono">{item.error}</p>
                        </details>
                      </div>
                    ) : null}
                  </article>
                ))}
              </div>
              <div
                className="hidden overflow-x-auto rounded-control border border-hairline lg:block"
                role="region"
                aria-label={t("notificationLog.tableCaption")}
                tabIndex={0}
              >
                <table className="w-full text-sm">
                  <caption className="sr-only">{t("notificationLog.tableCaption")}</caption>
                  <thead>
                    <tr className="border-b bg-fill text-xs text-muted-foreground">
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
                      <tr key={item.id} className="border-b last:border-0">
                        <td className="whitespace-nowrap px-3 py-2 text-xs text-muted-foreground">
                          {formatTimestamp(item.sent_at)}
                        </td>
                        <td className="px-3 py-2 text-xs">{destinationLabel(item)}</td>
                        <td className="px-3 py-2 text-xs">{eventTypeLabel(item.event_type)}</td>
                        <td className="px-3 py-2">
                          <DeliveryStatus status={item.status} />
                        </td>
                        <td className="px-3 py-2 text-right text-xs tabular-nums">{item.retry_count}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-xs text-muted-foreground">
                          {item.payload_size > 0
                            ? formatBytes(item.payload_size)
                            : "–"}
                        </td>
                        <td className="max-w-xs whitespace-normal break-words px-3 py-2 text-xs text-muted-foreground [overflow-wrap:anywhere]" title={item.error ?? ""}>
                          {item.error ? describeDeliveryError(item.error) : "–"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {totalPages > 1 && (
                <div className="mt-3 flex items-center justify-between">
                  <span className="text-xs text-muted-foreground">
                    {t("watchHistory.pageOf", { page: formatCount(currentPage), total: formatCount(totalPages) })}
                  </span>
                  <div className="flex gap-1">
                    <Button
                      variant="outline"
                      size="sm"
                      aria-label={t("common.previous")}
                      className="min-h-11 min-w-11"
                      disabled={offset === 0}
                      onClick={() => turnPage(Math.max(0, offset - PAGE_SIZE))}
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      aria-label={t("common.next")}
                      className="min-h-11 min-w-11"
                      disabled={offset + PAGE_SIZE >= total}
                      onClick={() => turnPage(offset + PAGE_SIZE)}
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
