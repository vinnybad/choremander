import { expect, test } from "@playwright/test";

import {
  ActivityCard,
  approveChore,
  callService,
  computed,
  openDashboard,
  reseed,
} from "../pages";

// Start from the committed fixture regardless of which spec files ran first.
test.beforeAll(reseed);

/**
 * The activity card — a timeline of everything that moved a child's balance.
 *
 * Nothing here is pre-seeded. The committed fixture deliberately carries no
 * completions and no claims, so every row these tests assert on is produced by
 * really calling the service that produces it. That is the point: the ledger,
 * the migration that creates it, the sensor that publishes it and the card that
 * draws it are only worth anything as one chain, and this is the only layer
 * where the whole chain runs at once.
 *
 * The `/parent/activity` view holds three cards:
 *
 *   "Activity"          everything at its default
 *   "Compact Activity"  density: compact
 *   "Plain Activity"    filters and relative times off, max_items 5
 *
 * State accumulates down this file — Playwright runs it serially — so each test
 * assumes what the ones above it left behind.
 */

const RADHA = "child_radha";
const KRISHNA = "child_krishna";

// Requires approval, so completing it produces a pending row rather than an
// immediate credit. That two-step is the interesting case.
const PIANO = "chore_practice_piano";

// Needs no approval, so it credits the moment it is completed.
const PLANTS = "chore_water_plants";

const VIEW = "/parent/activity";

