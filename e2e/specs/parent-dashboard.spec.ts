import { expect, test } from "@playwright/test";

import {
  ApprovalsCard,
  ChildCard,
  PointsCard,
  ReorderCard,
  openDashboard,
  recordServiceCalls,
  reseed,
} from "../pages";

// Start from the committed fixture regardless of which spec files ran first.
test.beforeAll(reseed);

/**
 * The parent-facing flows: approving work, adjusting balances, and reordering
 * chores.
 *
 * As on the kid dashboard, each mutating test owns a distinct (child, chore)
 * pair because daily_limit is 1:
 *
 *   Tidy Room / Krishna      -> approve
 *   Practice Piano / Radha   -> reject
 */

test.describe("approvals", () => {
  test("shows nothing to do when there are no pending approvals", async ({ page }) => {
    await openDashboard(page, "/parent");
    const approvals = ApprovalsCard.on(page);

    await expect(approvals.emptyState).toContainText("All caught up!");
  });

  test("approving a chore awards its points and clears the queue", async ({ page }) => {
    const traffic = await recordServiceCalls(page);

    // Complete an approval-gated chore as the kid would.
    await openDashboard(page, "/krishna");
    const evening = ChildCard.bySection(page, "Evening Chores");
    await evening.complete("Tidy Room");
    await expect(evening.waitingStars).toContainText("3");

    await openDashboard(page, "/parent");
    const approvals = ApprovalsCard.on(page);
    await expect(approvals.item("Tidy Room")).toBeVisible();

    await approvals.approve("Tidy Room").click();

    await expect(approvals.item("Tidy Room")).toHaveCount(0);
    expect(
      traffic.calls.filter((c) => c.service === "approve_chore"),
      "clicking Approve should send exactly one approve_chore",
    ).toHaveLength(1);

    // Tidy Room is worth 3: Krishna goes 25 -> 28 once approved.
    await openDashboard(page, "/krishna");
    await expect(ChildCard.bySection(page, "Morning Chores").myStars).toContainText("28");
  });

  test("rejecting a chore awards nothing and clears the queue", async ({ page }) => {
    await openDashboard(page, "/radha");
    const evening = ChildCard.bySection(page, "Evening Chores");
    await evening.complete("Practice Piano");
    await expect(evening.waitingStars).toContainText("4");

    await openDashboard(page, "/parent");
    const approvals = ApprovalsCard.on(page);
    await expect(approvals.item("Practice Piano")).toBeVisible();

    await approvals.reject("Practice Piano").click();
    await expect(approvals.item("Practice Piano")).toHaveCount(0);

    // Radha started at 15 and must stay there.
    await openDashboard(page, "/radha");
    await expect(ChildCard.bySection(page, "Morning Chores").myStars).toContainText("15");
  });
});

test.describe("manage points", () => {
  test("awarding a bonus updates the balance and confirms it", async ({ page }) => {
    const traffic = await recordServiceCalls(page);
    await openDashboard(page, "/parent");
    const points = PointsCard.on(page);

    await points.addButton("Radha").click();
    await expect(points.dialog).toBeVisible();

    await points.pointsInput.fill("7");
    await points.reasonInput.fill("Helped a sibling");
    await points.submit.click();

    const calls = traffic.calls.filter((c) => c.service === "add_points");
    expect(calls, "confirming the dialog should send one add_points").toHaveLength(1);
    expect(calls[0].data).toMatchObject({ points: 7, reason: "Helped a sibling" });

    await expect(points.notification).toContainText("Radha");
  });

  test("deducting points sends a penalty", async ({ page }) => {
    const traffic = await recordServiceCalls(page);
    await openDashboard(page, "/parent");
    const points = PointsCard.on(page);

    await points.removeButton("Krishna").click();
    await expect(points.dialog).toBeVisible();

    await points.pointsInput.fill("3");
    await points.submit.click();

    const calls = traffic.calls.filter((c) => c.service === "remove_points");
    expect(calls, "confirming the dialog should send one remove_points").toHaveLength(1);
    expect(calls[0].data).toMatchObject({ points: 3 });
  });

  test("an out-of-range amount is refused without calling the service", async ({ page }) => {
    const traffic = await recordServiceCalls(page);
    await openDashboard(page, "/parent");
    const points = PointsCard.on(page);

    await points.addButton("Krishna").click();
    await points.pointsInput.fill("101");
    await points.submit.click();

    expect(
      traffic.calls.filter((c) => c.service === "add_points"),
      "an amount above the allowed range must not reach Home Assistant",
    ).toHaveLength(0);
    await expect(points.dialog).toBeVisible();
  });
});

test.describe("reorder chores", () => {
  test("reordering is local until saved, then persists to the kid's card", async ({ page }) => {
    const traffic = await recordServiceCalls(page);
    await openDashboard(page, "/parent");
    const reorder = ReorderCard.forChild(page, "Krishna");

    await expect(reorder.saveButton).toBeDisabled();

    await reorder.moveDown("Make Bed").click();

    // Moving is a client-side edit: nothing should hit the server until Save.
    expect(
      traffic.calls.filter((c) => c.service === "set_chore_order"),
      "moving a chore must not call the service before Save is pressed",
    ).toHaveLength(0);
    await expect(reorder.saveButton).toBeEnabled();

    await reorder.saveButton.click();

    await expect
      .poll(
        () => traffic.calls.filter((c) => c.service === "set_chore_order").length,
        { message: "Save should send exactly one set_chore_order" },
      )
      .toBe(1);

    // The kid's card must reflect the new order after a reload.
    await openDashboard(page, "/krishna");
    await expect(ChildCard.bySection(page, "Morning Chores").choreNames).toHaveText([
      "Brush Teeth (Morning)",
      "Make Bed",
      "Feed the Dog",
    ]);
  });
});
