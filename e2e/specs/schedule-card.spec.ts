import { expect, test } from "@playwright/test";

import {
  ScheduleCard,
  callService,
  entityAttributes,
  openDashboard,
  recordServiceCalls,
  reseed,
} from "../pages";

// Start from the committed fixture regardless of which spec files ran first.
test.beforeAll(reseed);

/**
 * The schedule card, and the rota behind it.
 *
 * The fixture ships three rules and nothing else: Feed the Fish is Meera's six
 * mornings a week, Take Out the Bins is hers on Mondays and Thursdays, and Wash
 * the Dishes falls on Wednesdays and Saturdays with Krishna and Meera taking
 * turns. The other eight chores carry no rule at all, which the product reads
 * as "on every day, for whoever it is assigned to" - the way it behaved before
 * scheduling existed and still what every installed household looks like.
 *
 * Whose turn it is is resolved by the integration, never by the card, so the
 * assertions here are about what a parent sees and what the card writes back.
 * The rotation arithmetic itself - anchors in the future, cycles crossing a
 * year - is unit-tested in tests/unit/test_schedule.py, where the calendar can
 * be handed in directly instead of waited for.
 *
 * Every test arranges the whole rota it needs through `onlySeededRules` or
 * `nothingScheduled` before it opens a page, and never inherits one from the
 * test above. Schedules are stored per chore and survive a page load, so a file
 * that let them accumulate would pass in order and fail under `-g`, which is
 * how a single failure is usually looked at.
 *
 * The suite pins `timezoneId: "America/Los_Angeles"` to match the container.
 * Every date here is derived from the sensor's own `window_start` rather than
 * written down, because the window moves every Monday. Only one week of the
 * fortnight is in the DOM at a time, so an assertion about a day past offset 6
 * has to click through to the second week first.
 *
 * The `/parent/schedule` view holds two cards:
 *
 *   "Weekly Schedule"  everything at its default
 *   "Wrong Sensor"     aimed at sensor.choremander_overview
 */

const KRISHNA = "child_krishna";
const RADHA = "child_radha";
const MEERA = "child_meera";

// The three chores the fixture puts on a schedule.
const FISH = "chore_fish";
const BINS = "chore_bins";
const DISHES = "chore_dishes";

// Carry no rule in the fixture, and are what these tests put on one. Both are
// assigned to Krishna and Radha, so a rotation on either has two children to
// hand out and an unrotated day draws two sets of initials.
const TIDY = "chore_tidy_room";
const BED = "chore_bed";

const VIEW = "/parent/schedule";

/** Every field of a rule at the value that means "no rule at all". */
const NO_RULE = {
  due_days: [],
  rotation: "none",
  rotation_order: [],
  rotation_anchor: "",
  day_overrides: {},
  date_overrides: {},
};

/**
 * The rules the fixture ships, written out so they can be put back.
 *
 * Every field is sent, including the ones already at their default: the service
 * replaces a field it is given and leaves out one it is not, so restoring a
 * rota from an unknown starting point means naming all of it.
 */
const SEEDED_RULES: Record<string, Record<string, unknown>> = {
  [FISH]: {
    ...NO_RULE,
    due_days: ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"],
  },
  [BINS]: { ...NO_RULE, due_days: ["monday", "thursday"] },
  [DISHES]: {
    ...NO_RULE,
    due_days: ["wednesday", "saturday"],
    rotation: "occurrence",
    rotation_order: [KRISHNA, MEERA],
  },
};

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

async function setSchedule(choreId: string, rule: Record<string, unknown>): Promise<void> {
  await callService("choremander", "set_chore_schedule", { chore_id: choreId, ...rule });
}

/** Take every chore this file touches back off the schedule. */
async function nothingScheduled(): Promise<void> {
  for (const choreId of [FISH, BINS, DISHES, TIDY, BED]) {
    await setSchedule(choreId, NO_RULE);
  }
}

