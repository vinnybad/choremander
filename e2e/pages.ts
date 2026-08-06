import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { type Locator, type Page, expect } from "@playwright/test";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/**
 * Restore Choremander's data to the committed fixture.
 *
 * Call this in `test.beforeAll` of every spec file. Chores have `daily_limit: 1`
 * and approval queues persist, so a file that inherits another file's leftovers
 * asserts against different numbers depending on which files ran before it.
 * Reseeding per file makes each one independent of run order.
 */
export function reseed(): void {
  if (process.env.CHOREMANDER_SKIP_SEED === "1") return;
  execFileSync(path.join(repoRoot, "dev", "seed.sh"), {
    cwd: repoRoot,
    stdio: "ignore",
    timeout: 300_000,
  });
}

/**
 * Call a Home Assistant service over REST, reusing the bootstrap credentials.
 *
 * Some card states cannot be reached from the fixture alone — nothing is
 * affordable at the seeded balances (Krishna 25, Radha 15, cheapest reward 50),
 * so a spec that exercises claiming has to arrange the balance first. Doing it
 * over the API rather than by clicking through chores keeps the setup out of
 * the behaviour under test.
 */
export async function callService(
  domain: string,
  service: string,
  data: Record<string, unknown>,
): Promise<void> {
  const creds = JSON.parse(fs.readFileSync(path.join(repoRoot, "dev", ".token"), "utf8"));
  const baseURL = process.env.HASS_URL ?? creds.hass_url ?? "http://localhost:8123";

  const response = await fetch(`${baseURL}/api/services/${domain}/${service}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${creds.access_token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    throw new Error(
      `${domain}.${service} failed (HTTP ${response.status}): ${await response.text()}`,
    );
  }
}

/** Give a child enough points that a reward becomes claimable. */
export async function grantPoints(childId: string, points: number): Promise<void> {
  await callService("choremander", "add_points", { child_id: childId, points });
}

/** Read one entity's attributes over REST, reusing the bootstrap credentials. */
export async function entityAttributes(entityId: string): Promise<Record<string, any>> {
  const creds = JSON.parse(fs.readFileSync(path.join(repoRoot, "dev", ".token"), "utf8"));
  const baseURL = process.env.HASS_URL ?? creds.hass_url ?? "http://localhost:8123";

  const response = await fetch(`${baseURL}/api/states/${entityId}`, {
    headers: { Authorization: `Bearer ${creds.access_token}` },
  });
  if (!response.ok) {
    throw new Error(`reading ${entityId} failed (HTTP ${response.status})`);
  }
  return (await response.json()).attributes ?? {};
}

/**
 * Approve the chore a child is waiting on.
 *
 * Approving through the API rather than by clicking the approvals card keeps
 * the setup out of the behaviour under test - these specs are about what the
 * activity and streak cards render, not about how approval is triggered.
 */
export async function approveChore(childId: string, choreId: string): Promise<void> {
  const attributes = await entityAttributes("sensor.pending_approvals");
  const pending: Array<Record<string, string>> = attributes.chore_completions ?? [];
  const completion = pending.find(
    (row) => row.child_id === childId && row.chore_id === choreId,
  );
  if (!completion) {
    throw new Error(`no pending completion of ${choreId} for ${childId} to approve`);
  }
  await callService("choremander", "approve_chore", {
    completion_id: completion.completion_id,
  });
}

/**
 * Locators for the Choremander Lovelace cards.
 *
 * Every card subclasses Home Assistant's LitElement and renders into an OPEN
 * shadow root, so Playwright's CSS engine descends into them with no `>>>` and
 * no manual shadowRoot walking. The one rule: never use the `>` direct-child
 * combinator across a host boundary.
 *
 * The cards carry no data-testid, id, or aria-label attributes (except the two
 * inputs in the points dialog), so these handles lean on class names and the
 * `title` attributes the cards already set. Keeping them in this one file means
 * a markup change breaks in a single place rather than across every spec.
 */

/** A completed row flips its tooltip, which is the most reliable state hook. */
export const NOT_COMPLETED = '.chore-card[title="Click to complete"]';
export const COMPLETED = '.chore-card[title="Click to undo"]';

/**
 * Read one resolved CSS property off an element.
 *
 * The layout options are asserted on computed geometry rather than on
 * screenshots: what matters is that a row really is 56px tall and really has
 * 8px of padding, and that survives a font-rendering difference between this
 * machine and CI, which a pixel diff would not.
 */
export async function computed(locator: Locator, property: string): Promise<string> {
  return locator.evaluate(
    (el, prop) => getComputedStyle(el).getPropertyValue(prop).trim(),
    property,
  );
}

export class ChildCard {
  constructor(readonly root: Locator) {}

  /** Pick one of the two child cards on a kid dashboard by its section heading. */
  static bySection(page: Page, section: "Morning Chores" | "Evening Chores"): ChildCard {
    return new ChildCard(
      page
        .locator("choremander-child-card")
        .filter({ has: page.locator(".section-title", { hasText: section }) }),
    );
  }

  /**
   * Pick a card by its density.
   *
   * The card mirrors its layout options onto the host as attributes - that is
   * how the `:host([density=...])` rules in the stylesheet select on them - so
   * the same attributes serve as test handles. A card left at the default
   * density carries no attribute at all, which is exactly the property the
   * backward-compatibility test needs to assert.
   */
  static byDensity(
    page: Page,
    density: "cozy" | "compact",
    section?: "Morning Chores" | "Evening Chores",
  ): ChildCard {
    const cards = page.locator(`choremander-child-card[density="${density}"]`);
    return new ChildCard(
      section
        ? cards.filter({ has: page.locator(".section-title", { hasText: section }) })
        : cards,
    );
  }

  /** The card on the layout view with every visibility toggle turned off. */
  static stripped(page: Page): ChildCard {
    return new ChildCard(page.locator("choremander-child-card[hide-header]"));
  }

  get header(): Locator {
    return this.root.locator(".card-header");
  }

  get avatar(): Locator {
    return this.root.locator(".avatar-container");
  }

  get sectionTitle(): Locator {
    return this.root.locator(".section-title");
  }

  get numberBadges(): Locator {
    return this.root.locator(".chore-number-badge");
  }

  get chorePoints(): Locator {
    return this.root.locator(".chore-points");
  }

  get choresContainer(): Locator {
    return this.root.locator(".chores-container");
  }

  checkbox(name: string): Locator {
    return this.chore(name).locator(".chore-checkbox");
  }

  /** Rendered height of a chore row, which is what "compact" is really about. */
  async choreHeight(name: string): Promise<number> {
    const box = await this.chore(name).boundingBox();
    if (!box) throw new Error(`chore row "${name}" has no bounding box`);
    return box.height;
  }

  chore(name: string): Locator {
    return this.root.locator(".chore-card").filter({ hasText: name });
  }

  get choreNames(): Locator {
    return this.root.locator(".chore-name");
  }

  /**
   * What the card says instead of a chore list.
   *
   * One element for three different sentences - "Day off", "All Done!" and
   * "Nothing here" - because which of them is showing is the assertion, and a
   * handle per message would let a test pass by matching nothing.
   */
  get emptyState(): Locator {
    return this.root.locator(".empty-state");
  }

  get childName(): Locator {
    return this.root.locator(".child-name");
  }

  /** The child's spendable balance. */
  get myStars(): Locator {
    return this.root.locator(".stars-value.my-stars");
  }

  /** Points earned but still awaiting a parent's approval. */
  get waitingStars(): Locator {
    return this.root.locator(".stars-value.waiting-stars");
  }

  async balance(): Promise<number> {
    return Number((await this.myStars.first().innerText()).trim());
  }

  /**
   * Tap a chore and wait out the celebration animation.
   *
   * A successful completion covers the viewport with a fixed-position overlay
   * plus a confetti layer for up to 3.5s. Any follow-up click would land on the
   * overlay instead of the card, so it has to be waited out.
   */
  async complete(name: string): Promise<void> {
    await this.chore(name).click();
    await this.dismissCelebration();
  }

  async dismissCelebration(): Promise<void> {
    const overlay = this.root.page().locator(".celebration-overlay");
    if (await overlay.count()) {
      await expect(overlay.first()).toBeHidden({ timeout: 10_000 });
    }
  }
}

/**
 * Build a child card the fixture's dashboards do not carry, on the open page.
 *
 * The Lovelace views in dev/fixtures cover two of the three children, and a
 * state that only the third can reach - a scheduled day off, say - would
 * otherwise be untestable in a browser. The card is given the same `hass`
 * object Home Assistant hands its own cards, so it renders exactly what a view
 * would; nothing here stubs or fabricates state.
 *
 * That object is re-read on a timer because Home Assistant replaces it wholesale
 * on every state change and only pushes the new one into the cards it owns.
 * Without the timer this card would be frozen at whatever had loaded, and a
 * service call made from a test would never reach it.
 *
 * Call `openDashboard` first: the page has to have Home Assistant on it, and
 * loading any dashboard is what registers the card's custom element.
 */
export async function mountChildCard(
  page: Page,
  config: Record<string, unknown>,
): Promise<ChildCard> {
  const id = `probe-${Date.now().toString(36)}`;

  await page.evaluate(
    ({ id, config }) => {
      const app = document.querySelector("home-assistant") as (HTMLElement & {
        hass?: unknown;
      }) | null;
      if (!app) throw new Error("no <home-assistant> on the page to take hass from");

      const card = document.createElement("choremander-child-card") as HTMLElement & {
        setConfig: (c: Record<string, unknown>) => void;
        hass: unknown;
      };
      card.setConfig(config);
      card.id = id;
      document.body.appendChild(card);

      const sync = () => {
        card.hass = app.hass;
      };
      sync();
      // Cleared by the page going away at the end of the test.
      window.setInterval(sync, 250);
    },
    { id, config },
  );

  return new ChildCard(page.locator(`#${id}`));
}

export class ApprovalsCard {
  constructor(readonly root: Locator) {}

  static on(page: Page): ApprovalsCard {
    return new ApprovalsCard(page.locator("choremander-approvals-card"));
  }

  item(choreName: string): Locator {
    return this.root.locator(".approval-item").filter({ hasText: choreName });
  }

  approve(choreName: string): Locator {
    return this.item(choreName).locator('button.action-button.approve, button[title="Approve"]');
  }

  reject(choreName: string): Locator {
    return this.item(choreName).locator('button.action-button.reject, button[title="Reject"]');
  }

  get emptyState(): Locator {
    return this.root.locator(".empty-state");
  }
}

export class PointsCard {
  constructor(readonly root: Locator) {}

  static on(page: Page): PointsCard {
    return new PointsCard(page.locator("choremander-points-card"));
  }

  private row(childName: string): Locator {
    return this.root.locator(".child-row").filter({ hasText: childName });
  }

  addButton(childName: string): Locator {
    return this.row(childName).locator('button[title="Add Stars"]');
  }

  removeButton(childName: string): Locator {
    return this.row(childName).locator('button[title="Remove Stars"]');
  }

  get pointsInput(): Locator {
    return this.root.locator("#points-input");
  }

  get reasonInput(): Locator {
    return this.root.locator("#reason-input");
  }

  get notification(): Locator {
    return this.root.locator(".notification");
  }

  /** The dialog's confirm control, whose label differs between add and remove. */
  get submit(): Locator {
    return this.root.locator("button.dialog-button.confirm");
  }

  get cancel(): Locator {
    return this.root.locator("button.dialog-button.cancel");
  }

  get dialog(): Locator {
    return this.root.locator(".dialog-content");
  }
}

export class ReorderCard {
  constructor(readonly root: Locator) {}

  static forChild(page: Page, childName: string): ReorderCard {
    return new ReorderCard(
      page.locator("choremander-reorder-card").filter({ hasText: childName }),
    );
  }

  chore(name: string): Locator {
    return this.root.locator(".chore-item, .reorder-item").filter({ hasText: name });
  }

  moveDown(name: string): Locator {
    return this.chore(name).locator('button[title="Move down"]');
  }

  moveUp(name: string): Locator {
    return this.chore(name).locator('button[title="Move up"]');
  }

  get saveButton(): Locator {
    return this.root.locator('button:has-text("Save Order")');
  }

  get orderedNames(): Locator {
    return this.root.locator(".chore-item .chore-name, .reorder-item .chore-name");
  }
}

export class RewardsCard {
  constructor(readonly root: Locator) {}

  static on(page: Page): RewardsCard {
    return new RewardsCard(page.locator("choremander-rewards-card"));
  }

  /** Pick one of several rewards cards on a page by its header title. */
  static byTitle(page: Page, title: string): RewardsCard {
    return new RewardsCard(
      page
        .locator("choremander-rewards-card")
        .filter({ has: page.locator(".header-title", { hasText: title }) }),
    );
  }

  /** Pick a card by its density; see the note on ChildCard.byDensity. */
  static byDensity(page: Page, density: "cozy" | "compact"): RewardsCard {
    return new RewardsCard(page.locator(`choremander-rewards-card[density="${density}"]`));
  }

  get costBadges(): Locator {
    return this.root.locator(".cost-badge");
  }

  get rows(): Locator {
    return this.root.locator(".reward-row");
  }

  reward(name: string): Locator {
    return this.root.locator(".reward-row").filter({ hasText: name });
  }

  /** Reward names in rendered order — affordable ones sort to the top. */
  get rewardNames(): Locator {
    return this.root.locator(".reward-name");
  }

  /**
   * The claim affordance. A real <button>, so `toBeDisabled` is meaningful:
   * below the cost it is genuinely inert rather than merely dimmed.
   */
  claimButton(name: string): Locator {
    return this.reward(name).locator("button.reward-claim-button");
  }

  /** "N more Stars needed", shown under the progress bar when short. */
  shortfall(name: string): Locator {
    return this.reward(name).locator(".shortfall-label");
  }

  get count(): Locator {
    return this.root.locator(".reward-count");
  }

  // -- Confirmation dialog -------------------------------------------

  get dialog(): Locator {
    return this.root.locator(".confirm-dialog");
  }

  /** Shown only when the card is unscoped and several children can afford it. */
  claimant(childName: string): Locator {
    return this.dialog.locator(".claimant-option").filter({ hasText: childName });
  }

  get confirm(): Locator {
    return this.dialog.locator("button.btn-confirm");
  }

  get cancel(): Locator {
    return this.dialog.locator("button.btn-cancel");
  }

  get celebration(): Locator {
    return this.root.locator(".celebration-overlay");
  }

  /**
   * Claim a reward and wait out the celebration.
   *
   * The overlay is fixed-position and covers the viewport for 3s, so a bare
   * click followed by another interaction hits the overlay instead of the card.
   */
  async claim(name: string, claimant?: string): Promise<void> {
    await this.claimButton(name).click();
    if (claimant) await this.claimant(claimant).click();
    await this.confirm.click();
    await expect(this.celebration).toBeVisible();
    await expect(this.celebration).toBeHidden({ timeout: 10_000 });
  }
}

/**
 * Record every `call_service` the frontend sends over its websocket.
 *
 * Asserting on the wire is how these tests tell "the card actually asked Home
 * Assistant to do something" apart from "the card only updated optimistically".
 */
export async function recordServiceCalls(
  page: Page,
  domain = "choremander",
): Promise<{ calls: Array<{ service: string; data: Record<string, unknown> }> }> {
  const sink: { calls: Array<{ service: string; data: Record<string, unknown> }> } = {
    calls: [],
  };

  page.on("websocket", (ws) => {
    ws.on("framesent", (frame) => {
      try {
        const parsed = JSON.parse(String(frame.payload));
        if (parsed?.type === "call_service" && parsed?.domain === domain) {
          sink.calls.push({ service: parsed.service, data: parsed.service_data ?? {} });
        }
      } catch {
        // Non-JSON frames (pings, binary) are irrelevant here.
      }
    });
  });

  return sink;
}

/** Wait for a dashboard's cards to finish their first render. */
export async function openDashboard(page: Page, path: string): Promise<void> {
  await page.goto(path);
  await expect(page.locator("home-assistant")).toBeAttached();
  // Cards render asynchronously once the websocket delivers initial state.
  await expect(
    page.locator(
      "choremander-child-card, choremander-approvals-card, choremander-points-card, " +
        "choremander-rewards-card, choremander-activity-card, choremander-streak-card, " +
        "choremander-schedule-card",
    ),
  ).not.toHaveCount(0, { timeout: 30_000 });
}

/**
 * The activity card - a timeline of everything that moved a child's balance.
 *
 * Rows are keyed off the icon tone class rather than off their wording, which
 * mirrors how the card itself works: the stored `event_type` decides the tone,
 * and the sentence is only presentation.
 */
/**
 * An anchored matcher for a card's header title.
 *
 * `hasText` matches substrings, so a view holding both "Streaks" and "Compact
 * Streaks" would have the first name select both cards. Every title lookup goes
 * through here so that cannot happen by accident.
 */
function exactTitle(title: string): RegExp {
  return new RegExp(`^\\s*${title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*$`);
}

export class ActivityCard {
  constructor(readonly root: Locator) {}

  /** The first activity card on the page - the one left entirely at defaults. */
  static default(page: Page): ActivityCard {
    return new ActivityCard(
      page.locator("choremander-activity-card:not([density]):not([no-animations])").first(),
    );
  }

  static byTitle(page: Page, title: string): ActivityCard {
    return new ActivityCard(
      page
        .locator("choremander-activity-card")
        .filter({ has: page.locator(".header-title", { hasText: exactTitle(title) }) }),
    );
  }

  static byDensity(page: Page, density: "cozy" | "compact"): ActivityCard {
    return new ActivityCard(page.locator(`choremander-activity-card[density="${density}"]`));
  }

  get header(): Locator {
    return this.root.locator(".card-header");
  }

  get count(): Locator {
    return this.root.locator(".header-count");
  }

  get chips(): Locator {
    return this.root.locator(".chip");
  }

  chip(label: string): Locator {
    return this.root.locator(".chip", { hasText: label });
  }

  get dayLabels(): Locator {
    return this.root.locator(".day-label");
  }

  get events(): Locator {
    return this.root.locator(".event");
  }

  /** Rows whose icon carries a given tone, e.g. "good", "bad", "reward". */
  eventsWithTone(tone: string): Locator {
    return this.root.locator(`.event:has(.event-icon.${tone})`);
  }

  /** A row by the text it renders, which is what a parent actually reads. */
  event(text: string): Locator {
    return this.root.locator(".event", { hasText: text });
  }

  get empty(): Locator {
    return this.root.locator(".empty-text");
  }
}

/** The streak card - day dots, current and best streaks, and milestones. */
export class StreakCard {
  constructor(readonly root: Locator) {}

  static default(page: Page): StreakCard {
    return new StreakCard(page.locator("choremander-streak-card:not([density])").first());
  }

  static byTitle(page: Page, title: string): StreakCard {
    return new StreakCard(
      page
        .locator("choremander-streak-card")
        .filter({ has: page.locator(".header-title", { hasText: exactTitle(title) }) }),
    );
  }

  static byDensity(page: Page, density: "cozy" | "compact"): StreakCard {
    return new StreakCard(page.locator(`choremander-streak-card[density="${density}"]`));
  }

  /** One child's block inside the card. */
  child(childId: string): Locator {
    return this.root.locator(`.child[data-child-id="${childId}"]`);
  }

  get children(): Locator {
    return this.root.locator(".child");
  }

  streakCount(childId: string): Locator {
    return this.child(childId).locator(".streak-count");
  }

  bestStreak(childId: string): Locator {
    return this.child(childId).locator(".best strong");
  }

  dots(childId: string): Locator {
    return this.child(childId).locator(".dot");
  }

  /** Dots the child was actually active on, excluding today's own styling. */
  activeDots(childId: string): Locator {
    return this.child(childId).locator(".dot.active");
  }

  todayDot(childId: string): Locator {
    return this.child(childId).locator(".dot.today");
  }

  todayIdleDot(childId: string): Locator {
    return this.child(childId).locator(".dot.today-idle");
  }

  /**
   * Days the schedule gave this child off.
   *
   * Today counts: a rest day that happens to be today is drawn as
   * `rest today-rest`, so this handle finds it either way and a test does not
   * have to know which weekday it is running on.
   */
  restDots(childId: string): Locator {
    return this.child(childId).locator(".dot.rest");
  }

  badges(childId: string): Locator {
    return this.child(childId).locator(".badge");
  }

  earnedBadges(childId: string): Locator {
    return this.child(childId).locator(".badge.earned");
  }

  lockedBadges(childId: string): Locator {
    return this.child(childId).locator(".badge.locked");
  }

  avatar(childId: string): Locator {
    return this.child(childId).locator(".avatar");
  }
}

/**
 * The schedule card - a fortnight of whose turn it is, and the rota editor.
 *
 * Cells are addressed by chore id and ISO date rather than by row and column.
 * The window starts on whichever Monday the sensor published, so a handle built
 * out of grid coordinates would point at a different fortnight every week,
 * whereas the date a test derives from `window_start` is the same date the card
 * stamped onto the cell.
 */
export class ScheduleCard {
  constructor(readonly root: Locator) {}

  /** The card left entirely at its defaults. */
  static default(page: Page): ScheduleCard {
    return new ScheduleCard(page.locator("choremander-schedule-card:not([density])").first());
  }

  static byTitle(page: Page, title: string): ScheduleCard {
    return new ScheduleCard(
      page
        .locator("choremander-schedule-card")
        .filter({ has: page.locator(".header-title", { hasText: exactTitle(title) }) }),
    );
  }

  /**
   * The card that is aimed at the wrong sensor.
   *
   * Deliberately not `byTitle`: a card in the configuration-error state renders
   * only the error, header and all, so there is no `.header-title` left to
   * filter on and a title lookup can never match. Every card in
   * `misconfigured-entity.spec.ts` is found this way for the same reason.
   */
  static misconfigured(page: Page): ScheduleCard {
    return new ScheduleCard(
      page
        .locator("choremander-schedule-card")
        .filter({ has: page.locator(".config-error") }),
    );
  }

  static byDensity(page: Page, density: "cozy" | "compact"): ScheduleCard {
    return new ScheduleCard(page.locator(`choremander-schedule-card[density="${density}"]`));
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

  /** The seven weekday columns, letter and date. */
  get dayHeads(): Locator {
    return this.root.locator(".day-head");
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

  /** The dot marking a cell someone set by hand rather than by the pattern. */
  exceptionMarker(choreId: string, date: string): Locator {
    return this.cell(choreId, date).locator(".marker");
  }

  /** Shown instead of the grid when no chore in the house carries a rule. */
  get emptyState(): Locator {
    return this.root.locator(".empty-state");
  }

  get error(): Locator {
    return this.root.locator(".error");
  }

  /** The picker that puts a chore with no rule yet onto the schedule. */
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
