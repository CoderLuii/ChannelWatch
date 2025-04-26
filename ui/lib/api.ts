import type { AppSettings, AboutInfo, TestResult, SystemInfo } from "@/lib/types"

const API_BASE = "/api"

export async function fetchSettings(): Promise<AppSettings> {
  const response = await fetch(`${API_BASE}/settings`)

  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(`Failed to fetch settings: ${errorText}`)
  }

  return response.json()
}

export async function saveSettings(settings: AppSettings): Promise<{ message: string }> {
  const response = await fetch(`${API_BASE}/settings`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(settings),
  })

  if (!response.ok) {
    let errorMessage = `HTTP ${response.status}`
    try {
      const errorData = await response.json()
      if (errorData.detail) {
        if (Array.isArray(errorData.detail)) {
          errorMessage = errorData.detail.map((e: any) => `${e.loc.slice(-1)[0]}: ${e.msg}`).join("; ")
        } else {
          errorMessage = String(errorData.detail)
        }
      } else {
        errorMessage = JSON.stringify(errorData)
      }
    } catch (e) {
      errorMessage = await response.text()
    }
    throw new Error(errorMessage)
  }

  return response.json()
}

export async function fetchAboutInfo(): Promise<AboutInfo> {
  const response = await fetch(`${API_BASE}/about`)

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function fetchSystemInfo(): Promise<SystemInfo> {
  const response = await fetch(`${API_BASE}/system-info`)

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function runTest(testName: string): Promise<TestResult> {
  // Replace spaces with underscores for the URL path
  const urlTestName = encodeURIComponent(testName.replace(/ /g, "_"))

  const response = await fetch(`${API_BASE}/run_test/${urlTestName}`, {
    method: "POST",
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function signalRestart(): Promise<{ message: string }> {
  const response = await fetch(`${API_BASE}/restart_core`, {
    method: "POST",
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function signalContainerRestart(): Promise<{ message: string }> {
  const response = await fetch(`${API_BASE}/restart_container`, {
    method: "POST",
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export interface RecordingInfo {
  id: string
  title: string
  start_time: number
  channel: string
  scheduled_time: string
}

export async function fetchUpcomingRecordings(limit: number = 250): Promise<RecordingInfo[]> {
  const response = await fetch(`${API_BASE}/recordings/upcoming?limit=${limit}`)

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function fetchActiveRecordingsCount(): Promise<number> {
  const response = await fetch(`${API_BASE}/recordings/active`)

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export async function fetchActiveStreamsCount(): Promise<number> {
  const response = await fetch(`${API_BASE}/streams/active`)

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}

export interface ActivityItem {
  id: string
  type: string
  title: string
  message: string
  timestamp: string
  icon: string
}

export async function fetchRecentActivity(hours: number = 24, limit: number = 10): Promise<ActivityItem[]> {
  const response = await fetch(`${API_BASE}/recent-activity?hours=${hours}&limit=${limit}`);

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`);
  }

  return response.json();
}

export async function clearActivityHistory(): Promise<{ message: string }> {
  const response = await fetch(`${API_BASE}/clear-activity-history`, {
    method: "POST",
  })

  if (!response.ok) {
    throw new Error(`HTTP error ${response.status}`)
  }

  return response.json()
}


