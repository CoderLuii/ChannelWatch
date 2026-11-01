import { type ClassValue, clsx } from "clsx"
import { extendTailwindMerge } from "tailwind-merge"

// Teach tailwind-merge the custom type, elevation and radius tokens so they are
// not mistaken for colors or dropped when classes are combined.
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      text: ["2xs", "title", "display"],
      shadow: ["1", "2", "3"],
      radius: ["thumb", "control", "group", "sheet"],
    },
  },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** Hands a blob to the browser as a file. The revoke waits a tick because Safari can cancel a download revoked at once. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

export function formatBytes(bytes: number, decimals = 2): string {
  if (bytes === 0) return "0 Bytes"

  const k = 1024
  const sizes = ["Bytes", "KB", "MB", "GB", "TB", "PB"]

  const i = Math.floor(Math.log(bytes) / Math.log(k))

  const value = Number.parseFloat((bytes / Math.pow(k, i)).toFixed(decimals))
  return `${value} ${value === 1 && i === 0 ? "Byte" : sizes[i]}`
}

/** Below 100 GB one decimal, from 100 GB none, TB two. Base 1024, like the backend. */
export function formatDiskSizeFromGB(sizeInGB: number): string {
  const size = Math.abs(sizeInGB)
  if (size >= 1024) return `${(sizeInGB / 1024).toFixed(2)} TB`
  if (size >= 99.95) return `${sizeInGB.toFixed(0)} GB`
  return `${sizeInGB.toFixed(1)} GB`
}

/** Days give 2d 23h, hours give 23h 59m, anything shorter gives 35m. No seconds: a tile is not a clock. */
export function formatUptimeTile({ days, hours, minutes }: { days: number; hours: number; minutes: number }): string {
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

export function formatCount(value: number): string {
  return value.toLocaleString("en-US")
}

/** Brings a section that just opened into view, moving the page only as far as it has to. */
export function revealDisclosure(element: Element | null | undefined) {
  element?.scrollIntoView({
    block: "nearest",
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
  })
}
