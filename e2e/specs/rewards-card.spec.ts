import { expect, test } from "@playwright/test";

import { RewardsCard, grantPoints, openDashboard, recordServiceCalls, reseed } from "../pages";

// Start from the committed fixture regardless of which spec files ran first.
test.beforeAll(reseed);

/**
 * Claiming rewards — the flow reported broken in issue #3.
 *
 * The card used to render the reward icon as decoration with no handler, so
 * nothing on it was clickable. These tests drive the real card because that is
 * the only layer where the defect was visible: every service and sensor
 * involved worked correctly the whole time.
 *
 * Nothing is affordable at the seeded balances (Krishna 25, Radha 15, cheapest
 * reward 50), so tests that need a claimable reward grant points first. The
 * grants accumulate within this file — Playwright runs it serially — so each
 * test below assumes the balance the previous ones left:
 *
 *   1. unaffordable state       Krishna 25   no grant
 *   2. ordering                 Krishna 50   +25
 *   3. cancel                   Krishna 50   unchanged by a cancel
 *   4. claim                    Krishna 50 -> 0
 *   5. unscoped claimant picker Krishna +200, Radha +200
 */

const KRISHNA = "child_krishna";
const RADHA = "child_radha";

// Assigned to Krishna with override_point_value, so it costs a flat 50 and the
// numbers in the confirmation dialog are predictable.
const FIXED = "New Video Game";

// assigned_to is empty, so both children are candidates on an unscoped card.
const SHARED = "30 Min Screen Time";

test.describe("rewards card", () => {
  test("a reward beyond reach is inert and says how far off it is", async ({ page }) => {
    await openDashboard(page, "/krishna");
    const rewards = RewardsCard.on(page);

    // Krishna has 25 points against a 50-point reward.
    await expect(rewards.claimButton(FIXED)).toBeDisabled();
    await expect(rewards.shortfall(FIXED)).toHaveText(/25 more Stars needed/);

    // Dimming alone would still let a frustrated child fire the service call.
    await rewards.claimButton(FIXED).click({ force: true });
    await expect(rewards.dialog).toBeHidden();
  });

  test("claimable rewards sort above the rest", async ({ page }) => {
    await grantPoints(KRISHNA, 25); // 25 -> 50, exactly the cost of FIXED

    await openDashboard(page, "/krishna");
    const rewards = RewardsCard.on(page);

    await expect(rewards.claimButton(FIXED)).toBeEnabled();

    // The one Krishna can afford leads; the rest keep the original
    // jackpot-first, then cheapest-first ordering.
    await expect(rewards.rewardNames).toHaveText([
      FIXED,
      "Family Movie Night",
      "Trip to the Zoo",
      SHARED,
    ]);
  });

  test("cancelling a claim spends nothing", async ({ page }) => {
    // Attach before navigating: the recorder only sees websockets opened after
    // it is installed, and the frontend opens its socket during page load.
    const wire = await recordServiceCalls(page);
    await openDashboard(page, "/krishna");
    const rewards = RewardsCard.on(page);

    await rewards.claimButton(FIXED).click();
    await expect(rewards.dialog).toBeVisible();
    await rewards.cancel.click();

    await expect(rewards.dialog).toBeHidden();
    expect(wire.calls).toEqual([]);
    // Still affordable, so nothing was deducted behind the dialog.
    await expect(rewards.claimButton(FIXED)).toBeEnabled();
  });

  test("confirming a claim charges the child and celebrates", async ({ page }) => {
    const wire = await recordServiceCalls(page);
    await openDashboard(page, "/krishna");
    const rewards = RewardsCard.on(page);

    await rewards.claimButton(FIXED).click();

    // The dialog states the trade before committing to it: 50 on hand, 50 to
    // spend, 0 left.
    await expect(rewards.dialog).toContainText(FIXED);
    await expect(rewards.dialog.locator(".dialog-points-row.cost")).toContainText("50");
    await expect(rewards.dialog.locator(".dialog-points-row.remaining")).toContainText("0");

    await rewards.confirm.click();
    await expect(rewards.celebration).toBeVisible();
    await expect(rewards.celebration).toContainText(FIXED);

    // The scoped card names its own child rather than leaving child_id off,
    // which the service rejects outright.
    expect(wire.calls).toHaveLength(1);
    expect(wire.calls[0].service).toBe("claim_reward");
    expect(wire.calls[0].data).toMatchObject({ child_id: KRISHNA });

    await expect(rewards.celebration).toBeHidden({ timeout: 10_000 });
    // Spent down to nothing, so the same reward is now out of reach.
    await expect(rewards.claimButton(FIXED)).toBeDisabled();
  });

  test("an unscoped card asks who is claiming and charges that child", async ({ page }) => {
    // The parent dashboard configures the rewards card with no child_id. That
    // is the case the first fix for issue #3 got wrong: it sent no child_id at
    // all, and choremander.claim_reward declares it required.
    await grantPoints(KRISHNA, 200);
    await grantPoints(RADHA, 200);

    const wire = await recordServiceCalls(page);
    await openDashboard(page, "/parent");
    const rewards = RewardsCard.byTitle(page, "All Rewards");

    await rewards.claimButton(SHARED).click();

    // Both children can afford it and neither is implied by the config, so the
    // card has to ask rather than guess.
    await expect(rewards.claimant("Krishna")).toBeVisible();
    await expect(rewards.claimant("Radha")).toBeVisible();
    await expect(rewards.confirm).toBeDisabled();

    await rewards.claimant("Radha").click();
    await expect(rewards.confirm).toBeEnabled();
    await rewards.confirm.click();

    await expect(rewards.celebration).toBeVisible();
    expect(wire.calls).toHaveLength(1);
    expect(wire.calls[0].data).toMatchObject({ child_id: RADHA });
  });
});
