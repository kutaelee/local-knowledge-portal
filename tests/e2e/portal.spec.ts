import { expect, test, type Page } from "@playwright/test";

const apiURL = process.env.LKP_E2E_API_URL ?? "http://127.0.0.1:8010";

async function openNavigation(page: Page, group: string, item: RegExp | string) {
  const groupButton = page.getByRole("button", { name: group, exact: true });
  if ((await groupButton.getAttribute("aria-expanded")) !== "true") {
    await groupButton.click();
  }
  await page.getByRole("button", { name: item }).click();
}

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
  await expect(page.getByText("WSL · Docker 연결 상태", { exact: true })).toHaveCount(1);
  await expect(page.getByText("Codex 훅 수집기", { exact: true })).toHaveCount(1);
  await expect(page.getByText("Ollama 지식 편집기", { exact: true })).toHaveCount(1);
  await expect(page.getByText("프로젝트 웹·API", { exact: true })).toHaveCount(1);
  await expect(page.getByText("미닝 운정점 웹사이트", { exact: true })).toHaveCount(1);

  await page.reload();
  await expect(page.getByRole("heading", {
    name: "지금 무엇이 최신인지 한눈에 확인하세요.",
  })).toBeVisible();

  const localizedScreens = [
    ["지식", "원본 파일", "원본 저장소와 파일"],
    ["지식", "통합 검색", "원본 지식 검색"],
    ["활동", "Codex 작업", "활동 이력"],
    ["지식", "프로젝트 지식", "프로젝트 지식"],
    ["운영", "수집·운영", "지속형 작업 큐"],
    ["활동", "변경 기록", "변경 타임라인"],
    ["지식", "문서 관계", "문서 연결 지도"],
  ] as const;
  for (const [group, navigation, heading] of localizedScreens) {
    await openNavigation(page, group, navigation);
    await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible();
  }
});

test("Korean overview makes deferred semantic recovery actionable", async ({ page }) => {
  await page.route(`${apiURL}/api/v1/metrics/summary`, async (route) => {
    const upstream = await route.fetch();
    const payload = await upstream.json();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...payload,
        embedding_runtime: {
          ...payload.embedding_runtime,
          open: true,
          mode: "deferred_gpu_recovery",
          reason: "gpu_recovery_pending",
        },
      }),
    });
  });
  await page.route(`${apiURL}/api/v1/embedding/recovery`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        runtime: {
          open: true,
          mode: "deferred_gpu_recovery",
          reason: "gpu_recovery_pending",
          recent_timeouts: 3,
          threshold: 3,
          window_seconds: 3600,
          last_timeout_at: "2026-07-25T08:00:00Z",
        },
        reindex: {
          id: "reindex-queued",
          status: "queued",
          submitted_at: "2026-07-25T08:00:00Z",
          started_at: null,
          finished_at: null,
          requested_vram_mb: 8192,
          estimated_seconds: 3600,
          priority: 30,
          scheduling_note: "head-blocks-backfill:8192>6213",
          error: null,
        },
        validation: null,
        progress: {
          pending_documents: 116,
          pending_chunks: 1438,
          embedding_revision: "test-revision",
        },
        scheduler_decision: "head-blocks-backfill:8192>6213",
      }),
    });
  });

  await page.goto("/");
  await page.getByLabel("Language").selectOption("ko");
  const recoveryBanner = page.locator(".semantic-recovery:visible");
  await expect(recoveryBanner).toBeVisible();
  await expect(recoveryBanner).toContainText("의미 검색 복구가 GPU 예약을 기다리고 있습니다.");
  await expect(recoveryBanner).toContainText("예약 대기");
  await expect(recoveryBanner).toContainText("8 GB");
  await expect(recoveryBanner).toContainText("스케줄러가 상태를 확인했습니다");
  await expect(recoveryBanner).toContainText("문서 116건 · 검색 조각 1,438개");
});

