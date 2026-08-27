import { describe, expect, it } from "vitest"

import { buildMonitoringBanner } from "@/components/dashboard/status-panel"
import type { DVRStatusInfo } from "@/lib/types"

describe("buildMonitoringBanner", () => {
  it("does not treat an intentionally disabled DVR as degraded monitoring", () => {
    const disabledDvr = {
      id: "disabled-dvr",
      name: "Disabled DVR",
      enabled: false,
      connected: false,
      monitoring_ready: false,
      monitoring_status: "disabled",
    } as DVRStatusInfo

    expect(buildMonitoringBanner([disabledDvr])).toBeNull()
  })

  it("still reports an enabled DVR with stale monitoring", () => {
    const staleDvr = {
      id: "stale-dvr",
      name: "Stale DVR",
      enabled: true,
      connected: true,
      monitoring_ready: false,
      monitoring_status: "stale",
      monitoring_reason: "No fresh event stream data.",
    } as DVRStatusInfo

    expect(buildMonitoringBanner([staleDvr])).toMatchObject({
      tone: "degraded",
      detail: "No fresh event stream data.",
    })
  })
})
