"use client"

import { useEffect, useState } from "react"
import { Bug, ExternalLink, HelpCircle, Lightbulb, MessageCircle } from "lucide-react"

import { Button } from "@/components/base/button"
import { Card, CardContent } from "@/components/base/card"
import { Separator } from "@/components/base/separator"
import { FeatureRequestDialog } from "@/components/feature-request-dialog"
import { ReportProblemDialog } from "@/components/report-problem-dialog"
import { fetchSystemInfo } from "@/lib/api"
import { t } from "@/lib/i18n"
import type { AppSettings, SystemInfo } from "@/lib/types"

interface HelpFeedbackProps {
  appSettings: AppSettings | null
}

interface FeedbackRowProps {
  icon: React.ReactNode
  title: string
  description: string
  action: React.ReactNode
}

function FeedbackRow({ icon, title, description, action }: FeedbackRowProps) {
  return (
    <div className="flex flex-col gap-4 py-5 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          {icon}
        </span>
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-foreground">{title}</h2>
          <p className="mt-1 text-sm leading-6 text-muted-foreground">{description}</p>
        </div>
      </div>
      <div className="shrink-0 sm:pl-4">{action}</div>
    </div>
  )
}

function ExternalHelpLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md border border-input bg-background px-4 py-2 text-sm font-medium text-foreground shadow-sm transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
    >
      {children}
      <ExternalLink className="h-4 w-4" aria-hidden="true" />
      <span className="sr-only">{t("feedback.externalLink")}</span>
    </a>
  )
}

export function HelpFeedback({ appSettings }: HelpFeedbackProps) {
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchSystemInfo()
      .then((nextSystemInfo) => {
        if (!cancelled) setSystemInfo(nextSystemInfo)
      })
      .catch(() => {
        // The problem-report dialog remains available and will clearly show
        // unavailable diagnostics if this best-effort request fails.
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6" data-testid="help-feedback-page">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold tracking-tight">{t("feedback.title")}</h1>
        <p className="text-sm leading-6 text-muted-foreground sm:text-base">{t("feedback.description")}</p>
      </div>

      <Card>
        <CardContent className="px-4 py-0 sm:px-6">
          <FeedbackRow
            icon={<Bug className="h-5 w-5" aria-hidden="true" />}
            title={t("feedback.problem.title")}
            description={t("feedback.problem.description")}
            action={(
              <ReportProblemDialog
                systemInfo={systemInfo}
                appSettings={appSettings}
                trigger={(
                  <Button type="button" variant="outline" className="min-h-11 w-full sm:w-auto" data-testid="help-report-problem">
                    {t("feedback.problem.action")}
                  </Button>
                )}
              />
            )}
          />
          <Separator />
          <FeedbackRow
            icon={<Lightbulb className="h-5 w-5" aria-hidden="true" />}
            title={t("feedback.feature.rowTitle")}
            description={t("feedback.feature.rowDescription")}
            action={(
              <FeatureRequestDialog
                trigger={(
                  <Button type="button" variant="outline" className="min-h-11 w-full sm:w-auto" data-testid="help-request-feature">
                    {t("feedback.feature.action")}
                  </Button>
                )}
              />
            )}
          />
          <Separator />
          <FeedbackRow
            icon={<HelpCircle className="h-5 w-5" aria-hidden="true" />}
            title={t("feedback.question.title")}
            description={t("feedback.question.description")}
            action={(
              <div className="flex flex-col gap-2 sm:items-end">
                <ExternalHelpLink href="https://channelwatch.coderluii.dev/">
                  {t("feedback.question.documentation")}
                </ExternalHelpLink>
                <ExternalHelpLink href="https://community.getchannels.com/t/channelwatch-the-complete-monitoring-dashboard-for-channels-dvr/46085">
                  {t("feedback.question.community")}
                </ExternalHelpLink>
                <ExternalHelpLink href="https://github.com/CoderLuii/ChannelWatch/discussions">
                  <MessageCircle className="h-4 w-4" aria-hidden="true" />
                  {t("feedback.question.discussions")}
                </ExternalHelpLink>
              </div>
            )}
          />
        </CardContent>
      </Card>
    </div>
  )
}
