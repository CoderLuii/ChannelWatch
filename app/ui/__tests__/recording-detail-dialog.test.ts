import { describe, expect, it } from "vitest"

import { getRecordingArtworkNote } from "@/components/dashboard/recording-detail-dialog"

describe("getRecordingArtworkNote", () => {
  it("returns a subtle note when the recording has no artwork", () => {
    expect(
      getRecordingArtworkNote({
        id: "rec_1",
        title: "Example Recording",
        start_time: Math.floor(Date.now() / 1000) + 3600,
        channel: "Channel 5",
        scheduled_time: "Mon, Apr 20, 8:00 PM",
        image: "",
      }),
    ).toBe("Custom Channels for Channels did not provide artwork for this recording.")
  })

  it("returns null when artwork exists", () => {
    expect(
      getRecordingArtworkNote({
        id: "rec_1",
        title: "Example Recording",
        start_time: Math.floor(Date.now() / 1000) + 3600,
        channel: "Channel 5",
        scheduled_time: "Mon, Apr 20, 8:00 PM",
        image: "https://example.com/poster.jpg",
      }),
    ).toBeNull()
  })

  it("prefers explicit backend exhaustion state over the missing-image heuristic", () => {
    expect(
      getRecordingArtworkNote({
        id: "rec_1",
        title: "Example Recording",
        start_time: Math.floor(Date.now() / 1000) + 3600,
        channel: "Channel 5",
        scheduled_time: "Mon, Apr 20, 8:00 PM",
        image: "",
        artwork_fallback_exhausted: false,
      }),
    ).toBeNull()
  })
})
