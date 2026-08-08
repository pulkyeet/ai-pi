import { expect, test } from "@playwright/test";
import { BENCHMARK_LIST, installApiMocks, RUN_ID } from "./fixtures";

test.describe("Homepage — public surface", () => {
  test("loads and shows a readable benchmark report, logged out", async ({ page }) => {
    await installApiMocks(page);
    await page.goto("/");

    await expect(page.getByTestId("benchmark-list")).toBeVisible();
    await expect(page.getByText(BENCHMARK_LIST[0]!.query)).toBeVisible();

    await page.getByText(BENCHMARK_LIST[0]!.query).click();
    await expect(page).toHaveURL(`/r/${RUN_ID}`);
    await expect(page.getByRole("heading", { name: BENCHMARK_LIST[0]!.query })).toBeVisible();
    await expect(page.getByText("Acme Expense")).toBeVisible();
  });

  test("offers a way to run your own idea", async ({ page }) => {
    await installApiMocks(page);
    await page.goto("/");
    await expect(page.getByTestId("run-your-own")).toBeVisible();
  });
});
