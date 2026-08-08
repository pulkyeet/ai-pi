import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { installApiMocks, REPORT, RUN_ID } from "./fixtures";

test("report view has no detectable axe violations", async ({ page }) => {
  await installApiMocks(page);
  await page.goto(`/r/${RUN_ID}`);
  await expect(page.getByRole("heading", { name: REPORT.query })).toBeVisible();

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
});

test("homepage has no detectable axe violations", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");
  await expect(page.getByTestId("benchmark-list")).toBeVisible();

  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
});
