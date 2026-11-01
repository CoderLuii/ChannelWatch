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
import { StatusText } from "@/components/base/badge";
import { StatusBadge } from "@/components/base/status-badge";
import { diskSeverityColors, resolveDiskSeverity } from "@/components/dashboard/disk-space-card";
import { Progress } from "@/components/base/progress";
import { Input } from "@/components/base/input";
import { pageTitleClassName } from "@/components/base/page-header"
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
  FileArchive,
} from "lucide-react";
import {
  authHeaders,
  runTest,
  fetchSystemInfo,
  fetchSettings,
  downloadDebugBundle,
} from "@/lib/api";
import { t } from "@/lib/i18n";
import { coreStatusLabel } from "@/lib/health-verdict";
import { downloadBlob, formatDiskSizeFromGB, formatUptimeTile } from "@/lib/utils";
import { useToast } from "@/hooks/use-toast";
import { useDvrSelection } from "@/lib/dvr-selection-context";
import type { SystemInfo, AppSettings, DVRStatusInfo } from "@/lib/types";
import { ReportProblemDialog } from "@/components/report-problem-dialog";
import { isRateLimited, noteRateLimited } from "@/lib/rate-limit";

function getDvrDotClass(dvr: DVRStatusInfo): string {
  if (!dvr.connected) return "bg-danger";
  if (dvr.monitoring_ready === false) {
    return dvr.monitoring_status === "dead" ? "bg-danger" : "bg-warning";
  }
  return "bg-success";
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
  const [logsFailed, setLogsFailed] = useState(false);
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
      downloadBlob(blob, `channelwatch_debug_${ts}.zip`);
      setDebugBundleState("done");
      setTimeout(() => setDebugBundleState("idle"), 3000);
    } catch {
      setDebugBundleState("error");
      setTimeout(() => setDebugBundleState("idle"), 4000);
    }
  };

  // Poll logs every 5 seconds (unless paused). Skip ticks while the tab is
  // hidden or the API has asked the client to back off with a 429.
  useEffect(() => {
    if (logPaused) return;
    let cancelled = false;
    const controller = new AbortController();
    const fetchLogs = async () => {
      if (logFetchInFlightRef.current) return;
      if (typeof document !== "undefined" && document.hidden) return;
      if (isRateLimited()) return;
      const requestId = logFetchOwnerRef.current + 1;
      logFetchOwnerRef.current = requestId;
      logFetchInFlightRef.current = true;
      try {
        const resp = await fetch(`/api/logs?lines=${logLineCount}`, {
          headers: authHeaders(),
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (noteRateLimited(resp)) {
          if (!cancelled) setLogsFailed(true);
          return;
        }
        if (!cancelled) setLogsFailed(!resp.ok);
        if (resp.ok && !cancelled) {
          const data = await resp.json();
          if (!cancelled) setLogLines(data.lines || []);
        }
      } catch {
        if (!cancelled && !controller.signal.aborted) setLogsFailed(true);
      } finally {
        if (logFetchOwnerRef.current === requestId) {
          logFetchInFlightRef.current = false;
        }
      }
    };
    if (!logPaused) fetchLogs();
    const interval = setInterval(fetchLogs, 5000);
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
      downloadBlob(blob, "channelwatch.log");
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
      id: "Test Connectivity",
      name: t("diagnostics.test.connectivity.name"),
      icon: Wifi,
      category: "connectivity",
    },
    {
      id: "Test API Endpoints",
      name: t("diagnostics.test.apiEndpoints.name"),
      icon: Server,
      category: "connectivity",
    },
    {
      id: "Test Channel Watching Alert",
      name: t("diagnostics.test.cwAlert.name"),
      icon: Activity,
      category: "notifications",
    },
    {
      id: "Test VOD Watching Alert",
      name: t("diagnostics.test.vodAlert.name"),
      icon: Activity,
      category: "notifications",
    },
    {
      id: "Test Disk Space Alert",
      name: t("diagnostics.test.diskAlert.name"),
      icon: Gauge,
      category: "notifications",
    },
    {
      id: "Test Recording Scheduled Alert",
      name: t("diagnostics.test.recScheduled.name"),
      icon: Activity,
      category: "notifications",
    },
    {
      id: "Test Recording Started Alert",
      name: t("diagnostics.test.recStarted.name"),
      icon: Activity,
      category: "notifications",
    },
    {
      id: "Test Recording Completed Alert",
      name: t("diagnostics.test.recCompleted.name"),
      icon: Activity,
      category: "notifications",
    },
    {
      id: "Test Recording Stopped Alert",
      name: t("diagnostics.test.recStopped.name"),
      icon: Activity,
      category: "notifications",
    },
    {
      id: "Test Recording Cancelled Alert",
      name: t("diagnostics.test.recCancelled.name"),
      icon: Activity,
      category: "notifications",
    },
  ] as const;

  const [runAllProgress, setRunAllProgress] = useState<{
    current: number;
    total: number;
  } | null>(null);
  const [runAllElapsed, setRunAllElapsed] = useState<number | null>(null);
  const [testTarget, setTestTarget] = useState<string | null>(null);
  const [testStatus, setTestStatus] = useState<
    Record<string, { status: string; message?: string; elapsed?: number }>
  >({});

  const handleRunTest = async (test: (typeof tests)[number]) => {
    const testName = test.name;
    setTestStatus((prev) => ({ ...prev, [testName]: { status: "running" } }));
    const start = Date.now();

    try {
      const result = await runTest(test.id);
      const elapsed = (Date.now() - start) / 1000;
      setTestTarget(result.target_dvr?.name ?? null);
      setTestStatus((prev) => ({
        ...prev,
        [testName]: {
          status: result.status === "skipped"
            ? "skipped"
            : result.status === "passed" || (!result.status && result.success)
              ? "pass"
              : "fail",
          message: result.reason || result.message,
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
    setTestTarget(null);

    for (let i = 0; i < tests.length; i++) {
      setRunAllProgress({ current: i, total });
      await handleRunTest(tests[i]);
    }

    setRunAllElapsed((Date.now() - allStart) / 1000);
    setRunAllProgress(null);
  };

  const formatDiskSize = (sizeInGB: number | null | undefined): string => {
    if (sizeInGB === null || sizeInGB === undefined) {
      return t("diagnostics.system.na");
    }

    return formatDiskSizeFromGB(sizeInGB);
  };

  const calculateDiskUsage = () => {
    if (
      !systemInfo ||
      systemInfo.disk_total_gb === null ||
      systemInfo.disk_free_gb === null
    ) {
      return {
        usedGB: null,
        usedFormatted: t("diagnostics.system.na"),
        totalGB: null,
        totalFormatted: t("diagnostics.system.na"),
        freeGB: null,
      };
    }

    const totalGB = systemInfo.disk_total_gb;
    const freeGB = systemInfo.disk_free_gb;
    const usedGB = totalGB - freeGB;

    return {
      usedGB,
      usedFormatted: formatDiskSize(usedGB),
      totalGB,
      totalFormatted: formatDiskSize(totalGB),
      freeGB,
      usedPercent: systemInfo.disk_usage_percent,
    };
  };

  const diskBarColor = (usedPercent: number): string =>
    diskSeverityColors[
      resolveDiskSeverity({
        usedPercent,
        serverSeverity: systemInfo?.disk_severity,
        warningThresholdPercent: appSettings?.ds_warning_threshold_percent,
        criticalThresholdPercent: appSettings?.ds_critical_threshold_percent,
      })
    ].bar;

  const diskInfo = calculateDiskUsage();
  const debugBundleButtonLabel =
    debugBundleState === "loading"
      ? t("diagnostics.debugBundle.downloadingBtn")
      : debugBundleState === "done"
        ? t("diagnostics.debugBundle.downloadedBtn")
        : debugBundleState === "error"
          ? t("diagnostics.debugBundle.failedBtn")
          : t("diagnostics.debugBundle.btn");

  return (
    <div className="space-y-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between">
        <div className="min-w-0 flex flex-col gap-2">
          <h1 className={pageTitleClassName}>
            {t("diagnostics.title")}
          </h1>
        </div>
        <div className="flex w-full min-w-0 flex-col gap-2 sm:w-auto sm:flex-row sm:flex-wrap sm:items-center">
          <ReportProblemDialog
            systemInfo={systemInfo}
            appSettings={appSettings}
            trigger={
              <Button
                variant="outline"
                size="sm"
                className="min-h-11 w-full min-w-0 gap-1.5 whitespace-normal text-xs sm:w-auto sm:flex-shrink-0"
                data-testid="report-problem-open"
                aria-label={`${t("diagnostics.reportProblem.btn")}. ${t("diagnostics.reportProblem.aria")}`}
              >
                <Bug className="h-3.5 w-3.5" />
                {t("diagnostics.reportProblem.btn")}
              </Button>
            }
          />
          <Button
            variant="outline"
            size="sm"
            className="min-h-11 w-full min-w-0 gap-1.5 whitespace-normal text-xs sm:w-auto sm:flex-shrink-0"
            disabled={debugBundleState === "loading"}
            onClick={handleDownloadDebugBundle}
            aria-label={`${debugBundleButtonLabel}. ${t("diagnostics.debugBundle.aria")}`}
          >
            {debugBundleState === "loading" && (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            )}
            {debugBundleState === "done" && (
              <CheckCircle className="h-3.5 w-3.5 text-success" />
            )}
            {debugBundleState === "error" && (
              <AlertCircle className="h-3.5 w-3.5 text-danger" />
            )}
            {debugBundleState === "idle" && <FileArchive className="h-3.5 w-3.5" />}
            {debugBundleButtonLabel}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="min-h-11 w-full min-w-0 gap-1.5 whitespace-normal text-xs sm:w-auto sm:flex-shrink-0"
            aria-label={t("diagnostics.aria.export")}
            onClick={() => {
              const providers = getActiveProviders();
              const healthIssues = getHealthIssues();
              const testResults = [
                ...(testTarget ? [`  ${t("diagnostics.tests.targetDvr", { name: testTarget })}`] : []),
                ...Object.entries(testStatus).map(
                  ([name, s]) =>
                    `  ${s.status === "pass" ? "PASS" : s.status === "fail" ? "FAIL" : s.status === "skipped" ? "SKIP" : "..."} ${name}${s.elapsed != null ? ` (${s.elapsed.toFixed(1)}s)` : ""}${["fail", "skipped"].includes(s.status) && s.message ? ` - ${s.message}` : ""}`,
                ),
              ].join("\n");

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
                                status: `${dvr.monitoring_status || t("diagnostics.system.degraded")}${dvr.monitoring_reason ? ` \u00b7 ${dvr.monitoring_reason}` : ""}`,
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
                  value: coreStatusLabel(systemInfo?.core_status || "") || t("common.unknown"),
                }),
                t("diagnostics.export.rowUptime", {
                  value:
                    systemInfo?.uptime_data &&
                    Object.keys(systemInfo.uptime_data).length > 0
                      ? formatUptimeTile(systemInfo.uptime_data)
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
                          used: diskInfo.usedFormatted,
                          total: diskInfo.totalFormatted,
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
              downloadBlob(blob, `channelwatch-diagnostics-${new Date().toISOString().slice(0, 10)}.txt`);
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
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>
                <h2 className="flex items-center gap-2">
                <Terminal className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                {t("diagnostics.logs.title")}
                {logPaused && (
                  <StatusBadge variant="warning" className="ml-2">
                    {t("diagnostics.logs.paused")}
                  </StatusBadge>
                )}
                </h2>
              </CardTitle>
            </div>
            <div className="flex items-center gap-2">
              <Select
                value={String(logLineCount)}
                onValueChange={(v) => setLogLineCount(Number(v))}
              >
                <SelectTrigger
                  className="h-11 w-full text-xs sm:h-9 sm:w-32"
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
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <div className="relative basis-full sm:min-w-40 sm:flex-1 sm:basis-auto">
              <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
              <Input
                placeholder={t("diagnostics.logs.filterPlaceholder")}
                aria-label={t("diagnostics.logs.aria.filter")}
                value={logFilter}
                onChange={(e) => setLogFilter(e.target.value)}
                className="h-11 pl-8 text-xs sm:h-9"
              />
            </div>
            <div className="flex flex-1 items-center gap-1 sm:flex-none">
              <Button
                variant="outline"
                size="sm"
                aria-pressed={logLevelFilters.includes("error")}
                className={`min-h-11 flex-1 px-2 text-xs sm:min-h-9 ${logLevelFilters.includes("error") ? "bg-selected text-foreground hover:bg-selected" : "text-muted-foreground"}`}
                onClick={() =>
                  setLogLevelFilters((prev) =>
                    prev.includes("error")
                      ? prev.filter((f) => f !== "error")
                      : [...prev, "error"],
                  )
                }
              >
                <span aria-hidden="true" className="size-2 rounded-full bg-danger" />
                ERR<span className="sr-only"> {t("diagnostics.logs.aria.filterErrors")}</span>
              </Button>
              <Button
                variant="outline"
                size="sm"
                aria-pressed={logLevelFilters.includes("warn")}
                className={`min-h-11 flex-1 px-2 text-xs sm:min-h-9 ${logLevelFilters.includes("warn") ? "bg-selected text-foreground hover:bg-selected" : "text-muted-foreground"}`}
                onClick={() =>
                  setLogLevelFilters((prev) =>
                    prev.includes("warn")
                      ? prev.filter((f) => f !== "warn")
                      : [...prev, "warn"],
                  )
                }
              >
                <span aria-hidden="true" className="size-2 rounded-full bg-warning" />
                WARN<span className="sr-only"> {t("diagnostics.logs.aria.filterWarnings")}</span>
              </Button>
              <Button
                variant="outline"
                size="sm"
                aria-pressed={logLevelFilters.includes("info")}
                className={`min-h-11 flex-1 px-2 text-xs sm:min-h-9 ${logLevelFilters.includes("info") ? "bg-selected text-foreground hover:bg-selected" : "text-muted-foreground"}`}
                onClick={() =>
                  setLogLevelFilters((prev) =>
                    prev.includes("info")
                      ? prev.filter((f) => f !== "info")
                      : [...prev, "info"],
                  )
                }
              >
                <span aria-hidden="true" className="size-2 rounded-full bg-info" />
                INFO<span className="sr-only"> {t("diagnostics.logs.aria.filterInfo")}</span>
              </Button>
            </div>
            <div className="flex items-center gap-2 max-sm:ml-auto">
              <Button
                variant="outline"
                size="sm"
                className="min-h-11 min-w-11 px-2 sm:min-h-9 sm:min-w-9"
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
                className="min-h-11 min-w-11 px-2 sm:min-h-9 sm:min-w-9"
                onClick={handleCopyLogs}
                aria-label={t("diagnostics.logs.aria.copy")}
                title={t("diagnostics.logs.aria.copy")}
              >
                {copied ? (
                  <CheckCircle className="h-3.5 w-3.5 text-success" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="min-h-11 min-w-11 px-2 sm:min-h-9 sm:min-w-9"
                onClick={handleDownloadLogs}
                aria-label={t("diagnostics.logs.aria.download")}
                title={t("diagnostics.logs.aria.download")}
              >
                <Download className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
          <div
            ref={logContainerRef}
            className="h-56 select-text overflow-auto rounded-control border border-hairline bg-well p-3 font-mono text-xs leading-5 sm:h-72 lg:h-80"
          >
            {filteredLogLines.length === 0 ? (
              <span className="text-muted-foreground">
                {logFilter
                  ? t("diagnostics.logs.emptyFiltered")
                  : logsFailed
                    ? t("diagnostics.logs.failed")
                    : t("diagnostics.logs.empty")}
              </span>
            ) : (
              filteredLogLines.map((line, i) => (
                <div
                  key={i}
                  className={`whitespace-pre text-xs ${
                    line.includes("[FAIL]") || line.includes("ERROR")
                      ? "text-danger-fg"
                      : line.includes("[PASS]") ||
                          line.includes("Notification sent")
                        ? "text-success-fg"
                        : line.includes("WARNING") || line.includes("WARN")
                          ? "text-warning-fg"
                          : line.includes("🔧")
                            ? "font-semibold text-foreground"
                            : "text-muted-foreground"
                  }`}
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
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-[min(100%,14rem)] flex-1">
              <CardTitle>
                <h2 className="flex items-center gap-2">
                  <Server className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
                  {t("diagnostics.system.title")}
                </h2>
              </CardTitle>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="h-auto min-h-11 max-w-full gap-1 whitespace-normal px-3 py-2 text-center text-xs"
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
              <div className="rounded-control bg-fill p-3 space-y-2">
                <h3 className="text-sm font-medium mb-2">
                  {t("diagnostics.system.env")}
                </h3>
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.versionKey")}
                      </span>
                      <span>
                        {systemInfo?.channelwatch_version ||
                          t("common.unknown")}
                      </span>
                    </div>
                    <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.timezoneKey")}
                      </span>
                      <span>
                        {(systemInfo?.timezone || t("common.unknown")).split("/").map((part, index) => (
                          <React.Fragment key={part}>
                            {index > 0 ? <>/<wbr /></> : null}
                            {part}
                          </React.Fragment>
                        ))}
                      </span>
                    </div>
                    <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.coreStatusKey")}
                      </span>
                      <span
                        className={
                          "text-muted-foreground flex items-center"
                        }
                      >
                        {systemInfo?.core_status === "Running" ? (
                          <>
                            <StatusText>{t("diagnostics.system.running")}</StatusText>
                          </>
                        ) : (
                          coreStatusLabel(systemInfo?.core_status || "") || t("common.unknown")
                        )}
                      </span>
                    </div>
                    <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.uptimeKey")}
                      </span>
                      <span>
                        {systemInfo?.uptime_data &&
                        Object.keys(systemInfo.uptime_data).length > 0
                          ? formatUptimeTile(systemInfo.uptime_data)
                          : t("diagnostics.system.na")}
                      </span>
                    </div>
                    <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
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
                    <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.providersKey")}
                      </span>
                      <span>
                        {getActiveProviders().length > 0 ? (
                          getActiveProviders().join(", ")
                        ) : (
                          <span className="text-muted-foreground">
                            {t("diagnostics.system.noneConfigured")}
                          </span>
                        )}
                      </span>
                    </div>
                  </>
                )}
              </div>

              {!isMultiDvr && (
                <div className="rounded-control bg-fill p-3 space-y-2">
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
                      <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                        <span className="text-muted-foreground">
                          {t("diagnostics.system.hostKey")}
                        </span>
                        <span>
                          {`${dvrSections[0].host}:${dvrSections[0].port}`}
                        </span>
                      </div>
                      <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                        <span className="text-muted-foreground">
                          {t("diagnostics.system.statusKey")}
                        </span>
                        <span
                          className={
                            dvrSections[0].connected
                              ? "text-muted-foreground flex items-center"
                              : "text-danger-fg flex items-center"
                          }
                        >
                          {dvrSections[0].connected ? (
                            <>
                              <span aria-hidden="true" className="mr-1.5 size-2 rounded-full bg-success" />
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
                        <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                          <span className="text-muted-foreground">
                            {t("diagnostics.system.dvrVersionKey")}
                          </span>
                          <span>
                            {formatDvrVersion(dvrSections[0].version)}
                          </span>
                        </div>
                      )}
                      {dvrSections[0].monitoring_ready === false && (
                        <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                          <span className="text-muted-foreground">
                            {t("diagnostics.system.monitoringKey")}
                          </span>
                          <span className="text-warning-fg capitalize">
                            {dvrSections[0].monitoring_status ||
                              t("diagnostics.system.degraded")}
                          </span>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                        <span className="text-muted-foreground">
                          {t("diagnostics.system.dvrHostKey")}
                        </span>
                        <span>
                          {systemInfo?.channels_dvr_host ||
                            t("common.notConfigured")}
                        </span>
                      </div>
                      <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                        <span className="text-muted-foreground">
                          {t("diagnostics.system.dvrPortKey")}
                        </span>
                        <span>{systemInfo?.channels_dvr_port || "8089"}</span>
                      </div>
                      <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                        <span className="text-muted-foreground">
                          {t("diagnostics.system.statusKey")}
                        </span>
                        <span
                          className={
                            systemInfo?.channels_dvr_server_version
                              ? "text-muted-foreground flex items-center"
                              : "text-danger-fg flex items-center"
                          }
                        >
                          {systemInfo?.channels_dvr_server_version ? (
                            <>
                              <span aria-hidden="true" className="mr-1.5 size-2 rounded-full bg-success" />
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

              <div className="rounded-control bg-fill p-3 space-y-2">
                <h3 className="text-sm font-medium mb-2">
                  {t("diagnostics.system.storage")}
                </h3>
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : systemInfo && diskInfo.totalGB ? (
                  <>
                    <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.usedKey")}
                      </span>
                      <span>
                        {diskInfo.usedGB != null
                          ? diskInfo.usedFormatted
                          : t("diagnostics.system.na")}
                      </span>
                    </div>
                    <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.freeKey")}
                      </span>
                      <span>
                        {diskInfo.freeGB != null
                          ? formatDiskSize(diskInfo.freeGB)
                          : t("diagnostics.system.na")}
                      </span>
                    </div>
                    <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                      <span className="text-muted-foreground">
                        {t("diagnostics.system.totalKey")}
                      </span>
                      <span>{diskInfo.totalFormatted}</span>
                    </div>
                    {systemInfo.disk_usage_percent != null && (
                      <Progress
                        value={systemInfo.disk_usage_percent}
                        className="h-1.5 mt-1"
                        indicatorClassName={diskBarColor(systemInfo.disk_usage_percent)}
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
              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3 [&>*]:min-w-0">
                {dvrSections.map((dvr) => {
                  const versionDisplay = formatDvrVersion(dvr.version);
                  return (
                    <div
                      key={dvr.id}
                      className="rounded-control bg-fill p-3 space-y-2"
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
                        {dvr.connected ? (
                          <StatusText className="flex-shrink-0">{t("diagnostics.system.connected")}</StatusText>
                        ) : (
                          <StatusBadge variant="danger" className="flex-shrink-0">{t("diagnostics.system.offline")}</StatusBadge>
                        )}
                      </div>
                      {loading ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <>
                          <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                            <span className="text-muted-foreground">
                              {t("diagnostics.system.hostKey")}
                            </span>
                            <span className="text-right truncate ml-2">
                              {`${dvr.host}:${dvr.port}`}
                            </span>
                          </div>
                          {versionDisplay && (
                            <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                              <span className="text-muted-foreground">
                                {t("diagnostics.system.versionLabel")}
                              </span>
                              <span>{versionDisplay}</span>
                            </div>
                          )}
                          {dvr.monitoring_ready === false && (
                            <div className="flex flex-wrap justify-between gap-x-2 text-sm [overflow-wrap:anywhere]">
                              <span className="text-muted-foreground">
                                {t("diagnostics.system.monitoringKey")}
                              </span>
                              <span className="text-warning-fg capitalize">
                                {dvr.monitoring_status ||
                                  t("diagnostics.system.degraded")}
                              </span>
                            </div>
                          )}
                          {dvr.monitoring_reason && (
                            <p className="text-xs leading-5 text-muted-foreground/70">
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
          <CardTitle>
            <h2 className="flex items-center gap-2">
              <Activity className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              {t("diagnostics.tests.title")}
            </h2>
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
            <div className="mb-4 p-3 rounded-control bg-fill flex flex-wrap items-center gap-x-3 gap-y-1" role="status">
              <span className="text-sm font-medium">
                {t("diagnostics.tests.resultsLabel")}
              </span>
              <span className="text-sm text-success-fg">
                {t("diagnostics.tests.passed", {
                  n: Object.values(testStatus).filter(
                    (s) => s.status === "pass",
                  ).length,
                })}
              </span>
              <span className="text-sm text-danger-fg">
                {t("diagnostics.tests.failed", {
                  n: Object.values(testStatus).filter(
                    (s) => s.status === "fail",
                  ).length,
                })}
              </span>
              <span className="text-sm text-muted-foreground">
                {t("diagnostics.tests.skipped", {
                  n: Object.values(testStatus).filter(
                    (s) => s.status === "skipped",
                  ).length,
                })}
              </span>
              {testTarget && (
                <span className="min-w-0 break-words text-sm text-muted-foreground [overflow-wrap:anywhere]">
                  {t("diagnostics.tests.targetDvr", { name: testTarget })}
                </span>
              )}
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
              <div key={category} className="mb-4 last:-mb-4 last:[&>div:last-child]:border-b-0">
                <h3 className="mb-2 text-xs font-medium text-muted-foreground">
                  {categoryLabel}
                </h3>
                <div className="-mx-4 divide-y divide-hairline border-y border-hairline">
                  {categoryTests.map((test) => (
                    <div
                      key={test.name}
                      data-testid="diagnostic-test-row"
                      className="flex min-h-[52px] min-w-0 flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5"
                    >
                      <div className="flex min-w-[min(100%,11rem)] flex-1 items-center gap-3">
                        <test.icon className="size-4 flex-shrink-0 text-muted-foreground" aria-hidden="true" />
                        <div className="min-w-0 flex-1">
                          <h4 className="break-words text-sm font-medium">{test.name}</h4>
                          {(testStatus[test.name]?.status === "fail" || testStatus[test.name]?.status === "skipped") &&
                            testStatus[test.name]?.message && (
                              <p className={`mt-0.5 break-words text-xs leading-5 ${testStatus[test.name]?.status === "fail" ? "text-danger-fg" : "text-muted-foreground"}`}>
                                {testStatus[test.name].message}
                              </p>
                            )}
                        </div>
                      </div>
                      <div className="ml-auto flex flex-col items-end justify-center gap-1.5 sm:flex-row sm:items-center sm:flex-shrink-0">
                        {testStatus[test.name]?.elapsed != null &&
                          testStatus[test.name]?.status !== "running" && (
                            <span className="text-xs text-muted-foreground">
                              {testStatus[test.name].elapsed!.toFixed(1)}s
                            </span>
                          )}
                        <Button
                          onClick={() => handleRunTest(test)}
                          disabled={
                            testStatus[test.name]?.status === "running" ||
                            runAllProgress !== null
                          }
                          size="sm"
                          variant="ghost"
                          className="-my-1.5 h-auto min-h-11 min-w-11 whitespace-normal px-2 py-2 text-center"
                        >
                          {testStatus[test.name]?.status === "running" ? (
                            <span className="inline-flex items-center gap-1.5 text-xs"><Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />{t("diagnostics.tests.stateRunning")}</span>
                          ) : testStatus[test.name]?.status === "pass" ? (
                            <span className="inline-flex items-center gap-1.5 text-xs"><span aria-hidden="true" className="size-2 rounded-full bg-success" />{t("diagnostics.tests.statePassed")}</span>
                          ) : testStatus[test.name]?.status === "fail" ? (
                            <span className="inline-flex items-center gap-1.5 text-xs text-danger-fg"><span aria-hidden="true" className="size-2 rounded-full bg-danger" />{t("diagnostics.tests.stateFailed")}</span>
                          ) : testStatus[test.name]?.status === "skipped" ? (
                            t("diagnostics.tests.skippedLabel")
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
