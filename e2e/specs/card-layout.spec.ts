import { expect, test } from "@playwright/test";

import {
  ChildCard,
  RewardsCard,
  computed,
  openDashboard,
  recordServiceCalls,
  reseed,
} from "../pages";

// Start from the committed fixture regardless of which spec files ran first.
test.beforeAll(reseed);

/**
 * The `density` option and the visibility toggles (issue #4).
 *
 * Radha's dashboard carries three views that differ only in card config:
 *
 *   /radha          every card at the default density - the untouched card
 *   /radha/compact  the same three cards, card-for-card, with density: compact
 *   /radha/layout   density: cozy, and one card with every toggle turned off
 *
 * Keeping /radha/compact a literal mirror of /radha is what lets the geometry
 * assertions below mean something: the only difference between the two views is
 * the one key under test.
 *
 * Assertions are on computed CSS and bounding boxes rather than on screenshots.
 * The claim being made is "a chore row really is about half as tall, and the tap
 * target really is still finger-sized", and a pixel diff would neither prove
 * that nor survive a font-rendering difference on CI.
 *
 * This file completes "Make Bed" as Radha. Every chore has daily_limit 1, so
 * keep that pair to this file.
 */

/** Home Assistant's own minimum for a touch target, and Apple's HIG figure. */
const MIN_TOUCH_TARGET = 44;

