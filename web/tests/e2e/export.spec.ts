import { expect, test } from "@playwright/test";
import { installApiMocks, REPORT, RUN_ID } from "./fixtures";

test("JSON export downloads and matches the API payload", async ({ page }) => {
  await installApiMocks(page);
  await page.goto(`/r/${RUN_ID}`);
  await expect(page.getByRole("heading", { name: REPORT.query })).toBeVisible();

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByTestId("export-json").click(),
  ]);

  const path = await download.path();
  const fs = await import("node:fs/promises");
  const content = JSON.parse(await fs.readFile(path!, "utf-8"));
  expect(content).toEqual(REPORT);
  expect(download.suggestedFilename()).toBe(`${RUN_ID}.json`);
});

test("permalink can be copied to the clipboard", async ({ page, context, browserName }) => {
  test.skip(browserName === "webkit", "clipboard-read/write permissions aren't grantable on WebKit");
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await installApiMocks(page);
  await page.goto(`/r/${RUN_ID}`);
  await expect(page.getByRole("heading", { name: REPORT.query })).toBeVisible();

  await page.getByTestId("copy-permalink").click();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain(`/r/${RUN_ID}`);
});
