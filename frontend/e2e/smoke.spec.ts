import { expect, test } from "@playwright/test";

test("add endpoint, run sweep, see verdict", async ({page}) => {
  await page.goto("/endpoints");
  await page.getByLabel(/^name$/i).fill("mock");
  await page.getByLabel(/base url/i).fill("http://localhost:9000/v1");
  await page.getByRole("button",{name:/add endpoint/i}).click();
  await expect(page.getByRole("cell",{name:"mock"}).first()).toBeVisible();
  await page.goto("/"); await page.getByLabel(/^model$/i).fill("mock-model");
  await page.getByText(/advanced/i).click(); await page.getByLabel(/max concurrency/i).fill("8");
  await page.getByLabel(/step dwell/i).fill("1"); await page.getByRole("button",{name:/find sweet spot/i}).click();
  await expect(page).toHaveURL(/\/tests\/\d+/); await expect(page.getByText(/RUNNING/).first()).toBeVisible();
  await expect(page.getByText(/sweet spot/i)).toBeVisible({timeout:90_000});
  await expect(page.locator("table tbody tr").first()).toBeVisible();
  await expect(page.getByRole("heading",{name:"Prompt analysis"})).toBeVisible();
  await expect(page.getByLabel("Heatmap metric")).toBeVisible();
  await page.goto("/history"); await expect(page.getByRole("link",{name:/mock \/ mock-model/i})).toBeVisible();
});
