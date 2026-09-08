import { test, expect } from "@playwright/test"
import { installApiMocks } from "./support/mock-api"
test.use({timezoneId:"America/New_York"})
test("timeline table matches counts, legend state and repeated local hours", async ({page}) => {
  await page.clock.setFixedTime(new Date("2026-11-01T06:10:00Z"))
  await installApiMocks(page)
  const items=[
    {id:"a",type:"watching_channel",timestamp:"2026-11-01T05:05:00Z",title:"Test",message:"Test",icon:"activity"},
    {id:"b",type:"watching_channel",timestamp:"2026-11-01T05:05:00Z",title:"Test",message:"Test",icon:"activity"},
    {id:"c",type:"recording_event",timestamp:"2026-11-01T06:05:00Z",title:"Test",message:"Test",icon:"activity"},
  ]
  await page.route("**/api/activity-history**",route=>route.fulfill({json:{items,total:items.length,offset:0,limit:5000}}))
  await page.goto("/#overview")
  const summary=page.getByText("View activity counts by time",{exact:true})
  await summary.focus();await page.keyboard.press("Enter")
  const table=page.getByRole("table",{name:"24-hour activity by time"})
  await expect(table).toBeVisible()
  await expect(table.locator("tbody tr")).toHaveCount(2)
  await expect(table.locator("tbody td")).toHaveText(["2","0","0","0","1","0"])
  await expect(table).toContainText("EDT")
  await expect(table).toContainText("EST")
  await page.screenshot({path:test.info().outputPath("timeline-data.png"),fullPage:true})
  await page.getByRole("button",{name:/toggle.*recording/i}).click()
  await expect(table.locator("tbody tr")).toHaveCount(1)
  await expect(table.locator("tbody td")).toHaveText(["2","0"])
})
