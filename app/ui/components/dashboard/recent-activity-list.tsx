"use client"

import React, { useEffect, useState, useMemo, useRef } from "react"
import { t } from "@/lib/i18n"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Count, Tag } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/base/command"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/base/popover"
import { Separator } from "@/components/base/separator"
import { FilterGroup, FilterPill, FilterPills, FilterRow, FilterSheet } from "@/components/base/filter-sheet"
import { ClientFilterList } from "@/components/activity/client-filter-combobox"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { AlertCircle, Bell, Check, ChevronRight, Filter, Loader2, Play, Tv, Video, Zap } from "lucide-react"
import type { ActivityItem } from "@/lib/types"
import type { ActivityClientFacet } from "@/lib/api"
import { ActivityDetailDialog } from "@/components/dashboard/activity-detail-dialog"
import { useDvrSelection } from "@/lib/dvr-selection-context"
import { cn, formatCount } from "@/lib/utils"
import { ROUTINE_ACTIVITY_CLASSES, activityRowText, diskAlertColorClasses, healthEventPresentation, recordingEventPresentation, stripLeadingEmoji } from "@/lib/activity-presentation"
import { formatEventAge } from "@/lib/relative-time"

/** Rows shown before "Show all" below 1280, where the list sits in the page instead of its own scroller. */
const COLLAPSED_ROWS = 5

function getTypeLabel(type: string): string {
  const health = healthEventPresentation(type)
  if (health) return t(health.labelKey)
  switch (type) {
    case "watching_channel":
    case "stream_started":
      return t("type.liveTV")
    case "watching_vod":
    case "vod_playback":
      return t("type.vod")
    case "disk_alert":
      return t("type.diskAlert")
    case "recording_event":
    case "recording_started":
    case "recording_completed":
    case "recording_scheduled":
    case "recording_stopped":
    case "recording_cancelled":
      return t("type.recording")
    default:
      return t("type.activity")
  }
}

function getDayLabel(timestamp: string): string {
  try {
    const date = new Date(timestamp)
    if (!Number.isFinite(date.getTime()) || date.getTime() <= 0) return t("common.unknown")
    const now = new Date()
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    const yesterday = new Date(today.getTime() - 86400000)
    const itemDate = new Date(date.getFullYear(), date.getMonth(), date.getDate())

    if (itemDate.getTime() === today.getTime()) return t("common.today")
    if (itemDate.getTime() === yesterday.getTime()) return t("common.yesterday")
    return date.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" })
  } catch {
    return t("common.unknown")
  }
}

const ActivityIcon = ({ type, className, message }: { type: string; className?: string; message?: string }) => {
  if (type === 'recording_event' && message) {
    const Icon = recordingEventPresentation(message).icon
    return <Icon className={className} />
  }
  const health = healthEventPresentation(type)
  if (health) {
    const Icon = health.icon
    return <Icon className={className} />
  }

  switch (type) {
    case 'stream_started':
    case 'watching_channel':
      return <Tv className={className} />
    case 'recording_started':
    case 'recording_completed':
      return <Video className={className} />
    case 'disk_alert':
      return <AlertCircle className={className} />
    case 'vod_playback':
    case 'watching_vod':
      return <Play className={className} />
    default:
      return <Bell className={className} />
  }
}

const getIconColorClasses = (type: string, message?: string, extra?: ActivityItem["extra"]) => {
  if (type === 'recording_event' && message) {
    return recordingEventPresentation(message).colorClasses
  }
  const health = healthEventPresentation(type)
  if (health) return health.colorClasses
  if (type === 'disk_alert') return diskAlertColorClasses(extra)

  // Watching and recording are routine. Color is kept for things that need attention.
  return ROUTINE_ACTIVITY_CLASSES
}

const isTestDiskAlert = (activity: ActivityItem) => {
  if (activity.is_test === true) {
    return true
  }

  return activity.type === "disk_alert" && activity.title.includes("[TEST]")
}

const getDisplayTitle = (activity: ActivityItem) => {
  if (!isTestDiskAlert(activity)) {
    return stripLeadingEmoji(activity.title)
  }

  return stripLeadingEmoji(activity.title.replace(/\[TEST\]\s*/g, "").trim())
}

