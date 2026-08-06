import { type Locator, type Page, expect, test } from "@playwright/test";

import { callService, entityAttributes, recordServiceCalls, reseed } from "../pages";

// Start from the committed fixture regardless of which spec files ran first.
test.beforeAll(reseed);

/**
 * The schedule card, and the rota behind it.
 *
 * The fixture ships no schedules at all - every chore is on every day for
 * whoever it is assigned to, which is how the product behaved before
 * scheduling existed and is still what an install looks like on day one. So
 * this file starts by proving the card says exactly that, then builds a rota
 * through the real service and watches the grid follow.
 *
 * Whose turn it is is resolved by the integration, never by the card, so the
 * assertions here are about what a parent sees and what the card writes back.
 * The rotation arithmetic itself - anchors in the future, cycles crossing a
 * year - is unit-tested in tests/unit/test_schedule.py, where the calendar can
 * be handed in directly instead of waited for.
 *
 * The suite pins `timezoneId: "America/Los_Angeles"` to match the container.
 * Every date here is derived from the sensor's own `window_start` rather than
 * written down, because the window moves every Monday.
 *
 * The `/parent/schedule` view holds two cards:
 *
 *   "Weekly Schedule"  everything at its default
 *   "Wrong Sensor"     aimed at sensor.choremander_overview
 *
 * State accumulates down this file - Playwright runs it serially.
 */

const KRISHNA = "child_krishna";
const RADHA = "child_radha";

// Assigned to nobody in the fixture, which the integration reads as "anyone in
// the house" - so a rotation on it has both children to hand out.
const TIDY = "chore_tidy_room";
const BED = "chore_bed";

const VIEW = "/parent/schedule";

/**
 * Open the schedule view and wait for the card's first render.
 *
 * `openDashboard` in ../pages waits on the card types it already knows about,
 * and none of them are on this view.
 */
async function openSchedule(page: Page): Promise<void> {
  await page.goto(VIEW);
  await expect(page.locator("home-assistant")).toBeAttached();
  await expect(page.locator("choremander-schedule-card")).not.toHaveCount(0, {
    timeout: 30_000,
  });
}

class ScheduleCard {
  constructor(readonly root: Locator) {}

  /** Anchored, so one card's title can never also select another's. */
  static byTitle(page: Page, title: string): ScheduleCard {
    return new ScheduleCard(
      page.locator("choremander-schedule-card").filter({
        has: page.locator(".header-title", { hasText: new RegExp(`^\\s*${title}\\s*$`) }),
      }),
    );
  }

  get weekLabel(): Locator {
    return this.root.locator(".week-label");
  }

  get previousWeek(): Locator {
    return this.root.locator(".nav.prev");
  }

  get nextWeek(): Locator {
    return this.root.locator(".nav.next");
  }

  get choreNames(): Locator {
    return this.root.locator(".chore-name");
  }

  choreName(choreId: string): Locator {
    return this.root.locator(`.chore-name[data-chore-id="${choreId}"]`);
  }

  cell(choreId: string, date: string): Locator {
    return this.root.locator(`.cell[data-chore-id="${choreId}"][data-date="${date}"]`);
  }

  /** The initials drawn in one cell - who is on that chore that day. */
  who(choreId: string, date: string): Locator {
    return this.cell(choreId, date).locator(".initials");
  }

  get empty(): Locator {
    return this.root.locator(".empty");
  }

  get error(): Locator {
    return this.root.locator(".error");
  }

  get picker(): Locator {
    return this.root.locator(".add-select");
  }

  get cellDialog(): Locator {
    return this.root.locator(".cell-dialog");
  }

  get ruleDialog(): Locator {
    return this.root.locator(".rule-dialog");
  }
}

/** The published window, straight from the sensor the card reads. */
async function scheduleWindow(): Promise<{ window_start: string; chores: string[] }> {
  const attributes = await entityAttributes("sensor.choremander_schedule");
  return { window_start: attributes.window_start, chores: attributes.chores ?? [] };
}

/**
 * The ISO date `offset` days after the window's Monday.
 *
 * Built from the three numbers rather than parsed, for the same reason the card
 * does it that way: `new Date("2026-08-03")` is UTC midnight and reads as the
 * previous day everywhere west of Greenwich.
 */
