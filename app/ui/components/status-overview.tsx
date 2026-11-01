"use client";

import React, { useState, useEffect, useRef } from "react";
import { Button } from "@/components/base/button";
import { pageTitleClassName } from "@/components/base/page-header"
import type { AppSettings, DVRStatusInfo, RecordingInfo } from "@/lib/types";
import { RefreshCw, Loader2, Tv, Calendar, Clock, HardDrive } from "lucide-react";
import {
  fetchSystemInfo,
  fetchUpcomingRecordings,
  fetchStreamDetails,
  fetchCompleteActivityHistory,
  fetchSettings,
  fetchDvrStreams,
  fetchDvrUpcomingRecordings,
  fetchActivityClientFilters,
  failureKind,
  SESSION_RESTORED_EVENT,
} from "@/lib/api";
import type { ActivityClientFacet, StreamDetails } from "@/lib/api";
import { canonicalActivityClientValue } from "@/lib/activity-clients";
import {
  buildActivityTimeline,
  type ActivityTimelinePoint,
} from "@/lib/activity-timeline";
import { useDvrSelection } from "@/lib/dvr-selection-context";
import { t } from "@/lib/i18n";
import type { ActivityItem } from "@/lib/types";
import { collapseRepeatedDiskAlerts } from "@/lib/activity-presentation";
import { cn, formatDiskSizeFromGB, formatUptimeTile } from "@/lib/utils";
import { MetricCard } from "@/components/dashboard/metric-card";
import { UptimeCard } from "@/components/dashboard/uptime-card";
import {
  DiskSpaceCard,
  resolveDiskSeverity,
  type DiskSeverity,
  type DiskSpaceState,
} from "@/components/dashboard/disk-space-card";
import { ActivityTimeline } from "@/components/dashboard/activity-timeline";
import { StatusPanel } from "@/components/dashboard/status-panel";
import { RecentActivityList } from "@/components/dashboard/recent-activity-list";
import { UpcomingRecordingsList } from "@/components/dashboard/upcoming-recordings-list";
import { MobileDashboardDetails } from "@/components/dashboard/mobile-dashboard-details";
import { MaintenanceWindowsCard } from "@/components/dashboard/maintenance-windows-card";
import { HealthVerdictLine } from "@/components/dashboard/health-verdict-line";
import { NowList } from "@/components/dashboard/now-list";
import { buildHealthVerdict, type HealthVerdictActionKind } from "@/lib/health-verdict";
import { isRateLimited } from "@/lib/rate-limit";
import type { FailureKind } from "@/lib/error-catalog";
import { formatCountdown, formatFreshness } from "@/lib/relative-time";

interface StatusOverviewProps {
  settings: AppSettings | null;
  onNavigate?: (view: string) => void;
  noSignIn?: boolean;
  /** Home stays mounted behind other tabs. Paused, it holds its data and reads nothing. */
  paused?: boolean;
}

