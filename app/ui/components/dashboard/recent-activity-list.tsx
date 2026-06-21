"use client"

import React, { useState, useMemo } from "react"
import { t } from "@/lib/i18n"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuCheckboxItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator,
} from "@/components/base/dropdown-menu"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/base/select"
import { AlertCircle, Bell, Calendar, CheckCircle, Filter, Loader2, Play, Square, Tv, Video, X, Zap } from "lucide-react"
import type { ActivityItem } from "@/lib/types"
import { ActivityDetailDialog } from "@/components/dashboard/activity-detail-dialog"

const formatTimeAgo = (timestamp: string) => {
  try {
    const date = new Date(timestamp)
    const now = new Date()
    const diffMs = now.getTime() - date.getTime()

    const diffSecs = Math.floor(diffMs / 1000)
    if (diffSecs < 60) return t("activity.justNow")

    const diffMins = Math.floor(diffSecs / 60)
    if (diffMins < 60) return t("activity.minutesAgo", { n: diffMins })

    const diffHours = Math.floor(diffMins / 60)
    if (diffHours < 24) return t("activity.hoursAgo", { n: diffHours })

    const diffDays = Math.floor(diffHours / 24)
    return t("activity.daysAgo", { n: diffDays })
  } catch (e) {
    return t("common.unknown")
  }
}

