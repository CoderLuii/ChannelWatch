import { test, expect } from "@playwright/test"
import { installApiMocks } from "./support/mock-api"

test.beforeEach(async ({page}) => {
  await page.setViewportSize({width:390,height:844})
  await installApiMocks(page)
  await page.goto("/#overview")
  await expect(page.getByRole("heading",{name:"Dashboard Overview"})).toBeVisible()
})

test("mobile navigation enters, contains and restores focus", async ({page}) => {
  const trigger=page.getByRole("button",{name:"Toggle menu",exact:true})
  await trigger.click()
  const dialog=page.getByRole("dialog",{name:"Primary navigation",exact:true})
  await expect(dialog).toBeVisible()
  await expect.poll(()=>dialog.evaluate(el=>el.contains(document.activeElement))).toBe(true)
  await expect.poll(()=>page.locator("main").evaluate(el=>Boolean(el.closest("[inert]")))).toBe(true)
  for(let i=0;i<12;i++){
    await page.keyboard.press(i%2 ? "Shift+Tab" : "Tab")
    const focus = await dialog.evaluate(el=>({inside:el.contains(document.activeElement), active:document.activeElement?.outerHTML.slice(0,300)}))
    expect(focus.inside, JSON.stringify({i,...focus})).toBe(true)
  }
  await page.screenshot({path:test.info().outputPath("mobile-navigation.png")})
  await page.keyboard.press("Escape")
  await expect(dialog).toHaveCount(0)
  await expect(trigger).toBeFocused()
  expect(await page.locator("main").evaluate(el=>Boolean(el.closest("[inert]")))).toBe(false)
})

test("mobile navigation closes for routes, backdrop and desktop resize", async ({page}) => {
  const trigger=page.getByRole("button",{name:"Toggle menu",exact:true})
  await trigger.click()
  await page.getByRole("dialog",{name:"Primary navigation",exact:true}).getByRole("button",{name:"Settings",exact:true}).click()
  await expect(page).toHaveURL(/#settings/)
  await expect(page.getByRole("dialog",{name:"Primary navigation",exact:true})).toHaveCount(0)
  await expect(trigger).toBeFocused()
  await trigger.click()
  await expect(page.getByRole("dialog",{name:"Primary navigation",exact:true})).toBeVisible()
  await page.locator("div.fixed.inset-0.z-40").click({position:{x:375,y:400}})
  await expect(page.getByRole("dialog",{name:"Primary navigation",exact:true})).toHaveCount(0)
  await trigger.click()
  await page.setViewportSize({width:1280,height:900})
  await expect(page.getByRole("button",{name:"Expand sidebar"})).toBeVisible()
  await expect(page.getByRole("dialog",{name:"Primary navigation",exact:true})).toHaveCount(0)
  expect(await page.locator("main").evaluate(el=>Boolean(el.closest("[inert]")))).toBe(false)
  await page.setViewportSize({width:390,height:844})
  await expect(page.getByRole("dialog",{name:"Primary navigation",exact:true})).toHaveCount(0)
  await trigger.click()
  await expect(page.getByRole("dialog",{name:"Primary navigation",exact:true})).toBeVisible()
})
