"use client"

import { useEffect, useRef, type RefObject } from "react"

/** Rubber band at an edge: the further past it, the less the sheet follows. */
export function rubberband(overshoot: number, dimension: number, constant = 0.55): number {
  return (overshoot * dimension * constant) / (dimension + constant * Math.abs(overshoot))
}

/** Where a flick would come to rest: scroll deceleration, not the release point. */
export function projectedOffset(offset: number, velocityPxPerSec: number, deceleration = 0.998): number {
  return offset + ((velocityPxPerSec / 1000) * deceleration) / (1 - deceleration)
}

export function shouldDismissSheet(offset: number, velocityPxPerSec: number, height: number): boolean {
  return offset > 0 && projectedOffset(offset, velocityPxPerSec) > 0.5 * height
}

interface SheetDragOptions {
  /** Any element inside the sheet. The dialog around it is what moves. */
  anchorRef: RefObject<HTMLElement | null>
  /** Goes through the sheet's normal close path, so its own guards still run. */
  onDismiss: () => void
  /** True while closing would lose work: the sheet rubber-bands back and the owner shows its draft actions. */
  dismissGuard?: () => boolean
  onBlocked?: () => void
  /** Sheets only exist below this width. Above it the same element is a centered dialog. */
  maxWidth?: string
  enabled?: boolean
}

const COMMIT_PX = 10
const VELOCITY_WINDOW_MS = 100
const SAMPLE_LIMIT = 12

type Sample = { y: number; t: number }

function nearestScroller(target: Element | null, sheet: HTMLElement): HTMLElement | null {
  let node = target
  while (node && node !== sheet.parentElement) {
    if (node instanceof HTMLElement && node.scrollHeight > node.clientHeight + 1) {
      const overflow = getComputedStyle(node).overflowY
      if (overflow === "auto" || overflow === "scroll") return node
    }
    node = node.parentElement
  }
  return null
}

function isFormControl(target: Element | null): boolean {
  return Boolean(target?.closest("input, textarea, select, [contenteditable=true], [data-no-sheet-drag], [cmdk-input]"))
}

/**
 * Drag a bottom sheet down to close it. The handle and title row own every touch.
 * The body keeps its scroll, and only starts a drag when it is already at the top.
 * Transform is written straight to the element, so nothing inherits a changing variable.
 */
