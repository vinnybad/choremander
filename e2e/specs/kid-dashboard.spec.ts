import { expect, test } from "@playwright/test";

import {
  COMPLETED,
  ChildCard,
  NOT_COMPLETED,
  openDashboard,
  recordServiceCalls,
  reseed,
} from "../pages";

// Start from the committed fixture regardless of which spec files ran first.
test.beforeAll(reseed);

/**
 * The kid-facing flows. These justify driving a browser: the card applies an
 * optimistic local update on top of the service call, so asserting REST state
 * alone would not prove the UI actually works.
 *
 * Every chore carries daily_limit 1, so a chore can be completed only once per
 * run. Each mutating test therefore owns a DIFFERENT (child, chore) pair -
 * otherwise the second test to touch a chore would silently undo it instead of
 * completing it. Keep this allocation unique when adding tests:
 *
 *   Make Bed / Krishna            -> immediate award
 *   Do Homework / Krishna         -> approval gate
 *   Water the Plants / Radha      -> undo
 *   Brush Teeth (Evening) / Radha -> completed vs outstanding styling
 */

test.describe("kid dashboard", () => {
  test("shows only this child's chores, numbered and in order", async ({ page }) => {
    await openDashboard(page, "/krishna");
    const morning = ChildCard.bySection(page, "Morning Chores");

    await expect(morning.childName).toHaveText("Krishna");
    await expect(morning.myStars).toContainText("25");

    await expect(morning.choreNames).toHaveText([
      "Make Bed",
      "Brush Teeth (Morning)",
      "Feed the Dog",
    ]);

    // "Water the Plants" is assigned to Radha only and must not leak through.
    await expect(morning.root.getByText("Water the Plants")).toHaveCount(0);
  });

  test("tapping a no-approval chore awards its points immediately", async ({ page }) => {
    const traffic = await recordServiceCalls(page);
    await openDashboard(page, "/krishna");
    const morning = ChildCard.bySection(page, "Morning Chores");

    await expect(morning.chore("Make Bed")).toHaveAttribute("title", "Click to complete");
    await morning.complete("Make Bed");

    // Make Bed is worth 2 points and needs no approval: 25 -> 27.
    await expect(morning.myStars).toContainText("27");
    await expect(morning.chore("Make Bed")).toHaveAttribute("title", "Click to undo");

    expect(
      traffic.calls.filter((c) => c.service === "complete_chore"),
      "one tap should send exactly one complete_chore",
    ).toHaveLength(1);
  });

  test("a chore needing approval withholds points and shows them as waiting", async ({
    page,
  }) => {
    await openDashboard(page, "/krishna");
    const evening = ChildCard.bySection(page, "Evening Chores");

    await evening.complete("Do Homework");

    // Do Homework is worth 5 but requires approval, so the balance must not move
    // and the points must surface as "Waiting" instead.
    await expect(evening.waitingStars).toContainText("5");
    await expect(evening.myStars).not.toContainText("30");
  });

  test("tapping a completed chore undoes it", async ({ page }) => {
    await openDashboard(page, "/radha");
    const morning = ChildCard.bySection(page, "Morning Chores");

    await morning.complete("Water the Plants");
    await expect(morning.myStars).toContainText("17");
    await expect(morning.chore("Water the Plants")).toHaveAttribute("title", "Click to undo");

    await morning.chore("Water the Plants").click();

    await expect(morning.chore("Water the Plants")).toHaveAttribute(
      "title",
      "Click to complete",
    );
    await expect(morning.myStars).toContainText("15");
  });

  test("completed and outstanding chores are visually distinguishable", async ({ page }) => {
    await openDashboard(page, "/radha");
    const evening = ChildCard.bySection(page, "Evening Chores");

    const before = await evening.root.locator(COMPLETED).count();

    await evening.complete("Brush Teeth (Evening)");

    await expect(evening.root.locator(COMPLETED)).toHaveCount(before + 1);
    await expect(evening.chore("Brush Teeth (Evening)")).toHaveClass(/completed/);
    await expect(evening.root.locator(NOT_COMPLETED)).not.toHaveCount(0);
  });

  test("the dashboard renders without console errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("console", (msg) => {
      // The cards log heavily at info/debug level by design; only errors matter.
      if (msg.type() === "error") errors.push(msg.text());
    });
    page.on("pageerror", (err) => errors.push(String(err)));

    await openDashboard(page, "/krishna");
    await expect(ChildCard.bySection(page, "Morning Chores").childName).toBeVisible();

    expect(errors, `console errors on the kid dashboard:\n${errors.join("\n")}`).toEqual([]);
  });
});
