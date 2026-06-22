"use client";

import React, { useState, useEffect, useRef } from "react";
import { Button } from "@/components/base/button";
import type { AppSettings, DVRStatusInfo, RecordingInfo } from "@/lib/types";
import { RefreshCw, Loader2, Tv, Calendar } from "lucide-react";
import {
  fetchSystemInfo,
  fetchUpcomingRecordings,
  fetchStreamDetails,
  fetchRecentActivity,
  fetchSettings,
  fetchDvrStreams,
  fetchDvrUpcomingRecordings,
  fetchDvrActivityHistory,
} from "@/lib/api";
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
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date());
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
  const [appContainerStartTime, setAppContainerStartTime] =
    useState<Date | null>(null);
  const [coreUptime, setCoreUptime] = useState({
    days: 0,
    hours: 0,
    minutes: 0,
    seconds: 0,
  });
  const [containerUptimeDisplay, setContainerUptimeDisplay] =
    useState<string>("");
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
  const [streamingData, setStreamingData] = useState<
    Array<{
      name: string;
      streams: number;
      recordings: number;
      vod: number;
      isNow?: boolean;
      hour?: number;
      minute?: number;
      timestamp?: number;
    }>
  >([]);

  const [chartVisibility, setChartVisibility] = useState({
    streams: true,
    recordings: true,
    vod: true,
  });
  const [refreshedSettings, setRefreshedSettings] =
    useState<AppSettings | null>(null);
  const [activityLoading, setActivityLoading] = useState(false);

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

  const fetchSystemData = async () => {
    try {
      const systemInfo = await fetchSystemInfo(
        selectedDvr !== "all" ? { dvr_id: selectedDvr } : {},
      );

      if (systemInfo.container_start_time) {
        setAppContainerStartTime(new Date(systemInfo.container_start_time));
      }
      if (systemInfo.uptime_data) {
        setCoreUptime(systemInfo.uptime_data);
      }
      if (systemInfo.core_status) {
        setCoreProcessStatus(systemInfo.core_status);
      }
      if (systemInfo.channelwatch_version) {
        setChannelwatchVersion(systemInfo.channelwatch_version);
      }

      if (selectedDvr !== "all") {
        setDvrStatusList(systemInfo.dvr_status || []);
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
        setDvrStatusList(systemInfo.dvr_status || []);
      }
    } catch (error) {
      setDiskServerSeverity(undefined);
      setDiskSpace((prev: DiskSpaceState) => ({
        ...prev,
        loading: false,
        error: t("statusOverview.diskError"),
      }));
      console.error("Error fetching system info:", error);
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

  const processActivityDataForChart = (
    activityItems: ActivityItem[],
    customDate: Date = new Date(),
  ) => {
    const currentDate = customDate;
    const slotCount = 72;

    const startOfDay = new Date(currentDate);
    startOfDay.setHours(0, 0, 0, 0);

    const allPossibleSlots = [];
    for (let i = 0; i < 24 * 3; i++) {
      const slotTime = new Date(startOfDay);
      slotTime.setMinutes(i * 20);
      allPossibleSlots.push({
        time: slotTime,
        hour: slotTime.getHours(),
        minute: slotTime.getMinutes(),
        timestamp: slotTime.getTime(),
      });
    }

    let currentSlotIndex = -1;
    let minDiff = Infinity;

    for (let i = 0; i < allPossibleSlots.length; i++) {
      const diff = currentDate.getTime() - allPossibleSlots[i].timestamp;
      if (diff >= 0 && diff < minDiff) {
        minDiff = diff;
        currentSlotIndex = i;
      }
    }

    if (currentSlotIndex === -1) {
      const yesterdaySlot = new Date(startOfDay);
      yesterdaySlot.setDate(yesterdaySlot.getDate() - 1);
      yesterdaySlot.setHours(23, 40, 0, 0);
      currentSlotIndex = 0;
      allPossibleSlots[0] = {
        time: yesterdaySlot,
        hour: yesterdaySlot.getHours(),
        minute: yesterdaySlot.getMinutes(),
        timestamp: yesterdaySlot.getTime(),
      };
    }

    const nowIndex = slotCount - 6;

    const currentMinutes = currentDate.getMinutes();
    const currentSeconds = currentDate.getSeconds();
    const slotMinute = allPossibleSlots[currentSlotIndex].minute;

    const minutesElapsed = currentMinutes - slotMinute + currentSeconds / 60;
    const slotPositionsElapsed = minutesElapsed / 20;
    const currentPosition = Math.floor(nowIndex - slotPositionsElapsed);
    const slotsToGoBack = currentPosition;
    const startSlotIndex = currentSlotIndex - slotsToGoBack;

    const timeSlots = [];
    for (let i = 0; i < slotCount; i++) {
      let actualIndex = startSlotIndex + i;
      let slotTime;

      if (actualIndex < 0) {
        const daysToGoBack = Math.floor(Math.abs(actualIndex) / (24 * 3)) + 1;
        const wrappedIndex = 24 * 3 + (actualIndex % (24 * 3));
        slotTime = new Date(allPossibleSlots[wrappedIndex].time);
        slotTime.setDate(slotTime.getDate() - daysToGoBack);
      } else if (actualIndex >= allPossibleSlots.length) {
        const daysToGoForward = Math.floor(actualIndex / (24 * 3));
        const wrappedIndex = actualIndex % (24 * 3);
        slotTime = new Date(allPossibleSlots[wrappedIndex].time);
        slotTime.setDate(slotTime.getDate() + daysToGoForward);
      } else {
        slotTime = new Date(allPossibleSlots[actualIndex].time);
      }

      const slotHour = slotTime.getHours();
      const slotMinute = slotTime.getMinutes();

      let displayHour = slotHour % 12;
      if (displayHour === 0) displayHour = 12;
      const amPm = slotHour < 12 ? "AM" : "PM";

      let name;

      if (i > nowIndex) {
        name = "";
      } else if (slotMinute === 0 && slotHour % 3 === 0) {
        if (Math.abs(i - nowIndex) > 3) {
          name = `${displayHour}${amPm}`;
        } else {
          name = "";
        }
      } else if (i === nowIndex) {
        name = "Now";
      } else {
        name = "";
      }

      const isNow = i === nowIndex;
      const isBeforeCurrentTime = i <= nowIndex;

      timeSlots.push({
        name,
        hour: slotHour,
        minute: slotMinute,
        exactTime: slotTime,
        timestamp: slotTime.getTime(),
        isNow,
        isBeforeCurrentTime,
      });
    }

    const chartData = timeSlots.map((slot) => ({
      name: slot.name,
      hour: slot.hour,
      minute: slot.minute,
      exactTime: slot.exactTime,
      isNow: slot.isNow,
      isBeforeCurrentTime: slot.isBeforeCurrentTime,
      timestamp: slot.timestamp,
      streams: 0,
      recordings: 0,
      vod: 0,
    }));

    activityItems.forEach((activity) => {
      try {
        const activityDate = new Date(activity.timestamp);

        let closestIndex = -1;
        let minDistance = Infinity;

        chartData.forEach((slot, index) => {
          if (!slot.isBeforeCurrentTime) {
            return;
          }

          const distance = Math.abs(activityDate.getTime() - slot.timestamp);

          if (distance <= 10 * 60 * 1000 && distance < minDistance) {
            minDistance = distance;
            closestIndex = index;
          }
        });

        if (closestIndex >= 0) {
          if (
            activity.type === "watching_channel" ||
            activity.type === "stream_started"
          ) {
            chartData[closestIndex].streams += 1;
          } else if (
            activity.type === "recording_event" ||
            activity.type === "recording_started" ||
            activity.type === "recording_completed" ||
            activity.type === "recording_scheduled" ||
            activity.type === "recording_stopped" ||
            activity.type === "recording_cancelled"
          ) {
            chartData[closestIndex].recordings += 1;
          } else if (
            activity.type === "watching_vod" ||
            activity.type === "vod_playback"
          ) {
            chartData[closestIndex].vod += 1;
          }
        }
      } catch (error) {
        console.error("Error processing activity timestamp:", error);
      }
    });

    return chartData;
  };

  const fetchActivityData = async () => {
    setActivityLoading(true);
    try {
      let activity: ActivityItem[];
      if (selectedDvr !== "all") {
        const response = await fetchDvrActivityHistory(selectedDvr, {
          limit: 250,
          sort: "desc",
        });
        activity = response.items;
      } else {
        activity = await fetchRecentActivity(activityHours, 250);
      }
      setRecentActivity(activity);
      const chartData = processActivityDataForChart(activity);
      setStreamingData(chartData);
    } catch (error) {
      console.error("Error fetching recent activity:", error);
    } finally {
      setActivityLoading(false);
    }
  };

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
    ]).then(() => setDataLoaded(true));
    // DVR-switch reload only; including the fetch closures would re-trigger fetches
    // on unrelated state updates and cause request loops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDvr]);

  // Live uptime ticker: increment every second from container start
  useEffect(() => {
    if (!appContainerStartTime) return;
    const tick = () => {
      const now = new Date();
      let diff = Math.floor(
        (now.getTime() - appContainerStartTime.getTime()) / 1000,
      );
      if (diff < 0) diff = 0;
      const days = Math.floor(diff / 86400);
      diff %= 86400;
      const hours = Math.floor(diff / 3600);
      diff %= 3600;
      const minutes = Math.floor(diff / 60);
      const seconds = diff % 60;
      setCoreUptime({ days, hours, minutes, seconds });
      setContainerUptimeDisplay(`${days}d ${hours}h ${minutes}m`);
    };
    tick();
    const uptimeInterval = setInterval(tick, 1000);
    return () => clearInterval(uptimeInterval);
  }, [appContainerStartTime]);

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
    return lastUpdated.toLocaleTimeString();
  };

  const currentSettings = refreshedSettings || settings;

  const filteredActivity = React.useMemo((): ActivityItem[] => {
    if (selectedFilters.includes("all")) {
      return recentActivity;
    }

    return recentActivity.filter((activity: ActivityItem) => {
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
  }, [recentActivity, selectedFilters]);

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

  let nextRecordingLabel = t("dashboard.noUpcomingRecordings");
  if (upcomingRecordings > 0) {
    const next = upcomingRecordingsList[0];
    if (next && next.start_time) {
      const diffMin = Math.floor((next.start_time * 1000 - Date.now()) / 60000);
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
          containerUptimeDisplay={containerUptimeDisplay}
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
          dvrStatusList={dvrStatusList}
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
          activityLoading={activityLoading}
          dataLoaded={dataLoaded}
          hasError={failedFetches.has("activity")}
          onRetry={fetchActivityData}
          getFilterDisplayName={getFilterDisplayName}
        />

        <UpcomingRecordingsList
          recordings={upcomingRecordingsList}
          count={upcomingRecordings}
        />
      </div>
    </div>
  );
}