export function useSheetDrag({ anchorRef, onDismiss, dismissGuard, onBlocked, maxWidth = "639.98px", enabled = true }: SheetDragOptions) {
  // Refs, so a re-render mid-drag (a 30-second refresh, say) cannot tear the listeners down.
  const latest = useRef({ onDismiss, dismissGuard, onBlocked })
  latest.current = { onDismiss, dismissGuard, onBlocked }

  useEffect(() => {
    const sheet = anchorRef.current?.closest<HTMLElement>('[role="dialog"]')
    if (!sheet || !enabled) return

    let startY = 0
    let startX = 0
    let dy = 0
    // Where the sheet was when the finger took it, so a settling sheet is caught where it is.
    let grabbedAt = 0
    let dragging = false
    let tracking = false
    let fromZone = false
    let samples: Sample[] = []
    let settleTimer: ReturnType<typeof setTimeout> | null = null

    const reduced = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const isSheet = () => window.matchMedia(`(max-width: ${maxWidth})`).matches

    const clearInline = () => {
      sheet.style.removeProperty("transition")
      sheet.style.removeProperty("transform")
      sheet.style.removeProperty("box-shadow")
    }

    const begin = (y: number, x: number, zone: boolean) => {
      if (settleTimer) clearTimeout(settleTimer)
      startY = y
      startX = x
      dy = 0
      grabbedAt = 0
      dragging = false
      tracking = true
      fromZone = zone
      samples = [{ y, t: performance.now() }]
    }

    // Past the commit distance the finger owns the sheet, from here and from where it is now.
    const commit = (y: number) => {
      dragging = true
      startY = y
      grabbedAt = new DOMMatrixReadOnly(getComputedStyle(sheet).transform).m42
    }

    const follow = (y: number) => {
      const now = performance.now()
      samples.push({ y, t: now })
      if (samples.length > SAMPLE_LIMIT) samples.shift()
      dy = y - startY + grabbedAt
      const height = sheet.getBoundingClientRect().height
      sheet.style.transition = "none"
      if (dy >= 0) {
        sheet.style.transform = `translateY(${dy}px)`
        sheet.style.removeProperty("box-shadow")
      } else {
        const lifted = rubberband(-dy, height)
        sheet.style.transform = `translateY(${-lifted}px)`
        // The sheet sits on the screen's bottom edge, so lifting it must not show the page below.
        sheet.style.boxShadow = `0 ${Math.ceil(lifted) + 2}px 0 0 hsl(var(--popover))`
      }
    }

    const velocity = () => {
      const now = performance.now()
      const recent = samples.filter((sample) => now - sample.t <= VELOCITY_WINDOW_MS)
      if (recent.length < 2) return 0
      const first = recent[0]
      const last = recent[recent.length - 1]
      const elapsed = last.t - first.t
      return elapsed > 0 ? ((last.y - first.y) / elapsed) * 1000 : 0
    }

    const settleBack = () => {
      if (reduced()) {
        clearInline()
        return
      }
      sheet.style.transition = "transform var(--motion-slow) var(--ease-drawer)"
      sheet.style.removeProperty("transform")
      // The fill stays until clearInline, so the way back down shows no page under the sheet either.
      settleTimer = setTimeout(clearInline, 360)
    }

    const release = () => {
      if (!tracking) return
      tracking = false
      if (!dragging) return
      dragging = false
      const height = sheet.getBoundingClientRect().height
      if (!shouldDismissSheet(dy, velocity(), height)) {
        settleBack()
        return
      }
      if (latest.current.dismissGuard?.()) {
        settleBack()
        latest.current.onBlocked?.()
        return
      }
      // Leave from where the finger let go. The sheet's closed state supplies the target.
      sheet.style.transition = reduced() ? "none" : "transform var(--motion-base) var(--ease-drawer)"
      sheet.style.removeProperty("transform")
      sheet.style.removeProperty("box-shadow")
      latest.current.onDismiss()
    }

    const onPointerDown = (event: PointerEvent) => {
      if (tracking || !event.isPrimary || event.button !== 0 || !isSheet()) return
      const target = event.target as Element | null
      if (!target?.closest("[data-sheet-drag]") || target.closest("button, a")) return
      begin(event.clientY, event.clientX, true)
      sheet.setPointerCapture?.(event.pointerId)
    }

    const onPointerMove = (event: PointerEvent) => {
      if (!tracking || !fromZone || !event.isPrimary) return
      if (!dragging) {
        if (Math.abs(event.clientY - startY) < COMMIT_PX) return
        commit(event.clientY)
      }
      follow(event.clientY)
    }

    const onPointerEnd = (event: PointerEvent) => {
      if (fromZone && event.isPrimary) release()
    }

    const onTouchStart = (event: TouchEvent) => {
      if (tracking && event.touches.length > 1) return
      if (event.touches.length !== 1 || !isSheet()) return
      const target = event.target as Element | null
      if (target?.closest("[data-sheet-drag]") || isFormControl(target)) return
      const scroller = nearestScroller(target, sheet)
      if (scroller && scroller.scrollTop > 0) return
      begin(event.touches[0].clientY, event.touches[0].clientX, false)
    }

    const onTouchMove = (event: TouchEvent) => {
      if (!tracking || fromZone || event.touches.length !== 1) return
      const touch = event.touches[0]
      if (!dragging) {
        const movedY = touch.clientY - startY
        const movedX = touch.clientX - startX
        if (Math.abs(movedY) < COMMIT_PX && Math.abs(movedX) < COMMIT_PX) return
        // Up is a scroll, sideways belongs to whatever is under the finger.
        if (movedY <= 0 || Math.abs(movedX) > Math.abs(movedY)) {
          tracking = false
          return
        }
        commit(touch.clientY)
      }
      if (event.cancelable) event.preventDefault()
      follow(touch.clientY)
    }

    const onTouchEnd = () => {
      if (!fromZone) release()
    }

    sheet.addEventListener("pointerdown", onPointerDown)
    sheet.addEventListener("pointermove", onPointerMove)
    sheet.addEventListener("pointerup", onPointerEnd)
    sheet.addEventListener("pointercancel", onPointerEnd)
    sheet.addEventListener("touchstart", onTouchStart, { passive: true })
    sheet.addEventListener("touchmove", onTouchMove, { passive: false })
    sheet.addEventListener("touchend", onTouchEnd)
    sheet.addEventListener("touchcancel", onTouchEnd)

    return () => {
      if (settleTimer) clearTimeout(settleTimer)
      sheet.removeEventListener("pointerdown", onPointerDown)
      sheet.removeEventListener("pointermove", onPointerMove)
      sheet.removeEventListener("pointerup", onPointerEnd)
      sheet.removeEventListener("pointercancel", onPointerEnd)
      sheet.removeEventListener("touchstart", onTouchStart)
      sheet.removeEventListener("touchmove", onTouchMove)
      sheet.removeEventListener("touchend", onTouchEnd)
      sheet.removeEventListener("touchcancel", onTouchEnd)
    }
  }, [anchorRef, enabled, maxWidth])
}
