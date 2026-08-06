import { expect, test } from "@playwright/test";

import {
  COMPLETED,
  ChildCard,
  NOT_COMPLETED,
  callService,
  mountChildCard,
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
 *
 * The day-off test completes nothing; it takes Meera's three scheduled chores
 * off today, which no other test in this file reads.
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

  test("a child the schedule has nothing on today is told it is a day off", async ({
    page,
  }) => {
    // Meera has no dashboard of her own in the fixture, so the card is built on
    // the page against the same hass a view would hand it. She is the only
    // child in the house whose chores all carry a rule, which is what makes a
    // day off reachable at all: a chore with no rule is on every day.
    await openDashboard(page, "/krishna");

    // The browser's clock is pinned to the container's timezone, which is the
    // one the integration resolves a schedule in. Asking Node instead would ask
    // the machine running the test what day it is.
    const today = await page.evaluate(() => new Date().toLocaleDateString("en-CA"));

    // Her real day off is Sunday. Taking today off for each of her three chores
    // is what makes this test say the same thing on a Tuesday, and a date
    // override leaves the weekly pattern behind it exactly as it was.
    for (const choreId of ["chore_fish", "chore_bins", "chore_dishes"]) {
      await callService("choremander", "set_chore_schedule", {
        chore_id: choreId,
        date_overrides: { [today]: [] },
      });
    }

    const card = await mountChildCard(page, {
      entity: "sensor.choremander_overview",
      child_id: "child_meera",
      time_category: "morning",
    });

    // Emphatically not "All Done!" - she finished nothing. Congratulating a
    // child for a day they were never on cheapens the days they were.
    await expect(card.emptyState).toContainText("Day off");
    await expect(card.emptyState).toContainText("Nothing scheduled today");
    await expect(card.choreNames).toHaveCount(0);
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
