"use client";

import React, { useState, useEffect, useRef } from "react";
import { Button } from "@/components/base/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/base/card";
import { Badge } from "@/components/base/badge";
import { Progress } from "@/components/base/progress";
import { Input } from "@/components/base/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/base/select";
import {
  Loader2,
  AlertCircle,
  CheckCircle,
  Activity,
  Server,
  Gauge,
  Wifi,
  Terminal,
  Download,
  Copy,
  Pause,
  Play,
  Search,
  RefreshCw,
  Bug,
} from "lucide-react";
import {
  authHeaders,
  runTest,
  fetchSystemInfo,
  fetchSettings,
  downloadDebugBundle,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { useToast } from "@/hooks/use-toast";
import { useDvrSelection } from "@/lib/dvr-selection-context";
import type { SystemInfo, AppSettings, DVRStatusInfo } from "@/lib/types";

function getDvrDotClass(dvr: DVRStatusInfo): string {
  if (!dvr.connected) return "bg-red-500";
  if (dvr.monitoring_ready === false) {
    return dvr.monitoring_status === "dead" ? "bg-red-500" : "bg-amber-500";
  }
  return "bg-green-500";
}

function formatDvrVersion(version: string | null): string | null {
  if (!version) return null;
  const parts = version.split(".");
  return parts.length >= 3 ? `v${parts.slice(0, 3).join(".")}` : version;
}

export function DiagnosticsPanel() {
  const [systemInfo, setSystemInfo] = useState<SystemInfo | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { toast } = useToast();
  const { selectedDvr } = useDvrSelection();
  const [appSettings, setAppSettings] = useState<AppSettings | null>(null);

  const dvrSections = React.useMemo((): DVRStatusInfo[] => {
    if (!systemInfo?.dvr_status?.length) return [];
    if (selectedDvr !== "all") {
      return systemInfo.dvr_status.filter((d) => d.id === selectedDvr);
    }
    return systemInfo.dvr_status;
  }, [systemInfo?.dvr_status, selectedDvr]);

  const isMultiDvr = dvrSections.length > 1;
  const [logLines, setLogLines] = useState<string[]>([]);
  const [logLineCount, setLogLineCount] = useState(100);
  const [logPaused, setLogPaused] = useState(false);
  const [logFilter, setLogFilter] = useState("");
  const [logLevelFilters, setLogLevelFilters] = useState<string[]>([]);
  const [copied, setCopied] = useState(false);
  const [debugBundleState, setDebugBundleState] = useState<
    "idle" | "loading" | "done" | "error"
  >("idle");
  const logFetchInFlightRef = useRef(false);
  const logFetchOwnerRef = useRef(0);

  const handleDownloadDebugBundle = async () => {
    setDebugBundleState("loading");
    try {
      const blob = await downloadDebugBundle();
      const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `channelwatch_debug_${ts}.zip`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setDebugBundleState("done");
      setTimeout(() => setDebugBundleState("idle"), 3000);
    } catch {
      setDebugBundleState("error");
      setTimeout(() => setDebugBundleState("idle"), 4000);
    }
  };

  // Poll logs every 2 seconds (unless paused)
  useEffect(() => {
    if (logPaused) return;
    let cancelled = false;
    const controller = new AbortController();
    const fetchLogs = async () => {
      if (logFetchInFlightRef.current) return;
      const requestId = logFetchOwnerRef.current + 1;
      logFetchOwnerRef.current = requestId;
      logFetchInFlightRef.current = true;
      try {
        const resp = await fetch(`/api/logs?lines=${logLineCount}`, {
          headers: authHeaders(),
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (resp.ok && !cancelled) {
          const data = await resp.json();
          if (!cancelled) setLogLines(data.lines || []);
        }
      } catch {
      } finally {
        if (logFetchOwnerRef.current === requestId) {
          logFetchInFlightRef.current = false;
        }
      }
    };
    if (!logPaused) fetchLogs();
    const interval = setInterval(fetchLogs, 2000);
    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(interval);
    };
  }, [logPaused, logLineCount]);

  const filteredLogLines = logLines.filter((line) => {
    if (logFilter && !line.toLowerCase().includes(logFilter.toLowerCase()))
      return false;
    if (logLevelFilters.length === 0) return true;
    if (
      logLevelFilters.includes("error") &&
      (line.includes("[FAIL]") || line.includes("ERROR"))
    )
      return true;
    if (
      logLevelFilters.includes("warn") &&
      (line.includes("WARNING") || line.includes("WARN"))
    )
      return true;
    if (
      logLevelFilters.includes("info") &&
      !line.includes("[FAIL]") &&
      !line.includes("ERROR") &&
      !line.includes("WARNING") &&
      !line.includes("WARN")
    )
      return true;
    return false;
  });

  const handleCopyLogs = () => {
    navigator.clipboard.writeText(filteredLogLines.join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownloadLogs = async () => {
    try {
      const resp = await fetch("/api/logs/download", {
        headers: authHeaders(),
        credentials: "same-origin",
      });
      if (!resp.ok) {
        toast({
          variant: "destructive",
          title: t("diagnostics.logs.downloadFailed"),
          description: t("diagnostics.logs.downloadFailedServer", {
            status: resp.status,
          }),
        });
        return;
      }
      const blob = await resp.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "channelwatch.log";
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast({
        variant: "destructive",
        title: t("diagnostics.logs.downloadFailed"),
        description: t("diagnostics.logs.downloadFailedNetwork"),
      });
    }
  };

  // Auto-scroll log terminal (only within its own container)
  const logContainerRef = useRef<HTMLDivElement>(null);
  const userScrolledUp = useRef(false);

  // Track if user manually scrolled up
  useEffect(() => {
    const el = logContainerRef.current;
    if (!el) return;
    const handleScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      userScrolledUp.current = distFromBottom > 50;
    };
    el.addEventListener("scroll", handleScroll);
    return () => el.removeEventListener("scroll", handleScroll);
  }, []);

  // Auto-scroll on new log lines unless user scrolled up or paused
  useEffect(() => {
    const el = logContainerRef.current;
    if (!el || logPaused || userScrolledUp.current) return;
    el.scrollTop = el.scrollHeight;
  }, [filteredLogLines, logPaused]);

  useEffect(() => {
    const getSystemInfo = async () => {
      try {
        setLoading(true);
        const info = await fetchSystemInfo();
        setSystemInfo(info);
        setError(null);
      } catch (err) {
        console.error("Failed to fetch system info:", err);
        setError(
          err instanceof Error
            ? err.message
            : t("diagnostics.loadError.systemInfo"),
        );
        toast({
          title: t("common.error"),
          description: t("diagnostics.loadError.desc"),
          variant: "destructive",
        });
      } finally {
        setLoading(false);
      }
    };

    getSystemInfo();
    fetchSettings()
      .then((s) => setAppSettings(s))
      .catch(() => {});
  }, [toast]);

  const getActiveProviders = (): string[] => {
    if (!appSettings) return [];
    const providers: string[] = [];
    if (appSettings.apprise_pushover && appSettings.apprise_pushover !== "")
      providers.push(t("provider.pushover.name"));
    if (appSettings.apprise_discord && appSettings.apprise_discord !== "")
      providers.push(t("provider.discord.name"));
    if (appSettings.apprise_email && appSettings.apprise_email !== "")
      providers.push(t("provider.email.name"));
    if (appSettings.apprise_telegram && appSettings.apprise_telegram !== "")
      providers.push(t("provider.telegram.name"));
    if (appSettings.apprise_slack && appSettings.apprise_slack !== "")
      providers.push(t("provider.slack.name"));
    if (appSettings.apprise_gotify && appSettings.apprise_gotify !== "")
      providers.push(t("provider.gotify.name"));
    if (appSettings.apprise_matrix && appSettings.apprise_matrix !== "")
      providers.push(t("provider.matrix.name"));
    if (appSettings.apprise_custom && appSettings.apprise_custom !== "")
      providers.push(t("provider.custom.name"));
    return providers;
  };

  const getHealthIssues = (): string[] => {
    const issues: string[] = [];
    if (dvrSections.length > 0) {
      const offlineDvrs = dvrSections.filter((d) => !d.connected);
      const staleDvrs = dvrSections.filter((d) => d.monitoring_ready === false);
      offlineDvrs.forEach((d) =>
        issues.push(
          t("diagnostics.health.notConnected", { name: d.name || d.host }),
        ),
      );
      staleDvrs.forEach((d) =>
        issues.push(
          t("diagnostics.health.monitoringDegraded", {
            name: d.name || d.host,
            status: d.monitoring_status || t("diagnostics.system.degraded"),
          }),
        ),
      );
    } else if (!systemInfo?.channels_dvr_host) {
      issues.push(t("diagnostics.health.notConfigured"));
    }
    if (getActiveProviders().length === 0)
      issues.push(t("diagnostics.health.noProvidersMsg"));
    const alertsEnabled =
      appSettings &&
      (appSettings.alert_channel_watching ||
        appSettings.alert_vod_watching ||
        appSettings.alert_disk_space ||
        appSettings.alert_recording_events);
    if (appSettings && !alertsEnabled)
      issues.push(t("diagnostics.health.noAlertsMsg"));
    return issues;
  };

  const tests = [
    {
      name: t("diagnostics.test.connectivity.name"),
      description: t("diagnostics.test.connectivity.desc"),
      icon: Wifi,
      category: "connectivity",
    },
    {
      name: t("diagnostics.test.apiEndpoints.name"),
      description: t("diagnostics.test.apiEndpoints.desc"),
      icon: Server,
      category: "connectivity",
    },
    {
      name: t("diagnostics.test.cwAlert.name"),
      description: t("diagnostics.test.cwAlert.desc"),
      icon: Activity,
      category: "notifications",
    },
    {
      name: t("diagnostics.test.vodAlert.name"),
      description: t("diagnostics.test.vodAlert.desc"),
      icon: Activity,
      category: "notifications",
    },
    {
      name: t("diagnostics.test.diskAlert.name"),
      description: t("diagnostics.test.diskAlert.desc"),
      icon: Gauge,
      category: "notifications",
    },
    {
      name: t("diagnostics.test.recScheduled.name"),
      description: t("diagnostics.test.recScheduled.desc"),
      icon: Activity,
      category: "notifications",
    },
    {
      name: t("diagnostics.test.recStarted.name"),
      description: t("diagnostics.test.recStarted.desc"),
      icon: Activity,
      category: "notifications",
    },
    {
      name: t("diagnostics.test.recCompleted.name"),
      description: t("diagnostics.test.recCompleted.desc"),
      icon: Activity,
      category: "notifications",
    },
    {
      name: t("diagnostics.test.recStopped.name"),
      description: t("diagnostics.test.recStopped.desc"),
      icon: Activity,
      category: "notifications",
    },
    {
      name: t("diagnostics.test.recCancelled.name"),
      description: t("diagnostics.test.recCancelled.desc"),
      icon: Activity,
      category: "notifications",
    },
  ];

  const [runAllProgress, setRunAllProgress] = useState<{
    current: number;
    total: number;
  } | null>(null);
  const [runAllElapsed, setRunAllElapsed] = useState<number | null>(null);
  const [testStatus, setTestStatus] = useState<
    Record<string, { status: string; message?: string; elapsed?: number }>
  >({});

  const handleRunTest = async (testName: string, partOfRunAll = false) => {
    setTestStatus((prev) => ({ ...prev, [testName]: { status: "running" } }));
    const start = Date.now();

    try {
      const result = await runTest(testName);
      const elapsed = (Date.now() - start) / 1000;
      setTestStatus((prev) => ({
        ...prev,
        [testName]: {
          status: result.success ? "pass" : "fail",
          message: result.message,
          elapsed,
        },
      }));
    } catch (err) {
      const elapsed = (Date.now() - start) / 1000;
      setTestStatus((prev) => ({
        ...prev,
        [testName]: {
          status: "fail",
          message:
            err instanceof Error
              ? err.message
              : t("diagnostics.tests.testFailed"),
          elapsed,
        },
      }));
    }
  };

  const handleRunAllTests = async () => {
    const total = tests.length;
    const allStart = Date.now();
    setRunAllProgress({ current: 0, total });
    setTestStatus({});
    setRunAllElapsed(null);

    for (let i = 0; i < tests.length; i++) {
      setRunAllProgress({ current: i, total });
      await handleRunTest(tests[i].name, true);
    }

    setRunAllElapsed((Date.now() - allStart) / 1000);
    setRunAllProgress(null);
  };

  const formatDiskSize = (sizeInGB: number | null | undefined): string => {
    if (sizeInGB === null || sizeInGB === undefined) {
      return t("diagnostics.system.na");
    }

    if (sizeInGB >= 1000) {
      return (sizeInGB / 1000).toFixed(2) + " TB";
    } else {
      return Math.round(sizeInGB) + " GB";
    }
  };

  const calculateDiskUsage = () => {
    if (
      !systemInfo ||
      systemInfo.disk_total_gb === null ||
      systemInfo.disk_free_gb === null
    ) {
      return {
        usedGB: null,
        usedTB: t("diagnostics.system.na"),
        totalGB: null,
        totalTB: t("diagnostics.system.na"),
        freeGB: null,
      };
    }

    const totalGB = systemInfo.disk_total_gb;
    const freeGB = systemInfo.disk_free_gb;
    const usedGB = totalGB - freeGB;

    const totalTB = (totalGB / 1000).toFixed(2);
    const usedTB = (usedGB / 1000).toFixed(2);

    const totalTBFormatted = `${totalTB} TB`;
    const usedTBFormatted = `${usedTB} TB`;

    return {
      usedGB,
      usedTB: usedTBFormatted,
      totalGB,
      totalTB: totalTBFormatted,
      freeGB,
      usedPercent: systemInfo.disk_usage_percent,
    };
  };

  const getDiskStatusColor = (usedPercent: number): string => {
    if (usedPercent > 90) return "bg-red-500";
    if (usedPercent > 75) return "bg-amber-500";
    return "bg-green-500";
  };

  const diskInfo = calculateDiskUsage();

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between">
        <div className="flex flex-col gap-2">
          <h1 className="text-3xl font-bold tracking-tight">
            {t("diagnostics.title")}
          </h1>
          <p className="text-muted-foreground">
            {t("diagnostics.description")}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5 text-xs flex-shrink-0"
            aria-label={t("diagnostics.debugBundle.aria")}
            disabled={debugBundleState === "loading"}
            onClick={handleDownloadDebugBundle}
          >
            {debugBundleState === "loading" && (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            )}
            {debugBundleState === "done" && (
              <CheckCircle className="h-3.5 w-3.5 text-emerald-500" />
            )}
            {debugBundleState === "error" && (
              <AlertCircle className="h-3.5 w-3.5 text-red-500" />
            )}
            {debugBundleState === "idle" && <Bug className="h-3.5 w-3.5" />}
            {debugBundleState === "loading"
              ? t("diagnostics.debugBundle.downloadingBtn")
              : debugBundleState === "done"
                ? t("diagnostics.debugBundle.downloadedBtn")
                : debugBundleState === "error"
                  ? t("diagnostics.debugBundle.failedBtn")
                  : t("diagnostics.debugBundle.btn")}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5 text-xs flex-shrink-0"
            aria-label={t("diagnostics.aria.export")}
            onClick={() => {
              const providers = getActiveProviders();
              const healthIssues = getHealthIssues();
              const testResults = Object.entries(testStatus)
                .map(
                  ([name, s]) =>
                    `  ${s.status === "pass" ? "PASS" : s.status === "fail" ? "FAIL" : "..."} ${name}${s.elapsed != null ? ` (${s.elapsed.toFixed(1)}s)` : ""}${s.status === "fail" && s.message ? ` - ${s.message}` : ""}`,
                )
                .join("\n");

              const dvrLines =
                dvrSections.length > 0
                  ? dvrSections.flatMap(
                      (dvr) =>
                        [
                          ``,
                          t("diagnostics.export.dvrEntry", {
                            name: dvr.name || dvr.host,
                            id: dvr.id,
                          }),
                          t("diagnostics.export.dvrHost", {
                            host: dvr.host,
                            port: String(dvr.port),
                          }),
                          t("diagnostics.export.dvrConnection", {
                            status: dvr.connected
                              ? t("diagnostics.system.connected")
                              : t("diagnostics.system.notConnected"),
                          }),
                          dvr.version
                            ? t("diagnostics.export.dvrVersion", {
                                version: String(formatDvrVersion(dvr.version)),
                              })
                            : null,
                          dvr.monitoring_ready === false
                            ? t("diagnostics.export.dvrMonitoring", {
                                status: `${dvr.monitoring_status || t("diagnostics.system.degraded")}${dvr.monitoring_reason ? ` \u2014 ${dvr.monitoring_reason}` : ""}`,
                              })
                            : null,
                        ].filter(Boolean) as string[],
                    )
                  : [
                      t("diagnostics.export.hostFallback", {
                        host:
                          systemInfo?.channels_dvr_host ||
                          t("common.notConfigured"),
                        port: String(systemInfo?.channels_dvr_port || "8089"),
                      }),
                      t("diagnostics.export.connectionFallback", {
                        status: systemInfo?.channels_dvr_server_version
                          ? t("diagnostics.system.connected")
                          : t("diagnostics.system.notConnected"),
                      }),
                      t("diagnostics.export.dvrVersionFallback", {
                        version:
                          systemInfo?.channels_dvr_server_version ||
                          t("common.unknown"),
                      }),
                    ];

              const lines = [
                t("diagnostics.export.header"),
                t("diagnostics.export.generated", {
                  date: new Date().toLocaleString(),
                }),
                ``,
                t("diagnostics.export.sectionSystem"),
                t("diagnostics.export.rowVersion", {
                  value:
                    systemInfo?.channelwatch_version || t("common.unknown"),
                }),
                t("diagnostics.export.rowTimezone", {
                  value: systemInfo?.timezone || t("common.unknown"),
                }),
                t("diagnostics.export.rowCoreStatus", {
                  value: systemInfo?.core_status || t("common.unknown"),
                }),
                t("diagnostics.export.rowUptime", {
                  value:
                    systemInfo?.uptime_data &&
                    Object.keys(systemInfo.uptime_data).length > 0
                      ? `${systemInfo.uptime_data.days}d ${systemInfo.uptime_data.hours}h ${systemInfo.uptime_data.minutes}m`
                      : t("diagnostics.system.na"),
                }),
                t("diagnostics.export.rowLogRetention", {
                  value:
                    systemInfo?.log_retention_days != null
                      ? t("diagnostics.export.days", {
                          n: String(systemInfo.log_retention_days),
                        })
                      : t("diagnostics.system.na"),
                }),
                t("diagnostics.export.rowDisk", {
                  value:
                    diskInfo.usedGB != null
                      ? t("diagnostics.export.usedOf", {
                          used: formatDiskSize(diskInfo.usedGB),
                          total: String(diskInfo.totalTB),
                          percent: String(diskInfo.usedPercent),
                        })
                      : t("diagnostics.system.na"),
                }),
                t("diagnostics.export.rowProviders", {
                  value:
                    providers.length > 0
                      ? providers.join(", ")
                      : t("diagnostics.export.none"),
                }),
                ``,
                t("diagnostics.export.sectionDvr"),
                ...dvrLines,
                ``,
                t("diagnostics.export.sectionHealth"),
                healthIssues.length === 0
                  ? t("diagnostics.export.allChecksPassed")
                  : healthIssues
                      .map((i) => t("diagnostics.export.warning", { issue: i }))
                      .join("\n"),
                ``,
                t("diagnostics.export.sectionTests"),
                Object.keys(testStatus).length > 0
                  ? testResults
                  : t("diagnostics.export.noTests"),
                ``,
                t("diagnostics.export.sectionLogs"),
                ...logLines.slice(-50),
              ];

              const blob = new Blob([lines.join("\n")], { type: "text/plain" });
              const url = URL.createObjectURL(blob);
              const a = document.createElement("a");
              a.href = url;
              a.download = `channelwatch-diagnostics-${new Date().toISOString().slice(0, 10)}.txt`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            <Download className="h-3.5 w-3.5" />
            {t("diagnostics.exportBtn")}
          </Button>
        </div>
      </div>

      {/* Live Log Terminal */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Terminal className="h-5 w-5 text-primary" />
                {t("diagnostics.logs.title")}
                {logPaused && (
                  <Badge
                    variant="outline"
                    className="ml-2 text-yellow-500 border-yellow-500"
                  >
                    {t("diagnostics.logs.paused")}
                  </Badge>
                )}
              </CardTitle>
              <CardDescription className="mt-1">
                {t("diagnostics.logs.description")}
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              <Select
                value={String(logLineCount)}
                onValueChange={(v) => setLogLineCount(Number(v))}
              >
                <SelectTrigger
                  className="w-[100px] h-8 text-xs"
                  aria-label={t("diagnostics.logs.aria.lineCount")}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="50">
                    {t("diagnostics.logs.lines50")}
                  </SelectItem>
                  <SelectItem value="100">
                    {t("diagnostics.logs.lines100")}
                  </SelectItem>
                  <SelectItem value="250">
                    {t("diagnostics.logs.lines250")}
                  </SelectItem>
                  <SelectItem value="500">
                    {t("diagnostics.logs.lines500")}
                  </SelectItem>
                  <SelectItem value="0">
                    {t("diagnostics.logs.linesAll")}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 mb-2">
            <div className="relative flex-1">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                placeholder={t("diagnostics.logs.filterPlaceholder")}
                aria-label={t("diagnostics.logs.aria.filter")}
                value={logFilter}
                onChange={(e) => setLogFilter(e.target.value)}
                className="h-8 text-xs pl-8"
              />
            </div>
            <div className="flex items-center gap-1">
              <Button
                variant={
                  logLevelFilters.includes("error") ? "default" : "outline"
                }
                size="sm"
                className={`h-8 px-2 text-xs ${logLevelFilters.includes("error") ? "bg-red-600 hover:bg-red-700 text-white" : ""}`}
                onClick={() =>
                  setLogLevelFilters((prev) =>
                    prev.includes("error")
                      ? prev.filter((f) => f !== "error")
                      : [...prev, "error"],
                  )
                }
                aria-label={t("diagnostics.logs.aria.filterErrors")}
              >
                ERR
              </Button>
              <Button
                variant={
                  logLevelFilters.includes("warn") ? "default" : "outline"
                }
                size="sm"
                className={`h-8 px-2 text-xs ${logLevelFilters.includes("warn") ? "bg-yellow-600 hover:bg-yellow-700 text-white" : ""}`}
                onClick={() =>
                  setLogLevelFilters((prev) =>
                    prev.includes("warn")
                      ? prev.filter((f) => f !== "warn")
                      : [...prev, "warn"],
                  )
                }
                aria-label={t("diagnostics.logs.aria.filterWarnings")}
              >
                WARN
              </Button>
              <Button
                variant={
                  logLevelFilters.includes("info") ? "default" : "outline"
                }
                size="sm"
                className={`h-8 px-2 text-xs ${logLevelFilters.includes("info") ? "bg-blue-600 hover:bg-blue-700 text-white" : ""}`}
                onClick={() =>
                  setLogLevelFilters((prev) =>
                    prev.includes("info")
                      ? prev.filter((f) => f !== "info")
                      : [...prev, "info"],
                  )
                }
                aria-label={t("diagnostics.logs.aria.filterInfo")}
              >
                INFO
              </Button>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="h-8 px-2"
              onClick={() => setLogPaused(!logPaused)}
              aria-label={
                logPaused
                  ? t("diagnostics.logs.aria.resume")
                  : t("diagnostics.logs.aria.pause")
              }
              title={
                logPaused
                  ? t("diagnostics.logs.aria.resume")
                  : t("diagnostics.logs.aria.pause")
              }
            >
              {logPaused ? (
                <Play className="h-3.5 w-3.5" />
              ) : (
                <Pause className="h-3.5 w-3.5" />
              )}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 px-2"
              onClick={handleCopyLogs}
              aria-label={t("diagnostics.logs.aria.copy")}
              title={t("diagnostics.logs.aria.copy")}
            >
              {copied ? (
                <CheckCircle className="h-3.5 w-3.5 text-green-500" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 px-2"
              onClick={handleDownloadLogs}
              aria-label={t("diagnostics.logs.aria.download")}
              title={t("diagnostics.logs.aria.download")}
            >
              <Download className="h-3.5 w-3.5" />
            </Button>
          </div>
          <div
            ref={logContainerRef}
            className="bg-zinc-950 rounded-lg border border-zinc-800 font-mono text-xs leading-5 overflow-auto h-80 p-3 select-text"
          >
            {filteredLogLines.length === 0 ? (
              <span className="text-zinc-500">
                {logFilter
                  ? t("diagnostics.logs.emptyFiltered")
                  : t("diagnostics.logs.empty")}
              </span>
            ) : (
              filteredLogLines.map((line, i) => (
                <div
                  key={i}
                  className={
                    line.includes("[FAIL]") || line.includes("ERROR")
                      ? "text-red-400"
                      : line.includes("[PASS]") ||
                          line.includes("Notification sent")
                        ? "text-green-400"
                        : line.includes("WARNING") || line.includes("WARN")
                          ? "text-yellow-400"
                          : line.includes("🔧")
                            ? "text-blue-400 font-semibold"
                            : "text-zinc-300"
                  }
                >
                  {line}
                </div>
              ))
            )}
          </div>
        </CardContent>
      </Card>

      {/* System Information - moved above tests */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Server className="h-5 w-5 text-primary" />
                {t("diagnostics.system.title")}
              </CardTitle>
              <CardDescription className="mt-1">
                {t("diagnostics.system.description")}
              </CardDescription>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="h-7 gap-1 text-xs"
              aria-label={t("diagnostics.aria.refresh")}
              onClick={async () => {
                setLoading(true);
                try {
                  const info = await fetchSystemInfo();
                  setSystemInfo(info);
                  setError(null);
                } catch (err) {
                  setError(
                    err instanceof Error
                      ? err.message
                      : t("diagnostics.system.failedRefresh"),
                  );
                } finally {
                  setLoading(false);
                }
              }}
            >
              <RefreshCw
                className={`h-3 w-3 ${loading ? "animate-spin" : ""}`}
              />
              {t("diagnostics.refreshBtn")}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div
              className={`grid gap-4 ${isMultiDvr ? "md:grid-cols-2" : "md:grid-cols-2 lg:grid-cols-3"}`}
            >
              <div className="rounded-lg p-3 space-y-2 border">
                <h3 className="text-sm font-medium mb-2">
                  {t("diagnostics.system.env")}
                </h3>
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.versionKey")}
                      </span>
                      <span>
                        {systemInfo?.channelwatch_version ||
                          t("common.unknown")}
                      </span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.timezoneKey")}
                      </span>
                      <span>{systemInfo?.timezone || t("common.unknown")}</span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.coreStatusKey")}
                      </span>
                      <span
                        className={
                          systemInfo?.core_status === "Running"
                            ? "text-green-700 dark:text-green-500 flex items-center"
                            : "text-muted-foreground flex items-center"
                        }
                      >
                        {systemInfo?.core_status === "Running" ? (
                          <>
                            <CheckCircle className="h-3 w-3 mr-1" />
                            {t("diagnostics.system.connected")}
                          </>
                        ) : (
                          systemInfo?.core_status || t("common.unknown")
                        )}
                      </span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.uptimeKey")}
                      </span>
                      <span>
                        {systemInfo?.uptime_data &&
                        Object.keys(systemInfo.uptime_data).length > 0
                          ? `${systemInfo.uptime_data.days}d ${systemInfo.uptime_data.hours}h ${systemInfo.uptime_data.minutes}m`
                          : t("diagnostics.system.na")}
                      </span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.retentionKey")}
                      </span>
                      <span>
                        {systemInfo?.log_retention_days != null
                          ? t("diagnostics.export.days", {
                              n: String(systemInfo.log_retention_days),
                            })
                          : t("diagnostics.system.na")}
                      </span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.providersKey")}
                      </span>
                      <span>
                        {getActiveProviders().length > 0 ? (
                          getActiveProviders().join(", ")
                        ) : (
                          <span className="text-red-700 dark:text-red-500">
                            {t("diagnostics.system.noneConfigured")}
                          </span>
                        )}
                      </span>
                    </div>
                  </>
                )}
              </div>

              {!isMultiDvr && (
                <div className="rounded-lg p-3 space-y-2 border">
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-medium">
                      {dvrSections[0]
                        ? dvrSections[0].name ||
                          t("diagnostics.system.dvrConnectionHeading")
                        : t("diagnostics.system.connectionHeading")}
                    </h3>
                    {dvrSections[0] && (
                      <span
                        className={`inline-flex h-2 w-2 rounded-full ${getDvrDotClass(dvrSections[0])}`}
                        title={
                          dvrSections[0].connected
                            ? t("diagnostics.system.connected")
                            : t("status.dvr.notConnected")
                        }
                      />
                    )}
                  </div>
                  {loading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : dvrSections[0] ? (
                    <>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">
                          {t("diagnostics.system.hostKey")}
                        </span>
                        <span>
                          {dvrSections[0].host}:{dvrSections[0].port}
                        </span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">
                          {t("diagnostics.system.statusKey")}
                        </span>
                        <span
                          className={
                            dvrSections[0].connected
                              ? "text-green-700 dark:text-green-500 flex items-center"
                              : "text-red-700 dark:text-red-500 flex items-center"
                          }
                        >
                          {dvrSections[0].connected ? (
                            <>
                              <CheckCircle className="h-3 w-3 mr-1" />
                              {t("diagnostics.system.connected")}
                            </>
                          ) : (
                            <>
                              <AlertCircle className="h-3 w-3 mr-1" />
                              {t("diagnostics.system.notConnected")}
                            </>
                          )}
                        </span>
                      </div>
                      {dvrSections[0].version && (
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">
                            {t("diagnostics.system.dvrVersionKey")}
                          </span>
                          <span>
                            {formatDvrVersion(dvrSections[0].version)}
                          </span>
                        </div>
                      )}
                      {dvrSections[0].monitoring_ready === false && (
                        <div className="flex justify-between text-sm">
                          <span className="text-muted-foreground">
                            {t("diagnostics.system.monitoringKey")}
                          </span>
                          <span className="text-amber-600 dark:text-amber-400 capitalize">
                            {dvrSections[0].monitoring_status ||
                              t("diagnostics.system.degraded")}
                          </span>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">
                          {t("diagnostics.system.dvrHostKey")}
                        </span>
                        <span>
                          {systemInfo?.channels_dvr_host ||
                            t("common.notConfigured")}
                        </span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">
                          {t("diagnostics.system.dvrPortKey")}
                        </span>
                        <span>{systemInfo?.channels_dvr_port || "8089"}</span>
                      </div>
                      <div className="flex justify-between text-sm">
                        <span className="text-muted-foreground">
                          {t("diagnostics.system.statusKey")}
                        </span>
                        <span
                          className={
                            systemInfo?.channels_dvr_server_version
                              ? "text-green-700 dark:text-green-500 flex items-center"
                              : "text-red-700 dark:text-red-500 flex items-center"
                          }
                        >
                          {systemInfo?.channels_dvr_server_version ? (
                            <>
                              <CheckCircle className="h-3 w-3 mr-1" />
                              {t("diagnostics.system.connected")}
                            </>
                          ) : (
                            <>
                              <AlertCircle className="h-3 w-3 mr-1" />
                              {t("diagnostics.system.notConnected")}
                            </>
                          )}
                        </span>
                      </div>
                    </>
                  )}
                </div>
              )}

              <div className="rounded-lg p-3 space-y-2 border">
                <h3 className="text-sm font-medium mb-2">
                  {t("diagnostics.system.storage")}
                </h3>
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : systemInfo && diskInfo.totalGB ? (
                  <>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.usedKey")}
                      </span>
                      <span>
                        {diskInfo.usedGB
                          ? formatDiskSize(diskInfo.usedGB)
                          : t("diagnostics.system.na")}
                      </span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.freeKey")}
                      </span>
                      <span>
                        {diskInfo.freeGB
                          ? formatDiskSize(diskInfo.freeGB)
                          : t("diagnostics.system.na")}
                      </span>
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.totalKey")}
                      </span>
                      <span>{diskInfo.totalTB}</span>
                    </div>
                    {systemInfo.disk_usage_percent != null && (
                      <Progress
                        value={systemInfo.disk_usage_percent}
                        className="h-2 mt-1"
                        indicatorClassName={getDiskStatusColor(
                          systemInfo.disk_usage_percent,
                        )}
                        aria-label={t("diagnostics.system.storage")}
                      />
                    )}
                  </>
                ) : (
                  <span className="text-sm text-muted-foreground">
                    {t("diagnostics.system.noData")}
                  </span>
                )}
              </div>
            </div>

            {isMultiDvr && (
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {dvrSections.map((dvr) => {
                  const versionDisplay = formatDvrVersion(dvr.version);
                  return (
                    <div
                      key={dvr.id}
                      className="rounded-lg p-3 space-y-2 border"
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2 min-w-0">
                          <span
                            className={`inline-flex h-2 w-2 rounded-full flex-shrink-0 ${getDvrDotClass(dvr)}`}
                            title={
                              dvr.connected
                                ? t("diagnostics.system.connected")
                                : t("status.dvr.notConnected")
                            }
                          />
                          <h3 className="text-sm font-medium truncate">
                            {dvr.name || dvr.host}
                          </h3>
                        </div>
                        <Badge
                          variant={dvr.connected ? "default" : "secondary"}
                          className={`text-[10px] py-0 h-4 px-1.5 font-normal leading-none flex-shrink-0 ${dvr.connected ? "bg-green-600 text-green-50" : "bg-red-700 text-white dark:bg-red-600"}`}
                        >
                          {dvr.connected
                            ? t("diagnostics.system.connected")
                            : t("diagnostics.system.offline")}
                        </Badge>
                      </div>
                      {loading ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <>
                          <div className="flex justify-between text-sm">
                            <span className="text-muted-foreground">
                              {t("diagnostics.system.hostKey")}
                            </span>
                            <span className="text-right truncate ml-2">
                              {dvr.host}:{dvr.port}
                            </span>
                          </div>
                          {versionDisplay && (
                            <div className="flex justify-between text-sm">
                              <span className="text-muted-foreground">
                                {t("diagnostics.system.versionLabel")}
                              </span>
                              <span>{versionDisplay}</span>
                            </div>
                          )}
                          {dvr.monitoring_ready === false && (
                            <div className="flex justify-between text-sm">
                              <span className="text-muted-foreground">
                                {t("diagnostics.system.monitoringKey")}
                              </span>
                              <span className="text-amber-600 dark:text-amber-400 capitalize">
                                {dvr.monitoring_status ||
                                  t("diagnostics.system.degraded")}
                              </span>
                            </div>
                          )}
                          {dvr.monitoring_reason && (
                            <p className="text-[11px] text-muted-foreground/70 leading-snug">
                              {dvr.monitoring_reason}
                            </p>
                          )}
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-primary" />
            {t("diagnostics.tests.title")}
          </CardTitle>
          <CardDescription>
            {t("diagnostics.tests.description")}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-4">
            <Button
              onClick={handleRunAllTests}
              disabled={runAllProgress !== null}
              size="lg"
            >
              {runAllProgress !== null && (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              )}
              {runAllProgress !== null
                ? t("diagnostics.tests.running", {
                    current: runAllProgress.current + 1,
                    total: runAllProgress.total,
                  })
                : t("diagnostics.tests.runAll")}
            </Button>
          </div>
          {runAllProgress !== null && (
            <Progress
              value={
                ((runAllProgress.current + 1) / runAllProgress.total) * 100
              }
              className="h-2 mb-4"
              aria-label={t("diagnostics.tests.title")}
            />
          )}
          {Object.keys(testStatus).length > 0 && runAllProgress === null && (
            <div className="mb-4 p-3 rounded-lg border flex items-center gap-3">
              <span className="text-sm font-medium">
                {t("diagnostics.tests.resultsLabel")}
              </span>
              <span className="text-sm text-green-700 dark:text-green-500">
                {t("diagnostics.tests.passed", {
                  n: Object.values(testStatus).filter(
                    (s) => s.status === "pass",
                  ).length,
                })}
              </span>
              <span className="text-sm text-red-700 dark:text-red-500">
                {t("diagnostics.tests.failed", {
                  n: Object.values(testStatus).filter(
                    (s) => s.status === "fail",
                  ).length,
                })}
              </span>
              {runAllElapsed != null && (
                <span className="text-sm text-muted-foreground">
                  {t("diagnostics.tests.inTime", {
                    s: runAllElapsed.toFixed(1),
                  })}
                </span>
              )}
            </div>
          )}
          {["connectivity", "notifications"].map((category) => {
            const categoryTests = tests.filter((t) => t.category === category);
            const categoryLabel =
              category === "connectivity"
                ? t("diagnostics.tests.connectivity")
                : t("diagnostics.tests.notifications");
            return (
              <div key={category} className="mb-4">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                  {categoryLabel}
                </h4>
                <div className="grid gap-3 md:grid-cols-2">
                  {categoryTests.map((test) => (
                    <div
                      key={test.name}
                      className="flex items-center gap-3 p-3 rounded-lg border"
                    >
                      <div className="rounded-full bg-primary/10 p-2 flex-shrink-0">
                        <test.icon className="h-4 w-4 text-primary" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <h3 className="font-medium text-sm">{test.name}</h3>
                        <p className="text-xs text-muted-foreground truncate">
                          {test.description}
                        </p>
                        {testStatus[test.name]?.status === "fail" &&
                          testStatus[test.name]?.message && (
                            <p className="text-xs text-red-700 dark:text-red-400 truncate mt-0.5">
                              {testStatus[test.name].message}
                            </p>
                          )}
                      </div>
                      <div className="flex items-center gap-1.5 flex-shrink-0">
                        {testStatus[test.name]?.elapsed != null &&
                          testStatus[test.name]?.status !== "running" && (
                            <span className="text-xs text-muted-foreground">
                              {testStatus[test.name].elapsed!.toFixed(1)}s
                            </span>
                          )}
                        <Button
                          onClick={() => handleRunTest(test.name)}
                          disabled={
                            testStatus[test.name]?.status === "running" ||
                            runAllProgress !== null
                          }
                          size="sm"
                          variant="outline"
                          className="min-w-[60px]"
                        >
                          {testStatus[test.name]?.status === "running" ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : testStatus[test.name]?.status === "pass" ? (
                            <span className="text-green-500">✅</span>
                          ) : testStatus[test.name]?.status === "fail" ? (
                            <span className="text-red-500">❌</span>
                          ) : (
                            t("diagnostics.tests.runBtn")
                          )}
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </CardContent>
      </Card>
    </div>
  );
}