function getDayLabel(timestamp: string): string {
  try {
    const date = new Date(timestamp)
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
    if (message.startsWith('Scheduled:')) {
      return <Calendar className={className} />
    } else if (message.startsWith('Cancelled:')) {
      return <X className={className} />
    } else if (message.startsWith('Recording(') || message.startsWith('Recording (')) {
      return <Video className={className} />
    } else if (message.startsWith('Completed')) {
      return <CheckCircle className={className} />
    } else if (message.startsWith('Stopped:')) {
      return <Square className={className} />
    }
    return <Video className={className} />
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

const getIconColorClasses = (type: string, message?: string) => {
  if (type === 'recording_event' && message) {
    if (message.startsWith('Scheduled:')) {
      return 'bg-amber-500/20 text-amber-600 dark:text-amber-400'
    } else if (message.startsWith('Cancelled:')) {
      return 'bg-red-500/20 text-red-600 dark:text-red-400'
    } else if (message.startsWith('Recording(') || message.startsWith('Recording (')) {
      return 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400'
    } else if (message.startsWith('Completed')) {
      return 'bg-purple-500/20 text-purple-600 dark:text-purple-400'
    } else if (message.startsWith('Stopped:')) {
      return 'bg-slate-500/20 text-slate-600 dark:text-slate-400'
    }
    return 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400'
  }

  switch (type) {
    case 'stream_started':
    case 'watching_channel':
      return 'bg-blue-500/20 text-blue-600 dark:text-blue-400'
    case 'vod_playback':
    case 'watching_vod':
      return 'bg-amber-500/20 text-amber-600 dark:text-amber-400'
    case 'recording_started':
      return 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400'
    case 'recording_completed':
      return 'bg-purple-500/20 text-purple-600 dark:text-purple-400'
    case 'disk_alert':
      return 'bg-red-500/20 text-red-600 dark:text-red-400'
    default:
      return 'bg-slate-500/20 text-slate-600 dark:text-slate-400'
  }
}

const isTestDiskAlert = (activity: ActivityItem) => {
  if (activity.is_test === true) {
    return true
  }

  return activity.type === "disk_alert" && activity.title.includes("[TEST]")
}

const getDisplayTitle = (activity: ActivityItem) => {
  if (!isTestDiskAlert(activity)) {
    return activity.title
  }

  return activity.title.replace(/\[TEST\]\s*/g, "").trim()
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
}: RecentActivityListProps) {
  const [selectedActivity, setSelectedActivity] = useState<ActivityItem | null>(null)

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
      <Card className="flex flex-col h-[300px] sm:h-[350px] md:h-[420px] max-w-full overflow-hidden">
        <CardHeader className="pb-2 flex-shrink-0">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <Zap className="h-4 w-4 text-primary" />
              {t("activity.title")}
              {filteredActivity.length > 0 && (
                <Badge variant="secondary" className="text-[10px] py-0 h-4 px-1.5 font-normal leading-none">
                  {filteredActivity.length}
                </Badge>
              )}
            </CardTitle>
            <div className="flex gap-2 items-center">
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" size="sm" className="h-6 gap-1 text-xs" aria-label={t("activity.aria.filterType")}>
                    <Filter className="h-3 w-3" />
                    {getFilterDisplayName()}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuCheckboxItem
                    checked={selectedFilters.includes("all")}
                    onCheckedChange={() => onToggleFilter("all")}
                  >
                    {t("activity.filterAll")}
                  </DropdownMenuCheckboxItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuCheckboxItem
                    checked={selectedFilters.includes("channel-watching")}
                    onCheckedChange={() => onToggleFilter("channel-watching")}
                  >
                    {t("activity.filterLiveTV")}
                  </DropdownMenuCheckboxItem>
                  <DropdownMenuCheckboxItem
                    checked={selectedFilters.includes("vod-watching")}
                    onCheckedChange={() => onToggleFilter("vod-watching")}
                  >
                    {t("activity.filterVod")}
                  </DropdownMenuCheckboxItem>
                  <DropdownMenuCheckboxItem
                    checked={selectedFilters.includes("recording-events")}
                    onCheckedChange={() => onToggleFilter("recording-events")}
                  >
                    {t("activity.filterRecordings")}
                  </DropdownMenuCheckboxItem>
                </DropdownMenuContent>
              </DropdownMenu>
              <Select value={String(activityHours)} onValueChange={(v) => onChangeHours(Number(v))}>
                <SelectTrigger className="h-6 w-[140px] text-xs bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300 border-blue-200 dark:border-blue-800" aria-label={t("activity.aria.timeRange")}>
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
        </CardHeader>
        <CardContent className="p-0 flex-grow overflow-y-auto overflow-x-hidden pr-1 relative">
          {activityLoading && dataLoaded && (
            <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/50 pointer-events-none">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          )}
          {groupedActivity.length > 0 ? (
            groupedActivity.map((group) => (
              <div key={group.label}>
                <div className="sticky top-0 z-[5] bg-muted/80 backdrop-blur-sm px-3 py-1">
                  <span className="text-[10px] font-medium text-slate-700 dark:text-slate-300 uppercase tracking-wider">{group.label}</span>
                </div>
                {group.items.map((activity) => {
                  const colorClasses = getIconColorClasses(activity.type, activity.message)
                  const isTestAlert = isTestDiskAlert(activity)
                  const displayTitle = getDisplayTitle(activity)
                  const hasImage = !!activity.image_url
                  return (
                    <button
                      key={activity.id}
                      onClick={() => setSelectedActivity(activity)}
                      className="flex items-center gap-2 p-2 hover:bg-muted/50 transition-colors border-b border-border/20 w-full text-left cursor-pointer"
                    >
                      <div className={`rounded-full ${colorClasses.split(' ')[0]} p-1.5 flex-shrink-0`}>
                        <ActivityIcon
                          type={activity.type}
                          className={`h-3.5 w-3.5 ${colorClasses.split(' ').slice(1).join(' ')}`}
                          message={activity.message}
                        />
                      </div>
                      {hasImage && (
                        <div className="h-12 w-8 flex-shrink-0 overflow-hidden rounded-md border border-border/40 bg-muted">
                          {/* eslint-disable-next-line @next/next/no-img-element -- static export with dynamic remote artwork URL; next/image optimizer unavailable */}
                          <img
                            src={activity.image_url}
                            alt=""
                            className="h-full w-full object-cover"
                            loading="lazy"
                          />
                        </div>
                      )}
                      <div className="flex-1 min-w-0 pr-1 overflow-hidden">
                        <div className="flex items-center gap-1 mb-1">
                          <p className="text-sm font-medium leading-none truncate">{displayTitle}</p>
                          {isTestAlert && (
                            <Badge
                              variant="outline"
                              className="h-3.5 shrink-0 border-amber-300 bg-amber-100 px-1 text-[9px] font-normal leading-none text-amber-900 dark:border-amber-700 dark:bg-amber-900/30 dark:text-amber-200"
                            >
                              {t("activity.testBadge")}
                            </Badge>
                          )}
                          {activity.dvr_name && (
                            <Badge variant="secondary" className="text-[9px] py-0 h-3.5 px-1 font-normal leading-none shrink-0">{activity.dvr_name}</Badge>
                          )}
                        </div>
                        <p className="text-xs text-muted-foreground truncate max-w-full">{activity.message}</p>
                      </div>
                      <Badge variant="outline" className="flex-shrink-0 text-xs whitespace-nowrap py-0 h-5 px-1.5">
                        {formatTimeAgo(activity.timestamp)}
                      </Badge>
                    </button>
                  )
                })}
              </div>
            ))
          ) : (
            <div className="flex flex-col items-center justify-center h-full p-6 gap-2">
              {recentActivity.length === 0 && hasError ? (
                <>
                  <AlertCircle className="h-8 w-8 text-muted-foreground/40" />
                  <span className="text-sm text-muted-foreground">{t("activity.loadError")}</span>
                  <button
                    onClick={onRetry}
                    className="text-primary underline text-xs hover:opacity-80"
                  >
                    {t("activity.retry")}
                  </button>
                </>
              ) : (
                <>
                  <Zap className="h-8 w-8 text-muted-foreground/40" />
                  <span className="text-sm text-muted-foreground">
                    {selectedFilters.length > 0
                      ? t("activity.emptyFiltered", { range: activityHours
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
      <ActivityDetailDialog
        activity={selectedActivity}
        open={!!selectedActivity}
        onOpenChange={(open) => { if (!open) setSelectedActivity(null) }}
      />
    </>
  )
}