test.describe("card layout options", () => {
  test.describe("the default card is untouched", () => {
    // 1280x800 lands in the (min-width: 1024px) block, where the card's
    // largest sizes live. Pinning the widest case is the strongest version of
    // "nothing moved": if the largest literals still resolve, the smaller ones
    // in the other breakpoints are reached through the same fallbacks.
    test.use({ viewport: { width: 1280, height: 800 } });

    test("carries no layout attributes and keeps its original sizes", async ({ page }) => {
      await openDashboard(page, "/radha");
      const morning = ChildCard.bySection(page, "Morning Chores");

      // Every :host([...]) rule added for this feature keys off one of these.
      // With none of them present, no rule can match and every size falls back
      // to the literal the card shipped with.
      for (const attribute of [
        "density",
        "hide-header",
        "hide-avatar",
        "hide-numbers",
        "hide-chore-points",
        "hide-title",
        "no-animations",
      ]) {
        await expect(
          morning.root,
          `a card with no layout options set must not carry ${attribute}`,
        ).not.toHaveAttribute(attribute);
      }

      const row = morning.chore("Make Bed");
      expect(await computed(row, "padding")).toBe("24px 28px");
      expect(await computed(row, "min-height")).toBe("80px");
      expect(await computed(row, "border-top-width")).toBe("4px");
      expect(await computed(morning.choresContainer, "gap")).toBe("20px");
      // 1.7rem. Home Assistant sets the root font size to 14px, not the browser
      // default of 16px, so rem values resolve against that.
      expect(await computed(morning.chore("Make Bed").locator(".chore-name"), "font-size")).toBe(
        "23.8px",
      );

      const rewards = RewardsCard.on(page);
      expect(await computed(rewards.rows.first(), "padding")).toBe("16px");
      expect(await computed(rewards.costBadges.first(), "min-width")).toBe("70px");
    });

    test("compact roughly halves the height of a chore row", async ({ page }) => {
      await openDashboard(page, "/radha");
      const full = await ChildCard.bySection(page, "Morning Chores").choreHeight("Make Bed");

      await openDashboard(page, "/radha/compact");
      const compact = await ChildCard.byDensity(page, "compact", "Morning Chores").choreHeight(
        "Make Bed",
      );

      expect(compact).toBeLessThan(full * 0.7);
      expect(
        compact,
        "a chore row is the tap target, so it must stay comfortably finger-sized",
      ).toBeGreaterThanOrEqual(MIN_TOUCH_TARGET);
    });
  });

  test.describe("compact", () => {
    // The width the reporters were complaining about.
    test.use({ viewport: { width: 390, height: 844 } });

    test("tightens the card without shrinking the tap targets", async ({ page }) => {
      await openDashboard(page, "/radha/compact");
      const card = ChildCard.byDensity(page, "compact", "Morning Chores");

      const row = card.chore("Make Bed");
      expect(await computed(row, "padding")).toBe("8px 12px");
      expect(await computed(row, "min-height")).toBe("56px");
      expect(await computed(card.choresContainer, "gap")).toBe("8px");

      // The floors. A five-year-old is doing the tapping, so the row and the
      // checkbox stay past the 44px minimum however tight the rest gets.
      expect(await card.choreHeight("Make Bed")).toBeGreaterThanOrEqual(MIN_TOUCH_TARGET);
      const checkbox = await card.checkbox("Make Bed").boundingBox();
      expect(checkbox!.width).toBeGreaterThanOrEqual(40);
      expect(checkbox!.height).toBeGreaterThanOrEqual(40);
    });

    test("keeps the chore row on one line", async ({ page }) => {
      // Below 600px the default layout wraps the row so the checkbox drops onto
      // its own line, which is most of why the card is so tall on a phone.
      // Compact unwinds that, and this is the assertion that says so.
      await openDashboard(page, "/radha/compact");
      const card = ChildCard.byDensity(page, "compact", "Morning Chores");

      const row = await card.chore("Make Bed").boundingBox();
      const checkbox = await card.checkbox("Make Bed").boundingBox();

      const rowCentre = row!.y + row!.height / 2;
      const checkboxCentre = checkbox!.y + checkbox!.height / 2;
      expect(
        Math.abs(rowCentre - checkboxCentre),
        "the checkbox should sit beside the chore name, not below it",
      ).toBeLessThan(8);
    });

    test("applies to the rewards card too", async ({ page }) => {
      await openDashboard(page, "/radha/compact");
      const rewards = RewardsCard.byDensity(page, "compact");

      expect(await computed(rewards.rows.first(), "padding")).toBe("8px 10px");
      expect(await computed(rewards.costBadges.first(), "min-width")).toBe("46px");
    });

    test("still completes a chore", async ({ page }) => {
      // Compact changes CSS only - the rendered markup is identical either way.
      // Driving the flow through the same page object the default-density specs
      // use is how that claim gets checked rather than asserted.
      const traffic = await recordServiceCalls(page);
      await openDashboard(page, "/radha/compact");
      const card = ChildCard.byDensity(page, "compact", "Morning Chores");

      await expect(card.chore("Make Bed")).toHaveAttribute("title", "Click to complete");
      await card.complete("Make Bed");

      // Make Bed is worth 2 and needs no approval: 15 -> 17.
      await expect(card.myStars).toContainText("17");
      await expect(card.chore("Make Bed")).toHaveAttribute("title", "Click to undo");
      expect(traffic.calls.filter((c) => c.service === "complete_chore")).toHaveLength(1);
    });
  });

  test.describe("cozy and the visibility toggles", () => {
    test.use({ viewport: { width: 390, height: 844 } });

    test("cozy sits between the default and compact", async ({ page }) => {
      await openDashboard(page, "/radha/layout");
      const cozy = ChildCard.byDensity(page, "cozy");

      expect(await computed(cozy.chore("Make Bed"), "min-height")).toBe("66px");
      expect(await computed(cozy.choresContainer, "gap")).toBe("12px");
    });

    test("each toggle removes exactly its own part of the card", async ({ page }) => {
      await openDashboard(page, "/radha/layout");
      const stripped = ChildCard.stripped(page);

      await expect(stripped.header).toBeHidden();
      await expect(stripped.avatar).toBeHidden();
      await expect(stripped.sectionTitle).toBeHidden();
      await expect(stripped.numberBadges.first()).toBeHidden();
      await expect(stripped.chorePoints.first()).toBeHidden();

      // The chores themselves are untouched - the toggles hide chrome, not content.
      await expect(stripped.choreNames).toHaveText([
        "Practice Piano",
        "Brush Teeth (Evening)",
        "Tidy Room",
      ]);
    });
  });

  test.describe("custom styling", () => {
    test.use({ viewport: { width: 390, height: 844 } });

    test("a theme-level token reaches into the card", async ({ page }) => {
      await openDashboard(page, "/radha");
      const morning = ChildCard.bySection(page, "Morning Chores");
      const before = await computed(morning.chore("Make Bed"), "min-height");

      // This is the contract a Home Assistant theme or a card-mod rule relies
      // on: the card only ever reads --chm-* tokens, never defines them on
      // :host, so a value set further up the tree inherits through the shadow
      // boundary and beats the fallback.
      await page.addStyleTag({ content: ":root { --chm-chore-min-height: 137px; }" });

      expect(before).toBe("80px");
      await expect
        .poll(() => computed(morning.chore("Make Bed"), "min-height"))
        .toBe("137px");
    });

    test("an explicit density still wins over a theme", async ({ page }) => {
      // Card config is the more specific statement of intent, and it needs to
      // beat an inherited theme value, or a user could not opt one card out of
      // their own theme.
      await openDashboard(page, "/radha/compact");
      await page.addStyleTag({ content: ":root { --chm-chore-min-height: 137px; }" });

      const card = ChildCard.byDensity(page, "compact", "Morning Chores");
      await expect.poll(() => computed(card.chore("Make Bed"), "min-height")).toBe("56px");
    });
  });

  test("an unrecognised density falls back instead of erroring the card", async ({ page }) => {
    // Driving setConfig directly, because the point is what happens to a
    // mis-typed option before anything renders. A thrown error here would
    // replace a child's chore list with an error card.
    await openDashboard(page, "/radha");

    const result = await page.evaluate(() => {
      const el = document.createElement("choremander-child-card") as HTMLElement & {
        setConfig: (c: Record<string, unknown>) => void;
      };
      el.setConfig({
        entity: "sensor.choremander_overview",
        child_id: "child_radha",
        density: "enormous",
      });
      return { density: el.getAttribute("density"), tag: el.tagName.toLowerCase() };
    });

    expect(result.tag).toBe("choremander-child-card");
    expect(result.density, "an unknown density should be coerced to the default").toBeNull();
  });

  test("a default config sets no layout attributes at all", async ({ page }) => {
    await openDashboard(page, "/radha");

    const attributes = await page.evaluate(() => {
      const el = document.createElement("choremander-child-card") as HTMLElement & {
        setConfig: (c: Record<string, unknown>) => void;
      };
      el.setConfig({ entity: "sensor.choremander_overview", child_id: "child_radha" });
      return el.getAttributeNames();
    });

    expect(attributes).toEqual([]);
  });
});
