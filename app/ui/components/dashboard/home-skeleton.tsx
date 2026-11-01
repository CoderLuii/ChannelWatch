import { Card, CardContent, CardHeader } from "@/components/base/card"
import { pageTitleClassName } from "@/components/base/page-header"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"
import { UpcomingSkeletonRows } from "@/components/dashboard/upcoming-recordings-list"

function SkeletonTile({ className }: { className?: string }) {
  return (
    <Card className={cn("p-4", className)}>
      <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0 p-0">
        <div className="h-[18px] w-20 max-w-full rounded-thumb bg-muted" />
        <div className="size-4 shrink-0 rounded-thumb bg-muted" />
      </CardHeader>
      <CardContent className="p-0 pt-1.5">
        <div className="h-7 w-16 rounded-thumb bg-muted" />
        <div className="mt-1.5 flex h-10 items-center md:h-5">
          <div className="h-3 w-32 max-w-full rounded-thumb bg-muted" />
        </div>
      </CardContent>
    </Card>
  )
}

/** Holds Home's shape while the app starts, so the real page lands in the same place. */
export function HomeSkeleton() {
  return (
    <div data-testid="home-skeleton" role="status" aria-label={t("common.loading")} className="home-grid">
      <div className="home-area-verdict flex min-h-[62px] min-w-0 items-start gap-2.5 rounded-group border border-edge bg-card px-3.5 py-2.5 sm:min-h-11 sm:items-center">
        <div className="mt-[2px] size-[18px] shrink-0 rounded-full bg-muted sm:mt-0" />
        <div className="min-w-0 flex-1 animate-pulse py-1">
          <div className="h-3 w-44 max-w-full rounded-thumb bg-muted" />
        </div>
      </div>
      <div className="home-area-fresh flex flex-wrap items-center justify-between gap-x-3 gap-y-2 sm:gap-x-4">
        <div className="min-w-0 flex-1 basis-40">
          <h1 className={cn(pageTitleClassName, "max-md:sr-only")}>{t("dashboard.title")}</h1>
          <div className="flex h-[18px] items-center md:mt-1">
            <div className="h-3 w-28 max-w-full rounded-thumb bg-muted" />
          </div>
        </div>
        <div className="h-11 w-[8.5rem] shrink-0 rounded-control bg-fill" />
      </div>
      <div className="home-area-heart">
        <div className="home-area-now flex h-5 animate-pulse items-center">
          <div data-empty="" className="h-3 w-28 rounded-thumb bg-muted" />
        </div>
        <Card className="home-area-upcoming overflow-hidden xl:hidden">
          <div className="flex h-[52px] items-center px-4 pt-2">
            <div className="h-5 w-44 max-w-full rounded-thumb bg-muted" />
          </div>
          <UpcomingSkeletonRows />
        </Card>
      </div>
      <div className="home-area-tiles grid animate-pulse grid-cols-[repeat(auto-fit,minmax(min(100%,9.5rem),1fr))] gap-3 md:grid-cols-2 lg:grid-cols-4 xl:auto-rows-[minmax(12.375rem,auto)]">
        <SkeletonTile />
        <SkeletonTile />
        <SkeletonTile />
        <SkeletonTile />
      </div>
    </div>
  )
}
