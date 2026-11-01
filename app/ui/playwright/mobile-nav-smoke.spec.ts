import { test, expect } from "@playwright/test"
import { installApiMocks } from "./support/mock-api"

test.beforeEach(async ({page}) => {
  await page.setViewportSize({width:390,height:844})
  await installApiMocks(page)
  await page.goto("/#overview")
  await expect(page.getByRole("heading",{name:"Home",exact:true})).toBeVisible()
})

test("More sheet enters, contains and restores focus", async ({page}) => {
  const trigger=page.getByRole("button",{name:"More",exact:true})
  await trigger.click()
  const dialog=page.getByRole("dialog",{name:"More",exact:true})
  await expect(dialog).toBeVisible()
  await expect.poll(()=>dialog.evaluate(el=>el.contains(document.activeElement))).toBe(true)
  for(let i=0;i<12;i++){
    await page.keyboard.press(i%2 ? "Shift+Tab" : "Tab")
    const focus = await dialog.evaluate(el=>({inside:el.contains(document.activeElement), active:document.activeElement?.outerHTML.slice(0,300)}))
    expect(focus.inside, JSON.stringify({i,...focus})).toBe(true)
  }
  await page.screenshot({path:test.info().outputPath("mobile-navigation.png")})
  await page.keyboard.press("Escape")
  await expect(dialog).toHaveCount(0)
  await expect(trigger).toBeFocused()
})

test("More sheet closes for routes and the backdrop", async ({page}) => {
  const trigger=page.getByRole("button",{name:"More",exact:true})
  const dialog=page.getByRole("dialog",{name:"More",exact:true})
  await trigger.click()
  await dialog.getByRole("button",{name:"About",exact:true}).click()
  await expect(page).toHaveURL(/#about/)
  await expect(dialog).toHaveCount(0)
  await trigger.click()
  await expect(dialog).toBeVisible()
  await page.locator("div.fixed.inset-0.z-40").click({position:{x:195,y:100}})
  await expect(dialog).toHaveCount(0)
  await expect(trigger).toBeFocused()
})

test("filters sit behind pills and one shared sheet", async ({page}) => {
  const queries: URL[] = []
  await page.route("**/api/activity-history**", route => {
    queries.push(new URL(route.request().url()))
    return route.fallback()
  })
  await page.goto("/#watch-history")
  await page.getByRole("button", {name:/Filter activity by exact client/}).click()
  const sheet = page.getByTestId("filter-sheet")
  await expect(sheet).toBeVisible()
  await sheet.getByRole("option", {name:/Living Room Apple TV/}).click()
  await expect.poll(() => queries.some(url => url.searchParams.get("client") === "Living Room Apple TV")).toBe(true)
  await sheet.getByRole("button", {name:"Show results",exact:true}).click()
  await expect(sheet).toHaveCount(0)
  await expect(page.getByRole("button", {name:/Living Room Apple TV\. Filter activity by exact client/})).toBeVisible()
})

test("a swipe down closes the More sheet and a short slow one does not", async ({browser, browserName}) => {
  test.skip(browserName !== "chromium", "Driven through CDP touch events.")
  const context = await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true})
  const page = await context.newPage()
  await installApiMocks(page)
  await page.goto("/#overview")
  await page.getByRole("button",{name:"More",exact:true}).click()
  const dialog = page.getByRole("dialog",{name:"More",exact:true})
  await expect(dialog).toBeVisible()
  const handle = page.getByTestId("sheet-handle")
  const box = (await handle.boundingBox())!
  const cdp = await context.newCDPSession(page)
  const drag = async (dy: number, ms: number) => {
    const x = box.x + box.width / 2, y = box.y + box.height / 2
    await cdp.send("Input.dispatchTouchEvent",{type:"touchStart",touchPoints:[{x,y}]})
    for (let i = 1; i <= 8; i++) {
      await cdp.send("Input.dispatchTouchEvent",{type:"touchMove",touchPoints:[{x,y:y + dy * i / 8}]})
      await page.waitForTimeout(ms / 8)
    }
    await cdp.send("Input.dispatchTouchEvent",{type:"touchEnd",touchPoints:[]})
  }
  await drag(40, 500)
  await page.waitForTimeout(500)
  await expect(dialog).toBeVisible()
  await drag(220, 400)
  await expect(dialog).toHaveCount(0)
  await context.close()
})

test("Home keeps its scroll and reads nothing while another tab is open", async ({page}) => {
  await expect(page.getByTestId("health-verdict")).not.toHaveAttribute("data-tone","pending")
  await page.evaluate(() => window.scrollTo({top:500,behavior:"instant"}))
  let reads = 0
  page.on("request", request => { if (request.url().includes("/api/system-info")) reads++ })
  await page.getByRole("button",{name:/^History|Watch History/}).first().click()
  await expect(page.getByRole("heading",{name:"Watch History"})).toBeVisible()
  expect(await page.evaluate(() => window.scrollY)).toBe(0)
  expect(reads).toBe(0)
  await page.getByRole("button",{name:/^Home/}).first().click()
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(500)
  await expect.poll(() => reads).toBeGreaterThanOrEqual(1)
})
