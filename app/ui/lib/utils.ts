import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatBytes(bytes: number, decimals = 2): string {
  if (bytes === 0) return "0 Bytes"

  const k = 1024
  const sizes = ["Bytes", "KB", "MB", "GB", "TB", "PB"]

  const i = Math.floor(Math.log(bytes) / Math.log(k))

  return Number.parseFloat((bytes / Math.pow(k, i)).toFixed(decimals)) + " " + sizes[i]
}

interface DiskSizeFormatOptions {
  gbDecimals?: number
  tbDecimals?: number
}

export function formatDiskSizeFromGB(
  sizeInGB: number,
  { gbDecimals = 1, tbDecimals = 2 }: DiskSizeFormatOptions = {},
): string {
  if (Math.abs(sizeInGB) >= 1024) {
    return `${(sizeInGB / 1024).toFixed(tbDecimals)} TB`
  }

  return `${sizeInGB.toFixed(gbDecimals)} GB`
}