test("Korean overview does not mislabel an unavailable scheduler as no reservation", async ({ page }) => {
  await page.route(`${apiURL}/api/v1/metrics/summary`, async (route) => {
    const upstream = await route.fetch();
    const payload = await upstream.json();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...payload,
        embedding_runtime: {
          ...payload.embedding_runtime,
          open: true,
          mode: "deferred_gpu_recovery",
          reason: "gpu_recovery_pending",
        },
      }),
    });
  });
  await page.route(`${apiURL}/api/v1/embedding/recovery`, async (route) => {
    await route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
  });

  await page.goto("/");
  await page.getByLabel("Language").selectOption("ko");
  await expect(page.getByText("스케줄러 연결 확인 필요", { exact: true })).toBeVisible();
  await expect(page.getByText("GPU 스케줄러 상태를 확인할 수 없습니다.", { exact: true }))
    .toBeVisible();
  await expect(page.getByText("예약 없음", { exact: true })).not.toBeVisible();
});

test("overview, explorer, document versions, and provenance", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "See what is current, at a glance." }))
    .toBeVisible();
  await openNavigation(page, "Knowledge", /Source files/);
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

  await openNavigation(page, "Knowledge", /Unified search/);
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
  await openNavigation(page, "Activity", /Codex work/);
  await expect(page.getByRole("heading", { name: "Activity history" })).toBeVisible();
  await page.locator(".record-list > button").first().click();
  await expect(page.getByText("Reported result", { exact: true })).toBeVisible();
  await expect(page.getByText("Verified result", { exact: true })).toBeVisible();

  await openNavigation(page, "Knowledge", /Project knowledge/);
  await expect(page.getByRole("heading", { name: "Project knowledge" })).toBeVisible();
  await expect(page.getByText("Project · work type", { exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Current document", exact: true })).toBeVisible();
  await expect(page.getByText("Evidence-based knowledge curator", { exact: true })).toBeVisible();
  await expect(page.getByText(/qwen3\.5:9b-q4_K_M/).first()).toBeVisible();
  await expect(page.getByText(/evidence-blog-v10/)).toBeVisible();
  await expect(page.getByRole("tab", { name: "Current document", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );

  await page.getByRole("button", { name: "Held candidates" }).click();
  await expect(page.locator(".knowledge-tree")).toBeVisible();
  await expect(page.locator(".knowledge-tree").getByText("All projects", { exact: true }))
    .toBeVisible();
  await expect(page.locator(".record-list")).toBeVisible();

  await page.getByRole("button", { name: /Project documents/ }).click();
  await page.getByRole("tab", { name: "Work history", exact: true }).click();
  await expect(page.locator(".knowledge-tree")).toBeVisible();
  await expect(page.locator(".knowledge-tree").getByText("All projects", { exact: true }))
    .toBeVisible();
  await page.locator(".record-list > button").first().click();
  await expect(page.getByRole("heading", { name: "Execution verification", exact: true }))
    .toBeVisible();
  await expect(page.getByRole("heading", { name: "Knowledge references", exact: true }))
    .toBeVisible();
  await expect(page.locator(".page-controls").first()).toBeVisible();
});

test("keyword, semantic, hybrid, operations, and worker heartbeat", async ({ page }) => {
  await page.goto("/");
  await openNavigation(page, "Knowledge", /Unified search/);
  const input = page.getByPlaceholder(/Filename, error/);
  const mode = page.getByLabel("Search mode");
  for (const value of ["keyword", "semantic", "hybrid"]) {
    await mode.selectOption(value);
    await input.fill("PostgreSQL");
    await page.getByRole("button", { name: "Search", exact: true }).last().click();
    await expect(page.locator(".result-card").first()).toBeVisible({ timeout: 30_000 });
  }

  await openNavigation(page, "Operations", /Ingest & operations/);
  await expect(page.getByRole("heading", { name: "Durable queue" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Queue · processing (ms)" })).toBeVisible();
  const retry = page.getByRole("button", { name: "Retry" }).first();
  if (await retry.count()) {
    await expect(retry).toBeVisible();
    await retry.click();
  } else {
    await expect(page.locator("tbody tr").first()).toBeVisible();
  }
  await page.getByRole("button", { name: "Workers", exact: true }).click();
  await expect(page.getByText(/watcher/).first()).toBeVisible();
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
  await expect(page.getByRole("heading", { name: "Ollama model status" })).toBeVisible();
  const unifiedRuntime = page.locator(".gpu-external-row").filter({
    hasText: "Workstation Ollama",
  });
  await expect(unifiedRuntime).toHaveCount(1);
  await expect(page.getByText("Knowledge portal embedding Ollama", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Knowledge portal generation Ollama", { exact: true })).toHaveCount(0);
  const unload = unifiedRuntime.getByRole("button", { name: "Unload model" });
  if (await unload.count()) {
    await expect(unload).toBeEnabled();
  } else {
    await expect(unifiedRuntime.getByText("No loaded model", { exact: true })).toBeVisible();
  }
  await expect(page.getByRole("heading", { name: "Active" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Queued" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Recent completed" })).toBeVisible();

  const active = page.locator(".gpu-job-panel").filter({
    has: page.getByRole("heading", { name: "Active" }),
  });
  if (await active.locator("tbody tr").count()) {
    await expect(active.getByRole("button", { name: "Safe stop" }).first()).toBeVisible();
  }

  const completed = page.locator(".gpu-job-panel").filter({
    has: page.getByRole("heading", { name: "Recent completed" }),
  });
  const row = completed.locator("tbody tr").first();
  if (await row.count()) {
    await row.click();
    const inlineDetail = completed.locator(".gpu-inline-detail");
    await expect(inlineDetail.locator(".detail-grid").first()).toBeVisible();
    await inlineDetail.getByText("Technical details", { exact: true }).click();
    await expect(inlineDetail.getByText("Command", { exact: true })).toBeVisible();
  }
});

test("repository analysis and GPU queue render inside the portal workspace", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Language").selectOption("ko");

  await openNavigation(page, "운영", /레포 분석/);
  await expect(page).toHaveURL("/");
  await expect(page.locator(".topbar")).toBeVisible();
  await expect(page.locator(".sidebar")).toBeVisible();
  await expect(page.getByRole("heading", { name: "저장소 분석", exact: true })).toBeVisible();
  await expect(page.locator(".repo-analysis-page .gpu-topbar")).toHaveCount(0);
  await page.getByRole("tab", { name: "처리 흐름", exact: true }).click();
  await expect(page.getByRole("img", { name: /저장소가 수행하는 처리 생명주기/ }))
    .toBeVisible();
  await expect(page.getByRole("heading", { name: "처리 생명주기", exact: true }))
    .toBeVisible();
  await expect(page.getByText("6단계", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "실행 시작", exact: true }))
    .toBeVisible();
  await expect(page.getByRole("heading", { name: "결과 제공", exact: true }))
    .toBeVisible();

  await openNavigation(page, "운영", /GPU 작업 큐/);
  await expect(page).toHaveURL("/");
  await expect(page.locator(".topbar")).toBeVisible();
  await expect(page.locator(".sidebar")).toBeVisible();
  await expect(page.getByRole("heading", { name: "GPU 작업 큐", exact: true })).toBeVisible();
  await expect(page.locator(".gpu-page .gpu-topbar")).toHaveCount(0);
});

test("service health stays visible and registered control requires confirmation", async ({ page }) => {
  let confirmationRequests = 0;
  let controlRequests = 0;
  await page.route(
    "**/api/v1/service-manager/comfyui/stop/confirmation",
    async (route) => {
      confirmationRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          confirmation_token: "one-time-confirmation-token",
          expires_in_seconds: 90,
        }),
      });
    }
  );
  await page.route(
    "**/api/v1/service-manager/comfyui/stop",
    async (route) => {
      controlRequests += 1;
      const payload = route.request().postDataJSON();
      expect(payload.confirmed).toBe(true);
      expect(payload.confirmation_token).toBe("one-time-confirmation-token");
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "accepted" }),
      });
    }
  );
  await page.route("**/api/v1/service-manager", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ready",
        checked_at: "2026-07-26T05:00:00Z",
        summary: { healthy: 2, attention: 1, controllable: 2 },
        services: [
          {
            id: "comfyui",
            label: "ComfyUI",
            category: "ai",
            description: "GPU 예약 큐를 통해 실행되는 이미지 생성 인터페이스입니다.",
            state: "healthy",
            detail: "GPU 예약에서 실행 중",
            can_start: false,
            can_stop: true,
            warning: "실행 또는 대기 중인 생성 작업이 있으면 안전 중지가 거부됩니다.",
            components: [],
          },
          {
            id: "ollama",
            label: "Ollama 공용 모델 서버",
            category: "ai",
            description: "공유 모델 서버입니다.",
            state: "healthy",
            detail: "HTTP 200",
            can_start: false,
            can_stop: false,
            warning: null,
            components: [],
          },
        ],
      }),
    });
  });
  await page.goto("/");
  await page.getByLabel("Language").selectOption("ko");
  await expect(page.getByRole("button", { name: "서비스 연결 상태 열기" })).toBeVisible();

  await page.getByRole("button", { name: "운영", exact: true }).click();
  await page.getByRole("button", { name: /레포·서비스 관리/ }).click();
  await expect(
    page.getByRole("heading", { name: "레포·서비스 관리", exact: true })
  ).toBeVisible();
  await expect(page.getByText("Ollama 공용 모델 서버", { exact: true })).toBeVisible();
  await expect(page.getByText("조회 전용", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "안전 중지", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("서비스를 중지할까요?");
  await expect(page.getByRole("dialog")).toContainText("ComfyUI");
  await expect(page.getByRole("button", { name: "실행", exact: true })).toBeEnabled();
  expect(confirmationRequests).toBe(1);
  expect(controlRequests).toBe(0);
  await page.getByRole("button", { name: "취소", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(controlRequests).toBe(0);

  await page.getByRole("button", { name: "안전 중지", exact: true }).click();
  await expect(page.getByRole("button", { name: "실행", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "실행", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(controlRequests).toBe(1);
});

test("canonical project article opens sentence-level original evidence", async ({ page }) => {
  await page.route("**/api/v1/project-journal/projects?**", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        items: [{
          id: "mock-project",
          project: "mock-project",
          title: "A readable current project article",
          entry_count: 4,
          document_count: 3,
          latest_at: "2026-07-26T03:00:00Z",
          verification_status: "CITED",
          article_status: "current",
          revision_number: 2,
        }],
        page: 1,
        page_size: 25,
        total: 1,
      }),
    });
  });
  await page.route("**/api/v1/project-journal/projects/mock-project", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "mock-project",
        project: "mock-project",
        title: "A readable current project article",
        entry_count: 4,
        visible_entry_count: 1,
        latest_at: "2026-07-26T03:00:00Z",
        verification_status: "CITED",
        article_status: "current",
        content_type: "canonical_article",
        revision_number: 2,
        prompt_version: "project-article-v3-hierarchical-source-manifest",
        model: "qwen3.5:9b-q4_K_M",
        model_digest: "sha256:test",
        standfirst: {
          sentences: [{
            text: "The portal keeps one coherent article per project.",
            source_ids: ["D:doc:0"],
          }],
        },
        sections: [{
          key: "operation",
          title: "Current operation",
          paragraphs: [{
            sentences: [{
              text: "Changed sources create an append-only article revision.",
              source_ids: ["D:doc:0"],
            }],
          }],
        }],
        sources: [{
          id: "D:doc:0",
          source_type: "document_chunk",
          content_hash: "source-hash",
          title: "docs/architecture.md · Revision workflow",
          document_id: "doc",
          document_version_id: "version",
          chunk_id: "chunk",
          relative_path: "docs/architecture.md",
          canonical_path: "/sources/mock/docs/architecture.md",
          start_line: 10,
          end_line: 18,
          excerpt: "A source change creates a new immutable revision.",
          citation_number: 1,
        }],
        change_summary: {
          new_or_changed_sources: 1,
          removed_sources: 0,
          total_sources: 7,
          cited_sources: 1,
          batches: 1,
        },
        truncated: false,
      }),
    });
  });

  await page.goto("/");
  await openNavigation(page, "Knowledge", /Project knowledge/);
  await expect(page.getByRole("heading", {
    name: "A readable current project article",
  })).toBeVisible();
  await expect(page.locator(".project-article-standfirst")).toContainText(
    "The portal keeps one coherent article per project.",
  );
  const standfirstCitation = page.locator(
    ".project-article-standfirst .sentence-citations button",
  );
  await expect(standfirstCitation).toHaveCount(1);
  await standfirstCitation.click();
  await expect(page.getByText("Cited original", { exact: true })).toBeVisible();
  await expect(page.getByText("docs/architecture.md", { exact: true })).toBeVisible();
  await expect(page.getByText("10–18", { exact: true })).toBeVisible();
  await expect(page.getByText(
    "A source change creates a new immutable revision.",
    { exact: true },
  )).toBeVisible();
});
