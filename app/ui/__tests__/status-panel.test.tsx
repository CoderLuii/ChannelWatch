import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

import { StatusPanel } from "@/components/dashboard/status-panel"

describe("StatusPanel no sign-in row", () => {
  const base = {
    dvrStatusList: [],
    activeNotificationServices: 0,
    activeProviderNames: [],
    activeAlertTypes: [],
    coreProcessStatus: "Running",
    channelwatchVersion: "1.6.5",
    currentSettings: null,
    onNavigate: undefined,
  }

  it("shows the row in the first render when the app has no sign-in", () => {
    const html = renderToStaticMarkup(<StatusPanel {...base} noSignIn />)
    expect(html).toContain("status-no-sign-in-row")
    expect(html).toContain("No sign-in set")
  })

  it("leaves the row out otherwise", () => {
    expect(renderToStaticMarkup(<StatusPanel {...base} />)).not.toContain("status-no-sign-in-row")
  })
})
