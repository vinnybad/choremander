import { expect, test } from "@playwright/test";

import {
  StreakCard,
  approveChore,
  callService,
  computed,
  entityAttributes,
  openDashboard,
  reseed,
} from "../pages";

// Start from the committed fixture regardless of which spec files ran first.
test.beforeAll(reseed);

/**
 * The streak card, and the derivation behind it.
 *
 * Streaks used to be dead: `current_streak` and `best_streak` were stored on
 * every child, published by two sensors, and written by no code path at all, so
 * they could only ever read zero. They are now derived from the approved
 * completion history on each refresh.
 *
 * That makes this file the proof of the fix. It starts from a fixture with no
 * completions, watches both numbers read zero, then does one chore and watches
 * them become one — through the real services, the real sensor and the real
 * card. Multi-day streaks, gaps and the yesterday grace period cannot be staged
 * against a live clock, so those live in tests/unit/test_streaks.py, where the
 * calendar can be handed in directly.
 *
 * The `/parent/streaks` view holds three cards:
 *
 *   "Streaks"          everything at its default, 14 days
 *   "Compact Streaks"  density: compact, 7 days
 *   "Radha Only"       one child, the whole achievement ladder, no avatar
 *
 * State accumulates down this file — Playwright runs it serially.
 */

const RADHA = "child_radha";
const KRISHNA = "child_krishna";
const PIANO = "chore_practice_piano";

const VIEW = "/parent/streaks";

