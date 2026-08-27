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
})
