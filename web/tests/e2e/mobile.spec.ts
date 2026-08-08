import { expect, test } from "@playwright/test";
import { installApiMocks, RUN_ID } from "./fixtures";

test("drill-down panel is a full-screen sheet on a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installApiMocks(page);
  await page.goto(`/r/${RUN_ID}`);

  await page.getByTestId("cited-sentence").first().click();
  const panel = page.getByTestId("source-panel");
  await expect(panel).toBeVisible();

  const box = await panel.boundingBox();
  expect(box?.width).toBeGreaterThanOrEqual(380);
});