function dayOf(windowStart: string, offset: number): string {
  const [year, month, day] = windowStart.split("-").map(Number);
  const date = new Date(year, month - 1, day + offset);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

test.describe("schedule card", () => {
  test("a family that has scheduled nothing is told so, not shown an empty grid", async ({
    page,
  }) => {
    await openSchedule(page);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");

    await expect(card.empty).toContainText("Nothing is on a schedule yet");
    // The message has to say what that means, because "no schedule" is not
    // "nothing happens" - every chore is still on, every day.
    await expect(card.empty).toContainText("every day");
    await expect(card.choreNames).toHaveCount(0);
  });

  test("a chore that has no rule can be brought under one from the card", async ({ page }) => {
    // The recorder listens for the websocket the page opens, so it has to be
    // in place before the page is.
    const service = await recordServiceCalls(page);
    await openSchedule(page);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");

    // Nothing is scheduled, so every chore in the house is on offer here. A
    // family with no way in could never start.
    await expect(card.picker).toContainText("Make Bed");

    await card.picker.selectOption(BED);
    await expect(card.ruleDialog).toBeVisible();
    await card.ruleDialog.locator('.day-toggle[data-day="saturday"]').click();
    await card.ruleDialog.locator(".dialog-button.save").click();
    await expect(card.ruleDialog).toBeHidden();

    expect(service.calls).toEqual([
      { service: "set_chore_schedule", data: { chore_id: BED, due_days: ["saturday"] } },
    ]);

    const { window_start } = await scheduleWindow();
    await expect(card.choreName(BED)).toBeVisible();
    await expect(card.who(BED, dayOf(window_start, 5))).toHaveCount(2);
    await expect(card.cell(BED, dayOf(window_start, 0))).toHaveClass(/empty/);
  });

  test("the grid draws a row per scheduled chore and a column per day", async ({ page }) => {
    await callService("choremander", "set_chore_schedule", {
      chore_id: TIDY,
      due_days: ["monday", "wednesday", "friday"],
    });

    await openSchedule(page);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");
    const { window_start } = await scheduleWindow();

    await expect(card.choreNames).toHaveCount(2);
    await expect(card.choreName(TIDY)).toContainText("Tidy Room");

    for (const offset of [0, 2, 4]) {
      await expect(card.who(TIDY, dayOf(window_start, offset))).toHaveCount(2);
    }
    for (const offset of [1, 3, 5, 6]) {
      await expect(card.cell(TIDY, dayOf(window_start, offset))).toHaveClass(/empty/);
    }
  });

  test("both published weeks are reachable, and only those two", async ({ page }) => {
    await openSchedule(page);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");
    const { window_start } = await scheduleWindow();

    const [, , firstMonday] = window_start.split("-").map(Number);
    await expect(card.weekLabel).toContainText(String(firstMonday));
    await expect(card.previousWeek).toBeDisabled();
    await expect(card.nextWeek).toBeEnabled();

    await card.nextWeek.click();

    // The second week is the same rota drawn against next week's dates, which
    // is the point of publishing real dates rather than a weekly template.
    await expect(card.who(TIDY, dayOf(window_start, 7))).toHaveCount(2);
    await expect(card.previousWeek).toBeEnabled();
    await expect(card.nextWeek).toBeDisabled();
  });

  test("children take turns, and the grid shows whose it is", async ({ page }) => {
    await callService("choremander", "set_chore_schedule", {
      chore_id: TIDY,
      rotation: "occurrence",
      rotation_order: [KRISHNA, RADHA],
      // A Monday, so the first occurrence of the cycle is the first due day of
      // the week however far in the past the window starts.
      rotation_anchor: "2024-01-01",
    });

    await openSchedule(page);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");
    const { window_start } = await scheduleWindow();

    // One child per due day now, not both, and consecutive due days go to
    // different children.
    const monday = card.who(TIDY, dayOf(window_start, 0));
    const wednesday = card.who(TIDY, dayOf(window_start, 2));
    await expect(monday).toHaveCount(1);
    await expect(wednesday).toHaveCount(1);
    expect(await monday.textContent()).not.toBe(await wednesday.textContent());

    // Whatever the card draws has to be what the integration resolved - the
    // card does no rotation arithmetic of its own.
    const attributes = await entityAttributes("sensor.choremander_schedule");
    const index = attributes.chores.indexOf(TIDY);
    const onMonday: number[] = attributes.days[0][String(index)];
    const resolved: string = attributes.children[onMonday[0]];
    await expect(monday).toHaveText(resolved === KRISHNA ? "K" : "R");
  });

  test("a cell can be changed for just one date, and says it was", async ({ page }) => {
    const service = await recordServiceCalls(page);
    await openSchedule(page);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");
    const { window_start } = await scheduleWindow();
    const wednesday = dayOf(window_start, 2);

    await card.cell(TIDY, wednesday).click();
    await expect(card.cellDialog).toBeVisible();

    // Put both children on it, whoever the rotation had picked.
    for (const childId of [KRISHNA, RADHA]) {
      const chip = card.cellDialog.locator(`.chip[data-child-id="${childId}"]`);
      if (!(await chip.getAttribute("class"))?.includes("on")) await chip.click();
    }
    await card.cellDialog.locator('.scope-option[data-scope="date"]').click();
    await card.cellDialog.locator(".dialog-button.save").click();
    await expect(card.cellDialog).toBeHidden();

    // Only the field being edited is sent. A call carrying the whole schedule
    // would clear the rotation set two tests ago, which nothing on screen
    // asked to change.
    expect(service.calls).toHaveLength(1);
    const [call] = service.calls;
    expect(call.service).toBe("set_chore_schedule");
    expect(Object.keys(call.data).sort()).toEqual(["chore_id", "date_overrides"]);
    expect(call.data.chore_id).toBe(TIDY);

    const overrides = call.data.date_overrides as Record<string, string[]>;
    expect(Object.keys(overrides)).toEqual([wednesday]);
    expect([...overrides[wednesday]].sort()).toEqual([KRISHNA, RADHA].sort());

    await expect(card.who(TIDY, wednesday)).toHaveCount(2);
    // The rule is still a rotation; this one day is the exception and stays
    // marked as one.
    await expect(card.cell(TIDY, wednesday)).toHaveClass(/exception/);
    await expect(card.cell(TIDY, wednesday).locator(".marker")).toHaveCount(1);

    // The same weekday next week was not touched.
    await expect(card.who(TIDY, dayOf(window_start, 9))).toHaveCount(1);
  });

  test("or for every one of that weekday, which is a rule rather than an exception", async ({
    page,
  }) => {
    const service = await recordServiceCalls(page);
    await openSchedule(page);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");
    const { window_start } = await scheduleWindow();
    const friday = dayOf(window_start, 4);

    await card.cell(TIDY, friday).click();
    await expect(card.cellDialog).toBeVisible();

    for (const childId of [KRISHNA, RADHA]) {
      const chip = card.cellDialog.locator(`.chip[data-child-id="${childId}"]`);
      const on = (await chip.getAttribute("class"))?.includes("on");
      if (childId === RADHA ? !on : on) await chip.click();
    }
    await card.cellDialog.locator('.scope-option[data-scope="day"]').click();
    await card.cellDialog.locator(".dialog-button.save").click();
    await expect(card.cellDialog).toBeHidden();

    expect(service.calls).toEqual([
      {
        service: "set_chore_schedule",
        data: { chore_id: TIDY, day_overrides: { friday: [RADHA] } },
      },
    ]);

    // Both Fridays in the window move, and neither is an exception: this is
    // now what the rule says.
    await expect(card.who(TIDY, friday)).toHaveText("R");
    await expect(card.who(TIDY, dayOf(window_start, 11))).toHaveText("R");
    await expect(card.cell(TIDY, friday)).not.toHaveClass(/exception/);
  });

  test("a card aimed at the wrong sensor says which one it reads", async ({ page }) => {
    await openSchedule(page);
    const wrong = ScheduleCard.byTitle(page, "Wrong Sensor");

    // The overview sensor carries a `chores` attribute of its own, so a card
    // pointed at it could easily draw an empty grid and look like a family
    // with nothing scheduled.
    await expect(wrong.error).toContainText("sensor.choremander_schedule");
    await expect(wrong.error).toContainText("window_start");
  });
});