/** The fixture's rota exactly, whatever an earlier test left behind. */
async function onlySeededRules(): Promise<void> {
  for (const choreId of [TIDY, BED]) {
    await setSchedule(choreId, NO_RULE);
  }
  for (const [choreId, rule] of Object.entries(SEEDED_RULES)) {
    await setSchedule(choreId, rule);
  }
}

/**
 * Tidy Room, due three days a week with the children taking turns.
 *
 * The starting point for the cell-editing tests: a rotation puts exactly one
 * child in a cell, which is what makes "the parent put both of them on it"
 * visible as a change rather than as a coincidence.
 */
const TIDY_ROTA = {
  ...NO_RULE,
  due_days: ["monday", "wednesday", "friday"],
  rotation: "occurrence",
  rotation_order: [KRISHNA, RADHA],
  // A Monday, so the first occurrence of the cycle is the first due day of the
  // week however far in the past the window starts.
  rotation_anchor: "2024-01-01",
};

test.describe("schedule card", () => {
  test("the seeded rota is drawn from the sensor, not re-derived", async ({ page }) => {
    await onlySeededRules();
    await openDashboard(page, VIEW);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");
    const { window_start } = await scheduleWindow();

    // Three rows, because eight of the eleven seeded chores carry no rule and a
    // chore with no rule is left out of the payload entirely. Absent is not
    // off - those eight are still on every day, which is what the picker below
    // offers to change.
    await expect(card.choreNames).toHaveCount(3);
    await expect(card.choreName(FISH)).toContainText("Feed the Fish");
    await expect(card.choreName(BINS)).toContainText("Take Out the Bins");
    await expect(card.choreName(DISHES)).toContainText("Wash the Dishes");

    // Meera's own chores, so no rotation to do: she is on every due day and
    // nobody is on them the rest of the week.
    for (const offset of [0, 1, 2, 3, 4, 5]) {
      await expect(card.who(FISH, dayOf(window_start, offset))).toHaveText("M");
    }
    await expect(card.cell(FISH, dayOf(window_start, 6))).toHaveClass(/nobody/);
    await expect(card.who(BINS, dayOf(window_start, 0))).toHaveText("M");
    await expect(card.who(BINS, dayOf(window_start, 3))).toHaveText("M");
    await expect(card.cell(BINS, dayOf(window_start, 1))).toHaveClass(/nobody/);

    // Wash the Dishes alternates by occurrence from the default anchor, a
    // Monday, so every Wednesday is the even occurrence and every Saturday the
    // odd one.
    await expect(card.who(DISHES, dayOf(window_start, 2))).toHaveText("K");
    await expect(card.who(DISHES, dayOf(window_start, 5))).toHaveText("M");
    await expect(card.cell(DISHES, dayOf(window_start, 0))).toHaveClass(/nobody/);

    // The turns are the same fortnight after fortnight rather than depending on
    // when the window happens to start - which is only checkable on the second
    // week, and only one week is in the DOM at a time.
    await card.nextWeek.click();
    await expect(card.who(DISHES, dayOf(window_start, 9))).toHaveText("K");
    await expect(card.who(DISHES, dayOf(window_start, 12))).toHaveText("M");

    // A chore already on the schedule has a row; offering it again would let a
    // parent replace the rule they can see without meaning to. A chore with no
    // rule is offered, because that is the only way one ever gets a rule.
    await expect(card.picker).not.toContainText("Feed the Fish");
    await expect(card.picker).toContainText("Water the Plants");
    await expect(card.picker).toContainText("Tidy Room");
  });

  test("a family that has scheduled nothing is told so, not shown an empty grid", async ({
    page,
  }) => {
    // The state every household starts in.
    await nothingScheduled();

    await openDashboard(page, VIEW);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");

    await expect(card.emptyState).toContainText("Nothing is on a schedule yet");
    // The message has to say what that means, because "no schedule" is not
    // "nothing happens" - every chore is still on, every day.
    await expect(card.emptyState).toContainText("every day");
    await expect(card.choreNames).toHaveCount(0);
  });

  test("a chore that has no rule can be brought under one from the card", async ({ page }) => {
    await nothingScheduled();

    // The recorder listens for the websocket the page opens, so it has to be
    // in place before the page is.
    const service = await recordServiceCalls(page);
    await openDashboard(page, VIEW);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");

    // Nothing is scheduled, so every chore in the house is on offer here. A
    // family with no way in could never start.
    await expect(card.picker).toContainText("Make Bed");

    await card.picker.selectOption(BED);
    await expect(card.ruleDialog).toBeVisible();
    await card.ruleDialog.locator('.day-toggle[data-day="saturday"]').click();
    await card.ruleDialog.locator(".dialog-button.save").click();
    await expect(card.ruleDialog).toBeHidden();

    // The turns and the anchor go with it even though the parent touched
    // neither. A chore the sensor does not carry is one the card cannot see the
    // stored rule for, and an order left behind the last time this chore was
    // taken off the schedule would otherwise revive itself the moment it goes
    // back on.
    expect(service.calls).toEqual([
      {
        service: "set_chore_schedule",
        data: {
          chore_id: BED,
          due_days: ["saturday"],
          rotation_order: [],
          rotation_anchor: "",
        },
      },
    ]);

    const { window_start } = await scheduleWindow();
    await expect(card.choreName(BED)).toBeVisible();
    await expect(card.who(BED, dayOf(window_start, 5))).toHaveCount(2);
    await expect(card.cell(BED, dayOf(window_start, 0))).toHaveClass(/nobody/);
  });

  test("the grid draws a row per scheduled chore and a column per day", async ({ page }) => {
    await nothingScheduled();
    await setSchedule(BED, { ...NO_RULE, due_days: ["saturday"] });
    await setSchedule(TIDY, { ...NO_RULE, due_days: ["monday", "wednesday", "friday"] });

    await openDashboard(page, VIEW);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");
    const { window_start } = await scheduleWindow();

    await expect(card.choreNames).toHaveCount(2);
    await expect(card.dayHeads).toHaveCount(7);
    await expect(card.choreName(TIDY)).toContainText("Tidy Room");

    for (const offset of [0, 2, 4]) {
      await expect(card.who(TIDY, dayOf(window_start, offset))).toHaveCount(2);
    }
    for (const offset of [1, 3, 5, 6]) {
      await expect(card.cell(TIDY, dayOf(window_start, offset))).toHaveClass(/nobody/);
    }
  });

  test("both published weeks are reachable, and only those two", async ({ page }) => {
    await onlySeededRules();
    await openDashboard(page, VIEW);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");
    const { window_start } = await scheduleWindow();

    const [, , firstMonday] = window_start.split("-").map(Number);
    await expect(card.weekLabel).toContainText(String(firstMonday));
    await expect(card.previousWeek).toBeDisabled();
    await expect(card.nextWeek).toBeEnabled();

    await card.nextWeek.click();

    // The second week is the same rota drawn against next week's dates, which
    // is the point of publishing real dates rather than a weekly template.
    await expect(card.who(FISH, dayOf(window_start, 7))).toHaveText("M");
    await expect(card.cell(FISH, dayOf(window_start, 13))).toHaveClass(/nobody/);
    await expect(card.previousWeek).toBeEnabled();
    await expect(card.nextWeek).toBeDisabled();
  });

  test("children take turns, and the grid shows whose it is", async ({ page }) => {
    await nothingScheduled();
    await setSchedule(TIDY, TIDY_ROTA);

    await openDashboard(page, VIEW);
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
    await nothingScheduled();
    await setSchedule(TIDY, TIDY_ROTA);

    const service = await recordServiceCalls(page);
    await openDashboard(page, VIEW);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");
    const { window_start } = await scheduleWindow();
    const wednesday = dayOf(window_start, 2);

    await card.cell(TIDY, wednesday).click();
    await expect(card.cellDialog).toBeVisible();

    // Put both of Tidy Room's children on it, whoever the rotation had picked,
    // and leave the third child of the house out of it.
    await setChips(card, { [KRISHNA]: true, [RADHA]: true, [MEERA]: false });
    await card.cellDialog.locator('.scope-option[data-scope="date"]').click();
    await card.cellDialog.locator(".dialog-button.save").click();
    await expect(card.cellDialog).toBeHidden();

    // Only the field being edited is sent. A call carrying the whole schedule
    // would clear the rotation this test set up, which nothing on screen asked
    // to change.
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
    await expect(card.exceptionMarker(TIDY, wednesday)).toHaveCount(1);

    // The same weekday next week was not touched - a one-off is one day.
    await card.nextWeek.click();
    const nextWednesday = dayOf(window_start, 9);
    await expect(card.who(TIDY, nextWednesday)).toHaveCount(1);
    await expect(card.cell(TIDY, nextWednesday)).not.toHaveClass(/exception/);
  });

  test("or for every one of that weekday, and both of them say a parent said so", async ({
    page,
  }) => {
    await nothingScheduled();
    await setSchedule(TIDY, TIDY_ROTA);

    const service = await recordServiceCalls(page);
    await openDashboard(page, VIEW);
    const card = ScheduleCard.byTitle(page, "Weekly Schedule");
    const { window_start } = await scheduleWindow();
    const friday = dayOf(window_start, 4);

    await card.cell(TIDY, friday).click();
    await expect(card.cellDialog).toBeVisible();

    await setChips(card, { [KRISHNA]: false, [RADHA]: true, [MEERA]: false });
    await card.cellDialog.locator('.scope-option[data-scope="day"]').click();
    await card.cellDialog.locator(".dialog-button.save").click();
    await expect(card.cellDialog).toBeHidden();

    expect(service.calls).toEqual([
      {
        service: "set_chore_schedule",
        data: { chore_id: TIDY, day_overrides: { friday: [RADHA] } },
      },
    ]);

    // Both Fridays in the window move, and both are marked. The mark means "a
    // parent set this day by hand", not "this day is odd for one week only":
    // pinning every Friday is still the parent overriding the pattern, and a
    // Friday that quietly stopped following the rotation with nothing to say so
    // is the harder thing to notice and undo.
    await expect(card.who(TIDY, friday)).toHaveText("R");
    await expect(card.cell(TIDY, friday)).toHaveClass(/exception/);
    await expect(card.exceptionMarker(TIDY, friday)).toHaveCount(1);

    await card.nextWeek.click();
    const nextFriday = dayOf(window_start, 11);
    await expect(card.who(TIDY, nextFriday)).toHaveText("R");
    await expect(card.cell(TIDY, nextFriday)).toHaveClass(/exception/);
  });

  test("a card aimed at the wrong sensor says which one it reads", async ({ page }) => {
    await openDashboard(page, VIEW);
    const wrong = ScheduleCard.misconfigured(page);

    // The overview sensor carries a `chores` attribute of its own, so a card
    // pointed at it could easily draw an empty grid and look like a family
    // with nothing scheduled.
    await expect(wrong.error).toContainText("sensor.choremander_schedule");
    await expect(wrong.error).toContainText("window_start");
  });
});

/**
 * Put the cell dialog's child chips into a known state.
 *
 * Every child in the house gets a chip whether or not the chore is theirs, and
 * the dialog opens on whoever the rota resolved to, so a test that only clicks
 * the children it wants ON is asserting against whatever was already selected.
 */
async function setChips(
  card: ScheduleCard,
  wanted: Record<string, boolean>,
): Promise<void> {
  for (const [childId, on] of Object.entries(wanted)) {
    const chip = card.cellDialog.locator(`.chip[data-child-id="${childId}"]`);
    const isOn = ((await chip.getAttribute("class")) ?? "").split(/\s+/).includes("on");
    if (isOn !== on) await chip.click();
  }
}