test.describe("activity card", () => {
  test("says so plainly when nothing has happened yet", async ({ page }) => {
    await openDashboard(page, VIEW);
    const card = ActivityCard.byTitle(page, "Activity");

    await expect(card.events).toHaveCount(0);
    await expect(card.empty).toContainText("Nothing has happened yet");
  });

  test("a chore awaiting approval shows what is at stake, not a credit", async ({ page }) => {
    await callService("choremander", "complete_chore", { chore_id: PIANO, child_id: RADHA });

    await openDashboard(page, VIEW);
    const card = ActivityCard.byTitle(page, "Activity");
    const row = card.event("Practice Piano");

    await expect(row).toBeVisible();
    await expect(row).toContainText("Radha completed");
    await expect(row).toContainText("waiting for approval");
    await expect(row.locator(".points.at-stake")).toContainText("4");
    await expect(row.locator(".points.up")).toHaveCount(0);
    await expect(card.dayLabels.first()).toHaveText("Today");
  });

  test("approving replaces the pending row rather than adding a second one", async ({ page }) => {
    await approveChore(RADHA, PIANO);

    await openDashboard(page, VIEW);
    const card = ActivityCard.byTitle(page, "Activity");

    await expect(card.event("Practice Piano")).toHaveCount(1);
    const row = card.event("Practice Piano");
    await expect(row).not.toContainText("waiting for approval");
    await expect(row.locator(".points.up")).toContainText("+4");
  });

  test("a chore that needs no approval credits immediately", async ({ page }) => {
    await callService("choremander", "complete_chore", { chore_id: PLANTS, child_id: RADHA });

    await openDashboard(page, VIEW);
    const row = ActivityCard.byTitle(page, "Activity").event("Water the Plants");

    await expect(row.locator(".points.up")).toContainText("+2");
    await expect(row).not.toContainText("waiting for approval");
  });

  test("a bonus and a penalty carry the reason the parent typed", async ({ page }) => {
    await callService("choremander", "add_points", {
      child_id: KRISHNA,
      points: 12,
      reason: "Helped carry the shopping",
    });
    await callService("choremander", "remove_points", {
      child_id: KRISHNA,
      points: 3,
      reason: "Backchat at the table",
    });

    await openDashboard(page, VIEW);
    const card = ActivityCard.byTitle(page, "Activity");

    const bonus = card.event("Helped carry the shopping");
    await expect(bonus).toContainText("Krishna got a bonus");
    await expect(bonus.locator(".points.up")).toContainText("+12");

    const penalty = card.event("Backchat at the table");
    await expect(penalty).toContainText("Krishna had a penalty");
    await expect(penalty.locator(".points.down")).toContainText("-3");
    // The sentence must not name the currency, or a family that renamed Stars
    // would read "lost some Bucks".
    await expect(penalty).not.toContainText("Stars");
  });

  test("the newest event is at the top", async ({ page }) => {
    await openDashboard(page, VIEW);
    const card = ActivityCard.byTitle(page, "Activity");

    // The penalty was the last service call made, so it must lead.
    await expect(card.events.first()).toContainText("Backchat at the table");
  });

  test("each kind of event gets its own colour", async ({ page }) => {
    await openDashboard(page, VIEW);
    const card = ActivityCard.byTitle(page, "Activity");

    await expect(card.event("Water the Plants").locator(".event-icon.good")).toHaveCount(1);
    await expect(card.event("Backchat at the table").locator(".event-icon.bad")).toHaveCount(1);
  });

  test("filtering narrows the feed to one kind of event", async ({ page }) => {
    await openDashboard(page, VIEW);
    const card = ActivityCard.byTitle(page, "Activity");

    const total = await card.events.count();
    expect(total).toBeGreaterThan(2);

    await card.chip("Bonuses").click();
    const bonuses = await card.events.count();
    expect(bonuses).toBeGreaterThan(0);
    expect(bonuses).toBeLessThan(total);
    for (const row of await card.events.all()) {
      await expect(row).toContainText(/bonus|penalty/);
    }

    await card.chip("Chores").click();
    expect(await card.events.count()).toBeGreaterThan(0);
    for (const row of await card.events.all()) {
      await expect(row).toContainText("completed");
    }

    await card.chip("All").click();
    await expect(card.events).toHaveCount(total);
  });

  test("a filter finds matching events even when they are not the newest", async ({ page }) => {
    // The chore rows are now older than the bonus and the penalty. A card that
    // trimmed to max_items before filtering would show an empty Chores tab
    // while the events plainly exist further down. Filtering first is what
    // makes the chip tell the truth rather than merely look busy.
    await openDashboard(page, VIEW);
    const plain = ActivityCard.byTitle(page, "Plain Activity");

    await expect(plain.events).toHaveCount(4);

    const card = ActivityCard.byTitle(page, "Activity");
    await card.chip("Chores").click();
    await expect(card.events).toHaveCount(2);
    await expect(card.event("Practice Piano")).toHaveCount(1);
    await expect(card.event("Water the Plants")).toHaveCount(1);
  });

  test("the plain card shows clock times and no filter chips", async ({ page }) => {
    await openDashboard(page, VIEW);
    const plain = ActivityCard.byTitle(page, "Plain Activity");

    await expect(plain.chips).toHaveCount(0);
    // Relative time is off, so the meta line reads as a clock, never "ago".
    await expect(plain.events.first().locator(".event-meta")).not.toContainText("ago");
  });

  test("compact really is smaller, and its rows stay tappable", async ({ page }) => {
    await openDashboard(page, VIEW);

    const standard = ActivityCard.byTitle(page, "Activity");
    const compact = ActivityCard.byDensity(page, "compact");

    await expect(compact.root).toHaveAttribute("density", "compact");
    await expect(standard.root).not.toHaveAttribute("density", /.*/);

    const standardBox = await standard.events.first().boundingBox();
    const compactBox = await compact.events.first().boundingBox();
    expect(standardBox).not.toBeNull();
    expect(compactBox).not.toBeNull();
    expect(compactBox!.height).toBeLessThan(standardBox!.height);

    // 44px is the smallest comfortable touch target, and the whole row is a
    // surface a thumb travels over.
    expect(compactBox!.height).toBeGreaterThanOrEqual(44);
    expect(await computed(compact.events.first(), "min-height")).toBe("44px");
  });

  test("a theme can resize the card without touching its config", async ({ page }) => {
    await openDashboard(page, VIEW);
    const row = ActivityCard.byTitle(page, "Activity").events.first();

    expect(await computed(row, "min-height")).toBe("56px");

    // Home Assistant rewrites the URL to the default view shortly after load,
    // which tears down the execution context under addStyleTag. Retry past it.
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        await page.addStyleTag({
          content: ":root { --chm-activity-event-min-height: 91px; }",
        });
        break;
      } catch {
        await page.waitForTimeout(1000);
      }
    }

    await expect(async () => {
      expect(await computed(row, "min-height")).toBe("91px");
    }).toPass({ timeout: 10_000 });
  });
});