test.describe("streak card", () => {
  test("a family that has done nothing has no streak", async ({ page }) => {
    await openDashboard(page, VIEW);
    const card = StreakCard.byTitle(page, "Streaks");

    await expect(card.children).toHaveCount(2);
    await expect(card.streakCount(RADHA)).toHaveText("0");
    await expect(card.bestStreak(RADHA)).toHaveText("0");
    await expect(card.streakCount(KRISHNA)).toHaveText("0");
  });

  test("every day in the strip is idle, and today is marked as still open", async ({ page }) => {
    await openDashboard(page, VIEW);
    const card = StreakCard.byTitle(page, "Streaks");

    await expect(card.dots(RADHA)).toHaveCount(14);
    await expect(card.activeDots(RADHA)).toHaveCount(0);
    await expect(card.todayDot(RADHA)).toHaveCount(0);
    // A day that has not happened yet is not the same as a day that was
    // missed - there is still time to fix today.
    await expect(card.todayIdleDot(RADHA)).toHaveCount(1);
  });

  test("doing one chore lights today and starts the streak", async ({ page }) => {
    await callService("choremander", "complete_chore", { chore_id: PIANO, child_id: RADHA });
    await approveChore(RADHA, PIANO);

    await openDashboard(page, VIEW);
    const card = StreakCard.byTitle(page, "Streaks");

    await expect(card.streakCount(RADHA)).toHaveText("1");
    await expect(card.bestStreak(RADHA)).toHaveText("1");
    await expect(card.todayDot(RADHA)).toHaveCount(1);
    await expect(card.todayIdleDot(RADHA)).toHaveCount(0);

    // The other child did nothing, so nothing changed for them.
    await expect(card.streakCount(KRISHNA)).toHaveText("0");
    await expect(card.todayIdleDot(KRISHNA)).toHaveCount(1);
  });

  test("the sensor and the card agree about which days were active", async ({ page }) => {
    // The dots are drawn from `active_days`, and the number comes from the same
    // derivation. The fork this idea came from computes the two separately and
    // can render "12 day streak" above three lit dots.
    const attributes = await entityAttributes("sensor.choremander_streaks");
    const radha = attributes.children.find(
      (child: { id: string }) => child.id === RADHA,
    );

    expect(radha.current_streak).toBe(1);
    expect(radha.active_days).toHaveLength(1);

    await openDashboard(page, VIEW);
    const card = StreakCard.byTitle(page, "Streaks");
    await expect(card.streakCount(RADHA)).toHaveText(String(radha.current_streak));

    const lit = await card.child(RADHA).locator(".dot.today, .dot.active").count();
    expect(lit).toBe(radha.active_days.length);
  });

  test("a manual bonus does not fake a chore, so it starts no streak", async ({ page }) => {
    // `_award_points` used to increment the lifetime chore count for any award,
    // which would have made a bonus look like work in the achievements below.
    await callService("choremander", "add_points", {
      child_id: KRISHNA,
      points: 25,
      reason: "Pocket money",
    });

    await openDashboard(page, VIEW);
    const card = StreakCard.byTitle(page, "Streaks");

    await expect(card.streakCount(KRISHNA)).toHaveText("0");
    await expect(card.earnedBadges(KRISHNA)).toHaveCount(0, {
      // 25 points is short of every points milestone, and no chore was done, so
      // nothing should have unlocked. If the lifetime chore counter were still
      // being incremented by a bonus, "First!" would light up here.
    });
  });

  test("the milestone offered is the closest one, not the first on the list", async ({
    page,
  }) => {
    // Krishna now has 25 points and no chores, so he is halfway to "50 earned"
    // and has not started any streak. Offering him a three day streak - the
    // first locked entry in list order - would be the least encouraging of the
    // ten, which is the opposite of what the teaser is for.
    await openDashboard(page, VIEW);
    const card = StreakCard.byTitle(page, "Streaks");

    await expect(card.lockedBadges(KRISHNA)).toHaveCount(1);
    await expect(card.lockedBadges(KRISHNA)).toContainText("50 earned");
  });

  test("the first milestone unlocks and the next one is shown as the target", async ({ page }) => {
    await openDashboard(page, VIEW);
    const card = StreakCard.byTitle(page, "Streaks");

    await expect(card.earnedBadges(RADHA)).toHaveCount(1);
    await expect(card.earnedBadges(RADHA)).toContainText("First!");
    // "next" mode shows what has been earned plus exactly one thing to aim for.
    await expect(card.lockedBadges(RADHA)).toHaveCount(1);
    await expect(card.badges(RADHA)).toHaveCount(2);
  });

  test("the whole ladder can be shown instead", async ({ page }) => {
    await openDashboard(page, VIEW);
    const only = StreakCard.byTitle(page, "Radha Only");

    await expect(only.children).toHaveCount(1);
    await expect(only.badges(RADHA)).toHaveCount(10);
    await expect(only.earnedBadges(RADHA)).toHaveCount(1);
    await expect(only.lockedBadges(RADHA)).toHaveCount(9);
  });

  test("a card scoped to one child leaves the other out entirely", async ({ page }) => {
    await openDashboard(page, VIEW);
    const only = StreakCard.byTitle(page, "Radha Only");

    await expect(only.child(RADHA)).toHaveCount(1);
    await expect(only.child(KRISHNA)).toHaveCount(0);
    await expect(only.avatar(RADHA)).toHaveCount(0, );
  });

  test("the strip length follows the config", async ({ page }) => {
    await openDashboard(page, VIEW);

    await expect(StreakCard.byTitle(page, "Streaks").dots(RADHA)).toHaveCount(14);
    await expect(StreakCard.byDensity(page, "compact").dots(RADHA)).toHaveCount(7);
  });

  test("compact really is smaller", async ({ page }) => {
    await openDashboard(page, VIEW);

    const standard = StreakCard.byTitle(page, "Streaks");
    const compact = StreakCard.byDensity(page, "compact");

    await expect(compact.root).toHaveAttribute("density", "compact");
    await expect(standard.root).not.toHaveAttribute("density", /.*/);

    const standardBox = await standard.child(RADHA).boundingBox();
    const compactBox = await compact.child(RADHA).boundingBox();
    expect(standardBox).not.toBeNull();
    expect(compactBox).not.toBeNull();
    expect(compactBox!.height).toBeLessThan(standardBox!.height);

    expect(await computed(compact.dots(RADHA).first(), "width")).toBe("9px");
    expect(await computed(standard.dots(RADHA).first(), "width")).toBe("12px");
  });

  test("a theme can recolour the strip without touching any config", async ({ page }) => {
    await openDashboard(page, VIEW);
    const dot = StreakCard.byTitle(page, "Streaks").todayDot(RADHA);

    // Home Assistant rewrites the URL to the default view shortly after load,
    // which tears down the execution context under addStyleTag. Retry past it.
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        await page.addStyleTag({ content: ":root { --chm-color-green: rgb(1, 2, 3); }" });
        break;
      } catch {
        await page.waitForTimeout(1000);
      }
    }

    await expect(async () => {
      expect(await computed(dot, "background-color")).toBe("rgb(1, 2, 3)");
    }).toPass({ timeout: 10_000 });
  });
});
