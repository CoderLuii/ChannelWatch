"use client";

import React, { useState, useEffect, useRef } from "react";
import { Button } from "@/components/base/button";
import type { AppSettings, DVRStatusInfo, RecordingInfo } from "@/lib/types";
import { RefreshCw, Loader2, Tv, Calendar } from "lucide-react";
import {
  fetchSystemInfo,
  fetchUpcomingRecordings,
  fetchStreamDetails,
  fetchCompleteActivityHistory,
  fetchSettings,
  fetchDvrStreams,
  fetchDvrUpcomingRecordings,
  fetchActivityClientFilters,
} from "@/lib/api";
import type { ActivityClientFacet } from "@/lib/api";
import { canonicalActivityClientValue } from "@/lib/activity-clients";
import {
  buildActivityTimeline,
  type ActivityTimelinePoint,
} from "@/lib/activity-timeline";
import { useDvrSelection } from "@/lib/dvr-selection-context";
import { t } from "@/lib/i18n";
import type { ActivityItem } from "@/lib/types";
import { formatDiskSizeFromGB } from "@/lib/utils";
import { MetricCard } from "@/components/dashboard/metric-card";
import { UptimeCard } from "@/components/dashboard/uptime-card";
import {
  DiskSpaceCard,
  type DiskSpaceState,
} from "@/components/dashboard/disk-space-card";
import { ActivityTimeline } from "@/components/dashboard/activity-timeline";
import { StatusPanel } from "@/components/dashboard/status-panel";
import { RecentActivityList } from "@/components/dashboard/recent-activity-list";
import { UpcomingRecordingsList } from "@/components/dashboard/upcoming-recordings-list";

interface StatusOverviewProps {
  settings: AppSettings | null;
  onNavigate?: (view: string) => void;
}

type DiskSeverity = "normal" | "warning" | "critical";

