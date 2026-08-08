import { expect, test } from "@playwright/test";
import { installApiMocks, RUN_ID } from "./fixtures";

// The demo (masterplan §1: "I can show you the drill down"). This is the
// one test in the whole suite that proves the product's central claim end
// to end through the real stack: a click opens a panel whose highlighted
// text is byte-for-byte the claim's own verbatim quote.
test.describe("Drill-down — the demo", () => {
  test("clicking a cited sentence opens a panel with the exact quote highlighted, logged out", async ({
    page,
  }) => {
    await installApiMocks(page);
    await page.goto(`/r/${RUN_ID}`);
    await expect(page.getByText("Acme Expense")).toBeVisible();

    await page.getByTestId("cited-sentence").first().click();

    const panel = page.getByTestId("source-panel");
    await expect(panel).toBeVisible();
    await expect(panel.getByText("acme-expense.com/pricing")).toBeVisible();
    await expect(panel.getByTestId("grade-badge")).toHaveText("grade A");
    await expect(panel.getByTestId("span-highlight")).toHaveText("$29/mo today");
  });

  test("falls back to quote_context when the source page text has been evicted", async ({ page }) => {
    await installApiMocks(page);
    await page.goto(`/r/${RUN_ID}`);

    await page.getByText("manual receipt categorisation").click();
    const panel = page.getByTestId("source-panel");
    await expect(panel.getByTestId("span-highlight")).toHaveText("categorising them by hand is tedious");
    await expect(panel.getByText(/evicted from cache/)).toBeVisible();
  });

  test("shows the confidence formula, not just the number", async ({ page }) => {
    await installApiMocks(page);
    await page.goto(`/r/${RUN_ID}`);
    await page.getByTestId("cited-sentence").first().click();
    const formula = page.getByTestId("confidence-formula");
    await expect(formula).toContainText("grade A");
    await expect(formula).toContainText("domain");
  });

  test("lets a visitor page to another claim from the same source", async ({ page }) => {
    await installApiMocks(page);
    await page.goto(`/r/${RUN_ID}`);
    await page.getByTestId("cited-sentence").first().click();
    await page.getByText("pricing.free_tier: true").click();
    await expect(page.getByTestId("span-highlight")).toHaveText("free tier available");
  });

  test("is keyboard accessible: reachable, focus-trapped, and escapable", async ({ page }) => {
    await installApiMocks(page);
    await page.goto(`/r/${RUN_ID}`);

    await page.getByTestId("cited-sentence").first().focus();
    await page.keyboard.press("Enter");
    const panel = page.getByTestId("source-panel");
    await expect(panel).toBeVisible();
    await expect(panel).toBeFocused();

    // Tab well past the number of focusable elements in the panel; focus
    // must never escape into the page behind it.
    for (let i = 0; i < 10; i++) {
      await page.keyboard.press("Tab");
      const stillInPanel = await page.evaluate(() => {
        const panelEl = document.querySelector('[data-testid="source-panel"]');
        return !!panelEl && panelEl.contains(document.activeElement);
      });
      expect(stillInPanel).toBe(true);
    }

    await page.keyboard.press("Escape");
    await expect(panel).not.toBeVisible();
  });
});

test.describe("Permalinks", () => {
  test("a direct link to a run loads its completed report", async ({ page }) => {
    await installApiMocks(page);
    await page.goto(`/r/${RUN_ID}`);
    await expect(page.getByRole("heading", { name: "AI expense tracker for freelancers" })).toBeVisible();
    await expect(page.getByText("Coverage 82%")).toBeVisible();
    await expect(page.getByText(/funding data unavailable/)).toBeVisible();
  });
});
