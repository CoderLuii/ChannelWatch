"use client"

import React, { useState, useEffect } from "react"
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from "@/components/base/card"
import { Badge } from "@/components/base/badge"
import { Button } from "@/components/base/button"
import { Progress } from "@/components/base/progress"
import type { AppSettings, SystemInfo } from "@/lib/types"
import { formatBytes } from "@/lib/utils"
import { AlertCircle, Clock, HardDrive, Tv, Video, Settings, RefreshCw, ArrowRight, Activity, Calendar, Zap, CheckCircle, Shield, Bell, Play, X, Square, Filter, Check, Loader2 } from 'lucide-react'
import {
  AreaChart,
  Area,
  ResponsiveContainer,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip as RechartsTooltip,
  Legend,
} from "recharts"
import { fetchSystemInfo, fetchUpcomingRecordings, fetchActiveStreamsCount, fetchRecentActivity, fetchSettings } from "@/lib/api"
import type { ActivityItem } from "@/lib/api"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuCheckboxItem,
  DropdownMenuSeparator,
} from "@/components/base/dropdown-menu"
import { Checkbox } from "@/components/base/checkbox"

interface StatusOverviewProps {
  settings: AppSettings | null
  onNavigate?: (view: string) => void 
}

interface DiskSpaceState {
  usedPercent: number;
  freePercent: number;
  totalBytes: number;
  usedBytes: number;
  freeBytes: number;
  loading: boolean;
  error: string | null;
  totalTB: string;
  usedTB: string;
}