interface RecentActivityListProps {
  recentActivity: ActivityItem[]
  filteredActivity: ActivityItem[]
  selectedFilters: string[]
  onToggleFilter: (filter: string) => void
  activityHours: number
  onChangeHours: (hours: number) => void
  activityLoading: boolean
  dataLoaded: boolean
  hasError: boolean
  onRetry: () => void
  getFilterDisplayName: () => string
  clients: ActivityClientFacet[]
  selectedClient: string | null
  onSelectClient: (client: string | null) => void
  clientsLoading: boolean
  clientsError: boolean
  clientStatus: string | null
  /** How many exist when the list only holds the newest 250. */
  totalCount?: number
  onNavigate?: (view: string) => void
}

export function RecentActivityList({
  recentActivity,
  filteredActivity,
  selectedFilters,
  onToggleFilter,
  activityHours,
  onChangeHours,
  activityLoading,
  dataLoaded,
  hasError,
  onRetry,
  getFilterDisplayName,
  clients,
  selectedClient,
  onSelectClient,
  clientsLoading,
  clientsError,
  clientStatus,
  totalCount,
  onNavigate,
}: RecentActivityListProps) {
  const [selectedActivity, setSelectedActivity] = useState<ActivityItem | null>(null)
  const { availableDvrs, selectedDvr } = useDvrSelection()
  const dialogTriggerRef = useRef<HTMLButtonElement | null>(null)
  const [failedArtwork, setFailedArtwork] = useState<Set<string>>(() => new Set())
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [filterSheetOpen, setFilterSheetOpen] = useState(false)
  const hoursLabel = (hours: number) => t(hours === 24 ? "activity.last24h" : hours === 72 ? "activity.last3d" : hours === 168 ? "activity.last7d" : "activity.allTime")
  const typeFilterRows = [
    ["all", t("activity.filterAll")],
    ["channel-watching", t("activity.filterLiveTV")],
    ["vod-watching", t("activity.filterVod")],
    ["recording-events", t("activity.filterRecordings")],
  ] as const
  const collapsedIds = useMemo(() => new Set(filteredActivity.slice(COLLAPSED_ROWS).map((item) => item.id)), [filteredActivity])
  const countLabel = totalCount && totalCount > filteredActivity.length
    ? t("activity.rangeOf", { shown: formatCount(filteredActivity.length), total: formatCount(totalCount) })
    : formatCount(filteredActivity.length)
  const selectedClientLabel = clients.find((client) => client.value === selectedClient)?.label ?? selectedClient
  const typeFilterCount = selectedFilters.includes("all") ? 0 : selectedFilters.length
  const activeFilterCount = typeFilterCount + (selectedClient ? 1 : 0)
  const filterButtonLabel = activeFilterCount > 1
    ? t("activity.activeFilters", { count: activeFilterCount })
    : selectedClientLabel ?? getFilterDisplayName()

  // A row animates only when a refresh brings it in. First load and filter changes stay still.
  const knownIds = useRef<Set<ActivityItem["id"]> | null>(null)
  const filterKey = `${selectedDvr}|${selectedFilters.join(",")}|${selectedClient ?? ""}|${activityHours}`
  const lastFilterKey = useRef(filterKey)
  const arrivedIds = useRef(new Set<ActivityItem["id"]>())
  // A filter's fetch only starts after this commit's effects, so its rows must not count as arrivals.
  const awaitingFilterLoad = useRef(false)
  const sameFilters = lastFilterKey.current === filterKey
  const isNewRow = (id: ActivityItem["id"]) => sameFilters && (arrivedIds.current.has(id) || (knownIds.current !== null && !knownIds.current.has(id)))
  useEffect(() => {
    if (lastFilterKey.current !== filterKey) {
      lastFilterKey.current = filterKey
      knownIds.current = null
      arrivedIds.current.clear()
      awaitingFilterLoad.current = true
    }
    if (activityLoading) {
      awaitingFilterLoad.current = false
      return
    }
    if (!dataLoaded || awaitingFilterLoad.current) return
    const carryOver = knownIds.current !== null
    const known = knownIds.current ?? new Set<ActivityItem["id"]>()
    for (const item of filteredActivity) {
      if (carryOver && !known.has(item.id)) arrivedIds.current.add(item.id)
      known.add(item.id)
    }
    knownIds.current = known
  }, [filteredActivity, filterKey, activityLoading, dataLoaded])

  const groupedActivity = useMemo(() => {
    const groups: { label: string; items: ActivityItem[] }[] = []
    let currentLabel = ""
    for (const item of filteredActivity) {
      const label = getDayLabel(item.timestamp)
      if (label !== currentLabel) {
        currentLabel = label
        groups.push({ label, items: [item] })
      } else {
        groups[groups.length - 1].items.push(item)
      }
    }
    return groups
  }, [filteredActivity])

  return (
    <>
      <Card className="flex min-h-0 min-w-0 max-w-full flex-col overflow-hidden xl:max-h-[505px] xl:min-h-[280px]">
        <CardHeader className="flex-shrink-0 px-4 pb-2 pt-4">
          <div className="flex flex-col gap-2 xl:flex-row xl:items-center xl:justify-between">
            <CardTitle className="flex items-center gap-2 text-base font-semibold">
              <Zap className="size-4 text-muted-foreground" aria-hidden="true" />
              {t("activity.title")}
              {filteredActivity.length > 0 && (
                <Count>{countLabel}</Count>
              )}
            </CardTitle>
            <FilterPills>
              <FilterPill label={filterButtonLabel} active={activeFilterCount > 0} aria-label={`${filterButtonLabel}. ${t("activity.aria.filterType")}`} onClick={() => setFilterSheetOpen(true)} />
              <FilterPill label={hoursLabel(activityHours)} active={activityHours !== 24} aria-label={`${hoursLabel(activityHours)}. ${t("activity.aria.timeRange")}`} onClick={() => setFilterSheetOpen(true)} />
            </FilterPills>
            <div className="flex flex-wrap items-center gap-2 max-md:hidden">
              <Popover open={filtersOpen} onOpenChange={setFiltersOpen}>
                <PopoverTrigger asChild>
                  <Button variant="outline" size="sm" className="min-h-11 max-w-44 gap-1 text-xs sm:min-h-8" aria-label={`${filterButtonLabel}. ${t("activity.aria.filterType")}`}>
                    <Filter className="h-3 w-3" />
                    <span className="truncate">{filterButtonLabel}</span>
                  </Button>
                </PopoverTrigger>
                <PopoverContent align="end" collisionPadding={16} className="w-[min(22rem,calc(100vw-2rem))] space-y-3 overflow-y-auto overscroll-contain p-3">
                  <div>
                    <p className="px-2 pb-1 text-xs font-medium text-muted-foreground">{t("activity.types")}</p>
                    {[
                      ["all", t("activity.filterAll")],
                      ["channel-watching", t("activity.filterLiveTV")],
                      ["vod-watching", t("activity.filterVod")],
                      ["recording-events", t("activity.filterRecordings")],
                    ].map(([value, label]) => (
                      <Button
                        key={value}
                        type="button"
                        variant="ghost"
                        className="min-h-11 w-full justify-start gap-2 px-2 font-normal"
                        aria-pressed={selectedFilters.includes(value)}
                        onClick={() => onToggleFilter(value)}
                      >
                        <Check className={cn("h-4 w-4", selectedFilters.includes(value) ? "opacity-100" : "opacity-0")} />
                        {label}
                      </Button>
                    ))}
                  </div>
                  <Separator />
                  <div>
                    <p className="px-2 pb-1 text-xs font-medium text-muted-foreground">{t("activity.client")}</p>
                    <Command>
                      <CommandInput placeholder={t("activity.searchClients")} />
                      <CommandList className="max-h-48">
                        <CommandEmpty>{clientsLoading ? t("common.loading") : t("activity.noClients")}</CommandEmpty>
                        <CommandGroup>
                          <CommandItem value={t("activity.allClients")} className="min-h-11" onSelect={() => onSelectClient(null)}>
                            <Check className={cn("h-4 w-4", selectedClient === null ? "opacity-100" : "opacity-0")} />
                            <span>{t("activity.allClients")}</span>
                          </CommandItem>
                          {clients.map((client) => (
                            <CommandItem key={client.value} value={`${client.label} ${client.value}`} className="min-h-11" onSelect={() => onSelectClient(client.value)}>
                              <Check className={cn("h-4 w-4", selectedClient === client.value ? "opacity-100" : "opacity-0")} />
                              <span className="line-clamp-2 min-w-0 flex-1 break-words" title={client.label}>{client.label}</span>
                              <span className="text-xs tabular-nums text-muted-foreground">{client.count}</span>
                            </CommandItem>
                          ))}
                        </CommandGroup>
                      </CommandList>
                    </Command>
                    {clientsError ? <p className="px-2 pt-2 text-xs text-muted-foreground">{t("activity.clientFilterError")}</p> : null}
                  </div>
                  <Separator />
                  <p className="px-2 text-xs text-muted-foreground">{t("activity.timelineAggregate")}</p>
                  {activeFilterCount > 0 ? (
                    <Button
                      type="button"
                      variant="ghost"
                      className="min-h-11 w-full"
                      onClick={() => {
                        if (!selectedFilters.includes("all")) onToggleFilter("all")
                        onSelectClient(null)
                      }}
                    >
                      {t("activity.clearFilters")}
                    </Button>
                  ) : null}
                </PopoverContent>
              </Popover>
              <Select value={String(activityHours)} onValueChange={(v) => onChangeHours(Number(v))}>
                <SelectTrigger className="min-h-11 w-auto min-w-[9rem] text-xs sm:min-h-8" aria-label={t("activity.aria.timeRange")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="24" className="text-xs">{t("activity.last24h")}</SelectItem>
                  <SelectItem value="72" className="text-xs">{t("activity.last3d")}</SelectItem>
                  <SelectItem value="168" className="text-xs">{t("activity.last7d")}</SelectItem>
                  <SelectItem value="0" className="text-xs">{t("activity.allTime")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          {clientStatus ? <p role="status" aria-live="polite" className="mt-2 text-xs text-muted-foreground">{clientStatus}</p> : null}
        </CardHeader>
        <CardContent className="relative flex-grow overflow-y-auto overflow-x-hidden p-0">
          {activityLoading && dataLoaded && (
            <div className="loading-delayed absolute inset-0 z-10 flex items-center justify-center gap-2 bg-fill text-xs text-muted-foreground pointer-events-none">
              <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
              {t("dashboard.refreshing")}
            </div>
          )}
          {!dataLoaded ? (
            <div role="status" aria-label={t("statusOverview.loading")} className="animate-pulse">
              {[0, 1, 2].map((row) => (
                <div key={row} className="flex min-h-14 items-center gap-3 px-4">
                  <div className="size-7 shrink-0 rounded-full bg-muted" />
                  <div className="min-w-0 flex-1 space-y-2">
                    <div className="h-3 w-2/3 rounded-thumb bg-muted" />
                    <div className="h-3 w-1/3 rounded-thumb bg-muted" />
                  </div>
                </div>
              ))}
            </div>
          ) : groupedActivity.length > 0 ? (
            <>
            {groupedActivity.map((group) => (
              <div key={group.label} className={`border-t border-hairline first:border-t-0 ${group.items.every((item) => collapsedIds.has(item.id)) ? "max-xl:hidden" : ""}`}>
                <div className="px-4 pb-1 pt-3 text-xs font-medium text-muted-foreground xl:sticky xl:top-0 xl:z-[5] xl:bg-card">
                  {group.label}
                </div>
                {group.items.map((activity) => {
                  const colorClasses = getIconColorClasses(activity.type, activity.message, activity.extra)
                  const isTestAlert = isTestDiskAlert(activity)
                  const row = activityRowText(activity, getDisplayTitle(activity), getTypeLabel(activity.type))
                  const age = formatEventAge(activity.timestamp)
                  const artworkKey = `${activity.id}:${activity.image_url || ""}`
                  const hasImage = Boolean(activity.image_url && !failedArtwork.has(artworkKey))
                  return (
                    <button
                      key={activity.id}
                      data-new={isNewRow(activity.id) ? "" : undefined}
                      onClick={(event) => {
                        dialogTriggerRef.current = event.currentTarget
                        setSelectedActivity(activity)
                      }}
                      className={`press-row group/row flex min-h-14 w-full cursor-pointer flex-wrap items-start gap-x-3 pl-4 text-left transition-colors hover:bg-fill ${collapsedIds.has(activity.id) ? "max-xl:hidden" : ""}`}
                    >
                      <div className={`mt-1.5 rounded-full ${colorClasses.split(' ')[0]} p-1.5 flex-shrink-0 group-first-of-type/row:mt-0`}>
                        <ActivityIcon
                          type={activity.type}
                          className={`h-3.5 w-3.5 ${colorClasses.split(' ').slice(1).join(' ')}`}
                          message={activity.message}
                        />
                      </div>
                      <div className="flex min-w-0 flex-1 basis-[8rem] flex-wrap items-start gap-x-3 border-t border-hairline py-2.5 pr-4 group-first-of-type/row:border-t-0 group-first-of-type/row:pt-1">
                        <div className="min-w-0 flex-1 basis-[8rem]">
                          <p className="line-clamp-2 min-w-0 break-words text-sm font-medium leading-snug" title={row.title}>{row.title}</p>
                          <p className="mt-0.5 flex min-w-0 items-baseline gap-1 text-xs leading-snug text-muted-foreground">
                            {row.detail ? (
                              <>
                                <span className="min-w-0 truncate" title={row.detail}>{row.detail}</span>
                                <span aria-hidden="true">·</span>
                              </>
                            ) : null}
                            <span className="shrink-0 tabular-nums">{age}</span>
                          </p>
                          {isTestAlert || (activity.dvr_name && availableDvrs.length > 1) ? (
                            <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                              {isTestAlert && <Tag>{t("activity.testBadge")}</Tag>}
                              {activity.dvr_name && availableDvrs.length > 1 && (
                                <span className="min-w-0 truncate text-xs text-muted-foreground" title={activity.dvr_name}>{activity.dvr_name}</span>
                              )}
                            </div>
                          ) : null}
                        </div>
                        {hasImage ? (
                          <div aria-hidden="true" className="flex h-10 w-16 shrink-0 items-center justify-center overflow-hidden rounded-thumb bg-fill">
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
                      </div>
                    </button>
                  )
                })}
              </div>
            ))}
            {filteredActivity.length > COLLAPSED_ROWS ? (
              <button
                type="button"
                data-testid="activity-show-all"
                onClick={() => onNavigate?.("watch-history")}
                className="press-row flex min-h-11 w-full items-center justify-between gap-2 border-t border-hairline px-4 text-left text-sm font-medium text-primary transition-colors hover:bg-fill"
              >
                {t("activity.openHistory")}
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              </button>
            ) : null}
            </>
          ) : (
            <div className="flex flex-col items-center justify-center h-full p-6 gap-2 text-center">
              {recentActivity.length === 0 && hasError ? (
                <>
                  <AlertCircle className="h-8 w-8 text-muted-foreground/40" />
                  <span className="text-sm text-muted-foreground">{t("activity.loadError")}</span>
                  <Button type="button" variant="outline" size="sm" onClick={onRetry}>{t("activity.retry")}</Button>
                </>
              ) : (
                <>
                  <Zap className="h-8 w-8 text-muted-foreground/40" />
                  <span className="text-sm text-muted-foreground">
                    {!selectedFilters.includes("all") || selectedClient
                      ? selectedClientLabel
                        ? t("activity.emptyForClient", { client: selectedClientLabel, range: activityHours
                            ? activityHours <= 24 ? t("activity.emptyFilteredRange24h")
                              : activityHours <= 72 ? t("activity.emptyFilteredRange3d")
                              : t("activity.emptyFilteredRange7d")
                            : "" })
                        : t("activity.emptyFiltered", { range: activityHours
                          ? activityHours <= 24 ? t("activity.emptyFilteredRange24h")
                            : activityHours <= 72 ? t("activity.emptyFilteredRange3d")
                            : t("activity.emptyFilteredRange7d")
                          : "" })
                      : t("activity.empty")}
                  </span>
                  <span className="text-xs text-muted-foreground/60">{t("activity.emptyHint")}</span>
                </>
              )}
            </div>
          )}
        </CardContent>
      </Card>
      <FilterSheet
        open={filterSheetOpen}
        onOpenChange={setFilterSheetOpen}
        clearLabel={t("activity.clearFilters")}
        onClear={activeFilterCount > 0 ? () => {
          if (!selectedFilters.includes("all")) onToggleFilter("all")
          onSelectClient(null)
        } : undefined}
      >
        <FilterGroup title={t("activity.types")}>
          {typeFilterRows.map(([value, label]) => (
            <FilterRow key={value} label={label} selected={selectedFilters.includes(value)} onSelect={() => onToggleFilter(value)} />
          ))}
        </FilterGroup>
        <FilterGroup title={t("filters.timeRange")}>
          {[24, 72, 168, 0].map((hours) => (
            <FilterRow key={hours} label={hoursLabel(hours)} selected={activityHours === hours} onSelect={() => onChangeHours(hours)} />
          ))}
        </FilterGroup>
        <FilterGroup title={t("activity.client")} plain>
          <ClientFilterList clients={clients} value={selectedClient} onChange={onSelectClient} listClassName="max-h-44" />
          {clientsError ? <p className="pt-2 text-xs text-muted-foreground">{t("activity.clientFilterError")}</p> : null}
        </FilterGroup>
        <p className="text-xs text-muted-foreground">{t("activity.timelineAggregate")}</p>
      </FilterSheet>
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
