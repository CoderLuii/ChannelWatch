"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardHeader } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { PageHeader } from "@/components/base/page-header"
import { ActivityDetailDialog } from "@/components/dashboard/activity-detail-dialog"
import { ClientFilterCombobox, ClientFilterList } from "@/components/activity/client-filter-combobox"
import { FilterGroup, FilterPill, FilterPills, FilterRow, FilterSheet } from "@/components/base/filter-sheet"
import {
  ApiError,
  downloadActivityHistoryCsv,
  fetchActivityClientFilters,
  fetchActivityHistory,
  fetchDvrActivityHistory,
  loadFailureHeadline,
  SESSION_RESTORED_EVENT,
} from "@/lib/api"
import type { ActivityClientFacet, ActivityHistoryResponse, FetchActivityHistoryOptions } from "@/lib/api"
import { describeError } from "@/lib/error-catalog"
import { ROUTINE_ACTIVITY_CLASSES, activityRowText, diskAlertColorClasses, healthEventPresentation, stripLeadingEmoji, recordingEventPresentation } from "@/lib/activity-presentation"
import { downloadBlob, formatCount } from "@/lib/utils"
import { canonicalActivityClientValue } from "@/lib/activity-clients"
import { t } from "@/lib/i18n"
import { useDvrSelection } from "@/lib/dvr-selection-context"
import type { ActivityItem } from "@/lib/types"
import {
  AlertCircle,
  Bell,
  ChevronLeft,
  ChevronRight,
  Clock,
  Download,
  Loader2,
  Play,
  Search,
  Tv,
  Video,
} from "lucide-react"

type ActivitySortOrder = "desc" | "asc"
type WatchHistoryRequestOptions = Omit<FetchActivityHistoryOptions, "dvr_id">

const PAGE_SIZE_OPTIONS = [25, 50, 100]
const ACTIVITY_TYPES = new Set(["all", "channel", "vod", "recording"])

function readUrlFilterState() {
  const params = new URLSearchParams(window.location.search)
  const requestedType = params.get("wh_type") ?? "all"
  const requestedSort = params.get("wh_sort") ?? "desc"
  const requestedPageSize = Number(params.get("wh_page_size") ?? 25)
  return {
    search: params.get("wh_search") ?? "",
    type: ACTIVITY_TYPES.has(requestedType) ? requestedType : "all",
    client: params.get("wh_client")?.trim() || null,
    sort: requestedSort === "asc" ? ("asc" as const) : ("desc" as const),
    pageSize: PAGE_SIZE_OPTIONS.includes(requestedPageSize) ? requestedPageSize : 25,
  }
}

export async function fetchWatchHistoryForDvrSelection(
  selectedDvr: string,
  options: WatchHistoryRequestOptions,
): Promise<ActivityHistoryResponse> {
  if (selectedDvr !== "all") {
    return fetchDvrActivityHistory(selectedDvr, { ...options, scope: "watch" })
  }

  return fetchActivityHistory({ ...options, scope: "watch" })
}

function formatFullTimestamp(timestamp: string): string {
  try {
    const date = new Date(timestamp)
    if (!Number.isFinite(date.getTime()) || date.getTime() <= 0) return "–"
    return date.toLocaleString(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    })
  } catch {
    return timestamp
  }
}

function getActivityIcon(type: string, message?: string) {
  if (type === "recording_event" && message) {
    const Icon = recordingEventPresentation(message).icon
    return <Icon className="h-4 w-4" />
  }
  const health = healthEventPresentation(type)
  if (health) {
    const Icon = health.icon
    return <Icon className="h-4 w-4" />
  }

  switch (type) {
    case "stream_started":
    case "watching_channel":
      return <Tv className="h-4 w-4" />
    case "recording_started":
    case "recording_completed":
    case "recording_event":
      return <Video className="h-4 w-4" />
    case "disk_alert":
      return <AlertCircle className="h-4 w-4" />
    case "vod_playback":
    case "watching_vod":
      return <Play className="h-4 w-4" />
    default:
      return <Bell className="h-4 w-4" />
  }
}

