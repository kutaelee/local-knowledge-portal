import { expect, test } from "@playwright/test";

const apiURL = process.env.LKP_E2E_API_URL ?? "http://127.0.0.1:8010";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem("lkp-locale")) {
      localStorage.setItem("lkp-locale", "en");
    }
  });
});

test("localized overview explains freshness and persists language", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "See what is current, at a glance." }))
    .toBeVisible();
  await expect(page.getByText("Latest indexing", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recently indexed documents" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Source freshness" })).toBeVisible();

  await page.getByLabel("Language").selectOption("ko");
  await expect(page.getByRole("heading", {
    name: "지금 무엇이 최신인지 한눈에 확인하세요.",
  })).toBeVisible();
  await expect(page.getByText("마지막 인덱싱", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "최근 반영된 문서" })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", {
    name: "지금 무엇이 최신인지 한눈에 확인하세요.",
  })).toBeVisible();

  const localizedScreens = [
    ["저장소 탐색", "저장소와 파일"],
    ["검색", "원본 지식 검색"],
    ["활동 이력", "활동 이력"],
    ["지식 사례", "지식 사례"],
    ["운영", "지속형 작업 큐"],
    ["변경 타임라인", "변경 타임라인"],
    ["지식 그래프", "지식 그래프"],
  ] as const;
  for (const [navigation, heading] of localizedScreens) {
    await page.getByRole("button", { name: navigation, exact: true }).click();
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  }
});

test("overview, explorer, document versions, and provenance", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "See what is current, at a glance." }))
    .toBeVisible();
  await page.getByRole("button", { name: "Repository explorer" }).click();
  await expect(page.getByRole("heading", { name: "Repositories & files" })).toBeVisible();

  const tree = await (
    await request.get(`${apiURL}/api/v1/tree?limit=20000`)
  ).json();
  let target: { id: string; project: string; path: string } | null = null;
  const visiblePerProject = new Map<string, number>();
  for (const item of tree.items) {
    const visibleIndex = visiblePerProject.get(item.project) ?? 0;
    visiblePerProject.set(item.project, visibleIndex + 1);
    if (visibleIndex >= 250) continue;
    const versions = await (
      await request.get(`${apiURL}/api/v1/documents/${item.id}/versions`)
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

test("activity, knowledge cases, and automatic evidence editor", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Activity", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Activity history" })).toBeVisible();
  await page.locator(".record-list > button").first().click();
  await expect(page.getByText("Reported result", { exact: true })).toBeVisible();
  await expect(page.getByText("Verified result", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Knowledge cases", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Knowledge cases" })).toBeVisible();
  await expect(page.getByText("Local knowledge editor", { exact: true })).toBeVisible();
  await expect(page.getByText(/qwen3\.5:9b-q4_K_M/)).toBeVisible();
  await expect(page.getByText(/evidence-blog-v7/)).toBeVisible();
  await page.locator(".record-list > button").first().click();
  await expect(page.getByText("Root cause", { exact: true })).toBeVisible();
  await expect(page.getByText("Revisions & occurrences")).toBeVisible();

  await page.getByRole("button", { name: "Candidate review" }).click();
  await page.locator(".record-list > button").first().click();
  await expect(page.getByText("Reported / verified")).toBeVisible();
  await expect(page.getByText("Auto-publish after local LLM evidence validation"))
    .toBeVisible();
  await expect(page.getByText("The local editor is checking the evidence."))
    .toBeVisible();
});

test("keyword, semantic, hybrid, operations, and worker heartbeat", async ({ page }) => {
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
  if (await retry.count()) {
    await expect(retry).toBeVisible();
    await retry.click();
  } else {
    await expect(page.locator("tbody tr").first()).toBeVisible();
  }
  await page.getByRole("button", { name: "Workers" }).click();
  await expect(page.getByText("watcher-service", { exact: false }).first()).toBeVisible();
  await expect(page.getByText(/idle|stale|healthy|busy/).first()).toBeVisible();
  await page.getByRole("button", { name: "Backups" }).click();
  await expect(page.getByText("Succeeded", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/D:\\(LocalBackup|Backups)\\LocalKnowledgePortal/).first())
    .toBeVisible();
});
