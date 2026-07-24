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
  await expect(page.getByText("WSL · Docker 연결 상태", { exact: true })).toBeVisible();
  await expect(page.getByText("Codex 훅 수집기", { exact: true })).toBeVisible();
  await expect(page.getByText("Ollama 지식 편집기", { exact: true })).toBeVisible();
  await expect(page.getByText("프로젝트 웹·API", { exact: true })).toBeVisible();
  await expect(page.getByText("미닝 운정점 웹사이트", { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", {
    name: "지금 무엇이 최신인지 한눈에 확인하세요.",
  })).toBeVisible();

  const localizedScreens = [
    ["원본 파일", "원본 저장소와 파일"],
    ["통합 검색", "원본 지식 검색"],
    ["Codex 작업", "활동 이력"],
    ["프로젝트 지식", "프로젝트 지식"],
    ["수집·운영", "지속형 작업 큐"],
    ["변경 기록", "변경 타임라인"],
    ["문서 관계", "지식 그래프"],
  ] as const;
  for (const [navigation, heading] of localizedScreens) {
    await page.getByRole("button", { name: navigation }).click();
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  }
});

test("overview, explorer, document versions, and provenance", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "See what is current, at a glance." }))
    .toBeVisible();
  await page.getByRole("button", { name: /Source files/ }).click();
  await expect(page.getByRole("heading", { name: "Source repositories & files" })).toBeVisible();

  let target: { id: string; project: string; path: string } | null = null;
  const visiblePerProject = new Map<string, number>();
  for (let treePage = 1; treePage <= 20 && !target; treePage += 1) {
    const tree = await (
      await request.get(`${apiURL}/api/v1/tree?catalog=source&page=${treePage}&page_size=250`)
    ).json();
    for (const item of tree.items) {
      const visibleIndex = visiblePerProject.get(item.project) ?? 0;
      visiblePerProject.set(item.project, visibleIndex + 1);
      if (visibleIndex >= 250) continue;
      const versions = await (
        await request.get(`${apiURL}/api/v1/documents/${item.id}/versions?page_size=25`)
      ).json();
      if (versions.items.length > 1) {
        target = item;
        break;
      }
    }
  }
  expect(target).not.toBeNull();
  await page.getByRole("treeitem", { name: new RegExp(target!.project) }).click();
  await page.getByRole("treeitem", { name: new RegExp(target!.path.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) }).click();
  await expect(page.getByText("Version timeline")).toBeVisible();
  await expect(page.getByText("Latest diff")).toBeVisible();

  await page.getByRole("button", { name: /Unified search/ }).click();
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
  await page.getByRole("button", { name: /Codex work/ }).click();
  await expect(page.getByRole("heading", { name: "Activity history" })).toBeVisible();
  await page.locator(".record-list > button").first().click();
  await expect(page.getByText("Reported result", { exact: true })).toBeVisible();
  await expect(page.getByText("Verified result", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: /Project knowledge/ }).click();
  await expect(page.getByRole("heading", { name: "Project knowledge" })).toBeVisible();
  await expect(page.getByText("Project · work type", { exact: true })).toBeVisible();
  await expect(page.getByText("Newest updated first", { exact: true })).toBeVisible();
  await expect(page.getByText("Local knowledge editor", { exact: true })).toBeVisible();
  await expect(page.getByText(/qwen3\.5:9b-q4_K_M/).first()).toBeVisible();
  await expect(page.getByText(/evidence-blog-v9/)).toBeVisible();
  await page.locator(".record-list > button").first().click();
  await expect(page.getByText("Root cause", { exact: true })).toBeVisible();
  await expect(page.getByText("Revisions & occurrences")).toBeVisible();

  await page.getByRole("button", { name: "Held candidates" }).click();
  await expect(page.locator(".knowledge-tree")).toBeVisible();
  await expect(page.locator(".knowledge-tree").getByText("All projects", { exact: true }))
    .toBeVisible();
  await page.locator(".record-list > button").first().click();
  await expect(page.getByText("Reported / verified")).toBeVisible();
  await expect(page.getByText("Auto-publish after local LLM evidence validation"))
    .toBeVisible();
  await expect(page.getByText("Reuse value", { exact: true })).toBeVisible();
  await expect(page.getByText("value evidence needed", { exact: true })).toBeVisible();
  await expect(page.getByText(/Pending automatic selection|Automatic selection completed/))
    .toBeVisible();

  await page.getByRole("button", { name: "Project journal" }).click();
  await expect(page.locator(".knowledge-tree")).toBeVisible();
  await expect(page.locator(".knowledge-tree").getByText("All projects", { exact: true }))
    .toBeVisible();
  await page.locator(".record-list > button").first().click();
  await expect(page.getByText("Execution verification", { exact: true })).toBeVisible();
  await expect(page.getByText("Knowledge references", { exact: true })).toBeVisible();
  await expect(page.locator(".page-controls").first()).toBeVisible();
});

test("keyword, semantic, hybrid, operations, and worker heartbeat", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /Unified search/ }).click();
  const input = page.getByPlaceholder(/Filename, error/);
  const mode = page.getByLabel("Search mode");
  for (const value of ["keyword", "semantic", "hybrid"]) {
    await mode.selectOption(value);
    await input.fill("PostgreSQL");
    await page.getByRole("button", { name: "Search", exact: true }).last().click();
    await expect(page.locator(".result-card").first()).toBeVisible({ timeout: 30_000 });
  }

  await page.getByRole("button", { name: /Ingest & operations/ }).click();
  await expect(page.getByRole("heading", { name: "Durable queue" })).toBeVisible();
  const retry = page.getByRole("button", { name: "Retry" }).first();
  if (await retry.count()) {
    await expect(retry).toBeVisible();
    await retry.click();
  } else {
    await expect(page.locator("tbody tr").first()).toBeVisible();
  }
  await page.getByRole("button", { name: "Workers", exact: true }).click();
  await expect(page.getByText("watcher-service", { exact: false }).first()).toBeVisible();
  await expect(page.getByText(/idle|stale|healthy|busy/).first()).toBeVisible();
  await page.getByRole("button", { name: "Backups", exact: true }).click();
  await expect(page.getByText("Succeeded", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/D:\\(LocalBackup|Backups)\\LocalKnowledgePortal/).first())
    .toBeVisible();
});

test("GPU queue shows host capacity, job groups, and read-only details", async ({ page }) => {
  await page.goto("/gpu-queue");
  await expect(page.getByRole("heading", { name: "GPU work queue" })).toBeVisible();
  await expect(page.getByText("Total VRAM", { exact: true })).toBeVisible();
  await expect(page.getByText("GPU utilization", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Active" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Queued" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent completed" })).toBeVisible();

  const completed = page.locator(".gpu-job-panel").filter({
    has: page.getByRole("heading", { name: "Recent completed" }),
  });
  const row = completed.locator("tbody tr").first();
  if (await row.count()) {
    await row.click();
    await expect(page.locator(".gpu-detail .detail-grid")).toBeVisible();
    await expect(page.locator(".gpu-detail").getByText("Command", { exact: true })).toBeVisible();
  }
});
