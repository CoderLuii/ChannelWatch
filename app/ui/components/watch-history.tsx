"use client"

import { useEffect, useMemo, useState } from "react"
import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/base/card"
import { Input } from "@/components/base/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { ActivityDetailDialog } from "@/components/dashboard/activity-detail-dialog"
import { fetchActivityHistory, fetchDvrActivityHistory } from "@/lib/api"
import type { ActivityHistoryResponse, FetchActivityHistoryOptions } from "@/lib/api"
import { t } from "@/lib/i18n"
import { useDvrSelection } from "@/lib/dvr-selection-context"
import type { ActivityItem } from "@/lib/types"
import {
  AlertCircle,
  Bell,
  Calendar,
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  Clock,
  Loader2,
  Play,
  Search,
  Square,
  Tv,
  Video,
  X,
} from "lucide-react"

type ActivitySortOrder = "desc" | "asc"
type WatchHistoryRequestOptions = Omit<FetchActivityHistoryOptions, "dvr_id">

const PAGE_SIZE_OPTIONS = [25, 50, 100]

export async function fetchWatchHistoryForDvrSelection(
  selectedDvr: string,
  options: WatchHistoryRequestOptions,
): Promise<ActivityHistoryResponse> {
  if (selectedDvr !== "all") {
    return fetchDvrActivityHistory(selectedDvr, options)
  }

  return fetchActivityHistory(options)
}

