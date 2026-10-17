import React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { Tv } from "lucide-react"
import { describe, expect, it } from "vitest"

import { MetricCard } from "@/components/dashboard/metric-card"

describe("Active Streams presentation", () => {
  it("renders the backend VOD subtitle without replacing its program metadata", () => {
    const subtitle = "bedroom channels watching WYFF News 4 at 6pm"
    const html = renderToStaticMarkup(
      React.createElement(MetricCard, {
        title: "Active Streams",
        icon: Tv,
        value: 1,
        subtitle,
        loading: false,
        hasError: false,
        gradientClasses: "",
        iconBgClass: "",
        iconColorClass: "",
        valueColorClass: "",
        subtitleColorClass: "",
        loadingColorClass: "",
      }),
    )

    expect(html).toContain(subtitle)
    expect(html).not.toContain("Unknown")
  })

  it("shows configured custom artwork as a small decorative image", () => {
    const html = renderToStaticMarkup(
      React.createElement(MetricCard, {
        title: "Active Streams",
        icon: Tv,
        value: 1,
        subtitle: "Living room",
        loading: false,
        hasError: false,
        gradientClasses: "",
        iconBgClass: "",
        iconColorClass: "",
        valueColorClass: "",
        subtitleColorClass: "",
        loadingColorClass: "",
        backgroundImage: "https://example.test/custom-artwork.webp",
      }),
    )

    expect(html).toContain('data-testid="metric-card-artwork"')
    expect(html).toContain('src="https://example.test/custom-artwork.webp"')
    expect(html).toContain('alt=""')
    expect(html).toContain("pr-20")
  })

  it("omits the ChannelWatch logo fallback from content cards", () => {
    const html = renderToStaticMarkup(
      React.createElement(MetricCard, {
        title: "Active Streams",
        icon: Tv,
        value: 0,
        subtitle: "No active streams",
        loading: false,
        hasError: false,
        gradientClasses: "",
        iconBgClass: "",
        iconColorClass: "",
        valueColorClass: "",
        subtitleColorClass: "",
        loadingColorClass: "",
        backgroundImage: "/images/channelwatch-logo.png",
      }),
    )

    expect(html).not.toContain('data-testid="metric-card-artwork"')
    expect(html).not.toContain('src="/images/channelwatch-logo.png"')
    expect(html).not.toContain("pr-20")
  })
})
