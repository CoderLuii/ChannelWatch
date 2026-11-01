"use client"

import { useState, useEffect } from "react"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/base/card"
import { Button } from "@/components/base/button"
import { PageHeader } from "@/components/base/page-header"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/base/tabs"
import { Separator } from "@/components/base/separator"
import { Tag } from "@/components/base/badge"
import { Loader2, GitBranch, ExternalLink, AtSign, Code, ChevronDown } from "lucide-react"
import { fetchSystemInfo } from "@/lib/api"
import { t } from "@/lib/i18n"
import { cn } from "@/lib/utils"

export function AboutSection() {
  const [isLoading, setIsLoading] = useState(false)
  const [version, setVersion] = useState("-")
  const [mobileProjectDetailsOpen, setMobileProjectDetailsOpen] = useState(false)

  useEffect(() => {
    const getSystemInfo = async () => {
      try {
        setIsLoading(true)
        const info = await fetchSystemInfo()
        if (info.channelwatch_version) {
          setVersion(info.channelwatch_version)
        }
      } catch (err) {
        console.error("Failed to fetch version info:", err)
      } finally {
        setIsLoading(false)
      }
    }

    getSystemInfo()
  }, [])

  const aboutInfo = {
    app_name: t("about.appName"),
    description: t("about.description"),
    developer: t("about.developer"),
    github_url: "https://github.com/CoderLuii/ChannelWatch",
    dockerhub_url: "https://hub.docker.com/r/coderluii/channelwatch",
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 p-8 text-sm text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
        {t("common.loading")}
      </div>
    )
  }

  return (
    <div className="space-y-4 sm:space-y-5">
      <PageHeader title={t("nav.about")} />
      <Card className="overflow-hidden">
        <div className="relative">
          <div className="h-24 bg-cover bg-center bg-no-repeat md:h-48" style={{ backgroundImage: "url('/images/background-bio.webp')" }}></div>
        </div>
        <div className="relative px-4 pb-4">
          <div className="-mt-10 flex flex-col items-start gap-4 md:-mt-12 md:flex-row md:items-start md:gap-6">
            <div className="relative flex h-20 w-20 items-center justify-center overflow-hidden rounded-control border-4 border-card bg-black md:h-24 md:w-24">
              {/* eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer is unavailable and would require width/height props that change layout */}
              <img
                src="/images/coder-luii.png"
                alt={aboutInfo.developer}
                className="h-full w-full object-cover"
              />
              <div className="absolute inset-0 flex items-center justify-center bg-black/70 opacity-0 group-[.image-error]:opacity-100">
                <Code className="h-12 w-12 text-white" />
              </div>
            </div>
            <div className="space-y-1 flex-1 md:pt-14">
              <div className="flex flex-col md:flex-row md:items-center gap-2 md:justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-foreground">CoderLuii</h2>
                  <p className="text-sm text-muted-foreground">{t("about.creatorSubtitle")}</p>
                </div>
              </div>
              <div className="flex flex-wrap gap-2 mt-2">
                <Tag>{t("about.badge.cybersecurity")}</Tag>
                <Tag>{t("about.badge.python")}</Tag>
                <Tag>{t("about.badge.docker")}</Tag>
                <Tag>{t("about.badge.selfHosting")}</Tag>
                <Tag>{t("about.badge.automation")}</Tag>
              </div>
            </div>
          </div>
        </div>
      </Card>

      <Tabs defaultValue="project" className="w-full">
        <TabsList className="mb-3 grid grid-cols-3 gap-1 sm:mb-4 sm:w-fit">
          <TabsTrigger value="project" className="min-h-11">{t("about.tab.project")}</TabsTrigger>
          <TabsTrigger value="story" className="min-h-11">{t("about.tab.story")}</TabsTrigger>
          <TabsTrigger value="connect" className="min-h-11">{t("about.tab.connect")}</TabsTrigger>
        </TabsList>

        <TabsContent value="story" className="space-y-4">
          <Card className="overflow-hidden">
            <CardHeader className="border-b border-hairline">
              <div className="flex items-center gap-3">
                {/* eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable */}
                <img src="/images/channelwatch-logo.png" alt={t("about.logoAlt")} className="h-7 w-auto shrink-0" />
                <div className="min-w-0">
                  <CardTitle><h2>{t("about.story.heading")}</h2></CardTitle>
                  <CardDescription>{t("about.story.subheading")}</CardDescription>
                </div>
              </div>
            </CardHeader>

            <CardContent className="prose max-w-none space-y-4 pt-4 dark:prose-invert">
              <p className="text-base font-medium">{t("about.story.intro")}</p>

              <p>{t("about.story.problem")}</p>
              <p>{t("about.story.community")}</p>

              <p className="border-t border-hairline pt-3 text-sm text-muted-foreground">
                <span className="font-medium text-foreground">@CoderLuii</span> {t("about.story.attribution")}
              </p>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="project" className="space-y-4">
          <Card className="overflow-hidden">
            <CardHeader className="border-b border-hairline">
              <div className="flex items-center gap-3">
                {/* eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable */}
                <img src="/images/channelwatch-logo.png" alt={t("about.logoAlt")} className="h-7 w-auto shrink-0" />
                <div>
                  <CardTitle><h2>{aboutInfo.app_name}</h2></CardTitle>
                  <CardDescription>{t("about.version", { version })}</CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="min-w-0 space-y-4 pt-4 [overflow-wrap:anywhere]">
                <p className="text-base font-medium">{t("about.project.intro")}</p>

              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <div className="min-w-0 rounded-control bg-fill p-4 space-y-3">
                  <h3 className="text-sm font-medium">
                    {t("about.keyFeatures")}
                  </h3>
                  <ul className="list-none space-y-2 text-sm">
                     {([1,2,3,4,5,6,7] as const).map((n) => (
                       <li key={n} className="flex items-start gap-2">
                          <span aria-hidden="true" className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground" />
                         <span className="min-w-0 flex-1">{t(`about.feature.${n}`)}</span>
                       </li>
                     ))}
                   </ul>
                </div>

                <Button
                  type="button"
                  variant="outline"
                  className="h-auto min-h-11 min-w-0 w-full justify-between gap-2 whitespace-normal text-left md:hidden"
                  aria-expanded={mobileProjectDetailsOpen}
                  aria-controls="about-project-details"
                  onClick={() => setMobileProjectDetailsOpen((open) => !open)}
                >
                  <span className="min-w-0 flex-1 [overflow-wrap:anywhere]">{t(mobileProjectDetailsOpen ? "about.hideProjectDetails" : "about.showProjectDetails")}</span>
                  <ChevronDown className={cn("h-4 w-4 shrink-0 transition-transform duration-[var(--motion-base)] ease-out", mobileProjectDetailsOpen && "rotate-180")} aria-hidden="true" />
                </Button>

                <div id="about-project-details" className={cn("min-w-0 rounded-control bg-fill p-4 space-y-3", !mobileProjectDetailsOpen && "hidden md:block")}>
                  <h3 className="text-sm font-medium">
                    {t("about.techStack")}
                  </h3>
                  <ul className="list-none space-y-2 text-sm">
                     {(["python","fastapi","react","docker","tailwind"] as const).map((key) => (
                       <li key={key} className="flex items-start gap-2">
                          <span aria-hidden="true" className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground" />
                          <div className="min-w-0 flex-1">
                            <span className="font-medium">{t(`about.tech.${key}.name`)}</span>
                            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{t(`about.tech.${key}.desc`)}</p>
                          </div>
                       </li>
                     ))}
                   </ul>
                </div>
              </div>
            </CardContent>
            <CardFooter className="flex flex-col gap-3 border-t border-hairline pt-4 sm:flex-row">
              <Button asChild className="min-h-11 w-full sm:w-auto">
                <a
                  href={aboutInfo.github_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2 justify-center"
                >
                  <GitBranch className="h-4 w-4" />
                  {t("about.githubBtn")}
                </a>
              </Button>
              <Button variant="outline" asChild className="w-full sm:w-auto">
                <a
                  href={aboutInfo.dockerhub_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-2"
                >
                  <ExternalLink className="h-4 w-4" />
                  {t("about.dockerhubBtn")}
                </a>
              </Button>
            </CardFooter>
          </Card>
        </TabsContent>

        <TabsContent value="connect" className="space-y-4">
          <Card className="overflow-hidden">
            <CardHeader className="border-b border-hairline">
              <CardTitle><h2>{t("about.connectTitle")}</h2></CardTitle>
            </CardHeader>

            <CardContent className="space-y-4 pt-4">
              <div className="grid gap-3 md:grid-cols-2">
                <a
                  href="https://github.com/CoderLuii"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex min-h-[60px] items-center gap-3 rounded-control border border-hairline p-3 transition-colors hover:bg-fill"
                >
                  <GitBranch className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <div>
                    <h3 className="text-sm font-medium">{t("about.githubLabel")}</h3>
                    <p className="text-sm text-muted-foreground">@CoderLuii</p>
                  </div>
                </a>

                <a
                  href="https://x.com/CoderLuii"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex min-h-[60px] items-center gap-3 rounded-control border border-hairline p-3 transition-colors hover:bg-fill"
                >
                  <AtSign className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <div>
                    <h3 className="text-sm font-medium">{t("about.twitterLabel")}</h3>
                    <p className="text-sm text-muted-foreground">@CoderLuii</p>
                  </div>
                </a>
              </div>

              <Separator />

              <div>
                <h3 className="mb-3 text-sm font-medium">
                  {t("about.supportSection")}
                </h3>
                <div className="grid gap-3 md:grid-cols-2">
                  <a
                    href="https://www.paypal.com/donate/?hosted_button_id=PM2UXGVSTHDNL"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex min-h-[60px] items-center gap-3 rounded-control border border-hairline p-3 transition-colors hover:bg-fill"
                  >
                    <svg className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M7.076 21.337H2.47a.641.641 0 0 1-.633-.74L4.944.901C5.026.382 5.474 0 5.998 0h7.46c2.57 0 4.578.543 5.69 1.81 1.01 1.15 1.304 2.42 1.012 4.287-.023.143-.047.288-.077.437-.983 5.05-4.349 6.797-8.647 6.797h-2.19c-.524 0-.968.382-1.05.9l-1.12 7.106zm14.146-14.42a3.35 3.35 0 0 0-.607-.541c-.013.076-.026.175-.041.254-.93 4.778-4.005 7.201-9.138 7.201h-2.19c-.018 0-.034.002-.05.005L8.18 21.337h3.598c.37 0 .684-.271.74-.636l.03-.189.59-3.738.038-.217a.74.74 0 0 1 .74-.635h.467c3.01 0 5.36-1.222 6.051-4.764.29-1.489.15-2.734-.212-3.591z" />
                        <path d="M19.826 7.358c-.001.009-.001.018-.003.026-.327 1.679-1.436 5.19-6.508 5.19h-1.652a.929.929 0 0 0-.92.79l-.975 6.175-.028.175a.929.929 0 0 1-.919.79H6.27l-.064.402c-.055.345.213.658.563.658h3.94a.929.929 0 0 0 .919-.79l.038-.238.73-4.634.047-.25a.93.93 0 0 1 .919-.79h.58c3.749 0 6.68-1.52 7.54-5.92.36-1.847.174-3.388-.777-4.469a3.604 3.604 0 0 0-1.02-.82c.316.948.366 1.988.06 3.255z" />
                      </svg>
                    <span className="min-w-0">
                      <h4 className="text-sm font-medium">{t("about.paypalLabel")}</h4>
                      <p className="text-xs leading-5 text-muted-foreground">{t("about.paypalSub")}</p>
                    </span>
                  </a>

                  <a
                    href="https://buymeacoffee.com/CoderLuii"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex min-h-[60px] items-center gap-3 rounded-control border border-hairline p-3 transition-colors hover:bg-fill"
                  >
                    <svg className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M7.5 2h9A1.5 1.5 0 0 1 18 3.5v1A1.5 1.5 0 0 1 16.5 6h-9A1.5 1.5 0 0 1 6 4.5v-1A1.5 1.5 0 0 1 7.5 2zm0 5h9A1.5 1.5 0 0 1 18 8.5v1a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 9.5v-1A1.5 1.5 0 0 1 7.5 7zM6 15.5A1.5 1.5 0 0 1 7.5 14h9a1.5 1.5 0 0 1 1.5 1.5v1a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 16.5v-1zm1.5 4A1.5 1.5 0 0 0 6 21h12a1.5 1.5 0 0 0 0-3H7.5a1.5 1.5 0 0 0-1.5 1.5z" />
                      </svg>
                    <span className="min-w-0">
                      <h4 className="text-sm font-medium">{t("about.coffeeLabel")}</h4>
                      <p className="text-xs leading-5 text-muted-foreground">{t("about.coffeeSub")}</p>
                    </span>
                  </a>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}


