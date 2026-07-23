import { expect, test } from "@playwright/test";

test("overview, explorer, document versions, and provenance", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Your knowledge, observable." })).toBeVisible();
  await page.getByRole("button", { name: "Explorer" }).click();
  await expect(page.getByRole("heading", { name: "Projects & documents" })).toBeVisible();

  const tree = await (
    await request.get("http://127.0.0.1:8010/api/v1/tree?limit=20000")
  ).json();
  let target: { id: string; project: string; path: string } | null = null;
  const visiblePerProject = new Map<string, number>();
  for (const item of tree.items) {
    const visibleIndex = visiblePerProject.get(item.project) ?? 0;
    visiblePerProject.set(item.project, visibleIndex + 1);
    if (visibleIndex >= 250) continue;
    const versions = await (
      await request.get(`http://127.0.0.1:8010/api/v1/documents/${item.id}/versions`)
    ).json();
    if (versions.length > 1) {
      target = item;
      break;
    }
  }
  expect(target).not.toBeNull();
  await page.getByRole("treeitem", { name: new RegExp(target!.project) }).click();
  await page.getByRole("treeitem", { name: new RegExp(target!.path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }).click();
  await expect(page.getByText("Version timeline")).toBeVisible();
  await expect(page.getByText("Latest diff")).toBeVisible();

  await page.getByRole("button", { name: "Search", exact: true }).click();
  await page.getByPlaceholder(/Filename, error/).fill("PostgreSQL");
  await page.getByRole("button", { name: "Search", exact: true }).last().click();
  const result = page.locator(".result-card").first();
  await expect(result).toBeVisible({ timeout: 30_000 });
  await result.click();
  await expect(page.getByRole("button", { name: "Copy citation" })).toBeVisible();
  await expect(page.getByText("Version", { exact: true })).toBeVisible();
  await expect(page.getByText("Chunk", { exact: true })).toBeVisible();
});

test("activity, knowledge cases, and candidate evidence gate", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Activity", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Activity history" })).toBeVisible();
  await page.locator(".record-list > button").first().click();
  await expect(page.getByText("Reported result", { exact: true })).toBeVisible();
  await expect(page.getByText("Verified result", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Knowledge cases", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Knowledge cases" })).toBeVisible();
  await page.locator(".record-list > button").first().click();
  await expect(page.getByText("Root cause", { exact: true })).toBeVisible();
  await expect(page.getByText("Revisions & occurrences")).toBeVisible();

  await page.getByRole("button", { name: "Candidate review" }).click();
  await page.getByRole("button", { name: /Unmeasured performance claim/ }).click();
  await expect(page.getByText("NEEDS_EVIDENCE")).toBeVisible();
  await page.getByRole("button", { name: "Publish if evidence passes" }).click();
  await expect(page.getByText("NEEDS_EVIDENCE").last()).toBeVisible();
});

test("keyword, semantic, hybrid, failed retry, and worker heartbeat", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Search", exact: true }).click();
  const input = page.getByPlaceholder(/Filename, error/);
  const mode = page.getByLabel("Search mode");
  for (const value of ["keyword", "semantic", "hybrid"]) {
    await mode.selectOption(value);
    await input.fill("PostgreSQL");
    await page.getByRole("button", { name: "Search", exact: true }).last().click();
    await expect(page.locator(".result-card").first()).toBeVisible({ timeout: 30_000 });
  }

  await page.getByRole("button", { name: "Operations", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Durable queue" })).toBeVisible();
  const retry = page.getByRole("button", { name: "Retry" }).first();
  await expect(retry).toBeVisible();
  await retry.click();
  await page.getByRole("button", { name: "Workers" }).click();
  await expect(page.getByText("codex-capture", { exact: false }).first()).toBeVisible();
  await expect(page.getByText(/idle|stale|healthy/).first()).toBeVisible();
  await page.getByRole("button", { name: "Backups" }).click();
  await expect(page.getByText("succeeded", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/D:\\Backups\\LocalKnowledgePortal/).first()).toBeVisible();
});