function getIconColorClasses(type: string, message?: string, extra?: ActivityItem["extra"]) {
  if (type === "recording_event" && message) {
    return recordingEventPresentation(message).colorClasses
  }
  const health = healthEventPresentation(type)
  if (health) return health.colorClasses

  switch (type) {
    case "disk_alert":
      return diskAlertColorClasses(extra)
    default:
      return ROUTINE_ACTIVITY_CLASSES
  }
}

function getTypeLabel(type: string) {
  const health = healthEventPresentation(type)
  if (health) return t(health.labelKey)
  switch (type) {
    case "watching_channel":
    case "stream_started":
      return t("type.liveTV")
    case "watching_vod":
    case "vod_playback":
      return t("type.vod")
    case "recording_event":
    case "recording_started":
    case "recording_completed":
    case "recording_scheduled":
    case "recording_stopped":
    case "recording_cancelled":
      return t("type.recording")
    case "disk_alert":
      return t("type.diskAlert")
    default:
      return t("type.activity")
  }
}

export function WatchHistory({ onNavigate }: { onNavigate?: (view: string) => void }) {
  const [filterSheetOpen, setFilterSheetOpen] = useState(false)
  const { selectedDvr, availableDvrs } = useDvrSelection()
  const [items, setItems] = useState<ActivityItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [errorRemediation, setErrorRemediation] = useState<string | null>(null)
  const [errorHeadline, setErrorHeadline] = useState("")
  const [reloadToken, setReloadToken] = useState(0)
  const [failedArtwork, setFailedArtwork] = useState<Set<string>>(() => new Set())
  const [selectedActivity, setSelectedActivity] = useState<ActivityItem | null>(null)
  const dialogTriggerRef = useRef<HTMLButtonElement | null>(null)
  const [searchInput, setSearchInput] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [typeFilter, setTypeFilter] = useState("all")
  const [clientFilter, setClientFilter] = useState<string | null>(null)
  const [clientFacets, setClientFacets] = useState<ActivityClientFacet[]>([])
  const [clientsLoading, setClientsLoading] = useState(false)
  const [clientFacetError, setClientFacetError] = useState(false)
  const [clientStatus, setClientStatus] = useState<string | null>(null)
  const [sortOrder, setSortOrder] = useState<ActivitySortOrder>("desc")
  const [pageSize, setPageSize] = useState(25)
  const [offset, setOffset] = useState(0)
  const pageTopRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const reload = () => setReloadToken((token) => token + 1)
    window.addEventListener(SESSION_RESTORED_EVENT, reload)
    return () => window.removeEventListener(SESSION_RESTORED_EVENT, reload)
  }, [])

  // A new page starts at its first row, not wherever the pager happened to be.
  const turnPage = (nextOffset: number) => {
    setOffset(nextOffset)
    pageTopRef.current?.scrollIntoView({ block: "start" })
    pageTopRef.current?.focus({ preventScroll: true })
  }
  const [urlStateReady, setUrlStateReady] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  useEffect(() => {
    const initial = readUrlFilterState()
    setSearchInput(initial.search)
    setSearchQuery(initial.search.trim())
    setTypeFilter(initial.type)
    setClientFilter(initial.client)
    setSortOrder(initial.sort)
    setPageSize(initial.pageSize)
    setUrlStateReady(true)
  }, [])

  useEffect(() => {
    if (!urlStateReady) return

    const url = new URL(window.location.href)
    const setOptionalParam = (name: string, value: string, defaultValue = "") => {
      if (value && value !== defaultValue) url.searchParams.set(name, value)
      else url.searchParams.delete(name)
    }
    setOptionalParam("wh_search", searchInput.trim())
    setOptionalParam("wh_type", typeFilter, "all")
    setOptionalParam("wh_client", clientFilter ?? "")
    setOptionalParam("wh_sort", sortOrder, "desc")
    setOptionalParam("wh_page_size", String(pageSize), "25")
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`)
  }, [clientFilter, pageSize, searchInput, sortOrder, typeFilter, urlStateReady])

  useEffect(() => {
    setOffset(0)
  }, [selectedDvr])

  useEffect(() => {
    if (!urlStateReady) return
    const controller = new AbortController()
    setClientsLoading(true)
    setClientFacetError(false)

    fetchActivityClientFilters(
      {
        dvr_id: selectedDvr === "all" ? undefined : selectedDvr,
        event_type: typeFilter,
        hours: 0,
        scope: "watch",
      },
      controller.signal,
    )
      .then(({ clients }) => {
        setClientFacets(clients)
        setClientFilter((current) => {
          if (!current) return null
          const canonical = canonicalActivityClientValue(clients, current)
          if (!canonical) {
            setClientStatus(t("activity.clientReset"))
            setOffset(0)
            return null
          }
          return canonical
        })
      })
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return
        setClientFacetError(true)
      })
      .finally(() => {
        if (!controller.signal.aborted) setClientsLoading(false)
      })

    return () => controller.abort()
  }, [selectedDvr, typeFilter, urlStateReady])

  useEffect(() => {
    if (!urlStateReady) return
    const debounceId = window.setTimeout(() => {
      const normalizedQuery = searchInput.trim()
      setOffset(0)
      setSearchQuery((currentQuery) => (currentQuery === normalizedQuery ? currentQuery : normalizedQuery))
    }, 250)

    return () => window.clearTimeout(debounceId)
  }, [searchInput, urlStateReady])

  useEffect(() => {
    if (!urlStateReady) return
    const controller = new AbortController()
    const loadHistory = async () => {
      try {
        setLoading(true)
        setError(null)
        setErrorRemediation(null)

        const response = await fetchWatchHistoryForDvrSelection(selectedDvr, {
          offset,
          limit: pageSize,
          type: typeFilter,
          search: searchQuery,
          sort: sortOrder,
          client: clientFilter ?? undefined,
          signal: controller.signal,
        })

        setItems(response.items)
        setTotal(response.total)
        setHistoryLoaded(true)
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") return
        setError(describeError(err, t("watchHistory.loadError")).message)
        setErrorHeadline(loadFailureHeadline(err, t("watchHistory.thing")))
        setErrorRemediation(err instanceof ApiError ? err.payload.remediation ?? null : null)
        setItems([])
        setTotal(0)
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }

    loadHistory()
    return () => controller.abort()
  }, [clientFilter, offset, pageSize, reloadToken, searchQuery, sortOrder, typeFilter, selectedDvr, urlStateReady])

  const exportHistory = async () => {
    setExporting(true)
    setExportError(null)
    try {
      const blob = await downloadActivityHistoryCsv({
        scope: "watch",
        type: typeFilter,
        search: searchQuery,
        sort: sortOrder,
        client: clientFilter ?? undefined,
        dvr_id: selectedDvr === "all" ? undefined : selectedDvr,
      })
      downloadBlob(blob, "channelwatch-history.csv")
    } catch {
      setExportError(t("watchHistory.exportError"))
    } finally {
      setExporting(false)
    }
  }

  const pageCount = useMemo(() => Math.max(1, Math.ceil(total / pageSize)), [pageSize, total])
  const currentPage = useMemo(() => Math.floor(offset / pageSize) + 1, [offset, pageSize])
  const showingFrom = total === 0 ? 0 : offset + 1
  const showingTo = total === 0 ? 0 : Math.min(offset + items.length, total)
  const canGoPrevious = offset > 0
  const canGoNext = offset + pageSize < total
  const hasNonDefaultFilters = Boolean(searchInput || typeFilter !== "all" || clientFilter || sortOrder !== "desc" || pageSize !== 25)
  const typeOptions = [
    ["all", t("type.all")],
    ["channel", t("type.liveTV")],
    ["vod", t("type.vod")],
    ["recording", t("type.recordings")],
  ] as const
  const sortOptions = [
    ["desc", t("watchHistory.sortNewest")],
    ["asc", t("watchHistory.sortOldest")],
  ] as const
  const typeLabel = typeOptions.find(([value]) => value === typeFilter)?.[1] ?? t("type.all")
  const sortLabel = sortOptions.find(([value]) => value === sortOrder)?.[1] ?? t("watchHistory.sortNewest")
  const clientLabel = clientFacets.find((client) => client.value === clientFilter)?.label ?? clientFilter
  const clearFilters = () => {
    setSearchInput("")
    setSearchQuery("")
    setTypeFilter("all")
    setClientFilter(null)
    setSortOrder("desc")
    setPageSize(25)
    setOffset(0)
    setClientStatus(null)
  }

  return (
    <>
      <div className="space-y-5">
        <PageHeader
          title={t("watchHistory.title")}
          actions={error ? undefined : (
            <>
              {total > 0 ? (
                <div className="text-sm tabular-nums text-muted-foreground">
                  {t("watchHistory.showing", { from: formatCount(showingFrom), to: formatCount(showingTo), total: formatCount(total) })}
                </div>
              ) : null}
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="min-h-11 sm:min-h-9"
                onClick={exportHistory}
                disabled={exporting || loading}
              >
                {exporting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}
                {t("watchHistory.exportCsv")}
              </Button>
            </>
          )}
        />
        <Card>
          <CardHeader className="gap-4">
            <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,9rem),1fr))] gap-3 xl:grid-cols-[minmax(0,1.4fr)_190px_220px_170px_150px]">
              <div className="relative col-span-full xl:col-span-1">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  placeholder={t("watchHistory.searchPlaceholder")}
                  className="pl-9"
                  aria-label={t("watchHistory.aria.search")}
                />
              </div>

              <FilterPills className="col-span-full">
                <FilterPill label={typeLabel} active={typeFilter !== "all"} aria-label={`${typeLabel}. ${t("watchHistory.aria.filterType")}`} onClick={() => setFilterSheetOpen(true)} />
                <FilterPill label={clientLabel ?? t("activity.allClients")} active={Boolean(clientFilter)} aria-label={`${clientLabel ?? t("activity.allClients")}. ${t("watchHistory.aria.filterClient")}`} onClick={() => setFilterSheetOpen(true)} />
                <FilterPill label={sortLabel} active={sortOrder !== "desc"} aria-label={`${sortLabel}. ${t("watchHistory.aria.sort")}`} onClick={() => setFilterSheetOpen(true)} />
              </FilterPills>

              <div className="contents max-md:hidden">
              <Select
                value={typeFilter}
                onValueChange={(value) => {
                  setTypeFilter(value)
                  setOffset(0)
                }}
              >
                <SelectTrigger aria-label={t("watchHistory.aria.filterType")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t("type.all")}</SelectItem>
                  <SelectItem value="channel">{t("type.liveTV")}</SelectItem>
                  <SelectItem value="vod">{t("type.vod")}</SelectItem>
                  <SelectItem value="recording">{t("type.recordings")}</SelectItem>
                </SelectContent>
              </Select>

              <ClientFilterCombobox
                clients={clientFacets}
                value={clientFilter}
                onChange={(value) => {
                  setClientFilter(value)
                  setClientStatus(null)
                  setOffset(0)
                }}
                loading={clientsLoading}
                ariaLabel={t("watchHistory.aria.filterClient")}
              />

              <Select
                value={sortOrder}
                onValueChange={(value) => {
                  setSortOrder(value as ActivitySortOrder)
                  setOffset(0)
                }}
              >
                <SelectTrigger aria-label={t("watchHistory.aria.sort")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="desc">{t("watchHistory.sortNewest")}</SelectItem>
                  <SelectItem value="asc">{t("watchHistory.sortOldest")}</SelectItem>
                </SelectContent>
              </Select>

              <Select
                value={String(pageSize)}
                onValueChange={(value) => {
                  setPageSize(Number(value))
                  setOffset(0)
                }}
              >
                <SelectTrigger aria-label={t("watchHistory.aria.pageSize")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PAGE_SIZE_OPTIONS.map((option) => (
                    <SelectItem key={option} value={String(option)}>
                      {t("watchHistory.perPage", { count: option })}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              </div>
            </div>
            <FilterSheet open={filterSheetOpen} onOpenChange={setFilterSheetOpen} clearLabel={t("activity.clearFilters")} onClear={hasNonDefaultFilters ? clearFilters : undefined}>
              <FilterGroup title={t("activity.types")}>
                {typeOptions.map(([value, label]) => (
                  <FilterRow key={value} label={label} selected={typeFilter === value} onSelect={() => { setTypeFilter(value); setOffset(0) }} />
                ))}
              </FilterGroup>
              <FilterGroup title={t("activity.client")} plain>
                <ClientFilterList clients={clientFacets} value={clientFilter} onChange={(value) => { setClientFilter(value); setClientStatus(null); setOffset(0) }} listClassName="max-h-44" />
              </FilterGroup>
              <FilterGroup title={t("filters.sort")}>
                {sortOptions.map(([value, label]) => (
                  <FilterRow key={value} label={label} selected={sortOrder === value} onSelect={() => { setSortOrder(value as ActivitySortOrder); setOffset(0) }} />
                ))}
              </FilterGroup>
              <FilterGroup title={t("filters.perPage")}>
                {PAGE_SIZE_OPTIONS.map((option) => (
                  <FilterRow key={option} label={t("watchHistory.perPage", { count: option })} selected={pageSize === option} onSelect={() => { setPageSize(option); setOffset(0) }} />
                ))}
              </FilterGroup>
            </FilterSheet>
            {(exportError || clientStatus || clientFacetError || hasNonDefaultFilters) && <div className="flex min-h-6 flex-wrap items-center justify-between gap-2">
              <div aria-live="polite" className="text-sm text-muted-foreground">
                {exportError ?? clientStatus ?? (clientFacetError && !error ? t("activity.clientFilterError") : "")}
              </div>
              {hasNonDefaultFilters ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={clearFilters}
                >
                  {t("activity.clearFilters")}
                </Button>
              ) : null}
            </div>}
          </CardHeader>
        </Card>

        <div ref={pageTopRef} tabIndex={-1} role="group" aria-label={t("watchHistory.pageOf", { page: currentPage, total: pageCount })} className="scroll-mt-[calc(var(--app-header-height,4rem)+0.5rem)] focus:outline-none" />
        <Card>
          <CardContent className="relative p-0">
            {loading && !historyLoaded ? (
              <div className="flex min-h-[360px] items-center justify-center">
                <div className="loading-delayed flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("watchHistory.loading")}
                </div>
              </div>
            ) : error ? (
              <div className="flex min-h-[360px] flex-col items-center justify-center gap-3 px-6 text-center">
                <AlertCircle className="h-8 w-8 text-muted-foreground/50" />
                <div className="min-w-0 max-w-xl space-y-1 [overflow-wrap:anywhere]">
                  <p className="text-sm font-medium">{errorHeadline}</p>
                  {errorRemediation && <p className="text-sm text-muted-foreground">{errorRemediation}</p>}
                  {error === errorHeadline ? null : (
                    <details className="text-xs text-muted-foreground">
                      <summary className="cursor-pointer">{t("notificationLog.errorDetail")}</summary>
                      <p className="mt-1">{error}</p>
                    </details>
                  )}
                </div>
                <div className="flex flex-wrap justify-center gap-2">
                  <Button
                    variant="outline"
                    onClick={() => {
                      setReloadToken((currentToken) => currentToken + 1)
                    }}
                  >
                    {t("watchHistory.tryAgain")}
                  </Button>
                  {onNavigate ? <Button variant="ghost" onClick={() => onNavigate("diagnostics")}>{t("diagnostics.open")}</Button> : null}
                </div>
              </div>
            ) : items.length === 0 ? (
              <div className="flex min-h-[360px] flex-col items-center justify-center gap-2 px-6 text-center">
                <Clock className="h-8 w-8 text-muted-foreground/40" />
                <p className="text-sm font-medium">{t(hasNonDefaultFilters ? "watchHistory.emptyTitle" : "watchHistory.emptyNoneTitle")}</p>
                <p className="text-sm text-muted-foreground">
                  {clientFilter
                    ? t("watchHistory.emptyClientHint", { client: clientFilter })
                    : t(hasNonDefaultFilters ? "watchHistory.emptyHint" : "watchHistory.emptyNoneHint")}
                </p>
              </div>
            ) : (
              <div className="divide-y divide-hairline">
                {items.map((activity) => {
                  const colorClasses = getIconColorClasses(activity.type, activity.message, activity.extra)
                  const artworkKey = `${activity.id}:${activity.image_url || ""}`
                  const hasImage = Boolean(activity.image_url && !failedArtwork.has(artworkKey))
                  const row = activityRowText(activity, stripLeadingEmoji(activity.title), getTypeLabel(activity.type))
                  const showChannel = activity.channel_name && activity.channel_name !== row.title && activity.channel_name !== row.detail

                  return (
                    <button
                      key={activity.id}
                      type="button"
                      onClick={(event) => {
                        dialogTriggerRef.current = event.currentTarget
                        setSelectedActivity(activity)
                      }}
                      className="press-row flex w-full flex-wrap items-start gap-x-3 gap-y-1 px-4 py-3 text-left transition-colors hover:bg-fill"
                    >
                      <div className={`mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full ${colorClasses.split(" ")[0]}`}>
                        <div className={colorClasses.split(" ").slice(1).join(" ")}>
                          {getActivityIcon(activity.type, activity.message)}
                        </div>
                      </div>

                      <div className="min-w-0 flex-1 basis-[8rem]">
                        <p className="line-clamp-2 min-w-0 break-words text-sm font-medium leading-snug" title={row.title}>{row.title}</p>
                        <p className="mt-0.5 min-w-0 break-words text-xs leading-snug text-muted-foreground">
                          {row.detail ? <span title={row.detail}>{row.detail}<span className="hidden sm:inline"> · </span></span> : null}
                          <span className="block whitespace-nowrap tabular-nums sm:inline">{formatFullTimestamp(activity.timestamp)}</span>
                        </p>
                        {showChannel || (activity.dvr_name && availableDvrs.length > 1) ? (
                          <p className="mt-0.5 min-w-0 truncate text-xs text-muted-foreground">
                            {[showChannel ? activity.channel_name : null, availableDvrs.length > 1 ? activity.dvr_name : null].filter(Boolean).join(" · ")}
                          </p>
                        ) : null}
                      </div>

                      {hasImage ? (
                        <div aria-hidden="true" className="flex h-10 w-16 shrink-0 items-center justify-center overflow-hidden rounded-thumb bg-fill sm:h-14 sm:w-24">
                          {/* eslint-disable-next-line @next/next/no-img-element -- static export with dynamic remote artwork URL; next/image optimizer unavailable */}
                          <img
                            src={activity.image_url}
                            alt=""
                            onError={() => {
                              setFailedArtwork((current) => {
                                if (current.has(artworkKey)) return current
                                const next = new Set(current)
                                next.add(artworkKey)
                                return next
                              })
                            }}
                            className="h-full w-full object-contain"
                            loading="lazy"
                          />
                        </div>
                      ) : null}
                    </button>
                  )
                })}
              </div>
            )}
            {loading && historyLoaded ? (
              <div className="absolute inset-0 z-10 flex items-start justify-center bg-fill pt-6" aria-live="polite">
                <div className="flex items-center gap-2 rounded-control border border-hairline bg-raised px-3 py-2 text-sm text-muted-foreground shadow-3">
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  {t("watchHistory.loading")}
                </div>
              </div>
            ) : null}
          </CardContent>
        </Card>

        {pageCount > 1 && !error ? (
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-sm text-muted-foreground">
            {t("watchHistory.pageOf", { page: currentPage, total: pageCount })}
          </div>
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => turnPage(Math.max(0, offset - pageSize))}
              disabled={!canGoPrevious || loading}
              className="min-w-0 flex-1 sm:flex-none"
            >
              <ChevronLeft className="mr-1 h-4 w-4" />
              {t("common.previous")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => turnPage(offset + pageSize)}
              disabled={!canGoNext || loading}
              className="min-w-0 flex-1 sm:flex-none"
            >
              {t("common.next")}
              <ChevronRight className="ml-1 h-4 w-4" />
            </Button>
          </div>
        </div>
        ) : null}
      </div>

      <ActivityDetailDialog
        activity={selectedActivity}
        open={!!selectedActivity}
        onOpenChange={(open) => {
          if (!open) setSelectedActivity(null)
        }}
        onCloseAutoFocus={(event) => {
          const trigger = dialogTriggerRef.current
          if (trigger?.isConnected && trigger.getClientRects().length > 0) {
            event.preventDefault()
            trigger.focus()
          }
        }}
      />
    </>
  )
}
