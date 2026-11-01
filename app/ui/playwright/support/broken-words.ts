import type { Locator, Page } from "@playwright/test"

export interface BrokenWord {
  word: string
  lines: number
  wordWidth: number
  boxWidth: number
  parent: string
}

/**
 * Finds words the browser split across lines. Ordinary words must stay whole: any box they land in
 * should be wide enough to hold them, so a split means the box is too narrow. A word of more than 24
 * letters is allowed to break, and so is a path, address or time zone wider than its box, which has
 * nowhere else to wrap.
 */
export const findBrokenWords = (scope: Locator) =>
  scope.evaluate((element) => {
    const results: BrokenWord[] = []
    const range = document.createRange()
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const text = node.textContent ?? ""
      const parent = node.parentElement
      if (!parent || !text.trim() || parent.closest(".sr-only, script, style, [aria-hidden='true']")) continue
      const style = getComputedStyle(parent)
      if (style.visibility === "hidden" || style.display === "none") continue
      let box: HTMLElement = parent
      while (getComputedStyle(box).display === "inline" && box.parentElement) box = box.parentElement
      const boxStyle = getComputedStyle(box)
      const boxWidth = box.clientWidth - parseFloat(boxStyle.paddingLeft) - parseFloat(boxStyle.paddingRight)
      const measure = (start: number, end: number) => {
        range.setStart(node, start)
        range.setEnd(node, end)
        const rects = [...range.getClientRects()].filter((rect) => rect.width > 0 && rect.height > 0)
        return { width: rects.reduce((sum, rect) => sum + rect.width, 0), lines: new Set(rects.map((rect) => Math.round(rect.bottom / 4))).size }
      }
      for (const token of text.matchAll(/\S+/g)) {
        const isCompound = /[/.:_@\d]/.test(token[0])
        if (isCompound && measure(token.index, token.index + token[0].length).width > boxWidth) continue
        for (const match of token[0].matchAll(/[^/.\-\u2010\u2013\u2014]+/g)) {
          if (match[0].length > 24) continue
          const start = token.index + match.index
          const { width, lines } = measure(start, start + match[0].length)
          if (lines < 2) continue
          results.push({
            word: match[0],
            lines,
            wordWidth: Math.round(width),
            boxWidth: Math.round(boxWidth),
            parent: `${box.tagName.toLowerCase()}.${String(box.className).slice(0, 80)}`,
          })
        }
      }
    }
    return results
  })

/** Finds visible text on top of a labelled button it does not belong to. A tile's empty cover button is fine. */
export const findTextOverButtons = (scope: Locator) =>
  scope.evaluate((element) => {
    const results: { text: string; button: string }[] = []
    const buttons = [...element.querySelectorAll("button")].filter((button) => button.getBoundingClientRect().width > 0 && button.textContent?.trim())
    const range = document.createRange()
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const parent = node.parentElement
      if (!parent || !node.textContent?.trim() || parent.closest(".sr-only, script, style, [aria-hidden='true']")) continue
      range.selectNodeContents(node)
      for (const rect of range.getClientRects()) {
        if (rect.width === 0 || rect.height === 0) continue
        for (const button of buttons) {
          if (button.contains(parent)) continue
          const box = button.getBoundingClientRect()
          const overlapX = Math.min(rect.right, box.right) - Math.max(rect.left, box.left)
          const overlapY = Math.min(rect.bottom, box.bottom) - Math.max(rect.top, box.top)
          if (overlapX > 2 && overlapY > 2) results.push({ text: node.textContent.trim().slice(0, 40), button: (button.textContent ?? "").trim().slice(0, 40) })
        }
      }
    }
    return results
  })

/** The same 200% text the audits use: root font size, because headless browsers have no text-size setting. */
export const applyDoubleText = (page: Page) =>
  page.addInitScript(() => {
    document.addEventListener("DOMContentLoaded", () => {
      const style = document.createElement("style")
      style.textContent = "html{font-size:200% !important}"
      document.head.appendChild(style)
    })
  })
