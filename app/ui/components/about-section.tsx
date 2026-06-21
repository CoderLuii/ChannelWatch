"use client"

import { useState, useEffect } from "react"
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/base/card"
import { Button } from "@/components/base/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/base/tabs"
import { Separator } from "@/components/base/separator"
import { Badge } from "@/components/base/badge"
import { Loader2, GitBranch, ExternalLink, AtSign, Code } from "lucide-react"
import { fetchSystemInfo } from "@/lib/api"
import { t } from "@/lib/i18n"

export function AboutSection() {
  const [isLoading, setIsLoading] = useState(false)
  const [version, setVersion] = useState("X.X.X")

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
      <div className="flex justify-center items-center p-8">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <Card className="overflow-hidden">
        <div className="relative">
          <div className="bg-no-repeat bg-cover bg-center h-32 md:h-48" style={{ backgroundImage: "url('/images/background-bio.webp')" }}></div>
          <div className="absolute bottom-0 left-0 right-0 h-16 bg-gradient-to-t from-black/40 to-transparent"></div>
        </div>
        <div className="px-6 relative pb-6">
          <div className="flex flex-col md:flex-row gap-6 items-start md:items-end -mt-12">
            <div className="h-24 w-24 rounded-xl overflow-hidden border-4 border-background bg-black flex items-center justify-center relative">
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
            <div className="space-y-1 flex-1">
              <div className="flex flex-col md:flex-row md:items-center gap-2 md:justify-between">
                <div className="bg-black/70 backdrop-blur-sm px-3 py-1.5 rounded-md">
                  <h2 className="text-2xl font-bold text-white drop-shadow-md">Coder Luii</h2>
                  <p className="text-white drop-shadow-md">{t("about.creatorSubtitle")}</p>
                </div>
              </div>
              <div className="flex flex-wrap gap-2 mt-2">
                <Badge variant="secondary">{t("about.badge.cybersecurity")}</Badge>
                <Badge variant="secondary">{t("about.badge.python")}</Badge>
                <Badge variant="secondary">{t("about.badge.docker")}</Badge>
                <Badge variant="secondary">{t("about.badge.selfHosting")}</Badge>
                <Badge variant="secondary">{t("about.badge.automation")}</Badge>
              </div>
            </div>
          </div>
        </div>
      </Card>

      <Tabs defaultValue="story" className="w-full">
        <TabsList className="grid grid-cols-3 mb-4">
          <TabsTrigger value="story">{t("about.tab.story")}</TabsTrigger>
          <TabsTrigger value="project">{t("about.tab.project")}</TabsTrigger>
          <TabsTrigger value="connect">{t("about.tab.connect")}</TabsTrigger>
        </TabsList>

        <TabsContent value="story" className="space-y-4">
          <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10">
            <div className="relative">
              <div className="absolute inset-0 bg-gradient-to-r from-blue-900/20 to-blue-900/20 z-0"></div>
              <div className="absolute bottom-0 left-0 w-full h-1/3 bg-gradient-to-t from-card/80 to-transparent"></div>
              <div className="absolute -top-10 -right-10 w-40 h-40 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
              <div className="absolute -bottom-10 -left-10 w-60 h-60 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
              <CardHeader className="pb-2 border-b border-blue-200/10 relative z-10">
                <CardTitle className="text-2xl md:text-3xl font-bold text-center md:text-left relative flex flex-col md:flex-row md:items-center gap-2">
                  <div className="flex gap-2 items-center justify-center md:justify-start">
                    <div className="w-10 h-10 rounded-full bg-blue-500/20 backdrop-blur-sm flex items-center justify-center">
                      {/* eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable */}
                      <img src="/images/channelwatch-logo.png" alt={t("about.logoAlt")} className="h-7 w-auto" />
                    </div>
                    <span className="bg-clip-text text-transparent bg-gradient-to-r from-blue-400 to-blue-600 font-extrabold">
                      {t("about.story.heading")}
                    </span>
                  </div>
                  <span className="text-blue-800 dark:text-blue-200 ml-0 md:ml-1">{t("about.story.subheading")}</span>
                </CardTitle>
              </CardHeader>
            </div>

            <CardContent className="prose dark:prose-invert max-w-none pt-6 space-y-6 relative z-10">
              <div className="p-5 border-l-4 border-blue-600 bg-blue-100 dark:bg-blue-950/40 rounded-r-lg shadow-sm backdrop-blur-sm">
                <p className="text-lg font-medium italic">
                  Hello! I&apos;m <strong className="text-blue-800 dark:text-blue-300">Coder Luii</strong>. Many of us lead double lives - by day, I navigate the complex
                  world of cybersecurity as a Security Analyst, focused on protection and prevention. But when the workday
                  ends, a different kind of passion takes over: I become a tech tinkerer, driven by an insatiable
                  curiosity for automation, self-hosted solutions, and the endless possibilities of code.
                </p>
              </div>

              <div className="relative">
                <div className="absolute top-0 left-0 w-1 h-full bg-gradient-to-b from-blue-400/40 to-blue-500/40 rounded-full"></div>
                <p className="pl-4">
                  This blend of structured security thinking and freeform tinkering defines my approach. I believe
                  technology should <span className="font-medium">empower us</span>, make our lives simpler, and maybe even a little more fun. It was this
                  belief, combined with a personal need, that sparked the idea for <strong className="text-blue-800 dark:text-blue-300">ChannelWatch</strong>, my first
                  major project.
                </p>
              </div>

              <div className="relative flex items-center space-x-2 my-6">
                <div className="h-px flex-1 bg-gradient-to-r from-transparent to-blue-400"></div>
                <div className="text-blue-900 dark:text-blue-200 font-bold text-lg px-4 py-1 rounded-full bg-blue-100 dark:bg-blue-900/40 backdrop-blur-sm border border-blue-300 dark:border-blue-700">{t("about.story.divider1")}</div>
                <div className="h-px flex-1 bg-gradient-to-l from-transparent to-blue-400"></div>
              </div>

              <div className="p-4 bg-gradient-to-br from-blue-900/20 to-blue-900/5 rounded-lg backdrop-blur-sm border border-blue-400/10 shadow-sm mb-6">
                <p>
                  Like many Channels DVR enthusiasts, I wanted a deeper connection to my system - to know what was
                  playing, when recordings were happening, and how my setup was performing, all in real-time. I searched
                  for a solution, but couldn&apos;t find exactly what I envisioned.
                </p>
              </div>

              <p>
                So, fueled by late nights and a determination to learn, I decided to build it myself. This journey of
                creation became more than just a project - it became a passion that continues to drive me forward.
              </p>

              <p>
                ChannelWatch isn&apos;t just lines of code; it represents a <span className="font-medium">journey</span>. It&apos;s the result of countless hours spent
                learning, experimenting, failing, and iterating. It embodies the <span className="italic">&quot;always optimizing, always learning&quot; </span>
                 philosophy I strive for. From figuring out how to reliably capture DVR events to integrating various
                notification platforms and crafting informative alerts, every feature was a challenge embraced and
                overcome.
              </p>

              <div className="relative flex items-center space-x-2 my-6">
                <div className="h-px flex-1 bg-gradient-to-r from-transparent to-blue-400"></div>
                <div className="text-blue-900 dark:text-blue-200 font-bold text-lg px-4 py-1 rounded-full bg-blue-100 dark:bg-blue-900/40 backdrop-blur-sm border border-blue-300 dark:border-blue-700">{t("about.story.divider2")}</div>
                <div className="h-px flex-1 bg-gradient-to-l from-transparent to-blue-400"></div>
              </div>

              <p>
                Building ChannelWatch taught me more than just Python and Docker; it deepened my appreciation for the
                open-source spirit and the power of community. Sharing this tool, seeing others find it useful, and
                receiving feedback has been incredibly rewarding. It&apos;s proof that a spark of curiosity, combined with
                dedication, can lead to creating something valuable that benefits others.
              </p>

              <div className="bg-blue-100 dark:bg-blue-950/40 p-5 rounded-lg border border-blue-300 dark:border-blue-700 shadow-inner backdrop-blur-sm relative overflow-hidden">
                <div className="absolute -top-12 -right-12 w-24 h-24 rounded-full bg-blue-600/10 backdrop-blur-xl"></div>
                <div className="absolute -bottom-8 -left-8 w-16 h-16 rounded-full bg-blue-600/10 backdrop-blur-xl"></div>
                <p className="mb-0 relative z-10">
                  My journey as a creator is just beginning. ChannelWatch is the first step, born from a personal passion
                  and a desire to contribute something meaningful. I&apos;m excited to continue refining it, exploring new
                  ideas, and tackling future challenges in the world of cybersecurity, automation, and self-hosting.
                </p>
              </div>

              <div className="p-4 border-t border-blue-400/10 mt-6 flex flex-col items-center justify-center">
                <p className="text-center max-w-2xl mx-auto">
                  If my story resonates with you, or if ChannelWatch has enhanced your own setup, that&apos;s the best
                  encouragement I could ask for. <span className="font-medium">Connecting with fellow tech enthusiasts, tinkerers, and Channels users is
                  what fuels this ongoing adventure.</span>
                </p>
                <div className="mt-4 flex justify-center">
                  <div className="bg-blue-100 dark:bg-blue-900/40 px-4 py-2 rounded-full border border-blue-300 dark:border-blue-700 flex items-center gap-2 text-sm">
                    <span className="text-blue-900 dark:text-blue-200">@CoderLuii</span>
                    <span className="text-slate-700 dark:text-slate-300">{t("about.story.attribution")}</span>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="project" className="space-y-4">
          <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10">
            <div className="relative">
              <div className="absolute inset-0 bg-gradient-to-r from-blue-900/20 to-blue-900/20 z-0"></div>
              <div className="absolute bottom-0 left-0 w-full h-1/3 bg-gradient-to-t from-card/80 to-transparent"></div>
              <div className="absolute -bottom-14 -right-14 w-40 h-40 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
              <CardHeader className="relative z-10 border-b border-blue-200/10">
                <div className="flex items-center gap-3">
                  <div className="flex-shrink-0 w-10 h-10 rounded-full bg-blue-500/20 backdrop-blur-sm flex items-center justify-center">
                    {/* eslint-disable-next-line @next/next/no-img-element -- static export: next/image optimizer unavailable */}
                    <img src="/images/channelwatch-logo.png" alt={t("about.logoAlt")} className="h-7 w-auto" />
                  </div>
                  <div>
                    <CardTitle className="text-blue-700 dark:text-blue-300">{aboutInfo.app_name}</CardTitle>
                    <CardDescription>{t("about.version", { version })}</CardDescription>
                  </div>
                </div>
              </CardHeader>
            </div>
            <CardContent className="space-y-6 relative z-10 pt-6">
                <div className="p-5 border-l-4 border-blue-600 bg-blue-100 dark:bg-blue-950/40 rounded-r-lg shadow-sm backdrop-blur-sm">
                  <p className="text-lg font-medium">
                    {t("about.project.intro")}
                  </p>
                </div>

              <div className="relative">
                <div className="absolute top-0 left-0 w-1 h-full bg-gradient-to-b from-blue-400/40 to-blue-500/40 rounded-full"></div>
                <p className="pl-4">
                   {t("about.project.detail")}
                 </p>
              </div>

              <div className="grid gap-6 md:grid-cols-2">
                <div className="bg-blue-50 dark:bg-blue-950/20 backdrop-blur-sm rounded-xl p-5 space-y-3 border border-blue-200 dark:border-blue-800 shadow-sm">
                  <h3 className="font-medium text-blue-900 dark:text-blue-200 flex items-center gap-2">
                    <div className="w-1.5 h-1.5 rounded-full bg-blue-700 dark:bg-blue-300"></div>
                    {t("about.keyFeatures")}
                  </h3>
                  <ul className="list-none pl-4 space-y-2 text-sm">
                     {([1,2,3,4,5,6,7] as const).map((n) => (
                       <li key={n} className="flex items-start gap-2">
                          <div className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/40 flex items-center justify-center mt-0.5">
                            <div className="w-1.5 h-1.5 rounded-full bg-blue-700 dark:bg-blue-300"></div>
                          </div>
                         <span>{t(`about.feature.${n}`)}</span>
                       </li>
                     ))}
                   </ul>
                </div>

                <div className="bg-blue-50 dark:bg-blue-950/20 backdrop-blur-sm rounded-xl p-5 space-y-3 border border-blue-200 dark:border-blue-800 shadow-sm">
                  <h3 className="font-medium text-blue-900 dark:text-blue-200 flex items-center gap-2">
                    <div className="w-1.5 h-1.5 rounded-full bg-blue-700 dark:bg-blue-300"></div>
                    {t("about.techStack")}
                  </h3>
                  <ul className="list-none pl-4 space-y-2 text-sm">
                     {(["python","fastapi","react","docker","tailwind"] as const).map((key) => (
                       <li key={key} className="flex items-start gap-2">
                          <div className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/40 flex items-center justify-center mt-0.5">
                            <div className="w-1.5 h-1.5 rounded-full bg-blue-700 dark:bg-blue-300"></div>
                          </div>
                          <div>
                            <span className="font-medium text-blue-800 dark:text-blue-200">{t(`about.tech.${key}.name`)}</span>
                            <p className="text-xs text-slate-700 dark:text-slate-300 mt-0.5">{t(`about.tech.${key}.desc`)}</p>
                          </div>
                       </li>
                     ))}
                   </ul>
                </div>
              </div>

              <div className="bg-blue-50 dark:bg-blue-950/20 backdrop-blur-sm rounded-xl p-5 space-y-3 border border-blue-200 dark:border-blue-800 shadow-sm">
                <h3 className="font-medium text-blue-900 dark:text-blue-200 flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-blue-700 dark:bg-blue-300"></div>
                  {t("about.devApproach")}
                </h3>
                <div className="grid gap-4 md:grid-cols-3 mt-3">
                   {(["userCentric","apiFirst","openSource"] as const).map((key) => (
                     <div key={key} className="p-3 bg-blue-100 dark:bg-blue-900/30 rounded-lg border border-blue-300 dark:border-blue-800">
                       <h4 className="text-sm font-medium text-blue-900 dark:text-blue-200 mb-2">{t(`about.approach.${key}.title`)}</h4>
                       <p className="text-xs text-slate-700 dark:text-slate-300">{t(`about.approach.${key}.desc`)}</p>
                     </div>
                   ))}
                 </div>
              </div>
            </CardContent>
            <CardFooter className="flex flex-col sm:flex-row gap-3 border-t border-blue-200/10 pt-4 relative z-10">
              <Button asChild className="w-full sm:w-auto bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-600 transition-all">
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
              <Button variant="outline" asChild className="w-full sm:w-auto border-blue-200/20 hover:bg-blue-500/10">
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
          <Card className="overflow-hidden border-blue-400/20 dark:border-blue-500/20 shadow-lg dark:shadow-blue-900/10">
            <div className="relative">
              <div className="absolute inset-0 bg-gradient-to-r from-blue-900/20 to-blue-900/20 z-0"></div>
              <div className="absolute -top-14 -left-14 w-32 h-32 rounded-full bg-blue-500/10 backdrop-blur-3xl"></div>
              <CardHeader className="relative z-10 border-b border-blue-200/10">
                  <CardTitle className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-blue-400"></div>
                  {t("about.connectTitle")}
                </CardTitle>
                <CardDescription>{t("about.connectDescription")}</CardDescription>
              </CardHeader>
            </div>

            <CardContent className="space-y-6 relative z-10 pt-6">
              <div className="grid gap-4 md:gap-6 md:grid-cols-2">
                <a
                  href="https://github.com/CoderLuii"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-3 p-4 rounded-xl border border-blue-400/20 hover:bg-blue-500/10 transition-colors shadow-sm backdrop-blur-sm bg-blue-500/5"
                >
                  <div className="w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                    <GitBranch className="h-5 w-5 text-blue-700 dark:text-blue-300" />
                  </div>
                  <div>
                    <h3 className="font-medium text-blue-700 dark:text-blue-300">{t("about.githubLabel")}</h3>
                    <p className="text-sm text-muted-foreground">@CoderLuii</p>
                  </div>
                </a>

                <a
                  href="https://x.com/CoderLuii"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center gap-3 p-4 rounded-xl border border-blue-400/20 hover:bg-blue-500/10 transition-colors shadow-sm backdrop-blur-sm bg-blue-500/5"
                >
                  <div className="w-10 h-10 rounded-full bg-blue-500/20 flex items-center justify-center">
                    <AtSign className="h-5 w-5 text-blue-700 dark:text-blue-300" />
                  </div>
                  <div>
                    <h3 className="font-medium text-blue-700 dark:text-blue-300">{t("about.twitterLabel")}</h3>
                    <p className="text-sm text-muted-foreground">@CoderLuii</p>
                  </div>
                </a>
              </div>

              <Separator className="bg-blue-200/10" />

              <div>
                <h3 className="font-medium mb-3 flex items-center gap-2 text-blue-700 dark:text-blue-300">
                  <div className="w-1.5 h-1.5 rounded-full bg-blue-400"></div>
                  {t("about.supportSection")}
                </h3>
                <div className="grid gap-4 md:gap-6 md:grid-cols-2">
                  <a
                    href="https://www.paypal.com/donate/?hosted_button_id=PM2UXGVSTHDNL"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex flex-col items-center gap-2 p-5 rounded-xl border border-blue-400/20 hover:bg-blue-500/10 transition-colors text-center shadow-sm backdrop-blur-sm bg-blue-500/5"
                  >
                    <div className="w-12 h-12 rounded-full bg-blue-500/20 flex items-center justify-center">
                      <svg className="h-6 w-6 text-blue-700 dark:text-blue-300" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M7.076 21.337H2.47a.641.641 0 0 1-.633-.74L4.944.901C5.026.382 5.474 0 5.998 0h7.46c2.57 0 4.578.543 5.69 1.81 1.01 1.15 1.304 2.42 1.012 4.287-.023.143-.047.288-.077.437-.983 5.05-4.349 6.797-8.647 6.797h-2.19c-.524 0-.968.382-1.05.9l-1.12 7.106zm14.146-14.42a3.35 3.35 0 0 0-.607-.541c-.013.076-.026.175-.041.254-.93 4.778-4.005 7.201-9.138 7.201h-2.19c-.018 0-.034.002-.05.005L8.18 21.337h3.598c.37 0 .684-.271.74-.636l.03-.189.59-3.738.038-.217a.74.74 0 0 1 .74-.635h.467c3.01 0 5.36-1.222 6.051-4.764.29-1.489.15-2.734-.212-3.591z" />
                        <path d="M19.826 7.358c-.001.009-.001.018-.003.026-.327 1.679-1.436 5.19-6.508 5.19h-1.652a.929.929 0 0 0-.92.79l-.975 6.175-.028.175a.929.929 0 0 1-.919.79H6.27l-.064.402c-.055.345.213.658.563.658h3.94a.929.929 0 0 0 .919-.79l.038-.238.73-4.634.047-.25a.93.93 0 0 1 .919-.79h.58c3.749 0 6.68-1.52 7.54-5.92.36-1.847.174-3.388-.777-4.469a3.604 3.604 0 0 0-1.02-.82c.316.948.366 1.988.06 3.255z" />
                      </svg>
                    </div>
                    <h4 className="font-medium text-blue-700 dark:text-blue-300">{t("about.paypalLabel")}</h4>
                    <p className="text-xs text-muted-foreground">{t("about.paypalSub")}</p>
                  </a>

                  <a
                    href="https://buymeacoffee.com/CoderLuii"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex flex-col items-center gap-2 p-5 rounded-xl border border-blue-400/20 hover:bg-blue-500/10 transition-colors text-center shadow-sm backdrop-blur-sm bg-blue-500/5"
                  >
                    <div className="w-12 h-12 rounded-full bg-blue-500/20 flex items-center justify-center">
                      <svg className="h-6 w-6 text-blue-700 dark:text-blue-300" viewBox="0 0 24 24" fill="currentColor">
                        <path d="M7.5 2h9A1.5 1.5 0 0 1 18 3.5v1A1.5 1.5 0 0 1 16.5 6h-9A1.5 1.5 0 0 1 6 4.5v-1A1.5 1.5 0 0 1 7.5 2zm0 5h9A1.5 1.5 0 0 1 18 8.5v1a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 9.5v-1A1.5 1.5 0 0 1 7.5 7zM6 15.5A1.5 1.5 0 0 1 7.5 14h9a1.5 1.5 0 0 1 1.5 1.5v1a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 6 16.5v-1zm1.5 4A1.5 1.5 0 0 0 6 21h12a1.5 1.5 0 0 0 0-3H7.5a1.5 1.5 0 0 0-1.5 1.5z" />
                      </svg>
                    </div>
                    <h4 className="font-medium text-blue-700 dark:text-blue-300">{t("about.coffeeLabel")}</h4>
                    <p className="text-xs text-muted-foreground">{t("about.coffeeSub")}</p>
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


