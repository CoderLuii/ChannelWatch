"use client"

import React from "react"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/base/card"
import { Count } from "@/components/base/badge"
import type { StreamDetails } from "@/lib/api"
import { t } from "@/lib/i18n"
import { formatCount } from "@/lib/utils"

interface NowListProps {
  watching: StreamDetails["watching"]
  recording: StreamDetails["recording"]
  loading: boolean
  hasError: boolean
}

function Marker({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-foreground">
      <span aria-hidden="true" className="size-2 rounded-full bg-foreground" />
      {children}
    </span>
  )
}

function Art({ src }: { src: string }) {
  const [failed, setFailed] = React.useState(false)
  if (!src || failed) return null
  return (
    <span aria-hidden="true" className="flex h-[60px] w-10 shrink-0 items-center justify-center overflow-hidden rounded-thumb bg-fill">
      {/* eslint-disable-next-line @next/next/no-img-element -- static export with dynamic remote artwork URL; next/image optimizer unavailable */}
      <img src={src} alt="" loading="lazy" onError={() => setFailed(true)} className="h-full w-full object-contain" />
    </span>
  )
}

function Row({ title, meta, marker, image }: { title: string; meta?: string; marker: React.ReactNode; image?: string }) {
  return (
    <li className="flex min-h-14 flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
      {image ? <Art src={image} /> : null}
      <div className="min-w-0 flex-1 basis-[8rem]">
        <p className="line-clamp-2 break-words text-sm font-medium leading-snug" title={title}>{title}</p>
        {meta ? <p className="mt-0.5 truncate text-xs text-muted-foreground" title={meta}>{meta}</p> : null}
      </div>
      {marker}
    </li>
  )
}

/** The heart of Home: what is on right now, in the words a person would use. */
export function NowList({ watching, recording, loading, hasError }: NowListProps) {
  const total = watching.length + recording.length

  if (total === 0) {
    if (loading) {
      return <div data-testid="now-list" data-empty="" role="status" aria-label={t("statusOverview.loading")} className="flex h-5 animate-pulse items-center"><span className="h-3 w-28 rounded-thumb bg-muted" /></div>
    }
    return (
      <p data-testid="now-list" data-empty="" className="text-sm leading-5 text-muted-foreground">
        {hasError ? t("now.unknown") : t("now.empty")}
      </p>
    )
  }

  return (
    <Card data-testid="now-list" className="disclosure-enter min-w-0 overflow-hidden">
      <CardHeader className="px-4 pb-0 pt-3">
        <CardTitle className="flex items-center gap-2 text-base font-semibold">
          {hasError ? t("now.lastKnown") : t("now.title")}
          <Count>{formatCount(total)}</Count>
        </CardTitle>
      </CardHeader>
      <CardContent className="p-0 pt-1">
        <ul className="divide-y divide-hairline">
          {watching.map((stream, index) => (
            <Row
              key={`w-${stream.device}-${stream.channel}-${index}`}
              title={stream.channel}
              meta={stream.device}
              image={stream.image}
              marker={hasError ? null : <Marker>{t("now.live")}</Marker>}
            />
          ))}
          {recording.map((item, index) => (
            <Row
              key={`r-${item.title}-${index}`}
              title={item.title}
              meta={item.until ? t("now.until", { until: item.until }) : undefined}
              marker={hasError ? null : <Marker>{t("now.recording")}</Marker>}
            />
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