function formatFullTimestamp(timestamp: string): string {
  try {
    const date = new Date(timestamp)
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
    if (message.startsWith("Scheduled:")) return <Calendar className="h-4 w-4" />
    if (message.startsWith("Cancelled:")) return <X className="h-4 w-4" />
    if (message.startsWith("Recording(") || message.startsWith("Recording (")) return <Video className="h-4 w-4" />
    if (message.startsWith("Completed")) return <CheckCircle className="h-4 w-4" />
    if (message.startsWith("Stopped:")) return <Square className="h-4 w-4" />
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

function getIconColorClasses(type: string, message?: string) {
  if (type === "recording_event" && message) {
    if (message.startsWith("Scheduled:")) return "bg-amber-500/20 text-amber-600 dark:text-amber-400"
    if (message.startsWith("Cancelled:")) return "bg-red-500/20 text-red-600 dark:text-red-400"
    if (message.startsWith("Recording(") || message.startsWith("Recording (")) return "bg-emerald-500/20 text-emerald-600 dark:text-emerald-400"
    if (message.startsWith("Completed")) return "bg-purple-500/20 text-purple-600 dark:text-purple-400"
    if (message.startsWith("Stopped:")) return "bg-slate-500/20 text-slate-600 dark:text-slate-400"
  }

  switch (type) {
    case "stream_started":
    case "watching_channel":
      return "bg-blue-500/20 text-blue-600 dark:text-blue-400"
    case "vod_playback":
    case "watching_vod":
      return "bg-amber-500/20 text-amber-600 dark:text-amber-400"
    case "recording_started":
    case "recording_event":
      return "bg-emerald-500/20 text-emerald-600 dark:text-emerald-400"
    case "recording_completed":
      return "bg-purple-500/20 text-purple-600 dark:text-purple-400"
    case "disk_alert":
      return "bg-red-500/20 text-red-600 dark:text-red-400"
    default:
      return "bg-slate-500/20 text-slate-600 dark:text-slate-400"
  }
}

function getTypeLabel(type: string) {
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

export function WatchHistory() {
  const { selectedDvr } = useDvrSelection()
  const [items, setItems] = useState<ActivityItem[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [reloadToken, setReloadToken] = useState(0)
  const [selectedActivity, setSelectedActivity] = useState<ActivityItem | null>(null)
  const [searchInput, setSearchInput] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [typeFilter, setTypeFilter] = useState("all")
  const [sortOrder, setSortOrder] = useState<ActivitySortOrder>("desc")
  const [pageSize, setPageSize] = useState(25)
  const [offset, setOffset] = useState(0)

  useEffect(() => {
    setOffset(0)
  }, [selectedDvr])

  useEffect(() => {
    const debounceId = window.setTimeout(() => {
      const normalizedQuery = searchInput.trim()
      setOffset(0)
      setSearchQuery((currentQuery) => (currentQuery === normalizedQuery ? currentQuery : normalizedQuery))
    }, 250)

    return () => window.clearTimeout(debounceId)
  }, [searchInput])

  useEffect(() => {
    const loadHistory = async () => {
      try {
        setLoading(true)
        setError(null)

        const response = await fetchWatchHistoryForDvrSelection(selectedDvr, {
          offset,
          limit: pageSize,
          type: typeFilter,
          search: searchQuery,
          sort: sortOrder,
        })

        setItems(response.items)
        setTotal(response.total)
      } catch (err) {
        setError(err instanceof Error ? err.message : t("watchHistory.loadError"))
        setItems([])
        setTotal(0)
      } finally {
        setLoading(false)
      }
    }

    loadHistory()
  }, [offset, pageSize, reloadToken, searchQuery, sortOrder, typeFilter, selectedDvr])

  const pageCount = useMemo(() => Math.max(1, Math.ceil(total / pageSize)), [pageSize, total])
  const currentPage = useMemo(() => Math.floor(offset / pageSize) + 1, [offset, pageSize])
  const showingFrom = total === 0 ? 0 : offset + 1
  const showingTo = total === 0 ? 0 : Math.min(offset + items.length, total)
  const canGoPrevious = offset > 0
  const canGoNext = offset + pageSize < total

  return (
    <>
      <div className="space-y-6">
        <Card>
          <CardHeader className="gap-4">
            <div className="flex flex-col gap-2 lg:flex-row lg:items-end lg:justify-between">
              <div className="space-y-1">
                <div className="flex items-center gap-2">
                  <Clock className="h-5 w-5 text-primary" />
                  <CardTitle>{t("watchHistory.title")}</CardTitle>
                  <Badge variant="secondary">{total}</Badge>
                </div>
                <CardDescription>
                  {t("watchHistory.description")}
                </CardDescription>
              </div>
              <div className="text-sm text-muted-foreground">
                {t("watchHistory.showing", { from: showingFrom, to: showingTo, total })}
              </div>
            </div>

            <div className="grid gap-3 lg:grid-cols-[minmax(0,1.4fr)_200px_180px_160px]">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  placeholder={t("watchHistory.searchPlaceholder")}
                  className="pl-9"
                  aria-label={t("watchHistory.aria.search")}
                />
              </div>

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
                  <SelectItem value="disk">{t("type.diskAlerts")}</SelectItem>
                </SelectContent>
              </Select>

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
          </CardHeader>
        </Card>

        <Card>
          <CardContent className="p-0">
            {loading ? (
              <div className="flex min-h-[360px] items-center justify-center">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("watchHistory.loading")}
                </div>
              </div>
            ) : error ? (
              <div className="flex min-h-[360px] flex-col items-center justify-center gap-3 px-6 text-center">
                <AlertCircle className="h-8 w-8 text-muted-foreground/50" />
                <div className="space-y-1">
                  <p className="text-sm font-medium">{t("watchHistory.errorTitle")}</p>
                  <p className="text-sm text-muted-foreground">{error}</p>
                </div>
                  <Button
                  variant="outline"
                  onClick={() => {
                    setReloadToken((currentToken) => currentToken + 1)
                  }}
                >
                  {t("watchHistory.tryAgain")}
                </Button>
              </div>
            ) : items.length === 0 ? (
              <div className="flex min-h-[360px] flex-col items-center justify-center gap-2 px-6 text-center">
                <Clock className="h-8 w-8 text-muted-foreground/40" />
                <p className="text-sm font-medium">{t("watchHistory.emptyTitle")}</p>
                <p className="text-sm text-muted-foreground">
                  {t("watchHistory.emptyHint")}
                </p>
              </div>
            ) : (
              <div className="divide-y divide-border/40">
                {items.map((activity) => {
                  const colorClasses = getIconColorClasses(activity.type, activity.message)
                  const hasImage = !!activity.image_url

                  return (
                    <button
                      key={activity.id}
                      type="button"
                      onClick={() => setSelectedActivity(activity)}
                      className="flex w-full flex-col gap-3 p-4 text-left transition-colors hover:bg-muted/40 sm:flex-row sm:items-start"
                    >
                      <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${colorClasses.split(" ")[0]}`}>
                        <div className={colorClasses.split(" ").slice(1).join(" ")}>
                          {getActivityIcon(activity.type, activity.message)}
                        </div>
                      </div>

                      {hasImage && (
                        <div className="h-16 w-11 shrink-0 overflow-hidden rounded-md border border-border/40 bg-muted">
                          {/* eslint-disable-next-line @next/next/no-img-element -- static export with dynamic remote artwork URL; next/image optimizer unavailable */}
                          <img
                            src={activity.image_url}
                            alt=""
                            className="h-full w-full object-cover"
                            loading="lazy"
                          />
                        </div>
                      )}

                      <div className="min-w-0 flex-1 space-y-2">
                        <div className="flex flex-col gap-2 lg:flex-row lg:items-center lg:justify-between">
                          <div className="min-w-0 space-y-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <p className="truncate text-sm font-medium">{activity.title}</p>
                              <Badge variant="outline" className="text-[10px] uppercase tracking-wide text-muted-foreground">
                                {getTypeLabel(activity.type)}
                              </Badge>
                              {activity.dvr_name && <Badge variant="secondary">{activity.dvr_name}</Badge>}
                            </div>
                            <p className="line-clamp-2 text-sm text-muted-foreground">{activity.message}</p>
                          </div>

                          <div className="shrink-0 text-xs text-muted-foreground">
                            {formatFullTimestamp(activity.timestamp)}
                          </div>
                        </div>

                        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                          {activity.program_title && <span>{t("watchHistory.meta.program")}: {activity.program_title}</span>}
                          {activity.channel_name && <span>{t("watchHistory.meta.channel")}: {activity.channel_name}</span>}
                          {activity.device_name && <span>{t("watchHistory.meta.device")}: {activity.device_name}</span>}
                        </div>
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="text-sm text-muted-foreground">
            {t("watchHistory.pageOf", { page: currentPage, total: pageCount })}
          </div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOffset((currentOffset) => Math.max(0, currentOffset - pageSize))}
              disabled={!canGoPrevious || loading}
            >
              <ChevronLeft className="mr-1 h-4 w-4" />
              {t("common.previous")}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOffset((currentOffset) => currentOffset + pageSize)}
              disabled={!canGoNext || loading}
            >
              {t("common.next")}
              <ChevronRight className="ml-1 h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      <ActivityDetailDialog
        activity={selectedActivity}
        open={!!selectedActivity}
        onOpenChange={(open) => {
          if (!open) setSelectedActivity(null)
        }}
      />
    </>
  )
}