export function StatusOverview({ settings, onNavigate, noSignIn, paused = false }: StatusOverviewProps) {
  const { selectedDvr } = useDvrSelection();
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [lastSuccessAt, setLastSuccessAt] = useState<Date | null>(null);
  const [freshnessNow, setFreshnessNow] = useState(() => Date.now());
  const [diskSheetSignal, setDiskSheetSignal] = useState(0);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [failedFetches, setFailedFetches] = useState<Set<string>>(new Set());
  const [dataLoaded, setDataLoaded] = useState(false);
  const [systemFailureKind, setSystemFailureKind] = useState<FailureKind | undefined>(undefined);
  const metricsIntervalRef = React.useRef<ReturnType<
    typeof setInterval
  > | null>(null);
  const refreshInFlightRef = React.useRef(false);
  const pausedRef = React.useRef(paused);
  pausedRef.current = paused;
  const staleWhilePausedRef = React.useRef(false);
  const latestRefreshRef = React.useRef<
    (manual?: boolean) => Promise<void> | void
  >(() => undefined);
  const [coreStartedAt, setCoreStartedAt] = useState<Date | null>(null);
  const [uiStartedAt, setUiStartedAt] = useState<Date | null>(null);
  const [coreUptime, setCoreUptime] = useState<{
    days: number;
    hours: number;
    minutes: number;
    seconds: number;
  } | null>(null);
  const [uiUptimeDisplay, setUiUptimeDisplay] = useState<string>(t("uptime.unavailable"));
  const [diskSpace, setDiskSpace] = useState<DiskSpaceState>({
    usedPercent: 0,
    freePercent: 0,
    loading: true,
    error: null,
    totalFormatted: "",
    usedFormatted: "",
    freeFormatted: "",
    libraryShows: 0,
    libraryMovies: 0,
    libraryEpisodes: 0,
  });
  const [diskServerSeverity, setDiskServerSeverity] = useState<
    DiskSeverity | undefined
  >(undefined);
  const [dvrStatusList, setDvrStatusList] = useState<DVRStatusInfo[]>([]);
  const [activeStreams, setActiveStreams] = useState<number | null>(null);
  const [watching, setWatching] = useState<StreamDetails["watching"]>([]);
  const [recording, setRecording] = useState<StreamDetails["recording"]>([]);
  const [streamSubtitle, setStreamSubtitle] = useState(
    t("statusOverview.noActiveStreams"),
  );
  const [streamImage, setStreamImage] = useState("");
  const [upcomingRecordings, setUpcomingRecordings] = useState<number | null>(null);
  const [upcomingRecordingsList, setUpcomingRecordingsList] = useState<
    RecordingInfo[]
  >([]);
  const [recentActivity, setRecentActivity] = useState<ActivityItem[]>([]);
  const [activityTotal, setActivityTotal] = useState(0);
  const [clientActivity, setClientActivity] = useState<ActivityItem[] | null>(null);
  const [selectedClient, setSelectedClient] = useState<string | null>(null);
  const [clientFacets, setClientFacets] = useState<ActivityClientFacet[]>([]);
  const [clientFacetsLoading, setClientFacetsLoading] = useState(false);
  const [clientFacetsError, setClientFacetsError] = useState(false);
  const [clientFilterStatus, setClientFilterStatus] = useState<string | null>(null);
  const aggregateActivityRequestRef = useRef<AbortController | null>(null);
  const clientActivityRequestRef = useRef<AbortController | null>(null);
  const [activeNotificationServices, setActiveNotificationServices] =
    useState(0);
  const [activeProviderNames, setActiveProviderNames] = useState<string[]>([]);
  const [activeAlertTypes, setActiveAlertTypes] = useState<string[]>([]);
  const [coreProcessStatus, setCoreProcessStatus] = useState(
    t("statusOverview.loading"),
  );
  const [channelwatchVersion, setChannelwatchVersion] = useState("");
  const [activityHours, setActivityHours] = useState(24);
  const [selectedFilters, setSelectedFilters] = useState<string[]>(["all"]);
  const [streamingData, setStreamingData] = useState<ActivityTimelinePoint[]>([]);

  const [refreshedSettings, setRefreshedSettings] =
    useState<AppSettings | null>(null);
  const [activityLoading, setActivityLoading] = useState(false);
  const [clientActivityLoading, setClientActivityLoading] = useState(false);

  useEffect(() => () => {
    aggregateActivityRequestRef.current?.abort();
    clientActivityRequestRef.current?.abort();
  }, []);

  const applyDiskData = (
    diskTotalGb: number | null,
    diskFreeGb: number | null,
    diskUsagePercent: number | null,
    libShows: number,
    libMovies: number,
    libEpisodes: number,
  ) => {
    if (diskTotalGb && diskFreeGb !== null && diskUsagePercent !== null) {
      const totalGB = diskTotalGb;
      const freeGB = diskFreeGb;
      const usedGB = totalGB - freeGB;
      const usedPercent = diskUsagePercent;
      const freePercent = Math.round((100 - usedPercent) * 10) / 10;
      setDiskSpace({
        usedPercent,
        freePercent,
        loading: false,
        error: null,
        totalFormatted: formatDiskSizeFromGB(totalGB),
        freeFormatted: formatDiskSizeFromGB(freeGB),
        libraryShows: libShows,
        libraryMovies: libMovies,
        libraryEpisodes: libEpisodes,
        usedFormatted: formatDiskSizeFromGB(usedGB),
        totalGb: totalGB,
        freeGb: freeGB,
      });
    } else {
      setDiskSpace((prev: DiskSpaceState) => ({
        ...prev,
        loading: false,
        error: t("statusOverview.diskUnavailable"),
      }));
    }
  };

  const markFetchFailure = (family: string, failed: boolean) => {
    setFailedFetches((previous) => {
      if (previous.has(family) === failed) return previous;
      const next = new Set(previous);
      if (failed) next.add(family);
      else next.delete(family);
      return next;
    });
  };

  const fetchSystemData = async (): Promise<DVRStatusInfo[] | null> => {
    try {
      const systemInfo = await fetchSystemInfo(
        selectedDvr !== "all"
          ? { dvr_id: selectedDvr, include_all_dvr_status: true }
          : {},
      );

      const parsedCoreStart = systemInfo.channelwatch_core_started_at
        ? new Date(systemInfo.channelwatch_core_started_at)
        : null;
      const parsedUiStart = systemInfo.channelwatch_ui_started_at
        ? new Date(systemInfo.channelwatch_ui_started_at)
        : systemInfo.container_start_time
          ? new Date(systemInfo.container_start_time)
          : null;
      setCoreStartedAt(parsedCoreStart && Number.isFinite(parsedCoreStart.getTime()) ? parsedCoreStart : null);
      setUiStartedAt(parsedUiStart && Number.isFinite(parsedUiStart.getTime()) ? parsedUiStart : null);
      if (systemInfo.uptime_data) {
        setCoreUptime(systemInfo.uptime_data);
      }
      if (systemInfo.core_status) {
        setCoreProcessStatus(systemInfo.core_status);
      } else {
        setCoreProcessStatus("Unknown");
      }
      if (systemInfo.channelwatch_version) {
        setChannelwatchVersion(systemInfo.channelwatch_version);
      }

      const nextDvrStatuses = systemInfo.dvr_status || [];
      if (selectedDvr !== "all") {
        setDvrStatusList(nextDvrStatuses);
        setDiskServerSeverity(systemInfo.disk_severity ?? undefined);
        applyDiskData(
          systemInfo.disk_total_gb,
          systemInfo.disk_free_gb,
          systemInfo.disk_usage_percent,
          systemInfo.library_shows,
          systemInfo.library_movies,
          systemInfo.library_episodes,
        );
      } else {
        setDiskServerSeverity(systemInfo.disk_severity ?? undefined);
        applyDiskData(
          systemInfo.disk_total_gb,
          systemInfo.disk_free_gb,
          systemInfo.disk_usage_percent,
          systemInfo.library_shows,
          systemInfo.library_movies,
          systemInfo.library_episodes,
        );
        setDvrStatusList(nextDvrStatuses);
      }
      markFetchFailure("system", false);
      setSystemFailureKind(undefined);
      return nextDvrStatuses;
    } catch (error) {
      const kind = failureKind(error);
      setSystemFailureKind(kind);
      setCoreProcessStatus("Unavailable");
      setDiskServerSeverity(undefined);
      setDiskSpace((prev: DiskSpaceState) => ({
        ...prev,
        loading: false,
        error: t(kind === "unreachable" ? "statusOverview.diskError" : "statusOverview.diskUnavailable"),
      }));
      markFetchFailure("system", true);
      console.error("Error fetching system info:", error);
      return null;
    }
  };

  const fetchRecordingsInfo = async () => {
    try {
      if (selectedDvr !== "all") {
        const [upcomingRecords, streamData] = await Promise.all([
          fetchDvrUpcomingRecordings(selectedDvr, 250),
          fetchDvrStreams(selectedDvr),
        ]);
        setUpcomingRecordingsList(upcomingRecords);
        setUpcomingRecordings(upcomingRecords.length);
        setActiveStreams(streamData.total);
        setWatching(streamData.watching ?? []);
        setRecording(streamData.recording ?? []);
        setStreamSubtitle(streamData.subtitle);
        setStreamImage(streamData.image || "");
      } else {
        const upcomingRecords = await fetchUpcomingRecordings(250);
        setUpcomingRecordingsList(upcomingRecords);
        setUpcomingRecordings(upcomingRecords.length);
        const streamData = await fetchStreamDetails();
        setActiveStreams(streamData.total);
        setWatching(streamData.watching ?? []);
        setRecording(streamData.recording ?? []);
        setStreamSubtitle(streamData.subtitle);
        setStreamImage(streamData.image || "");
      }
      markFetchFailure("recordings", false);
    } catch (error) {
      markFetchFailure("recordings", true);
      console.error("Error fetching recordings info:", error);
    }
  };

  const loadActivity = async (
    controller: AbortController,
    client?: string,
    maxItems?: number,
    hours: number = activityHours,
  ): Promise<ActivityItem[]> => {
    return fetchCompleteActivityHistory({
      sort: "desc",
      hours,
      client,
      dvr_id: selectedDvr === "all" ? undefined : selectedDvr,
      signal: controller.signal,
    }, maxItems);
  };

  const fetchAggregateActivityData = async (background = false) => {
    aggregateActivityRequestRef.current?.abort();
    const controller = new AbortController();
    aggregateActivityRequestRef.current = controller;
    if (!background) setActivityLoading(true);
    try {
      const [timelineActivity, recent] = activityHours === 24
        ? await loadActivity(controller).then((activity) => [activity, activity.slice(0, 250)] as const)
        : await Promise.all([
            loadActivity(controller, undefined, undefined, 24),
            loadActivity(controller, undefined, 250, activityHours),
          ]);
      if (controller.signal.aborted) return;
      setRecentActivity(recent);
      setActivityTotal(activityHours === 24 ? timelineActivity.length : recent.length);
      const chartData = buildActivityTimeline(timelineActivity);
      setStreamingData(chartData);
      markFetchFailure("activity", false);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      markFetchFailure("activity", true);
      console.error("Error fetching recent activity:", error);
    } finally {
      if (aggregateActivityRequestRef.current === controller) {
        setActivityLoading(false);
      }
    }
  };

  const fetchClientActivityData = async () => {
    clientActivityRequestRef.current?.abort();
    if (!selectedClient) {
      setClientActivity(null);
      setClientActivityLoading(false);
      markFetchFailure("clientActivity", false);
      return;
    }
    const controller = new AbortController();
    clientActivityRequestRef.current = controller;
    setClientActivityLoading(true);
    try {
      const activity = await loadActivity(controller, selectedClient, 250);
      if (!controller.signal.aborted) {
        setClientActivity(activity);
        markFetchFailure("clientActivity", false);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      markFetchFailure("clientActivity", true);
      console.error("Error fetching client activity:", error);
    } finally {
      if (clientActivityRequestRef.current === controller) {
        setClientActivityLoading(false);
      }
    }
  };

  const fetchActivityData = async (background = false) => {
    await Promise.all([
      fetchAggregateActivityData(background),
      fetchClientActivityData(),
    ]);
  };

  const refreshAfterSignIn = useRef(fetchActivityData);
  refreshAfterSignIn.current = fetchActivityData;
  useEffect(() => {
    const refresh = () => void refreshAfterSignIn.current(true);
    window.addEventListener(SESSION_RESTORED_EVENT, refresh);
    return () => window.removeEventListener(SESSION_RESTORED_EVENT, refresh);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setClientFacetsLoading(true);
    setClientFacetsError(false);
    fetchActivityClientFilters(
      {
        dvr_id: selectedDvr === "all" ? undefined : selectedDvr,
        hours: activityHours,
      },
      controller.signal,
    )
      .then(({ clients }) => {
        setClientFacets(clients);
        setSelectedClient((current) => {
          if (!current) return null;
          const canonical = canonicalActivityClientValue(clients, current);
          if (!canonical) {
            setClientFilterStatus(t("activity.clientReset"));
            return null;
          }
          return canonical;
        });
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setClientFacetsError(true);
      })
      .finally(() => {
        if (!controller.signal.aborted) setClientFacetsLoading(false);
      });

    return () => controller.abort();
  }, [activityHours, selectedDvr]);

  useEffect(() => {
    if (!dataLoaded) return;
    fetchClientActivityData();
    // Exact client changes refetch only the list. The aggregate Timeline is
    // intentionally independent and must not incur a duplicate request.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedClient]);

  const calculateActiveNotificationServices = (
    settingsToUse = settings || refreshedSettings,
  ): { count: number; names: string[] } => {
    if (!settingsToUse) return { count: 0, names: [] };

    const names: string[] = [];

    if (settingsToUse.apprise_pushover) names.push(t("provider.pushover.name"));
    if (settingsToUse.apprise_discord) names.push(t("provider.discord.name"));
    if (settingsToUse.apprise_email && settingsToUse.apprise_email_to)
      names.push(t("provider.email.name"));
    if (settingsToUse.apprise_telegram) names.push(t("provider.telegram.name"));
    if (settingsToUse.apprise_slack) names.push(t("provider.slack.name"));
    if (settingsToUse.apprise_gotify) names.push(t("provider.gotify.name"));
    if (settingsToUse.apprise_matrix) names.push(t("provider.matrix.name"));
    if (settingsToUse.apprise_custom) names.push(t("provider.custom.name"));

    return { count: names.length, names };
  };

  const getActiveAlertTypes = (
    settingsToUse = settings || refreshedSettings,
  ) => {
    if (!settingsToUse) return [];
    const alertTypes = [];
    if (settingsToUse.alert_channel_watching)
      alertTypes.push(t("alerts.channelWatching.title"));
    if (settingsToUse.alert_disk_space)
      alertTypes.push(t("alerts.diskSpace.title"));
    if (settingsToUse.alert_vod_watching)
      alertTypes.push(t("alerts.vodWatching.title"));
    if (settingsToUse.alert_recording_events)
      alertTypes.push(t("alerts.recordingEvents.title"));
    if (settingsToUse.alert_dvr_health)
      alertTypes.push(t("alerts.health.label"));
    return alertTypes;
  };

  // Single auto-refresh timer for all dashboard data (30 seconds)
  // Resets when user manually clicks Refresh or when activityHours changes
  const startMetricsTimer = React.useCallback(() => {
    if (metricsIntervalRef.current) clearInterval(metricsIntervalRef.current);
    metricsIntervalRef.current = setInterval(() => {
      if (isRateLimited() || pausedRef.current) return;
      latestRefreshRef.current();
    }, 30000);
  }, []);

  const transientMonitoringSignature = React.useMemo(
    () => dvrStatusList
      .filter((status) => ["starting", "reconnecting", "missing"].includes(status.monitoring_status || ""))
      .map((status) => `${status.id}:${status.monitoring_status}`)
      .sort()
      .join("|"),
    [dvrStatusList],
  );

  useEffect(() => {
    if (!transientMonitoringSignature) return;
    let cancelled = false;
    let timeoutId: ReturnType<typeof setTimeout> | null = null;

    const wait = () => new Promise<void>((resolve) => {
      timeoutId = setTimeout(resolve, 2000);
    });

    const pollMonitoringRecovery = async () => {
      for (let attempt = 0; attempt < 30 && !cancelled; attempt += 1) {
        await wait();
        if (cancelled) return;
        if (pausedRef.current) continue;
        const statuses = await fetchSystemData();
        if (cancelled) return;
        setLastUpdated(new Date());
        if (statuses === null) continue;
        const stillStarting = statuses.some((status) =>
          ["starting", "reconnecting", "missing"].includes(status.monitoring_status || ""),
        );
        if (!stillStarting) return;
      }
    };

    void pollMonitoringRecovery();
    return () => {
      cancelled = true;
      if (timeoutId) clearTimeout(timeoutId);
    };
    // The signature changes only when a DVR enters or leaves a transient
    // monitor state.  The bounded loop owns its own system-info refreshes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [transientMonitoringSignature, selectedDvr]);

  useEffect(() => {
    if (dataLoaded && !pausedRef.current) fetchActivityData();
    startMetricsTimer();
    return () => {
      if (metricsIntervalRef.current) clearInterval(metricsIntervalRef.current);
    };
    // Intentionally only re-run on activityHours change; adding fetchActivityData,
    // startMetricsTimer, or dataLoaded would restart the timer/fetch on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activityHours]);

  const isInitialMount = useRef(true);
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    if (pausedRef.current) {
      staleWhilePausedRef.current = true;
      return;
    }
    setDataLoaded(false);
    setDiskSpace((prev: DiskSpaceState) => ({
      ...prev,
      loading: true,
      error: null,
    }));
    Promise.allSettled([
      fetchSystemData(),
      fetchRecordingsInfo(),
      fetchActivityData(),
    ]).then(() => {
      setDataLoaded(true);
      setLastUpdated(new Date());
    });
    // DVR-switch reload only; including the fetch closures would re-trigger fetches
    // on unrelated state updates and cause request loops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDvr]);

  // Uptime changes client-side without adding a minute-by-minute API poll.
  useEffect(() => {
    if (!coreStartedAt && !uiStartedAt) return;
    const tick = () => {
      const now = new Date();
      if (coreStartedAt) {
        let coreDiff = Math.max(0, Math.floor((now.getTime() - coreStartedAt.getTime()) / 1000));
        const days = Math.floor(coreDiff / 86400);
        coreDiff %= 86400;
        const hours = Math.floor(coreDiff / 3600);
        coreDiff %= 3600;
        const minutes = Math.floor(coreDiff / 60);
        const seconds = coreDiff % 60;
        setCoreUptime({ days, hours, minutes, seconds });
      }
      if (uiStartedAt) {
        let uiDiff = Math.max(0, Math.floor((now.getTime() - uiStartedAt.getTime()) / 1000));
        const days = Math.floor(uiDiff / 86400);
        uiDiff %= 86400;
        const hours = Math.floor(uiDiff / 3600);
        uiDiff %= 3600;
        const minutes = Math.floor(uiDiff / 60);
        setUiUptimeDisplay(formatUptimeTile({ days, hours, minutes }));
      }
    };
    tick();
    const uptimeInterval = setInterval(tick, 60_000);
    return () => clearInterval(uptimeInterval);
  }, [coreStartedAt, uiStartedAt]);

  useEffect(() => {
    if (pausedRef.current && dataLoaded) {
      staleWhilePausedRef.current = true;
      return;
    }
    fetchSettings()
      .then((newSettings) => {
        setRefreshedSettings(newSettings);

        const services = calculateActiveNotificationServices(newSettings);
        setActiveNotificationServices(services.count);
        setActiveProviderNames(services.names);
        setActiveAlertTypes(getActiveAlertTypes(newSettings));

        Promise.allSettled([
          fetchSystemData(),
          fetchRecordingsInfo(),
          fetchActivityData(),
        ]).then(() => {
          setDataLoaded(true);
          setLastUpdated(new Date());
        });
      })
      .catch((error) => {
        console.error("Error fetching settings:", error);

        const services = calculateActiveNotificationServices();
        setActiveNotificationServices(services.count);
        setActiveProviderNames(services.names);
        setActiveAlertTypes(getActiveAlertTypes());

        Promise.allSettled([
          fetchSystemData(),
          fetchRecordingsInfo(),
          fetchActivityData(),
        ]).then(() => {
          setDataLoaded(true);
          setLastUpdated(new Date());
        });
      });
    // Bootstrap-on-settings-change effect; including the fetch/notification helpers
    // would refire on every render and cause settings-fetch loops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  const refreshDashboardData = async (manual = false) => {
    // The 30-second tick stays silent, so the page only moves when something changed.
    if (manual) setIsRefreshing(true);
    // Ref-based gate (state updates are async and can race between rapid ticks).
    if (refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;

    try {
      await Promise.allSettled([
        fetchSystemData(),
        fetchRecordingsInfo(),
        // The button already shows a manual refresh, so the list stays put either way.
        fetchActivityData(true),
      ]);

      setDataLoaded(true);
    } catch (error) {
      setFailedFetches(new Set(["system", "recordings", "activity"]));
      setDataLoaded(true);
    } finally {
      refreshInFlightRef.current = false;
      setIsRefreshing(false);
      setLastUpdated(new Date());
      if (manual) {
        startMetricsTimer();
      }
    }
  };

  latestRefreshRef.current = refreshDashboardData;

  // Only a clean pass counts as fresh. A failed read leaves the last good time where it was.
  const lastUpdatedTime = lastUpdated?.getTime() ?? 0;
  const failureCount = failedFetches.size;
  useEffect(() => {
    if (lastUpdatedTime > 0 && failureCount === 0) setLastSuccessAt(new Date(lastUpdatedTime));
  }, [lastUpdatedTime, failureCount]);

  useEffect(() => {
    if (paused) return;
    setFreshnessNow(Date.now());
    const timer = setInterval(() => setFreshnessNow(Date.now()), 15_000);
    return () => clearInterval(timer);
  }, [paused, lastUpdatedTime]);

  // Back on Home: read at once, then pick up a DVR or settings change made elsewhere.
  const wasPausedRef = React.useRef(paused);
  useEffect(() => {
    if (wasPausedRef.current && !paused) {
      if (staleWhilePausedRef.current) {
        staleWhilePausedRef.current = false;
        fetchSettings()
          .then((newSettings) => {
            setRefreshedSettings(newSettings);
            const services = calculateActiveNotificationServices(newSettings);
            setActiveNotificationServices(services.count);
            setActiveProviderNames(services.names);
            setActiveAlertTypes(getActiveAlertTypes(newSettings));
          })
          .catch(() => undefined);
      }
      void latestRefreshRef.current(false);
      startMetricsTimer();
    }
    wasPausedRef.current = paused;
    // Runs on the paused edge only; the refresh goes through the latest closure.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused]);

  const currentSettings = refreshedSettings || settings;
  const diskSeverity = resolveDiskSeverity({
    usedPercent: diskSpace.usedPercent,
    serverSeverity: diskServerSeverity,
    warningThresholdPercent: currentSettings?.ds_warning_threshold_percent,
    criticalThresholdPercent: currentSettings?.ds_critical_threshold_percent,
  });
  const diskSummaryStatus = diskSeverity === "normal"
    ? undefined
    : {
        tone: diskSeverity,
        label: t(`disk.severity.${diskSeverity}`),
      };

  const filteredActivity = React.useMemo((): ActivityItem[] => {
    const sourceActivity = collapseRepeatedDiskAlerts(
      selectedClient ? (clientActivity ?? []) : recentActivity,
    );
    if (selectedFilters.includes("all")) {
      return sourceActivity;
    }

    return sourceActivity.filter((activity: ActivityItem) => {
      if (
        selectedFilters.includes("channel-watching") &&
        (activity.type === "watching_channel" ||
          activity.type === "stream_started")
      ) {
        return true;
      }

      if (
        selectedFilters.includes("vod-watching") &&
        (activity.type === "vod_playback" || activity.type === "watching_vod")
      ) {
        return true;
      }

      if (
        selectedFilters.includes("recording-events") &&
        (activity.type === "recording_event" ||
          activity.type === "recording_started" ||
          activity.type === "recording_completed" ||
          activity.type === "recording_scheduled" ||
          activity.type === "recording_stopped" ||
          activity.type === "recording_cancelled")
      ) {
        return true;
      }

      if (activity.type === "disk_alert" || activity.type === "disk_recovered") {
        return true;
      }

      return false;
    });
  }, [clientActivity, recentActivity, selectedClient, selectedFilters]);

  const toggleFilter = (filter: string) => {
    if (filter === "all") {
      setSelectedFilters(["all"]);
      return;
    }

    setSelectedFilters((prev: string[]) => {
      const withoutAll = prev.filter((f: string) => f !== "all");

      if (withoutAll.includes(filter)) {
        const filtered = withoutAll.filter((f: string) => f !== filter);
        return filtered.length === 0 ? ["all"] : filtered;
      } else {
        return [...withoutAll, filter];
      }
    });
  };

  const getFilterDisplayName = () => {
    if (selectedFilters.includes("all")) {
      return t("statusOverview.filterAll");
    } else if (selectedFilters.length === 3) {
      return t("statusOverview.filterAllFilters");
    } else if (selectedFilters.length === 1) {
      if (selectedFilters[0] === "channel-watching") return t("type.liveTV");
      if (selectedFilters[0] === "vod-watching") return t("type.vod");
      if (selectedFilters[0] === "recording-events")
        return t("type.recordings");
    }
    return t("statusOverview.filtersCount", { count: selectedFilters.length });
  };

  const statusPanelDvrStatusList = React.useMemo(
    () => selectedDvr === "all"
      ? dvrStatusList
      : dvrStatusList.filter((status) => status.id === selectedDvr),
    [dvrStatusList, selectedDvr],
  );

  let nextRecordingLabel = t("dashboard.noUpcomingRecordings");
  if (upcomingRecordings) {
    const next = upcomingRecordingsList[0];
    if (next && next.start_time && lastUpdated) {
      const countdown = formatCountdown(next.start_time, lastUpdated.getTime());
      nextRecordingLabel = countdown
        ? t("dashboard.nextRecording", { title: next.title, countdown })
        : t("dashboard.recordingNow", { title: next.title });
    } else {
      nextRecordingLabel = t("dashboard.recordingsScheduled", {
        count: upcomingRecordings,
      });
    }
  }

  const healthVerdict = buildHealthVerdict({
    loaded: dataLoaded,
    coreStatus: coreProcessStatus,
    dvrs: statusPanelDvrStatusList,
    diskSeverity,
    diskDetail: diskSpace.freeFormatted && diskSpace.totalFormatted
      ? t("dashboard.verdict.diskDetail", { free: diskSpace.freeFormatted, total: diskSpace.totalFormatted })
      : undefined,
    hasFailedFetches: failedFetches.size > 0,
    failureKind: systemFailureKind,
    notificationServices: currentSettings ? activeNotificationServices : undefined,
  });

  const handleVerdictAction = (kind: HealthVerdictActionKind) => {
    if (kind === "disk") setDiskSheetSignal((current) => current + 1);
    else if (kind === "add-dvr") onNavigate?.("settings:general");
    else onNavigate?.(kind === "notifications" ? "settings:notifications" : "diagnostics");
  };

  const scrollToSection = (testId: string) => {
    document.querySelector(`[data-testid="${testId}"]`)?.scrollIntoView({
      block: "start",
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
    });
  };

  const hasFailures = failedFetches.size > 0;
  const freshnessText = formatFreshness(lastSuccessAt, hasFailures, freshnessNow);

  // One grid with named areas per width (see .home-grid). reading-flow keeps focus and
  // screen readers in visual order where the browser supports it.
  return (
    <div className="home-grid">
      <HealthVerdictLine verdict={healthVerdict} onAction={handleVerdictAction} className="home-area-verdict" />
      <div className="home-freshness home-area-fresh flex flex-wrap items-center justify-between gap-x-3 gap-y-2 sm:gap-x-4">
        <div className="min-w-0 flex-1 basis-40">
          <h1 className={cn(pageTitleClassName, "max-md:sr-only")}>
            {t("dashboard.title")}
          </h1>
          <p
            data-testid="home-freshness"
            className={`break-words text-xs tabular-nums md:mt-1 ${hasFailures ? "text-warning-fg" : "text-muted-foreground"}`}
            title={lastSuccessAt?.toLocaleString() ?? undefined}
            aria-live={hasFailures ? "polite" : "off"}
          >
            {freshnessText}
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="-mr-3 h-auto min-h-11 whitespace-nowrap px-3 py-2 text-center"
          onClick={() => refreshDashboardData(true)}
          disabled={isRefreshing}
        >
          {isRefreshing ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          {isRefreshing ? t("dashboard.refreshing") : t("common.refresh")}
        </Button>
      </div>

      <div className="home-area-heart">
      <div className="home-area-now min-w-0">
        <NowList
          watching={watching}
          recording={recording}
          loading={!dataLoaded}
          hasError={failedFetches.has("recordings")}
        />
      </div>

      <div className="home-area-upcoming min-w-0 xl:[&>*]:h-full">
        <UpcomingRecordingsList
          recordings={upcomingRecordingsList}
          count={upcomingRecordings ?? 0}
          loading={!dataLoaded}
          hasError={failedFetches.has("recordings")}
          onRetry={fetchRecordingsInfo}
        />
      </div>
      </div>

      {/* Key Metrics Section */}
      <div data-testid="dashboard-kpi-grid" className="home-area-tiles grid grid-cols-[repeat(auto-fit,minmax(min(100%,9.5rem),1fr))] gap-3 md:grid-cols-2 lg:grid-cols-4 xl:auto-rows-[minmax(12.375rem,auto)]">
        <MetricCard
          title={t("dashboard.activeStreams")}
          icon={Tv}
          value={activeStreams}
          subtitle={activeStreams === 0 ? t("dashboard.tile.nothingPlaying") : streamSubtitle}
          backgroundImage={streamImage}
          loading={!dataLoaded}
          hasError={failedFetches.has("recordings")}
          asOf={lastSuccessAt?.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
          onActivate={activeStreams ? () => scrollToSection("now-list") : undefined}
          activateLabel={t("dashboard.tile.scrollToNow", { value: activeStreams ?? "–" })}
        />

        <MetricCard
          title={t("dashboard.upcomingRecordings")}
          mobileTitle={t("dashboard.tile.upcoming")}
          icon={Calendar}
          value={upcomingRecordings}
          subtitle={nextRecordingLabel}
          backgroundImage={upcomingRecordingsList[0]?.image || ""}
          loading={!dataLoaded}
          hasError={failedFetches.has("recordings")}
          asOf={lastSuccessAt?.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}
          onActivate={upcomingRecordings ? () => scrollToSection("upcoming-recordings") : undefined}
          activateLabel={t("dashboard.tile.scrollToUpcoming", { value: upcomingRecordings ?? "–" })}
        />

        <MobileDashboardDetails
          id="mobile-disk-details"
          integratedCard
          title={t("disk.title")}
          summary={t("dashboard.tile.diskFreeOf", { total: diskSpace.totalFormatted })}
          errorSummary={diskSpace.error || t("statusOverview.diskUnavailable")}
          summaryStatus={dataLoaded && !diskSpace.error && !failedFetches.has("system") ? diskSummaryStatus : undefined}
          hasError={dataLoaded && (Boolean(diskSpace.error) || failedFetches.has("system"))}
          tile={{ label: t("dashboard.tile.diskLeft"), value: diskSpace.freeFormatted || null, icon: HardDrive, loading: !dataLoaded }}
          openSignal={diskSheetSignal}
        ><DiskSpaceCard
          diskSpace={diskSpace}
          loading={!dataLoaded}
          hasError={failedFetches.has("system")}
          serverSeverity={diskSeverity}
          warningThresholdPercent={
            currentSettings?.ds_warning_threshold_percent
          }
          criticalThresholdPercent={
            currentSettings?.ds_critical_threshold_percent
          }
          warningThresholdGb={currentSettings?.ds_warning_threshold_gb}
          criticalThresholdGb={currentSettings?.ds_critical_threshold_gb}
        /></MobileDashboardDetails>

        <MobileDashboardDetails
          integratedCard
          id="mobile-uptime-details"
          title={t("uptime.title")}
          summary={t("dashboard.tile.uptimeSince")}
          errorSummary={t("uptime.unavailable")}
          hasError={failedFetches.has("system")}
          tile={{ label: t("dashboard.tile.uptime"), value: coreUptime ? formatUptimeTile(coreUptime) : null, icon: Clock, loading: !dataLoaded }}
        ><UptimeCard
          coreUptime={coreUptime}
          uiUptimeDisplay={uiUptimeDisplay}
          dvrStatusList={dvrStatusList}
          loading={!dataLoaded}
          hasError={failedFetches.has("system")}
        /></MobileDashboardDetails>
      </div>

      <div data-testid="dashboard-activity-recordings" className="home-area-activity min-w-0 xl:[&>*]:h-full">
        <RecentActivityList
          recentActivity={recentActivity}
          filteredActivity={filteredActivity}
          selectedFilters={selectedFilters}
          onToggleFilter={toggleFilter}
          activityHours={activityHours}
          onChangeHours={setActivityHours}
          activityLoading={activityLoading || clientActivityLoading}
          dataLoaded={dataLoaded}
          hasError={failedFetches.has("activity") || failedFetches.has("clientActivity")}
          onRetry={fetchActivityData}
          getFilterDisplayName={getFilterDisplayName}
          clients={clientFacets}
          selectedClient={selectedClient}
          onSelectClient={(client) => {
            setSelectedClient(client);
            setClientFilterStatus(null);
          }}
          clientsLoading={clientFacetsLoading}
          clientsError={clientFacetsError}
          clientStatus={clientFilterStatus}
          totalCount={selectedClient || !selectedFilters.includes("all") ? undefined : activityTotal}
          onNavigate={onNavigate}
        />
      </div>

      <div className="home-area-timeline min-w-0 xl:[&>*]:h-full">
        <ActivityTimeline streamingData={streamingData} loading={!dataLoaded} />
      </div>

      <div className="home-area-status min-w-0">
        <StatusPanel
          dvrStatusList={statusPanelDvrStatusList}
          activeNotificationServices={activeNotificationServices}
          activeProviderNames={activeProviderNames}
          activeAlertTypes={activeAlertTypes}
          coreProcessStatus={coreProcessStatus}
          channelwatchVersion={channelwatchVersion}
          currentSettings={currentSettings}
          onNavigate={onNavigate}
          selectedDvr={selectedDvr}
          noSignIn={noSignIn}
          dataLoaded={dataLoaded}
        />
      </div>

      <div className="home-area-maintenance min-w-0">
        <MaintenanceWindowsCard
          selectedDvr={selectedDvr}
          refreshKey={lastUpdated?.getTime() ?? 0}
        />
      </div>
    </div>
  );
}
