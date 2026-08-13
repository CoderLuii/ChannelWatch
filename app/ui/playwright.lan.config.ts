import { networkInterfaces } from "node:os"

import { defineConfig, devices } from "@playwright/test"

function privateLanIpv4(): string {
  const addresses = Object.values(networkInterfaces()).flatMap((entries) => entries ?? [])
  const address = addresses.find((entry) => {
    if (entry.family !== "IPv4" || entry.internal) return false
    const octets = entry.address.split(".").map(Number)
    return (
      octets[0] === 10 ||
      (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
      (octets[0] === 192 && octets[1] === 168)
    )
  })?.address

  if (!address) {
    throw new Error(
      "LAN browser tests require a non-loopback RFC1918 IPv4 address; none is available on this host.",
    )
  }
  return address
}

const port = 3011
const baseURL = `http://${privateLanIpv4()}:${port}`

export default defineConfig({
  testDir: "./playwright",
  testMatch: /.*lan-reporting\.spec\.ts/,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "lan-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "lan-firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "lan-webkit", use: { ...devices["Desktop Safari"] } },
  ],
  webServer: {
    command: `corepack pnpm build && corepack pnpm exec vite preview --host 0.0.0.0 --port ${port} --strictPort --outDir out`,
    url: baseURL,
    reuseExistingServer: false,
    timeout: 120000,
  },
})
