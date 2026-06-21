import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import type { DVRStatusInfo } from "@/lib/types";

const __dirname = dirname(fileURLToPath(import.meta.url));
function srcFile(rel: string): string {
  return readFileSync(resolve(__dirname, rel), "utf8");
}

describe("diagnostics-panel: multi-DVR source structure", () => {
  it("imports useDvrSelection", () => {
    const src = srcFile("../components/diagnostics-panel.tsx");
    expect(src).toContain("useDvrSelection");
    expect(src).toContain("dvr-selection-context");
  });

  it("imports DVRStatusInfo type", () => {
    const src = srcFile("../components/diagnostics-panel.tsx");
    expect(src).toContain("DVRStatusInfo");
  });

  it("derives dvrSections from systemInfo.dvr_status", () => {
    const src = srcFile("../components/diagnostics-panel.tsx");
    expect(src).toContain("dvrSections");
    expect(src).toContain("dvr_status");
  });

  it("uses isMultiDvr to toggle grid layout", () => {
    const src = srcFile("../components/diagnostics-panel.tsx");
    expect(src).toContain("isMultiDvr");
  });

  it("renders per-DVR health dots via getDvrDotClass", () => {
    const src = srcFile("../components/diagnostics-panel.tsx");
    expect(src).toContain("getDvrDotClass");
  });

  it("filters dvrSections by selectedDvr when not 'all'", () => {
    const src = srcFile("../components/diagnostics-panel.tsx");
    expect(src).toContain('selectedDvr !== "all"');
    expect(src).toContain("d.id === selectedDvr");
  });

  it("includes DVR connection info in diagnostics export", () => {
    const src = srcFile("../components/diagnostics-panel.tsx");
    expect(src).toContain("diagnostics.export.sectionDvr");
    expect(src).toContain("dvrLines");
  });

  it("updates getHealthIssues to surface per-DVR offline and stale states", () => {
    const src = srcFile("../components/diagnostics-panel.tsx");
    expect(src).toContain("offlineDvrs");
    expect(src).toContain("staleDvrs");
  });

  it("renders one section per DVR in multi-DVR mode", () => {
    const src = srcFile("../components/diagnostics-panel.tsx");
    expect(src).toContain("dvrSections.map");
    expect(src).toContain("dvr.id");
    expect(src).toContain("dvr.name");
  });

  it("keeps log polling ownership scoped to the current request", () => {
    const src = srcFile("../components/diagnostics-panel.tsx");
    expect(src).toContain("logFetchOwnerRef");
    expect(src).toContain("logFetchOwnerRef.current === requestId");
    expect(src).not.toContain("logFetchInFlightRef.current = false\n    }");
  });
});

describe("getDvrDotClass helper logic", () => {
  function getDvrDotClass(
    dvr: Pick<
      DVRStatusInfo,
      "connected" | "monitoring_ready" | "monitoring_status"
    >,
  ): string {
    if (!dvr.connected) return "bg-red-500";
    if (dvr.monitoring_ready === false) {
      return dvr.monitoring_status === "dead" ? "bg-red-500" : "bg-amber-500";
    }
    return "bg-green-500";
  }

  it("returns green when connected and monitoring is healthy", () => {
    expect(getDvrDotClass({ connected: true, monitoring_ready: true })).toBe(
      "bg-green-500",
    );
  });

  it("returns green when connected and monitoring_ready is undefined", () => {
    expect(getDvrDotClass({ connected: true })).toBe("bg-green-500");
  });

  it("returns amber when connected but monitoring is stale", () => {
    expect(
      getDvrDotClass({
        connected: true,
        monitoring_ready: false,
        monitoring_status: "stale",
      }),
    ).toBe("bg-amber-500");
  });

  it("returns red when connected but monitoring is dead", () => {
    expect(
      getDvrDotClass({
        connected: true,
        monitoring_ready: false,
        monitoring_status: "dead",
      }),
    ).toBe("bg-red-500");
  });

  it("returns red when not connected regardless of monitoring state", () => {
    expect(getDvrDotClass({ connected: false, monitoring_ready: true })).toBe(
      "bg-red-500",
    );
    expect(getDvrDotClass({ connected: false, monitoring_ready: false })).toBe(
      "bg-red-500",
    );
  });
});

describe("dvrSections derivation logic", () => {
  const allDvrs: DVRStatusInfo[] = [
    {
      id: "dvr_1",
      name: "Living Room",
      host: "192.168.1.10",
      port: 8089,
      connected: true,
      version: "2026.01.01",
      version_compatible: true,
      version_warning: null,
      disk_usage_percent: null,
      disk_total_gb: null,
      disk_free_gb: null,
      active_streams: 0,
      library_shows: 0,
      library_movies: 0,
      library_episodes: 0,
    },
    {
      id: "dvr_2",
      name: "Bedroom",
      host: "192.168.1.11",
      port: 8089,
      connected: false,
      version: null,
      version_compatible: null,
      version_warning: null,
      disk_usage_percent: null,
      disk_total_gb: null,
      disk_free_gb: null,
      active_streams: 0,
      library_shows: 0,
      library_movies: 0,
      library_episodes: 0,
    },
  ];

  function deriveSections(
    dvrs: DVRStatusInfo[],
    selectedDvr: string,
  ): DVRStatusInfo[] {
    if (!dvrs.length) return [];
    if (selectedDvr !== "all") return dvrs.filter((d) => d.id === selectedDvr);
    return dvrs;
  }

  it("returns all DVRs when selectedDvr is 'all'", () => {
    expect(deriveSections(allDvrs, "all")).toHaveLength(2);
  });

  it("returns only the selected DVR when a specific id is chosen", () => {
    const result = deriveSections(allDvrs, "dvr_1");
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("dvr_1");
  });

  it("returns empty array when dvr_status is empty", () => {
    expect(deriveSections([], "all")).toHaveLength(0);
  });

  it("returns empty array when selected DVR id does not match", () => {
    expect(deriveSections(allDvrs, "dvr_999")).toHaveLength(0);
  });
});

describe("status-overview: passes selectedDvr to StatusPanel", () => {
  it("StatusPanel receives selectedDvr prop in status-overview", () => {
    const src = srcFile("../components/status-overview.tsx");
    expect(src).toContain("selectedDvr={selectedDvr}");
  });

  it("uses selected-DVR scoped system-info instead of duplicate per-DVR system polling", () => {
    const src = srcFile("../components/status-overview.tsx");
    expect(src).toContain(
      'selectedDvr !== "all" ? { dvr_id: selectedDvr } : {}',
    );
    expect(src).not.toContain("fetchDvrSystemInfo");
  });

  it("passes server-computed disk severity from system-info into DiskSpaceCard", () => {
    const src = srcFile("../components/status-overview.tsx");
    expect(src).toContain("setDiskServerSeverity(systemInfo.disk_severity ?? undefined)");
    expect(src).toContain("serverSeverity={diskServerSeverity}");
  });

  it("uses a latest refresh ref for interval ticks", () => {
    const src = srcFile("../components/status-overview.tsx");
    expect(src).toContain("latestRefreshRef");
    expect(src).toContain("latestRefreshRef.current()");
    expect(src).toContain("latestRefreshRef.current = refreshDashboardData");
    expect(src).not.toContain(
      "setInterval(() => {\n      refreshDashboardData()",
    );
  });
});