export function StatusOverview({ settings, onNavigate }: StatusOverviewProps) {
  const { selectedDvr } = useDvrSelection();
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [failedFetches, setFailedFetches] = useState<Set<string>>(new Set());
  const [dataLoaded, setDataLoaded] = useState(false);
  const metricsIntervalRef = React.useRef<ReturnType<
    typeof setInterval
  > | null>(null);
  const refreshInFlightRef = React.useRef(false);
  const latestRefreshRef = React.useRef<
    (manual?: boolean) => Promise<void> | void
  >(() => undefined);
  const [coreStartedAt, setCoreStartedAt] = useState<Date | null>(null);
  const [uiStartedAt, setUiStartedAt] = useState<Date | null>(null);
  const [coreUptime, setCoreUptime] = useState({
    days: 0,
    hours: 0,
    minutes: 0,
    seconds: 0,
  });
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
  const [activeStreams, setActiveStreams] = useState(0);
  const [streamSubtitle, setStreamSubtitle] = useState(
    t("statusOverview.noActiveStreams"),
  );
  const [streamImage, setStreamImage] = useState("");
  const [upcomingRecordings, setUpcomingRecordings] = useState(0);
  const [upcomingRecordingsList, setUpcomingRecordingsList] = useState<
    RecordingInfo[]
  >([]);
  const [recentActivity, setRecentActivity] = useState<ActivityItem[]>([]);
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

  const [chartVisibility, setChartVisibility] = useState({
    streams: true,
    recordings: true,
    vod: true,
  });
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
      const freePercent = 100 - usedPercent;
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
      });
    } else {
      setDiskSpace((prev: DiskSpaceState) => ({
        ...prev,
        loading: false,
        error: t("statusOverview.diskUnavailable"),
      }));
    }
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
      return nextDvrStatuses;
    } catch (error) {
      setDiskServerSeverity(undefined);
      setDiskSpace((prev: DiskSpaceState) => ({
        ...prev,
        loading: false,
        error: t("statusOverview.diskError"),
      }));
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
        setStreamSubtitle(streamData.subtitle);
        setStreamImage(streamData.image || "");
      } else {
        const upcomingRecords = await fetchUpcomingRecordings(250);
        setUpcomingRecordingsList(upcomingRecords);
        setUpcomingRecordings(upcomingRecords.length);
        const streamData = await fetchStreamDetails();
        setActiveStreams(streamData.total);
        setStreamSubtitle(streamData.subtitle);
        setStreamImage(streamData.image || "");
      }
    } catch (error) {
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

  const fetchAggregateActivityData = async () => {
    aggregateActivityRequestRef.current?.abort();
    const controller = new AbortController();
    aggregateActivityRequestRef.current = controller;
    setActivityLoading(true);
    try {
      const [timelineActivity, recent] = activityHours === 24
        ? await loadActivity(controller).then((activity) => [activity, activity.slice(0, 250)] as const)
        : await Promise.all([
            loadActivity(controller, undefined, undefined, 24),
            loadActivity(controller, undefined, 250, activityHours),
          ]);
      if (controller.signal.aborted) return;
      setRecentActivity(recent);
      const chartData = buildActivityTimeline(timelineActivity);
      setStreamingData(chartData);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
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
      return;
    }
    const controller = new AbortController();
    clientActivityRequestRef.current = controller;
    setClientActivityLoading(true);
    try {
      const activity = await loadActivity(controller, selectedClient, 250);
      if (!controller.signal.aborted) setClientActivity(activity);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      console.error("Error fetching client activity:", error);
    } finally {
      if (clientActivityRequestRef.current === controller) {
        setClientActivityLoading(false);
      }
    }
  };

  const fetchActivityData = async () => {
    await Promise.all([
      fetchAggregateActivityData(),
      fetchClientActivityData(),
    ]);
  };

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
      alertTypes.push(t("alerts.health.title"));
    return alertTypes;
  };

  // Single auto-refresh timer for all dashboard data (30 seconds)
  // Resets when user manually clicks Refresh or when activityHours changes
  const startMetricsTimer = React.useCallback(() => {
    if (metricsIntervalRef.current) clearInterval(metricsIntervalRef.current);
    metricsIntervalRef.current = setInterval(() => {
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
    if (dataLoaded) fetchActivityData();
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
        setUiUptimeDisplay(days > 0 ? `${days}d ${hours}h ${minutes}m` : `${hours}h ${minutes}m`);
      }
    };
    tick();
    const uptimeInterval = setInterval(tick, 60_000);
    return () => clearInterval(uptimeInterval);
  }, [coreStartedAt, uiStartedAt]);

  useEffect(() => {
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
        ]).then((results) => {
          const labels = ["system", "recordings", "activity"];
          const failed = new Set(
            results
              .map((r, i) => (r.status === "rejected" ? labels[i] : null))
              .filter(Boolean) as string[],
          );
          setFailedFetches(failed);
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
        ]).then((results) => {
          const labels = ["system", "recordings", "activity"];
          const failed = new Set(
            results
              .map((r, i) => (r.status === "rejected" ? labels[i] : null))
              .filter(Boolean) as string[],
          );
          setFailedFetches(failed);
          setDataLoaded(true);
          setLastUpdated(new Date());
        });
      });
    // Bootstrap-on-settings-change effect; including the fetch/notification helpers
    // would refire on every render and cause settings-fetch loops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings]);

  const refreshDashboardData = async (manual = false) => {
    // Ref-based gate (state updates are async and can race between rapid ticks).
    if (refreshInFlightRef.current) return;
    refreshInFlightRef.current = true;

    try {
      setIsRefreshing(true);

      const labels = ["system", "recordings", "activity"];
      const results = await Promise.allSettled([
        fetchSystemData(),
        fetchRecordingsInfo(),
        fetchActivityData(),
      ]);

      const failed = new Set(
        results
          .map((r, i) => (r.status === "rejected" ? labels[i] : null))
          .filter(Boolean) as string[],
      );
      setFailedFetches(failed);
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

  const formatLastUpdated = () => {
    return lastUpdated?.toLocaleTimeString() ?? t("common.loading");
  };

  const currentSettings = refreshedSettings || settings;

  const filteredActivity = React.useMemo((): ActivityItem[] => {
    const sourceActivity = selectedClient ? (clientActivity ?? []) : recentActivity;
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

      if (activity.type === "disk_alert") {
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

  const handleToggleChartVisibility = (key: keyof typeof chartVisibility) => {
    setChartVisibility((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const statusPanelDvrStatusList = React.useMemo(
    () => selectedDvr === "all"
      ? dvrStatusList
      : dvrStatusList.filter((status) => status.id === selectedDvr),
    [dvrStatusList, selectedDvr],
  );

  let nextRecordingLabel = t("dashboard.noUpcomingRecordings");
  if (upcomingRecordings > 0) {
    const next = upcomingRecordingsList[0];
    if (next && next.start_time && lastUpdated) {
      const diffMin = Math.floor((next.start_time * 1000 - lastUpdated.getTime()) / 60000);
      if (diffMin <= 0) {
        nextRecordingLabel = t("dashboard.recordingNow");
      } else {
        const h = Math.floor(diffMin / 60);
        const m = diffMin % 60;
        const countdown = h > 0 ? `in ${h}h ${m}m` : `in ${m}m`;
        nextRecordingLabel = t("dashboard.nextRecording", {
          title: next.title,
          countdown,
        });
      }
    } else {
      nextRecordingLabel = t("dashboard.recordingsScheduled", {
        count: upcomingRecordings,
      });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight md:pt-0 pt-1">
            {t("dashboard.title")}
          </h1>
          <p
            className={`text-sm ${failedFetches.size > 0 ? "text-red-500" : "text-muted-foreground"}`}
            aria-live="polite"
          >
            {t("dashboard.lastUpdated", { time: formatLastUpdated() })}
            {failedFetches.size > 0 ? t("dashboard.staleWarning") : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refreshDashboardData(true)}
            disabled={isRefreshing}
            aria-label={t("common.refresh")}
          >
            {isRefreshing ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4 mr-2" />
            )}
            {t("common.refresh")}
          </Button>
        </div>
      </div>

      {/* Key Metrics Section */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <MetricCard
          title={t("dashboard.activeStreams")}
          icon={Tv}
          value={activeStreams}
          subtitle={streamSubtitle}
          backgroundImage={streamImage}
          loading={!dataLoaded}
          hasError={failedFetches.has("recordings")}
          gradientClasses="bg-gradient-to-br from-blue-50 to-blue-100 dark:from-blue-950 dark:to-blue-900 border-blue-200 dark:border-blue-800"
          iconBgClass="bg-blue-500/20"
          iconColorClass="text-blue-600 dark:text-blue-400"
          valueColorClass="text-blue-700 dark:text-blue-300"
          subtitleColorClass="text-blue-700 dark:text-blue-400"
          loadingColorClass="text-blue-700 dark:text-blue-400"
        />

        <UptimeCard
          coreUptime={coreUptime}
          uiUptimeDisplay={uiUptimeDisplay}
          dvrStatusList={dvrStatusList}
          loading={!dataLoaded}
          hasError={failedFetches.has("system")}
        />

        <MetricCard
          title={t("dashboard.upcomingRecordings")}
          icon={Calendar}
          value={upcomingRecordings}
          subtitle={nextRecordingLabel}
          backgroundImage={upcomingRecordingsList[0]?.image || ""}
          loading={!dataLoaded}
          hasError={failedFetches.has("recordings")}
          gradientClasses="bg-gradient-to-br from-amber-50 to-amber-100 dark:from-amber-950 dark:to-amber-900 border-amber-200 dark:border-amber-800"
          iconBgClass="bg-amber-500/20"
          iconColorClass="text-amber-600 dark:text-amber-400"
          valueColorClass="text-amber-700 dark:text-amber-300"
          subtitleColorClass="text-amber-700 dark:text-amber-400"
          loadingColorClass="text-amber-700 dark:text-amber-400"
        />

        <DiskSpaceCard
          diskSpace={diskSpace}
          loading={!dataLoaded}
          hasError={failedFetches.has("system")}
          serverSeverity={diskServerSeverity}
          warningThresholdPercent={
            currentSettings?.ds_warning_threshold_percent
          }
          criticalThresholdPercent={
            currentSettings?.ds_critical_threshold_percent
          }
        />
      </div>

      {/* Combined Streaming Activity and Status */}
      <div className="grid gap-4 md:grid-cols-3">
        <ActivityTimeline
          streamingData={streamingData}
          chartVisibility={chartVisibility}
          onToggleVisibility={handleToggleChartVisibility}
        />

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
        />
      </div>

      {/* Recent Activity and Upcoming Recordings side by side */}
      <div className="grid gap-4 md:grid-cols-2">
        <RecentActivityList
          recentActivity={recentActivity}
          filteredActivity={filteredActivity}
          selectedFilters={selectedFilters}
          onToggleFilter={toggleFilter}
          activityHours={activityHours}
          onChangeHours={setActivityHours}
          activityLoading={activityLoading || clientActivityLoading}
          dataLoaded={dataLoaded}
          hasError={failedFetches.has("activity")}
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
        />

        <UpcomingRecordingsList
          recordings={upcomingRecordingsList}
          count={upcomingRecordings}
        />
      </div>
    </div>
  );
}
