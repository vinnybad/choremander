import { expect, test } from "@playwright/test";

import { openDashboard, reseed } from "../pages";

// Start from the committed fixture regardless of which spec files ran first.
test.beforeAll(reseed);

/**
 * A card pointed at the wrong entity has to say so.
 *
 * Every card reads a named attribute off its sensor and falls back to `[]` when
 * it is absent, so a card aimed at a real-but-wrong entity renders the same
 * "nothing here" state as a card that is working and genuinely has nothing to
 * show. That is not a hypothetical: the README documented the approvals card
 * against `sensor.choremander_overview`, which carries no `chore_completions`,
 * and the card answered "All caught up!" while approvals sat pending
 * ([issue #5](https://github.com/vinnybad/choremander/issues/5)). Nobody could
 * see the difference, which is why it survived a fixture fix and a release.
 *
 * The `/parent/misconfigured` view holds every card aimed at a real sensor that
 * is not the one it reads. Each entity there exists, so "Entity not found"
 * cannot be the thing under test - the card has to notice that the data it
 * needs is missing and name the entity that carries it.
 *
 * This view is read-only: nothing here completes a chore or spends a point, so
 * it shares no (child, chore) pair with the other spec files.
 */

const VIEW = "/parent/misconfigured";

/**
 * Each card, the attribute it reads, and the entity that actually carries it.
 * `wrongEntity` is what the fixture aims the card at.
 */
const CARDS = [
  {
    tag: "choremander-approvals-card",
    wrongEntity: "sensor.choremander_overview",
    rightEntity: "sensor.pending_approvals",
    attribute: "chore_completions",
  },
  {
    tag: "choremander-activity-card",
    wrongEntity: "sensor.choremander_overview",
    rightEntity: "sensor.choremander_activity",
    attribute: "events",
  },
  {
    tag: "choremander-streak-card",
    wrongEntity: "sensor.choremander_overview",
    rightEntity: "sensor.choremander_streaks",
    attribute: "current_streak",
  },
  {
    tag: "choremander-child-card",
    wrongEntity: "sensor.pending_approvals",
    rightEntity: "sensor.choremander_overview",
    attribute: "chores",
  },
  {
    tag: "choremander-points-card",
    wrongEntity: "sensor.pending_approvals",
    rightEntity: "sensor.choremander_overview",
    attribute: "children",
  },
  {
    tag: "choremander-reorder-card",
    wrongEntity: "sensor.pending_approvals",
    rightEntity: "sensor.choremander_overview",
    attribute: "chores",
  },
  {
    tag: "choremander-rewards-card",
    wrongEntity: "sensor.pending_approvals",
    rightEntity: "sensor.choremander_overview",
    attribute: "rewards",
  },
] as const;

test.describe("a card aimed at the wrong entity", () => {
  for (const card of CARDS) {
    test(`${card.tag} says so instead of rendering an empty card`, async ({ page }) => {
      await openDashboard(page, VIEW);
      const error = page.locator(card.tag).locator(".config-error");

      await expect(error, `${card.tag} rendered no configuration error`).toBeVisible();

      // The three things someone has to know to fix it: which card is wrong,
      // what it was reading, and where that data actually lives.
      await expect(error).toContainText(card.wrongEntity);
      await expect(error).toContainText(card.attribute);
      await expect(error).toContainText(card.rightEntity);
    });
  }

  test("the streak card is not fooled by a sensor that has children without streaks", async ({
    page,
  }) => {
    // sensor.choremander_overview publishes a `children` list, so a top-level
    // attribute check passes and the card would draw a full strip of zeros for
    // every child. The data it needs is one level down, on each child.
    await openDashboard(page, VIEW);
    const streaks = page.locator("choremander-streak-card");

    await expect(streaks.locator(".config-error")).toBeVisible();
    await expect(
      streaks.locator(".dots"),
      "a misconfigured streak card must not draw a day strip at all",
    ).toHaveCount(0);
  });

  test("a correctly configured card is left alone", async ({ page }) => {
    // The guard must key off the data, not off the entity id: the same card
    // class on the working view has to render normally.
    await openDashboard(page, "/parent");

    await expect(page.locator("choremander-approvals-card .config-error")).toHaveCount(0);
    await expect(page.locator("choremander-approvals-card .empty-state")).toContainText(
      "All caught up!",
    );
  });
});