export function StatusOverview({ settings, onNavigate }: StatusOverviewProps) {
  const [lastUpdated, setLastUpdated] = useState<Date>(new Date())
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [containerStartTime, setContainerStartTime] = useState<Date | null>(null)
  const [containerUptime, setContainerUptime] = useState({
    days: 0,
    hours: 0,
    minutes: 0,
    seconds: 0
  })
  const [diskSpace, setDiskSpace] = useState<DiskSpaceState>({
    usedPercent: 0,
    freePercent: 0,
    totalBytes: 0,
    usedBytes: 0,
    freeBytes: 0,
    loading: true,
    error: null,
    totalTB: "",
    usedTB: "",
  })
  const [dvrConnectionStatus, setDvrConnectionStatus] = useState<{
    connected: boolean;
    version: string | null;
  }>({
    connected: false,
    version: null
  })
  const [activeStreams, setActiveStreams] = useState(0)
  const [upcomingRecordings, setUpcomingRecordings] = useState(0)
  const [upcomingRecordingsList, setUpcomingRecordingsList] = useState<Array<{
    id: string;
    title: string;
    channel: string;
    scheduled_time: string;
  }>>([])
  const [recentActivity, setRecentActivity] = useState<ActivityItem[]>([])
  const [activeNotificationServices, setActiveNotificationServices] = useState(0)
  const [activeAlertTypes, setActiveAlertTypes] = useState<string[]>([])
  const [selectedFilters, setSelectedFilters] = useState<string[]>(["all"])
  const [streamingData, setStreamingData] = useState<Array<{
    name: string;
    streams: number;
    recordings: number;
    vod: number;
    isNow?: boolean;
    hour?: number;
    minute?: number;
    timestamp?: number;
  }>>([])
  
  const [chartVisibility, setChartVisibility] = useState({
    streams: true,
    recordings: true,
    vod: true
  })
  const [refreshedSettings, setRefreshedSettings] = useState<AppSettings | null>(null)

  const fetchDiskInfo = async () => {
    try {
      const systemInfo = await fetchSystemInfo();
      
      if (systemInfo.disk_total_gb && systemInfo.disk_free_gb !== null && systemInfo.disk_usage_percent !== null) {
        
        const totalGB = systemInfo.disk_total_gb;
        const freeGB = systemInfo.disk_free_gb;
        const usedGB = totalGB - freeGB;
        const usedPercent = systemInfo.disk_usage_percent;
        const freePercent = 100 - usedPercent;
        
        
        const totalTB = (totalGB / 1024).toFixed(2);
        const usedTB = (usedGB / 1024).toFixed(2);
        
        
        
        const totalBytes = totalGB * 1000 * 1000 * 1000;
        const freeBytes = freeGB * 1000 * 1000 * 1000;
        const usedBytes = totalBytes - freeBytes;
        
        console.log("OVERVIEW PAGE - Disk space from API:", {
          disk_total_gb: totalGB,
          disk_free_gb: freeGB,
          disk_usage_percent: usedPercent,
          totalTB,
          usedTB
        });
        
        setDiskSpace({
          usedPercent,
          freePercent,
          totalBytes,
          usedBytes,
          freeBytes,
          loading: false,
          error: null,
          totalTB,
          usedTB
        });
      } else {
        setDiskSpace((prev: DiskSpaceState) => ({
          ...prev,
          loading: false,
          error: "Disk information not available",
        }));
      }
    } catch (error) {
      setDiskSpace((prev: DiskSpaceState) => ({
        ...prev,
        loading: false,
        error: "Error fetching disk information",
      }));
      console.error("Error fetching system info:", error);
    }
  };

  
  const fetchRecordingsInfo = async () => {
    try {
      
      const upcomingRecords = await fetchUpcomingRecordings(250); 
      setUpcomingRecordingsList(upcomingRecords);
      setUpcomingRecordings(upcomingRecords.length);
      
      
      const streamsCount = await fetchActiveStreamsCount();
      setActiveStreams(streamsCount);
    } catch (error) {
      console.error("Error fetching recordings info:", error);
    }
  };

  
  const fetchSystemUptime = async () => {
    try {
      console.log("Fetching system uptime info...");
      const systemInfo = await fetchSystemInfo();
      console.log("System info response:", systemInfo);
      
      if (systemInfo.start_time) {
        console.log("Got valid start_time:", systemInfo.start_time);
        const startTime = new Date(systemInfo.start_time);
        setContainerStartTime(startTime);
      }
      
      
      if (systemInfo.uptime_data) {
        console.log("Got uptime data from API:", systemInfo.uptime_data);
        setContainerUptime(systemInfo.uptime_data);
      }
      
      
      setDvrConnectionStatus({
        connected: !!systemInfo.channels_dvr_server_version,
        version: systemInfo.channels_dvr_server_version
      });
      console.log("DVR connection status:", !!systemInfo.channels_dvr_server_version, systemInfo.channels_dvr_server_version);
    } catch (error) {
      console.error("Error fetching system uptime:", error);
    }
  };

  
  const processActivityDataForChart = (activityItems: ActivityItem[], customDate: Date = new Date()) => {
    
    const currentDate = customDate;
    console.log("Processing chart data with time:", currentDate.toLocaleTimeString(), currentDate.toLocaleDateString());
    
    
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
        timestamp: slotTime.getTime()
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
        timestamp: yesterdaySlot.getTime()
      };
    }
    
    
    const nowIndex = slotCount - 6;
    
    const currentMinutes = currentDate.getMinutes();
    const currentSeconds = currentDate.getSeconds();
    const slotMinute = allPossibleSlots[currentSlotIndex].minute;
    
    
    const minutesElapsed = (currentMinutes - slotMinute) + (currentSeconds / 60);
    
    
    
    const slotPositionsElapsed = minutesElapsed / 20;
    
    
    
    
    
    const currentPosition = Math.floor(nowIndex - slotPositionsElapsed);
    const slotsToGoBack = currentPosition;
    
    console.log(`Current time: ${currentDate.toLocaleString()}`);
    console.log(`Nearest slot before: ${allPossibleSlots[currentSlotIndex].hour}:${allPossibleSlots[currentSlotIndex].minute}`);
    console.log(`Minutes elapsed in slot: ${minutesElapsed.toFixed(2)}, which is ${slotPositionsElapsed.toFixed(2)} slot positions`);
    console.log(`Current position: ${currentPosition}, slots to go back: ${slotsToGoBack}`);
    
    
    const startSlotIndex = currentSlotIndex - slotsToGoBack;
    
    
    const timeSlots = [];
    for (let i = 0; i < slotCount; i++) {
      
      let actualIndex = startSlotIndex + i;
      let slotTime;
      
      if (actualIndex < 0) {
        
        const daysToGoBack = Math.floor(Math.abs(actualIndex) / (24 * 3)) + 1;
        const wrappedIndex = (24 * 3) + (actualIndex % (24 * 3));
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
        isBeforeCurrentTime
      });
    }
    
    console.log("Current time:", currentDate.toLocaleString());
    console.log("Slot before Now:", timeSlots[nowIndex-1].hour + ":" + timeSlots[nowIndex-1].minute);
    console.log("Now slot:", timeSlots[nowIndex].name);
    console.log("Slot after Now:", timeSlots[nowIndex+1].hour + ":" + timeSlots[nowIndex+1].minute);
    
    
    const chartData = timeSlots.map(slot => ({
      name: slot.name,
      hour: slot.hour,
      minute: slot.minute,
      exactTime: slot.exactTime,
      isNow: slot.isNow,
      isBeforeCurrentTime: slot.isBeforeCurrentTime,
      timestamp: slot.timestamp,
      streams: 0,
      recordings: 0,
      vod: 0
    }));
    
    
    activityItems.forEach(activity => {
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
          if (activity.type === "watching_channel" || activity.type === "stream_started") {
            chartData[closestIndex].streams += 1;
          } else if (activity.type === "recording_event") {
            chartData[closestIndex].recordings += 1;
          } else if (activity.type === "watching_vod" || activity.type === "vod_playback") {
            chartData[closestIndex].vod += 1;
          }
        }
      } catch (error) {
        console.error("Error processing activity timestamp:", error);
      }
    });
    
    console.log("Chart data points:", chartData.length);
    console.log("Now index:", nowIndex);
    console.log("Now slot:", chartData[nowIndex]?.name);
    console.log("Start time:", chartData[0]?.exactTime?.toLocaleString());
    console.log("End time:", chartData[chartData.length-1]?.exactTime?.toLocaleString());
    
    return chartData;
  };

  
  const fetchActivityData = async () => {
    try {
      
      const activity = await fetchRecentActivity(24, 250);
      setRecentActivity(activity);
      
      
      const chartData = processActivityDataForChart(activity);
      setStreamingData(chartData);
      
      
      console.log("Updated chart data with current time:", new Date().toLocaleTimeString());
    } catch (error) {
      console.error("Error fetching recent activity:", error);
    }
  };

  
  const calculateActiveNotificationServices = (settingsToUse = settings || refreshedSettings) => {
    if (!settingsToUse) return 0
    
    let count = 0
    
    
    if (settingsToUse.pushover_user_key && settingsToUse.pushover_api_token) {
      count += 1
    }
    
    
    if (settingsToUse.apprise_discord) count += 1
    if (settingsToUse.apprise_email && settingsToUse.apprise_email_to) count += 1
    if (settingsToUse.apprise_telegram) count += 1
    if (settingsToUse.apprise_slack) count += 1
    if (settingsToUse.apprise_gotify) count += 1
    if (settingsToUse.apprise_matrix) count += 1
    if (settingsToUse.apprise_mqtt) count += 1
    if (settingsToUse.apprise_custom) count += 1
    
    return count
  }

  
  const getActiveAlertTypes = (settingsToUse = settings || refreshedSettings) => {
    if (!settingsToUse) return []
    
    const alertTypes = []
    if (settingsToUse.alert_channel_watching) alertTypes.push("Channel Watching")
    if (settingsToUse.alert_disk_space) alertTypes.push("Disk Space")
    if (settingsToUse.alert_vod_watching) alertTypes.push("VOD Watching")
    if (settingsToUse.alert_recording_events) alertTypes.push("Recording Events")
    
    return alertTypes
  }

  
  useEffect(() => {
    
    const intervalId = setInterval(() => {
      console.log("Auto-refreshing chart data at:", new Date().toLocaleTimeString());
      fetchActivityData();
    }, 60000); 
    
    
    return () => clearInterval(intervalId);
  }, []);
  
  
  useEffect(() => {
    console.log("Component mounted, fetching initial data")
    
    
    fetchSettings()
      .then(newSettings => {
        setRefreshedSettings(newSettings)
        
        
        const servicesCount = calculateActiveNotificationServices(newSettings)
        setActiveNotificationServices(servicesCount)
        
        
        const alertTypes = getActiveAlertTypes(newSettings)
        setActiveAlertTypes(alertTypes)
        
        
        fetchDiskInfo()
        fetchRecordingsInfo()
        fetchSystemUptime()
        fetchActivityData()
      })
      .catch((error) => {
        console.error("Error fetching settings:", error)
        
        const servicesCount = calculateActiveNotificationServices()
        setActiveNotificationServices(servicesCount)
        
        const alertTypes = getActiveAlertTypes()
        setActiveAlertTypes(alertTypes)
        
        
        fetchDiskInfo()
        fetchRecordingsInfo()
        fetchSystemUptime()
        fetchActivityData()
      })
  }, [settings])

  
  const refreshDashboardData = async () => {
    if (isRefreshing) return;
    
    try {
      setIsRefreshing(true);
      
      
      await Promise.all([
        fetchDiskInfo(),
        fetchRecordingsInfo(),
        fetchSystemUptime(),
        fetchActivityData()
      ]);
    } catch (error) {
      
    } finally {
      setIsRefreshing(false);
      setLastUpdated(new Date());
    }
  };

  
  const formatLastUpdated = () => {
    return lastUpdated.toLocaleTimeString()
  }

  
  const activeAlertCount = [
    (refreshedSettings || settings)?.alert_channel_watching,
    (refreshedSettings || settings)?.alert_vod_watching,
    (refreshedSettings || settings)?.alert_disk_space,
    (refreshedSettings || settings)?.alert_recording_events,
  ].filter(Boolean).length
  
  
  const currentSettings = refreshedSettings || settings;

  
  const getDiskStatusColor = (usedPercent: number) => {
    if (usedPercent > 90) return "bg-red-600 dark:bg-red-400";
    if (usedPercent > 75) return "bg-amber-600 dark:bg-amber-400";
    return "bg-emerald-600 dark:bg-emerald-400";
  };

  
  const formatTimeAgo = (timestamp: string) => {
    try {
      const date = new Date(timestamp)
      const now = new Date()
      const diffMs = now.getTime() - date.getTime()
      
      const diffSecs = Math.floor(diffMs / 1000)
      if (diffSecs < 60) return "Just now"
      
      const diffMins = Math.floor(diffSecs / 60)
      if (diffMins < 60) return `${diffMins}m ago`
      
      const diffHours = Math.floor(diffMins / 60)
      if (diffHours < 24) return `${diffHours}h ago`
      
      const diffDays = Math.floor(diffHours / 24)
      return `${diffDays}d ago`
    } catch (e) {
      return "Unknown"
    }
  }

  
  const ActivityIcon = ({ type, className, message }: { type: string, className?: string, message?: string }) => {
    
    if (type === 'recording_event' && message) {
      if (message.startsWith('Scheduled:')) {
        return <Calendar className={className} />
      } else if (message.startsWith('Cancelled:')) {
        return <X className={className} />
      } else if (message.startsWith('Recording(')) {
        return <Video className={className} />
      } else if (message.startsWith('Completed')) {
        return <CheckCircle className={className} />
      } else if (message.startsWith('Stopped:')) {
        return <Square className={className} />
      }
      
      return <Video className={className} />
    }

    
    switch (type) {
      case 'stream_started':
        return <Tv className={className} />
      case 'recording_started':
      case 'recording_completed':
        return <Video className={className} />
      case 'disk_alert':
        return <AlertCircle className={className} />
      case 'vod_playback':
        return <Play className={className} />
      default:
        return <Bell className={className} />
    }
  }

  
  const getIconColorClasses = (type: string, message?: string) => {
    
    if (type === 'recording_event' && message) {
      if (message.startsWith('Scheduled:')) {
        return 'bg-amber-500/20 text-amber-600 dark:text-amber-400'
      } else if (message.startsWith('Cancelled:')) {
        return 'bg-red-500/20 text-red-600 dark:text-red-400'
      } else if (message.startsWith('Recording(')) {
        return 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400'
      } else if (message.startsWith('Completed')) {
        return 'bg-purple-500/20 text-purple-600 dark:text-purple-400'
      } else if (message.startsWith('Stopped:')) {
        return 'bg-slate-500/20 text-slate-600 dark:text-slate-400'
      }
      
      return 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400'
    }

    
    switch (type) {
      case 'stream_started':
        return 'bg-blue-500/20 text-blue-600 dark:text-blue-400'
      case 'vod_playback':
        return 'bg-amber-500/20 text-amber-600 dark:text-amber-400'
      case 'recording_started':
        return 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400'
      case 'recording_completed':
        return 'bg-purple-500/20 text-purple-600 dark:text-purple-400'
      case 'disk_alert':
        return 'bg-red-500/20 text-red-600 dark:text-red-400'
      default:
        return 'bg-slate-500/20 text-slate-600 dark:text-slate-400'
    }
  }

  
  const getFilteredActivity = (): ActivityItem[] => {
    
    if (selectedFilters.includes("all")) {
      return recentActivity
    }
    
    return recentActivity.filter((activity: ActivityItem) => {
      
      if (selectedFilters.includes("channel-watching") && 
          (activity.type === "watching_channel" || activity.type === "stream_started")) {
        return true
      }
      
      
      if (selectedFilters.includes("vod-watching") && 
          (activity.type === "vod_playback" || activity.type === "watching_vod")) {
        return true
      }
      
      
      if (selectedFilters.includes("recording-events") && 
          (activity.type === "recording_event" || 
           activity.type === "recording_started" || 
           activity.type === "recording_completed")) {
        return true
      }
      
      return false
    })
  }
  
  
  const toggleFilter = (filter: string) => {
    if (filter === "all") {
      
      setSelectedFilters(["all"])
      return
    }
    
    setSelectedFilters((prev: string[]) => {
      
      const withoutAll = prev.filter((f: string) => f !== "all")
      
      
      if (withoutAll.includes(filter)) {
        
        const filtered = withoutAll.filter((f: string) => f !== filter)
        return filtered.length === 0 ? ["all"] : filtered
      } else {
        return [...withoutAll, filter]
      }
    })
  }
  
  
  const getFilterDisplayName = () => {
    if (selectedFilters.includes("all")) {
      return "All"
    } else if (selectedFilters.length === 3) {
      return "All Filters"
    } else if (selectedFilters.length === 1) {
      if (selectedFilters[0] === "channel-watching") return "Channel Watching"
      if (selectedFilters[0] === "vod-watching") return "VOD Watching"
      if (selectedFilters[0] === "recording-events") return "Recording Events"
    }
    return `${selectedFilters.length} Filters`
  }

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight md:pt-0 pt-1">Dashboard Overview</h1>
          <p className="text-muted-foreground text-sm">
            Last updated: {formatLastUpdated()}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={refreshDashboardData} disabled={isRefreshing}>
            {isRefreshing ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <RefreshCw className="h-4 w-4 mr-2" />
            )}
            Refresh
          </Button>
        </div>
      </div>

      {/* Key Metrics Section */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card className="bg-gradient-to-br from-blue-50 to-blue-100 dark:from-blue-950 dark:to-blue-900 border-blue-200 dark:border-blue-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Streams</CardTitle>
            <div className="rounded-full bg-blue-500/20 p-1">
              <Tv className="h-4 w-4 text-blue-600 dark:text-blue-400" />
            </div>
          </CardHeader>
          <CardContent className="py-2">
            <div className="text-3xl font-bold text-blue-700 dark:text-blue-300">{activeStreams}</div>
            <p className="text-xs text-blue-600/80 dark:text-blue-400/80">
              {activeStreams === 0
                ? "No active streams"
                : activeStreams === 1
                  ? "1 device streaming"
                  : `${activeStreams} devices streaming`}
            </p>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-purple-50 to-purple-100 dark:from-purple-950 dark:to-purple-900 border-purple-200 dark:border-purple-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">System Uptime</CardTitle>
            <div className="rounded-full bg-purple-500/20 p-1">
              <Clock className="h-4 w-4 text-purple-600 dark:text-purple-400" />
            </div>
          </CardHeader>
          <CardContent className="py-2">
            <div className="mt-3 grid grid-cols-4 gap-1.5">
              <div className="flex flex-col items-center">
                <div className="text-xl font-bold text-purple-700 dark:text-purple-300">{containerUptime.days}</div>
                <div className="text-[10px] uppercase font-medium text-purple-600/70 dark:text-purple-400/70">Days</div>
              </div>
              <div className="flex flex-col items-center">
                <div className="text-xl font-bold text-purple-700 dark:text-purple-300">{containerUptime.hours}</div>
                <div className="text-[10px] uppercase font-medium text-purple-600/70 dark:text-purple-400/70">
                  Hours
                </div>
              </div>
              <div className="flex flex-col items-center">
                <div className="text-xl font-bold text-purple-700 dark:text-purple-300">{containerUptime.minutes}</div>
                <div className="text-[10px] uppercase font-medium text-purple-600/70 dark:text-purple-400/70">Mins</div>
              </div>
              <div className="flex flex-col items-center">
                <div className="text-xl font-bold text-purple-700 dark:text-purple-300">{containerUptime.seconds}</div>
                <div className="text-[10px] uppercase font-medium text-purple-600/70 dark:text-purple-400/70">Secs</div>
              </div>
            </div>

            <p className="text-[11px] text-purple-600/80 dark:text-purple-400/80 mt-4"> 
              Last restart: {containerStartTime ? containerStartTime.toLocaleString() : "Unknown"}
            </p>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-amber-50 to-amber-100 dark:from-amber-950 dark:to-amber-900 border-amber-200 dark:border-amber-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Upcoming Recordings</CardTitle>
            <div className="rounded-full bg-amber-500/20 p-1">
              <Calendar className="h-4 w-4 text-amber-600 dark:text-amber-400" />
            </div>
          </CardHeader>
          <CardContent className="py-2">
            <div className="text-3xl font-bold text-amber-700 dark:text-amber-300">{upcomingRecordings}</div>
            <p className="text-xs text-amber-600/80 dark:text-amber-400/80">
              {upcomingRecordings === 0
                ? "No upcoming recordings"
                : upcomingRecordings === 1
                  ? "1 recording scheduled"
                  : `${upcomingRecordings} recordings scheduled`}
            </p>
          </CardContent>
        </Card>

        <Card className="bg-gradient-to-br from-emerald-50 to-emerald-100 dark:from-emerald-950 dark:to-emerald-900 border-emerald-200 dark:border-emerald-800">
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Disk Space</CardTitle>
            <div className="rounded-full bg-emerald-500/20 p-1">
              <HardDrive className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
            </div>
          </CardHeader>
          <CardContent className="py-2">
            {diskSpace.loading ? (
              <div className="text-sm text-emerald-600/80 dark:text-emerald-400/80">Loading...</div>
            ) : diskSpace.error ? (
              <div className="text-sm text-red-600/80 dark:text-red-400/80">{diskSpace.error}</div>
            ) : (
              <>
                <div className="text-3xl font-bold text-emerald-700 dark:text-emerald-300">{diskSpace.freePercent}% Free</div>
                <div>
                  {diskSpace.usedTB} TB used of {diskSpace.totalTB} TB
                </div>
                <Progress 
                  value={diskSpace.usedPercent}
                  className="h-2 mt-2"
                  indicatorClassName={getDiskStatusColor(diskSpace.usedPercent)}
                />
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Combined Streaming Activity and Status */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card className="md:col-span-2">
          <CardHeader className="pb-1 pt-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Activity className="h-4 w-4 text-primary" />
              24-Hour Timeline
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {/* Use responsive height with min/max constraints */}
            <div className="h-[180px] sm:h-[200px] w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={streamingData} margin={{ top: 10, right: 0, left: 5, bottom: 10 }}>
                  <defs>
                    <linearGradient id="colorStreams" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#8884d8" stopOpacity={0.8} />
                      <stop offset="95%" stopColor="#8884d8" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="colorRecordings" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#82ca9d" stopOpacity={0.8} />
                      <stop offset="95%" stopColor="#82ca9d" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="colorVOD" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#ffc658" stopOpacity={0.8} />
                      <stop offset="95%" stopColor="#ffc658" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis 
                    dataKey="name" 
                    tick={{ fontSize: 10 }}
                    axisLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                    tickLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                    padding={{ left: 0, right: 0 }}
                    
                    tickFormatter={(value) => value || ""}
                    
                    interval={0}
                    
                    minTickGap={50}
                    height={30}
                  />
                  <YAxis 
                    tick={{ fontSize: 10 }}
                    axisLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                    tickLine={{ stroke: 'rgba(100, 116, 139, 0.2)' }}
                    domain={[0, 'auto']}
                    width={25}
                  />
                  <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                  <RechartsTooltip
                    contentStyle={{
                      backgroundColor: "hsl(var(--card))",
                      borderColor: "hsl(var(--border))",
                      fontSize: "12px",
                    }}
                    formatter={(value: number, name: string) => {
                      return [value, name];
                    }}
                    labelFormatter={(label, payload) => {
                      
                      if (label === "Now") {
                        return "Now";
                      }
                      
                      
                      
                      if (Array.isArray(payload) && payload.length > 0) {
                        
                        
                        const chartX = payload[0].payload?.timestamp;
                        if (chartX) {
                          
                          const point = streamingData.find(d => d.timestamp === chartX);
                          if (point) {
                            
                            let displayHour = (point.hour ?? 0) % 12;
                            if (displayHour === 0) displayHour = 12;
                            const amPm = (point.hour ?? 0) < 12 ? "AM" : "PM";
                            const formattedMinute = (point.minute ?? 0).toString().padStart(2, '0');
                            return `${displayHour}:${formattedMinute} ${amPm}`;
                          }
                        }
                      }
                      
                      
                      if (label && label !== "") {
                        return label;
                      }
                      
                      
                      return "Unknown time";
                    }}
                  />
                  {/* Use custom legend by setting it to false here */}
                  <Legend content={() => null} />
                  
                  {chartVisibility.streams && (
                    <Area
                      type="monotone"
                      name="Live Channel"
                      stroke="#8884d8"
                      fillOpacity={1}
                      fill="url(#colorStreams)"
                      strokeWidth={2}
                      
                      connectNulls={true}
                      
                      dataKey="streams"
                    />
                  )}
                  {chartVisibility.recordings && (
                    <Area
                      type="monotone"
                      name="Recording Event"
                      stroke="#82ca9d"
                      fillOpacity={1}
                      fill="url(#colorRecordings)"
                      strokeWidth={2}
                      
                      connectNulls={true}
                      
                      dataKey="recordings"
                    />
                  )}
                  {chartVisibility.vod && (
                    <Area
                      type="monotone"
                      name="VOD Content"
                      stroke="#ffc658"
                      fillOpacity={1}
                      fill="url(#colorVOD)"
                      strokeWidth={2}
                      
                      connectNulls={true}
                      
                      dataKey="vod"
                    />
                  )}
                  {/* Add a dot to mark the now position */}
                  {streamingData.findIndex(d => d.isNow) >= 0 && (
                    <Area
                      type="monotone"
                      dataKey={() => 0}
                      
                      name=""
                      fill="none"
                      stroke="none"
                      legendType="none"
                      dot={(props) => {
                        const nowIndex = streamingData.findIndex(d => d.isNow);
                        if (props.index !== nowIndex) return <g />; 
                        return (
                          <circle
                            cx={props.cx}
                            cy={props.cy}
                            r={6}
                            stroke="#fff"
                            strokeWidth={2}
                            fill="#ff6b6b"
                          />
                        );
                      }}
                    />
                  )}
                </AreaChart>
              </ResponsiveContainer>
            </div>
            {/* Custom interactive legend */}
            <div className="flex justify-center items-center gap-6 mt-2 mb-1 text-xs">
              <button 
                onClick={() => setChartVisibility(prev => ({ ...prev, streams: !prev.streams }))}
                className="flex items-center gap-1.5 opacity-90 hover:opacity-100 transition-opacity"
              >
                <div className="w-4 h-4 rounded flex items-center justify-center" style={{ backgroundColor: chartVisibility.streams ? '#8884d8' : 'transparent', border: '1px solid #8884d8' }}>
                  {chartVisibility.streams && <Check className="h-3 w-3 text-white" />}
                </div>
                <span>Live Channel</span>
              </button>
              <button 
                onClick={() => setChartVisibility(prev => ({ ...prev, recordings: !prev.recordings }))}
                className="flex items-center gap-1.5 opacity-90 hover:opacity-100 transition-opacity"
              >
                <div className="w-4 h-4 rounded flex items-center justify-center" style={{ backgroundColor: chartVisibility.recordings ? '#82ca9d' : 'transparent', border: '1px solid #82ca9d' }}>
                  {chartVisibility.recordings && <Check className="h-3 w-3 text-white" />}
                </div>
                <span>Recording Event</span>
              </button>
              <button 
                onClick={() => setChartVisibility(prev => ({ ...prev, vod: !prev.vod }))}
                className="flex items-center gap-1.5 opacity-90 hover:opacity-100 transition-opacity"
              >
                <div className="w-4 h-4 rounded flex items-center justify-center" style={{ backgroundColor: chartVisibility.vod ? '#ffc658' : 'transparent', border: '1px solid #ffc658' }}>
                  {chartVisibility.vod && <Check className="h-3 w-3 text-white" />}
                </div>
                <span>VOD Content</span>
              </button>
            </div>
          </CardContent>
        </Card>

        {/* Updated Status Section to match the image */}
        <Card className="overflow-hidden flex flex-col h-full">
          <CardHeader className="pb-1 pt-3 flex-shrink-0">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Shield className="h-4 w-4 text-primary" />
              Status
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0 flex-grow">
            <div className="grid grid-cols-1 divide-y h-full">
              {/* DVR Connection */}
              <div className="p-3 hover:bg-muted/50 transition-colors flex items-center justify-between flex-grow">
                <div>
                  <div className="text-sm font-medium">DVR Connection</div>
                  <div className="text-xs text-muted-foreground">
                    {currentSettings?.channels_dvr_host ? `${currentSettings.channels_dvr_host}:${currentSettings.channels_dvr_port || "8089"}` : "Not configured"}
                  </div>
                </div>
                <Badge variant={dvrConnectionStatus.connected ? "default" : "destructive"} className={dvrConnectionStatus.connected ? "bg-green-600 text-green-50 hover:bg-green-700" : ""}>
                  {dvrConnectionStatus.connected ? "Connected" : (currentSettings?.channels_dvr_host ? "Not Connected" : "Not Configured")}
                </Badge>
              </div>

              {/* Active Alerts */}
              <div className="p-3 hover:bg-muted/50 transition-colors flex items-center justify-between flex-grow">
                <div className="text-sm font-medium">Active Alerts</div>
                <div className="group relative inline-block">
                  <Badge 
                    variant={activeAlertCount > 0 ? "default" : "secondary"}
                    className={activeAlertCount > 0 ? "cursor-default" : ""}
                  >
                    {activeAlertCount} Enabled
                  </Badge>
                  {activeAlertCount > 0 && (
                    <div className="absolute z-10 invisible group-hover:visible bg-popover text-popover-foreground rounded-md shadow-md p-2 right-0 w-48 mt-1 text-xs">
                      <div className="font-medium mb-1">Enabled Alert Types:</div>
                      <ul className="list-disc pl-4">
                        {activeAlertTypes.map((type, index) => (
                          <li key={index}>{type}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              </div>

              {/* Notification Services */}
              <div className="p-3 hover:bg-muted/50 transition-colors flex items-center justify-between flex-grow">
                <div className="text-sm font-medium">Notification Services</div>
                <Badge variant="outline">{activeNotificationServices} Active</Badge>
              </div>
            </div>
          </CardContent>
          <CardFooter className="border-t p-2 flex-shrink-0">
            <Button
              variant="outline"
              size="sm"
              className="w-full h-7 text-xs"
              onClick={() => onNavigate?.('settings')}
              disabled={!onNavigate}
            >
              <Settings className="mr-1.5 h-3.5 w-3.5" />
              Manage Settings
            </Button>
          </CardFooter>
        </Card>
      </div>

      {/* Recent Activity and Upcoming Recordings side by side */}
      <div className="grid gap-4 md:grid-cols-2">
        {/* Recent Activity */}
        <Card className="flex flex-col h-[300px] sm:h-[350px] md:h-[420px] max-w-full overflow-hidden">
          <CardHeader className="pb-2 flex-shrink-0">
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2 text-base">
                <Zap className="h-4 w-4 text-primary" />
                Recent Activity
              </CardTitle>
              <div className="flex gap-2 items-center">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="sm" className="h-6 gap-1 text-xs">
                      <Filter className="h-3 w-3" />
                      {getFilterDisplayName()}
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuCheckboxItem 
                      checked={selectedFilters.includes("all")}
                      onCheckedChange={() => toggleFilter("all")}
                    >
                      All
                    </DropdownMenuCheckboxItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuCheckboxItem 
                      checked={selectedFilters.includes("channel-watching")}
                      onCheckedChange={() => toggleFilter("channel-watching")}
                    >
                      Channel Watching
                    </DropdownMenuCheckboxItem>
                    <DropdownMenuCheckboxItem 
                      checked={selectedFilters.includes("vod-watching")}
                      onCheckedChange={() => toggleFilter("vod-watching")}
                    >
                      VOD Watching
                    </DropdownMenuCheckboxItem>
                    <DropdownMenuCheckboxItem 
                      checked={selectedFilters.includes("recording-events")}
                      onCheckedChange={() => toggleFilter("recording-events")}
                    >
                      Recording Events
                    </DropdownMenuCheckboxItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                <Badge variant="outline" className="bg-primary/10 text-primary text-xs">
                  Last 24 Hours
                </Badge>
              </div>
            </div>
          </CardHeader>
          <CardContent className="p-0 flex-grow overflow-y-auto overflow-x-hidden pr-1">
            {getFilteredActivity().length > 0 ? (
              getFilteredActivity().map((activity) => {
                const colorClasses = getIconColorClasses(activity.type, activity.message)
                return (
                  <div 
                    key={activity.id} 
                    className="flex items-center gap-2 p-2 hover:bg-muted/50 transition-colors border-b border-border/20 w-full"
                  >
                    <div className={`rounded-full ${colorClasses.split(' ')[0]} p-1.5 flex-shrink-0`}>
                      <ActivityIcon 
                        type={activity.type} 
                        className={`h-3.5 w-3.5 ${colorClasses.split(' ').slice(1).join(' ')}`} 
                        message={activity.message}
                      />
                    </div>
                    <div className="flex-1 min-w-0 pr-1 overflow-hidden">
                      <p className="text-sm font-medium leading-none mb-1 truncate max-w-full">{activity.title}</p>
                      <p className="text-xs text-muted-foreground truncate max-w-full">{activity.message}</p>
                    </div>
                    <Badge variant="outline" className="flex-shrink-0 text-xs whitespace-nowrap py-0 h-5 px-1.5">
                      {formatTimeAgo(activity.timestamp)}
                    </Badge>
                  </div>
                )
              })
            ) : (
              <div className="flex items-center justify-center h-full p-6 text-sm text-muted-foreground">
                {selectedFilters.length > 0 
                  ? "No matching activity in the last 24 hours"
                  : "No recent activity"}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Upcoming Recordings */}
        <Card className="flex flex-col h-[300px] sm:h-[350px] md:h-[420px] max-w-full overflow-hidden">
          <CardHeader className="pb-2 flex-shrink-0">
            <CardTitle className="flex items-center gap-2 text-base">
              <Calendar className="h-4 w-4 text-primary" />
              Upcoming Recordings
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0 flex-grow overflow-y-auto overflow-x-hidden pr-1">
            {upcomingRecordingsList.length > 0 ? (
              upcomingRecordingsList.map((recording) => (
                <div 
                  key={recording.id} 
                  className="flex items-center gap-2 p-2 hover:bg-muted/50 transition-colors border-b border-border/20 w-full"
                >
                <div className="rounded-full bg-amber-500/10 p-1.5 flex-shrink-0">
                  <Clock className="h-3.5 w-3.5 text-amber-600 dark:text-amber-400" />
                </div>
                <div className="flex-1 min-w-0 pr-1 overflow-hidden">
                    <p className="text-sm font-medium leading-none mb-1 truncate max-w-full">{recording.title}</p>
                    <p className="text-xs text-muted-foreground truncate max-w-full">{recording.channel} - {recording.scheduled_time}</p>
                </div>
                <Badge className="flex-shrink-0 bg-amber-500/10 text-amber-600 dark:text-amber-400 hover:bg-amber-500/20 text-xs py-0 h-5 px-1.5">
                  Scheduled
                </Badge>
              </div>
              ))
            ) : (
              <div className="flex items-center justify-center h-full p-6 text-sm text-muted-foreground">
                No upcoming recordings scheduled
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}


