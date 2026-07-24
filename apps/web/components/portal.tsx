"use client";

import { useGSAP } from "@gsap/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ColumnDef, flexRender, getCoreRowModel, useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Activity, AlertTriangle, Blocks, BookOpen, ChevronDown, ChevronRight,
  CircleCheck, Clock3, Command, Database, FileCode2, Files, Folder,
  GitBranch, HeartPulse, LayoutDashboard, Moon, Network, PanelRightClose,
  RefreshCcw, Search, ServerCog, Sun, TerminalSquare, BookCheck, Languages, Cpu,
} from "lucide-react";
import gsap from "gsap";
import {
  FormEvent, useEffect, useMemo, useRef, useState,
} from "react";
import {
  Area, AreaChart, ResponsiveContainer, Tooltip, XAxis,
} from "recharts";
import { api, Metrics, SearchResult, TreeItem } from "@/lib/api";
import { ActivityHistory, DocumentViewer, KnowledgeCases } from "./knowledge-views";

gsap.registerPlugin(useGSAP);

type View = "overview" | "explorer" | "document" | "search" | "activities" |
  "knowledge" | "operations" | "timeline" | "graph";
type Job = {
  id: string; status: string; job_type: string; path: string; attempt_count: number;
  max_attempts: number; error_type: string | null; error_message: string | null; created_at: string;
};
type Locale = "ko" | "en";

const nav: { id: View; icon: React.ComponentType<{ size?: number }> }[] = [
  { id: "overview", icon: LayoutDashboard },
  { id: "explorer", icon: Folder },
  { id: "search", icon: Search },
  { id: "activities", icon: Activity },
  { id: "knowledge", icon: BookCheck },
  { id: "operations", icon: ServerCog },
  { id: "timeline", icon: Clock3 },
  { id: "graph", icon: Network },
];

const translations = {
  ko: {
    nav: {
      overview: "현황", explorer: "저장소 탐색", document: "문서",
      search: "검색", activities: "활동 이력", knowledge: "지식 사례",
      operations: "운영", timeline: "변경 타임라인", graph: "지식 그래프",
      gpuQueue: "GPU 작업 큐",
    },
    workspace: "작업 공간",
    globalSearch: "문서, 경로, 코드 심볼 검색…",
    globalSearchLabel: "전체 검색",
    healthy: "정상",
    offline: "연결 안 됨",
    language: "언어",
    theme: "테마 전환",
    context: "정보 패널 전환",
    pipeline: "수집 파이프라인",
    indexingService: "인덱싱 서비스",
    localOnly: "로컬 전용 · 원본 읽기 전용",
    contextPanel: {
      title: "선택 항목 정보", lines: "원본 줄", version: "문서 버전", chunk: "검색 단위",
      hash: "콘텐츠 해시", indexed: "인덱싱 시각", copy: "인용 정보 복사",
      empty: "검색 결과를 선택하면 원본 경로와 변경 불가능한 출처 정보를 확인할 수 있습니다.",
      services: "연결 상태", healthy: "정상", offline: "연결 안 됨", unavailable: "사용 불가",
    },
    overview: {
      eyebrow: "지식베이스 운영 현황",
      title: "지금 무엇이 최신인지 한눈에 확인하세요.",
      subtitle: "문서 갱신 시각, 수집 대기열, 실패 작업, 소스 상태를 실제 데이터 기준으로 보여줍니다.",
      search: "지식 검색",
      dataAsOf: "데이터 기준",
      latestIndex: "마지막 인덱싱",
      statusGood: "지식베이스가 최신 상태입니다",
      statusIndexing: "새 파일을 인덱싱하고 있습니다",
      statusAttention: "확인이 필요한 작업이 있습니다",
      statusGoodDetail: "대기 중이거나 실패한 수집 작업이 없습니다.",
      projects: "프로젝트",
      projectsNote: "등록된 소스에서 식별한 프로젝트 수",
      documents: "인덱싱된 파일",
      documentsNote: "지식 문서, 코드, 설정·데이터 파일의 최신 상태",
      chunks: "검색 조각",
      chunksNote: "전체는 키워드·심볼 검색, 선별 문서만 의미 검색",
      knowledgeDocs: "지식 문서",
      codeFiles: "코드",
      supportFiles: "설정·데이터",
      semanticCoverage: "의미 검색",
      initialScan: "초기 스캔",
      liveChanges: "실시간 변경",
      pending: "대기 작업",
      pendingNote: "가장 오래된 대기",
      ingestion: "실제 수집량",
      throughput: "최근 12시간 인덱싱",
      noThroughput: "최근 12시간에 완료된 인덱싱이 없습니다.",
      pipelineRevision: "검색 인덱스 기준",
      activeRevision: "현재 적용 중인 모델과 파이프라인",
      embedding: "임베딩 모델",
      revision: "벡터 리비전",
      pipelineVersion: "파이프라인 버전",
      repositoryMode: "저장소 의미 검색 범위",
      docsOnly: "문서만 · 코드는 키워드/심볼",
      searchLatency: "최근 1시간 검색 지연",
      cacheUsage: "질의 임베딩 캐시",
      noSearches: "검색 기록 없음",
      workers: "현재 동작 중인 서비스",
      queue: "작업 큐",
      failed: "실패",
      processing: "처리 중",
      succeeded: "누적 완료 이력",
      recentSucceeded: "최근 3시간 완료",
      queueRate: "시간당 처리량",
      estimatedDrain: "예상 대기 해소",
      estimateUnavailable: "산정 불가",
      oldestPending: "최장 대기",
      recentTitle: "최근 반영된 문서",
      recentHelp: "인덱스에 가장 최근 새 버전이 만들어진 문서입니다.",
      sourceTitle: "소스 최신성",
      sourceHelp: "마지막 전체 대조 시각과 현재 활성 문서 수입니다.",
      reconciled: "전체 대조",
      indexed: "인덱싱",
      sourceModified: "원본 수정",
      fresh: "최신",
      aging: "확인 필요",
      stale: "오래됨",
      created: "새 문서",
      modified: "수정",
      restored: "복원",
      browse: "문서 둘러보기",
      browseNote: "프로젝트와 폴더 구조로 탐색",
      jobs: "작업 상태 확인",
      jobsNote: "실패 원인과 재시도 확인",
      timeline: "변경 흐름 보기",
      timelineNote: "관측된 파일 변경을 시간순 확인",
      never: "기록 없음",
      justNow: "방금",
    },
    explorer: {
      eyebrow: "읽기 전용 탐색기",
      title: "저장소와 파일",
      visible: "표시 파일",
      limited: "일부만 표시",
      treeLabel: "소스 트리",
      loadError: "소스 트리를 불러오지 못했습니다.",
      emptyTitle: "인덱싱된 파일이 없습니다",
      emptyDetail: "스캔과 worker를 실행하면 저장소 트리가 채워집니다.",
    },
    searchView: {
      eyebrow: "근거 기반 검색",
      title: "원본 지식 검색",
      subtitle: "모든 결과에 문서·버전·검색 단위·경로·원본 줄 출처가 포함됩니다.",
      placeholder: "파일명, 오류, ADR, 심볼 또는 자연어…",
      mode: "검색 방식",
      modes: { hybrid: "하이브리드", keyword: "키워드", semantic: "의미", path: "경로", symbol: "심볼" },
      submit: "검색",
      project: "프로젝트",
      allProjects: "전체 프로젝트",
      tags: "상황 태그",
      tagAll: "선택 태그 모두",
      tagAny: "선택 태그 중 하나",
      clearTags: "태그 해제",
      results: "개 결과",
      confidence: "신뢰도",
      loadError: "검색에 실패했습니다. 의미 검색에는 Ollama가 필요하지만 키워드 검색은 계속 사용할 수 있습니다.",
      emptyTitle: "근거가 있는 결과가 없습니다",
      emptyDetail: "파일명, 경로 일부, 정확한 심볼 또는 더 넓은 표현으로 검색해 보세요.",
      scores: { lexical: "키워드", vector: "의미", fused: "종합" },
    },
    operations: {
      eyebrow: "운영",
      title: "지속형 작업 큐",
      subtitle: "재시도는 새 작업으로 기록되며 기존 이력은 보존됩니다.",
      refresh: "새로고침",
      tabs: { jobs: "작업", workers: "Worker", backups: "백업" },
      headers: {
        status: "상태", type: "유형", path: "경로", attempts: "시도",
        error: "오류", worker: "Worker", host: "호스트", mode: "감시 방식",
        guard: "부하 보호", cpu: "CPU", heartbeat: "Heartbeat",
        processed: "처리", failed: "실패", created: "생성 시각",
        revision: "리비전", checksum: "SHA-256",
      },
      retry: "재시도",
      enabled: "적용",
      paused: "일시 정지",
      queueEmpty: "작업 이력이 없습니다",
      queueEmptyDetail: "아직 생성된 수집 작업이 없습니다.",
      noHeartbeat: "Worker heartbeat가 없습니다",
      noHeartbeatDetail: "인덱서 worker를 시작하세요.",
      noBackup: "백업 증거가 없습니다",
      noBackupDetail: "백업 스크립트를 실행하세요.",
    },
    timelineView: {
      eyebrow: "관측된 변경 이력",
      title: "변경 타임라인",
      subtitle: "파일시스템과 수집 파이프라인 이벤트를 최신순으로 표시합니다.",
      system: "시스템 이벤트",
      emptyTitle: "아직 이벤트가 없습니다",
      emptyDetail: "인덱싱·실패·재시도 이력이 여기에 표시됩니다.",
      events: {
        reconciled: "전체 대조", indexed: "인덱싱 완료", watcher_batch: "파일 변경 감지",
        ignored: "수집 제외", deleted: "삭제 감지", renamed: "이름 변경",
        restored: "복원", failed: "처리 실패", retried: "재시도",
      },
    },
    graphView: {
      eyebrow: "문서 관계",
      title: "지식 그래프",
      subtitle: "범위를 제한해 브라우저가 무제한 데이터를 한 번에 그리지 않도록 합니다.",
      emptyTitle: "링크 추출을 위한 그래프 어댑터가 준비되었습니다",
      emptyDetail: "Markdown 위키링크와 문서 링크가 추출되면 표시됩니다. 기본 탐색은 문서 탐색과 검색을 사용합니다.",
    },
    statusLabels: {
      healthy: "정상", succeeded: "완료", active: "활성", idle: "대기",
      processing: "처리 중", busy: "처리 중", cooldown: "냉각 중",
      paused: "일시 정지", pending: "대기", failed: "실패",
      dead_letter: "격리", cancelled: "취소", stopped: "중지",
      error: "오류", stale: "응답 지연", offline: "연결 안 됨",
    },
  },
  en: {
    nav: {
      overview: "Overview", explorer: "Repository explorer", document: "Document",
      search: "Search", activities: "Activity", knowledge: "Knowledge cases",
      operations: "Operations", timeline: "Timeline", graph: "Knowledge graph",
      gpuQueue: "GPU queue",
    },
    workspace: "Workspace",
    globalSearch: "Search documents, paths, symbols…",
    globalSearchLabel: "Global search",
    healthy: "Healthy",
    offline: "Offline",
    language: "Language",
    theme: "Toggle theme",
    context: "Toggle context panel",
    pipeline: "Pipeline",
    indexingService: "Indexing service",
    localOnly: "localhost only · read-only sources",
    contextPanel: {
      title: "Context", lines: "Lines", version: "Version", chunk: "Chunk",
      hash: "Hash", indexed: "Indexed", copy: "Copy citation",
      empty: "Select a result to inspect immutable provenance.",
      services: "Services", healthy: "healthy", offline: "offline", unavailable: "unavailable",
    },
    overview: {
      eyebrow: "Knowledge base status",
      title: "See what is current, at a glance.",
      subtitle: "Live document freshness, queue state, failures, and source reconciliation from the database.",
      search: "Search knowledge",
      dataAsOf: "Data as of",
      latestIndex: "Latest indexing",
      statusGood: "Knowledge is up to date",
      statusIndexing: "New files are being indexed",
      statusAttention: "Some jobs need attention",
      statusGoodDetail: "No pending or failed ingest work.",
      projects: "Projects",
      projectsNote: "Project groups identified in registered sources",
      documents: "Indexed files",
      documentsNote: "Current knowledge, code, and support files",
      chunks: "Search chunks",
      chunksNote: "All support lexical/symbol search; selected documents support semantics",
      knowledgeDocs: "knowledge docs",
      codeFiles: "code",
      supportFiles: "support",
      semanticCoverage: "semantic",
      initialScan: "initial scan",
      liveChanges: "live changes",
      pending: "Pending jobs",
      pendingNote: "Oldest pending",
      ingestion: "Actual ingestion",
      throughput: "Indexed in the last 12 hours",
      noThroughput: "No indexing completed in the last 12 hours.",
      pipelineRevision: "Search index basis",
      activeRevision: "Active model and pipeline",
      embedding: "Embedding model",
      revision: "Vector revision",
      pipelineVersion: "Pipeline version",
      repositoryMode: "Repository semantic scope",
      docsOnly: "Docs only · code uses keyword/symbol",
      searchLatency: "Search latency (last hour)",
      cacheUsage: "Query embedding cache",
      noSearches: "No recent searches",
      workers: "Active services",
      queue: "Job queue",
      failed: "Failed",
      processing: "Processing",
      succeeded: "Cumulative history",
      recentSucceeded: "Succeeded in 3h",
      queueRate: "Hourly throughput",
      estimatedDrain: "Estimated drain",
      estimateUnavailable: "Unavailable",
      oldestPending: "Oldest pending",
      recentTitle: "Recently indexed documents",
      recentHelp: "Documents whose newest version most recently entered the index.",
      sourceTitle: "Source freshness",
      sourceHelp: "Last full reconciliation and current active document count.",
      reconciled: "Reconciled",
      indexed: "Indexed",
      sourceModified: "Source modified",
      fresh: "Fresh",
      aging: "Check soon",
      stale: "Stale",
      created: "Created",
      modified: "Modified",
      restored: "Restored",
      browse: "Browse sources",
      browseNote: "Navigate projects and folders",
      jobs: "Inspect job health",
      jobsNote: "Review failures and retries",
      timeline: "View change timeline",
      timelineNote: "Follow observed file changes",
      never: "No record",
      justNow: "just now",
    },
    explorer: {
      eyebrow: "Read-only explorer", title: "Repositories & files",
      visible: "visible files", limited: "limited view", treeLabel: "Source tree",
      loadError: "The source tree could not be loaded.",
      emptyTitle: "No indexed documents",
      emptyDetail: "Run a scan and worker to populate the tree.",
    },
    searchView: {
      eyebrow: "Retrieval", title: "Search the source of truth",
      subtitle: "Every result carries document, version, chunk, path, and line provenance.",
      placeholder: "Filename, error, ADR, symbol, or natural language…",
      mode: "Search mode",
      modes: { hybrid: "Hybrid", keyword: "Keyword", semantic: "Semantic", path: "Path", symbol: "Symbol" },
      submit: "Search", results: " results", confidence: "confidence",
      project: "Project", allProjects: "All projects", tags: "Situation tags",
      tagAll: "Match all selected", tagAny: "Match any selected", clearTags: "Clear tags",
      loadError: "Search failed. Semantic mode requires Ollama; keyword mode remains available.",
      emptyTitle: "No grounded result",
      emptyDetail: "Try a filename, path segment, exact symbol, or broader wording.",
      scores: { lexical: "lex", vector: "vec", fused: "rrf" },
    },
    operations: {
      eyebrow: "Operations", title: "Durable queue",
      subtitle: "Retries create a new auditable job; original history is retained.",
      refresh: "Refresh", tabs: { jobs: "Jobs", workers: "Workers", backups: "Backups" },
      headers: {
        status: "Status", type: "Type", path: "Path", attempts: "Attempts",
        error: "Error", worker: "Worker", host: "Host", mode: "Mode",
        guard: "Guard", cpu: "CPU", heartbeat: "Heartbeat",
        processed: "Processed", failed: "Failed", created: "Created",
        revision: "Revision", checksum: "SHA-256",
      },
      retry: "Retry", enabled: "Enabled", paused: "Paused",
      queueEmpty: "Queue is empty", queueEmptyDetail: "No ingest jobs have been created yet.",
      noHeartbeat: "No worker heartbeat", noHeartbeatDetail: "Start the indexer worker.",
      noBackup: "No backup evidence", noBackupDetail: "Run the backup script.",
    },
    timelineView: {
      eyebrow: "Observed history", title: "Timeline",
      subtitle: "Filesystem and pipeline events, newest first.", system: "system event",
      emptyTitle: "No events yet",
      emptyDetail: "Indexed, failed, and retried work will appear here.",
      events: {
        reconciled: "Reconciled", indexed: "Indexed", watcher_batch: "Watcher batch",
        ignored: "Ignored", deleted: "Deleted", renamed: "Renamed",
        restored: "Restored", failed: "Failed", retried: "Retried",
      },
    },
    graphView: {
      eyebrow: "Relationships", title: "Knowledge graph",
      subtitle: "Scoped graph views prevent the browser from rendering an unbounded dataset.",
      emptyTitle: "Graph adapter is ready for links",
      emptyDetail: "Markdown wikilinks and document links will appear after link extraction. Explorer and search remain the primary navigation.",
    },
    statusLabels: {
      healthy: "Healthy", succeeded: "Succeeded", active: "Active", idle: "Idle",
      processing: "Processing", busy: "Busy", cooldown: "Cooldown",
      paused: "Paused", pending: "Pending", failed: "Failed",
      dead_letter: "Dead letter", cancelled: "Cancelled", stopped: "Stopped",
      error: "Error", stale: "Stale", offline: "Offline",
    },
  },
} as const;

function useTheme() {
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  useEffect(() => {
    const saved = localStorage.getItem("lkp-theme");
    const next = saved === "light" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
  }, []);
  const toggle = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    document.documentElement.dataset.theme = next;
    localStorage.setItem("lkp-theme", next);
  };
  return { theme, toggle };
}

function useLocale() {
  const [locale, setLocaleState] = useState<Locale>("ko");
  useEffect(() => {
    const saved = localStorage.getItem("lkp-locale");
    const next: Locale = saved === "en" || saved === "ko"
      ? saved : navigator.language.toLowerCase().startsWith("ko") ? "ko" : "en";
    setLocaleState(next);
    document.documentElement.lang = next;
  }, []);
  const setLocale = (next: Locale) => {
    setLocaleState(next);
    localStorage.setItem("lkp-locale", next);
    document.documentElement.lang = next;
  };
  return { locale, setLocale };
}

export function Portal() {
  const [view, setView] = useState<View>("overview");
  const [contextOpen, setContextOpen] = useState(true);
  const [selected, setSelected] = useState<SearchResult | null>(null);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const { theme, toggle } = useTheme();
  const { locale, setLocale } = useLocale();
  const text = translations[locale];
  const main = useRef<HTMLElement>(null);
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api<{ status: string; database: boolean; ollama: boolean }>("/health/ready"),
    refetchInterval: 15_000,
  });
  useGSAP(() => {
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    gsap.fromTo(".view-enter", { opacity: 0, y: 8 }, { opacity: 1, y: 0, duration: 0.24 });
  }, { dependencies: [view], scope: main });
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        document.getElementById("global-search")?.focus();
      }
    };
    addEventListener("keydown", handler);
    return () => removeEventListener("keydown", handler);
  }, []);

  const submitGlobal = (event: FormEvent) => {
    event.preventDefault();
    if (query.trim()) setView("search");
  };

  return (
    <div className={`portal ${contextOpen ? "" : "context-collapsed"}`}>
      <header className="topbar">
        <button className="brand" onClick={() => setView("overview")} aria-label={text.nav.overview}>
          <span className="brand-mark"><Blocks size={17} /></span>
          <span>Local Knowledge</span>
          <span className="local-pill">LOCAL</span>
        </button>
        <form className="global-search" onSubmit={submitGlobal}>
          <Search size={17} />
          <input
            id="global-search" value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder={text.globalSearch} aria-label={text.globalSearchLabel}
          />
          <kbd><Command size={12} />K</kbd>
        </form>
        <div className="top-actions">
          <span className={`health ${health.isSuccess ? "ok" : "bad"}`}>
            <span /> {health.isSuccess ? text.healthy : text.offline}
          </span>
          <label className="language-select" aria-label={text.language}>
            <Languages size={15} />
            <select value={locale} onChange={(event) => setLocale(event.target.value as Locale)}>
              <option value="ko">한국어</option>
              <option value="en">English</option>
            </select>
          </label>
          <button className="icon-button" onClick={toggle} aria-label={text.theme}>
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          </button>
          <button
            className="icon-button" onClick={() => setContextOpen((value) => !value)}
            aria-label={text.context}
          ><PanelRightClose size={17} /></button>
        </div>
      </header>

      <aside className="sidebar">
        <nav aria-label="Primary navigation">
          <p className="nav-heading">{text.workspace}</p>
          {nav.map((item) => (
            <button
              key={item.id} className={view === item.id ? "active" : ""}
              onClick={() => setView(item.id)}
            >
              <item.icon size={17} /> {text.nav[item.id]}
            </button>
          ))}
          <a href="/gpu-queue"><Cpu size={17} /> {text.nav.gpuQueue}</a>
        </nav>
        <div className="sidebar-bottom">
          <p>{text.pipeline}</p>
          <div><span className="pulse-dot" /> {text.indexingService}</div>
          <small>{text.localOnly}</small>
        </div>
      </aside>

      <main ref={main} className="main">
        <div className="view-enter" key={view}>
          {view === "overview" && <Overview onNavigate={setView} locale={locale} />}
          {view === "explorer" && <Explorer locale={locale} onOpen={(item) => {
            setDocumentId(item.id); setView("document");
          }} />}
          {view === "document" && documentId &&
            <DocumentViewer documentId={documentId} locale={locale} />}
          {view === "search" && (
            <SearchView locale={locale} initialQuery={query} onSelect={(item) => {
              setSelected(item); setContextOpen(true);
            }} />
          )}
          {view === "operations" && <Operations locale={locale} />}
          {view === "activities" && <ActivityHistory locale={locale} />}
          {view === "knowledge" && <KnowledgeCases locale={locale} />}
          {view === "timeline" && <Timeline locale={locale} />}
          {view === "graph" && <GraphNotice locale={locale} />}
        </div>
      </main>

      <aside className="context">
        <ContextPanel selected={selected} health={health.data} locale={locale} />
      </aside>
    </div>
  );
}

function formatRelative(value: string | null | undefined, locale: Locale, empty: string) {
  if (!value) return empty;
  const deltaSeconds = Math.round((Date.parse(value) - Date.now()) / 1000);
  const absolute = Math.abs(deltaSeconds);
  if (absolute < 30) return locale === "ko" ? "방금" : "just now";
  const formatter = new Intl.RelativeTimeFormat(locale === "ko" ? "ko-KR" : "en-US", {
    numeric: "auto",
  });
  if (absolute < 3600) return formatter.format(Math.round(deltaSeconds / 60), "minute");
  if (absolute < 86400) return formatter.format(Math.round(deltaSeconds / 3600), "hour");
  return formatter.format(Math.round(deltaSeconds / 86400), "day");
}

function formatDuration(seconds: number, locale: Locale) {
  if (seconds < 60) return locale === "ko" ? `${Math.round(seconds)}초` : `${Math.round(seconds)}s`;
  if (seconds < 3600) {
    const minutes = Math.round(seconds / 60);
    return locale === "ko" ? `${minutes}분` : `${minutes}m`;
  }
  const hours = Math.round(seconds / 3600);
  return locale === "ko" ? `${hours}시간` : `${hours}h`;
}

function freshnessLevel(value: string | null): "fresh" | "aging" | "stale" {
  if (!value) return "stale";
  const ageMinutes = (Date.now() - Date.parse(value)) / 60000;
  if (ageMinutes <= 15) return "fresh";
  if (ageMinutes <= 60) return "aging";
  return "stale";
}

function Overview({ onNavigate, locale }: {
  onNavigate: (view: View) => void;
  locale: Locale;
}) {
  const metrics = useQuery({
    queryKey: ["metrics"], queryFn: () => api<Metrics>("/api/v1/metrics/summary"),
    refetchInterval: 10_000,
  });
  const data = metrics.data;
  const text = translations[locale].overview;
  const number = useMemo(
    () => new Intl.NumberFormat(locale === "ko" ? "ko-KR" : "en-US"),
    [locale],
  );
  const pending = data?.jobs?.pending ?? 0;
  const processing = (data?.jobs?.processing ?? 0) + (data?.jobs?.leased ?? 0);
  const failed = (data?.jobs?.failed ?? 0) + (data?.jobs?.dead_letter ?? 0);
  const statusTone = failed > 0 ? "danger" : pending + processing > 0 ? "warning" : "success";
  const statusTitle = failed > 0
    ? text.statusAttention : pending + processing > 0 ? text.statusIndexing : text.statusGood;
  const statusDetail = failed > 0
    ? locale === "ko"
      ? `실패 ${number.format(failed)}건 · 대기 ${number.format(pending)}건입니다. 실패 원인을 먼저 확인하세요.`
      : `${number.format(failed)} failed · ${number.format(pending)} pending. Review failures first.`
    : pending + processing > 0
      ? locale === "ko"
        ? `${number.format(processing)}건 처리 중 · ${number.format(pending)}건 대기 중입니다.`
        : `${number.format(processing)} processing · ${number.format(pending)} pending.`
      : text.statusGoodDetail;
  const cards = [
    [text.projects, data?.projects ?? 0, GitBranch, text.projectsNote],
    [
      text.documents,
      data?.documents ?? 0,
      Files,
      data
        ? `${number.format(data.document_breakdown.knowledge_documents)} ${text.knowledgeDocs} · ${
          number.format(data.document_breakdown.code_files)} ${text.codeFiles} · ${
          number.format(data.document_breakdown.support_files)} ${text.supportFiles}`
        : text.documentsNote,
    ],
    [
      text.chunks,
      data?.chunks ?? 0,
      Blocks,
      data
        ? `${number.format(data.semantic_chunks)} ${text.semanticCoverage} (${
          Math.round(data.semantic_coverage * 100)}%)`
        : text.chunksNote,
    ],
    [
      text.pending,
      pending,
      Clock3,
      data
        ? `${number.format(data.pending_breakdown.initial_scan)} ${text.initialScan} · ${
          number.format(data.pending_breakdown.live_changes)} ${text.liveChanges}`
        : text.pendingNote,
    ],
  ] as const;
  const throughput = data?.throughput ?? [];
  return (
    <section>
      <div className="page-title">
        <div><p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
          <p>{text.subtitle}</p></div>
        <button className="primary" onClick={() => onNavigate("search")}><Search size={16} /> {text.search}</button>
      </div>
      {metrics.isError && <ErrorState message="API metrics are unavailable." />}
      <div className={`freshness-banner ${statusTone}`}>
        <div className="freshness-icon">
          {statusTone === "danger" ? <AlertTriangle size={20} /> :
            statusTone === "warning" ? <RefreshCcw size={20} /> : <CircleCheck size={20} />}
        </div>
        <div><strong>{statusTitle}</strong><p>{statusDetail}</p></div>
        <dl>
          <div><dt>{text.dataAsOf}</dt><dd title={data?.generated_at
            ? new Date(data.generated_at).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")
            : undefined}>{formatRelative(data?.generated_at, locale, text.never)}</dd></div>
          <div><dt>{text.latestIndex}</dt><dd title={data?.latest_indexed_at
            ? new Date(data.latest_indexed_at).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")
            : undefined}>{formatRelative(data?.latest_indexed_at, locale, text.never)}</dd></div>
        </dl>
      </div>
      <div className="stat-grid">
        {cards.map(([label, value, Icon, note]) => (
          <article className="stat-card" key={label}>
            <div className="stat-head"><span>{label}</span><Icon size={17} /></div>
            <strong>{number.format(Number(value))}</strong><small>{note}</small>
          </article>
        ))}
      </div>
      <div className="overview-grid">
        <article className="panel throughput">
          <div className="panel-head"><div><p className="eyebrow">{text.ingestion}</p><h2>{text.throughput}</h2></div>
            <span className="chip success"><Activity size={13} /> DB</span></div>
          {throughput.length ? <div className="chart">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={throughput}>
                  <defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0" stopColor="var(--accent)" stopOpacity={0.45} />
                    <stop offset="1" stopColor="var(--accent)" stopOpacity={0} />
                  </linearGradient></defs>
                  <XAxis dataKey="bucket" tickFormatter={(value) =>
                    new Date(value).toLocaleTimeString(locale === "ko" ? "ko-KR" : "en-US", {
                      hour: "2-digit",
                    })}
                    tick={{ fill: "var(--muted)", fontSize: 10 }} axisLine={false} tickLine={false} />
                  <Tooltip labelFormatter={(value) =>
                    new Date(String(value)).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")}
                    contentStyle={{ background: "var(--panel-2)", border: "1px solid var(--border)" }} />
                  <Area type="monotone" dataKey="count" stroke="var(--accent)" fill="url(#fill)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            </div> : <div className="chart-empty">{text.noThroughput}</div>}
        </article>
        <article className="panel system-panel">
          <div className="panel-head"><div><p className="eyebrow">{text.pipelineRevision}</p><h2>{text.activeRevision}</h2></div><Database size={18} /></div>
          <dl>
            <div><dt>{text.embedding}</dt><dd>{data?.embedding_model ?? "—"}</dd></div>
            <div><dt>{text.revision}</dt><dd className="mono">{data?.embedding_revision ?? "—"}</dd></div>
            <div><dt>{text.pipelineVersion}</dt><dd>{data?.pipeline_version ?? "—"}</dd></div>
            <div><dt>{text.repositoryMode}</dt><dd>{data?.repository_embedding_mode === "docs_only"
              ? text.docsOnly : data?.repository_embedding_mode ?? "—"}</dd></div>
            <div><dt>{text.searchLatency}</dt><dd>{data?.search_latency_last_hour.hybrid
              ? `p50 ${Math.round(data.search_latency_last_hour.hybrid.p50_ms)}ms · p95 ${
                Math.round(data.search_latency_last_hour.hybrid.p95_ms)}ms`
              : text.noSearches}</dd></div>
            <div><dt>{text.cacheUsage}</dt><dd>{data
              ? `${number.format(data.query_embedding_cache.entries)} / ${
                number.format(data.query_embedding_cache.max_entries)}`
              : "—"}</dd></div>
            <div><dt>{text.workers}</dt><dd>{data?.workers ?? 0}</dd></div>
          </dl>
        </article>
      </div>
      <div className="overview-detail-grid">
        <article className="panel">
          <div className="panel-head"><div><p className="eyebrow">{text.indexed}</p>
            <h2>{text.recentTitle}</h2><p className="panel-help">{text.recentHelp}</p></div>
            <Clock3 size={18} /></div>
          <div className="recent-documents">
            {data?.recent_documents.map((document) => <div key={document.id}>
              <FileCode2 size={16} />
              <span><strong>{document.filename}</strong>
                <small>{document.project ?? document.source_root} · {document.relative_path}</small></span>
              <span className="recent-time">
                <em>{text[document.change_type as "created" | "modified" | "restored"] ??
                  document.change_type}</em>
                <time title={new Date(document.indexed_at).toLocaleString(
                  locale === "ko" ? "ko-KR" : "en-US",
                )}>{formatRelative(document.indexed_at, locale, text.never)}</time>
              </span>
            </div>)}
            {!data?.recent_documents.length && <div className="inline-empty">{text.never}</div>}
          </div>
        </article>
        <article className="panel">
          <div className="panel-head"><div><p className="eyebrow">{text.reconciled}</p>
            <h2>{text.sourceTitle}</h2><p className="panel-help">{text.sourceHelp}</p></div>
            <RefreshCcw size={18} /></div>
          <div className="source-freshness">
            {data?.source_roots.map((root) => {
              const level = freshnessLevel(root.last_reconciled_at);
              return <div key={root.id}>
                <span><strong>{root.name}</strong>
                  <small>{number.format(root.document_count)} {text.documents.toLowerCase()}</small></span>
                <span className="source-time">
                  <em className={`freshness-label ${level}`}>{text[level]}</em>
                  <time title={root.last_reconciled_at
                    ? new Date(root.last_reconciled_at).toLocaleString(
                      locale === "ko" ? "ko-KR" : "en-US",
                    ) : undefined}>
                    {formatRelative(root.last_reconciled_at, locale, text.never)}
                  </time>
                </span>
              </div>;
            })}
            {!data?.source_roots.length && <div className="inline-empty">{text.never}</div>}
          </div>
        </article>
      </div>
      <article className="queue-explainer panel">
        <div><p className="eyebrow">{text.queue}</p>
          <strong>{statusTitle}</strong><small>{statusDetail}</small></div>
        <dl>
          <div><dt>{text.processing}</dt><dd>{number.format(processing)}</dd></div>
          <div><dt>{text.pending}</dt><dd>{number.format(pending)}</dd></div>
          <div><dt>{text.failed}</dt><dd className={failed ? "danger-text" : ""}>{number.format(failed)}</dd></div>
          <div title={`${text.succeeded}: ${number.format(data?.jobs?.succeeded ?? 0)}`}>
            <dt>{text.recentSucceeded}</dt><dd>{number.format(data?.succeeded_last_3h ?? 0)}</dd>
          </div>
          <div><dt>{text.queueRate}</dt><dd>{number.format(
            Math.round(data?.queue_rate_per_hour ?? 0)
          )}/h</dd></div>
          <div><dt>{text.estimatedDrain}</dt><dd>{data?.queue_eta_seconds == null
            ? text.estimateUnavailable : formatDuration(data.queue_eta_seconds, locale)}</dd></div>
        </dl>
      </article>
      <div className="quick-actions">
        <button onClick={() => onNavigate("explorer")}><BookOpen size={18} /><span><strong>{text.browse}</strong><small>{text.browseNote}</small></span></button>
        <button onClick={() => onNavigate("operations")}><TerminalSquare size={18} /><span><strong>{text.jobs}</strong><small>{text.jobsNote}</small></span></button>
        <button onClick={() => onNavigate("timeline")}><Clock3 size={18} /><span><strong>{text.timeline}</strong><small>{text.timelineNote}</small></span></button>
      </div>
    </section>
  );
}

function Explorer({ onOpen, locale }: {
  onOpen: (item: TreeItem) => void;
  locale: Locale;
}) {
  const text = translations[locale].explorer;
  const tree = useQuery({ queryKey: ["tree"], queryFn: () => api<{ items: TreeItem[]; truncated: boolean }>("/api/v1/tree?limit=20000") });
  const [open, setOpen] = useState<Set<string>>(new Set());
  const grouped = useMemo(() => {
    const map = new Map<string, TreeItem[]>();
    for (const item of tree.data?.items ?? []) {
      const group = map.get(item.project) ?? [];
      group.push(item); map.set(item.project, group);
    }
    return map;
  }, [tree.data]);
  if (tree.isLoading) return <SkeletonRows />;
  if (tree.isError) return <ErrorState message={text.loadError} />;
  return (
    <section>
      <div className="page-title compact"><div><p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
        <p>{tree.data?.items.length ?? 0} {text.visible}{tree.data?.truncated ? ` · ${text.limited}` : ""}</p></div></div>
      <div className="panel tree-panel" role="tree" aria-label={text.treeLabel}>
        {[...grouped.entries()].map(([project, items]) => {
          const expanded = open.has(project);
          return <div key={project}>
            <button className="tree-project" role="treeitem" aria-expanded={expanded}
              onClick={() => setOpen((previous) => {
                const next = new Set(previous); next.has(project) ? next.delete(project) : next.add(project); return next;
              })}>
              {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}<Folder size={16} />{project}
              <span>{items.length}</span>
            </button>
            {expanded && <div role="group" className="tree-children">
              {items.slice(0, 250).map((item) => <button key={item.id} role="treeitem" onClick={() => onOpen(item)}>
                <FileCode2 size={15} /><span>{item.path}</span>
                <em>{translations[locale].statusLabels[
                  item.state as keyof typeof translations.ko.statusLabels
                ] ?? item.state}</em>
              </button>)}
            </div>}
          </div>;
        })}
        {!grouped.size && <EmptyState title={text.emptyTitle} detail={text.emptyDetail} />}
      </div>
    </section>
  );
}

function SearchView({ initialQuery, onSelect, locale }: {
  initialQuery: string;
  onSelect: (result: SearchResult) => void;
  locale: Locale;
}) {
  const text = translations[locale].searchView;
  const [value, setValue] = useState(initialQuery);
  const [mode, setMode] = useState("hybrid");
  const [submitted, setSubmitted] = useState(initialQuery);
  const [project, setProject] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [tagMode, setTagMode] = useState<"all" | "any">("all");
  const parent = useRef<HTMLDivElement>(null);
  const facets = useQuery({
    queryKey: ["search-facets"],
    queryFn: () => api<{
      projects: { name: string; count: number }[];
      tags: { name: string; count: number }[];
    }>("/api/v1/search/facets"),
  });
  const results = useQuery({
    queryKey: ["search", submitted, mode, project, selectedTags, tagMode],
    queryFn: () => api<{ confidence: string; results: SearchResult[]; total: number }>("/api/v1/search/hybrid", {
      method: "POST", body: JSON.stringify({
        query: submitted, mode, top_k: 50,
        project: project || null, tags: selectedTags, tag_mode: tagMode,
      }),
    }),
    enabled: submitted.trim().length > 0,
  });
  const virtual = useVirtualizer({
    count: results.data?.results.length ?? 0, getScrollElement: () => parent.current,
    estimateSize: () => 190, overscan: 4, useFlushSync: false,
  });
  return (
    <section>
      <div className="page-title compact"><div><p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
        <p>{text.subtitle}</p></div></div>
      <form className="search-box" onSubmit={(event) => { event.preventDefault(); setSubmitted(value); }}>
        <Search size={18} /><input value={value} onChange={(e) => setValue(e.target.value)} placeholder={text.placeholder} />
        <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label={text.mode}>
          {Object.entries(text.modes).map(([value, label]) =>
            <option key={value} value={value}>{label}</option>)}
        </select><button className="primary">{text.submit}</button>
      </form>
      <div className="search-filters" aria-label={text.tags}>
        <label>{text.project}
          <select value={project} onChange={(event) => setProject(event.target.value)}>
            <option value="">{text.allProjects}</option>
            {facets.data?.projects.map((item) =>
              <option key={item.name} value={item.name}>{item.name} ({item.count})</option>)}
          </select>
        </label>
        <label>{text.tags}
          <select value={tagMode} onChange={(event) =>
            setTagMode(event.target.value as "all" | "any")}>
            <option value="all">{text.tagAll}</option>
            <option value="any">{text.tagAny}</option>
          </select>
        </label>
        <div className="tag-filter-list">
          {facets.data?.tags.slice(0, 24).map((item) => {
            const active = selectedTags.includes(item.name);
            return <button type="button" key={item.name} className={active ? "active" : ""}
              aria-pressed={active} onClick={() => setSelectedTags((current) =>
                active ? current.filter((tag) => tag !== item.name) : [...current, item.name])}>
              {item.name}<small>{item.count}</small>
            </button>;
          })}
          {!!selectedTags.length && <button type="button" onClick={() => setSelectedTags([])}>
            {text.clearTags}
          </button>}
        </div>
      </div>
      <div className="result-meta"><span>{results.data?.total ?? 0}{text.results}</span>
        {results.data && <span className={`confidence ${results.data.confidence}`}>
          {results.data.confidence} {text.confidence}</span>}</div>
      {results.isLoading && <SkeletonRows />}
      {results.isError && <ErrorState message={text.loadError} />}
      <div ref={parent} className="result-scroll">
        <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
          {virtual.getVirtualItems().map((row) => {
            const result = results.data!.results[row.index];
            return <button key={result.provenance.chunk_id} className="result-card"
              ref={virtual.measureElement} data-index={row.index} onClick={() => onSelect(result)}
              style={{ transform: `translateY(${row.start}px)` }}>
              <div className="result-title"><FileCode2 size={17} /><strong>{result.title}</strong>
                <span>L{result.provenance.start_line}–{result.provenance.end_line}</span></div>
              <p className="path">{result.provenance.source_root} / {result.provenance.relative_path}</p>
              {!!result.tags.length && <div className="result-tags">
                {result.tags.slice(0, 6).map((tag) => <span key={tag}>{tag}</span>)}
              </div>}
              {result.heading_or_symbol && <h3>{result.heading_or_symbol}</h3>}
              <p className="snippet">{result.snippet}</p>
              <div className="score-row">
                {result.lexical_rank != null && <span>{text.scores.lexical} {result.lexical_rank.toFixed(3)}</span>}
                {result.vector_similarity != null && <span>{text.scores.vector} {result.vector_similarity.toFixed(3)}</span>}
                <span>{text.scores.fused} {result.fused_rank.toFixed(4)}</span>
                <em>{result.match_reason.join(" + ")}</em>
              </div>
            </button>;
          })}
        </div>
      </div>
      {!results.isLoading && submitted && !results.data?.results.length &&
        <EmptyState title={text.emptyTitle} detail={text.emptyDetail} />}
    </section>
  );
}

function Operations({ locale }: { locale: Locale }) {
  const text = translations[locale].operations;
  const statusLabels = translations[locale].statusLabels;
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<"jobs" | "workers" | "backups">("jobs");
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: async () => {
      const [recent, failed, dead] = await Promise.all([
        api<{ items: Job[] }>("/api/v1/jobs?page_size=80"),
        api<{ items: Job[] }>("/api/v1/jobs?status=failed&page_size=20"),
        api<{ items: Job[] }>("/api/v1/jobs?status=dead_letter&page_size=20"),
      ]);
      const unique = new Map<string, Job>();
      for (const item of [...failed.items, ...dead.items, ...recent.items]) unique.set(item.id, item);
      return { items: [...unique.values()] };
    },
    refetchInterval: 5000,
  });
  const workers = useQuery({
    queryKey: ["workers"],
    queryFn: () => api<Array<{
      worker_id: string; hostname: string; state: string; last_seen_at: string;
      current_job_id: string | null; processed_count: number; failed_count: number;
      metadata: {
        watch_mode?: string; process_cpu_percent?: number; cpu_alert?: boolean;
        cpu_warning_percent?: number; reconciliation_seconds?: number;
        resource_guard_enabled?: boolean; pause_requested?: boolean;
        embedding_batch_size?: number; burst_jobs?: number;
      };
    }>>("/api/v1/workers"),
    refetchInterval: 5000,
  });
  const backups = useQuery({
    queryKey: ["backups"],
    queryFn: () => api<Array<{
      id: string; path: string; status: string; created_at: string;
      manifest: { sha256?: string; file_size?: number; schema_revision?: string };
    }>>("/api/v1/backups"),
  });
  const retry = useMutation({
    mutationFn: (id: string) => api(`/api/v1/jobs/${id}/retry`, { method: "POST" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] }),
  });
  const columns = useMemo<ColumnDef<Job>[]>(() => [
    { accessorKey: "status", header: text.headers.status, cell: ({ getValue }) => {
      const value = String(getValue());
      return <Status value={value} label={
        statusLabels[value as keyof typeof statusLabels] ?? value
      } />;
    } },
    { accessorKey: "job_type", header: text.headers.type },
    { accessorKey: "path", header: text.headers.path, cell: ({ getValue }) => <span className="table-path">{String(getValue())}</span> },
    { id: "attempts", header: text.headers.attempts, cell: ({ row }) => `${row.original.attempt_count}/${row.original.max_attempts}` },
    { accessorKey: "error_type", header: text.headers.error, cell: ({ row }) => <span title={row.original.error_message ?? ""}>{row.original.error_type ?? "—"}</span> },
    { id: "action", header: "", cell: ({ row }) => row.original.status === "failed" || row.original.status === "dead_letter"
      ? <button className="retry" onClick={() => retry.mutate(row.original.id)}><RefreshCcw size={13} /> {text.retry}</button> : null },
  ], [retry, statusLabels, text]);
  const table = useReactTable({ data: jobs.data?.items ?? [], columns, getCoreRowModel: getCoreRowModel() });
  return (
    <section>
      <div className="page-title compact"><div><p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
        <p>{text.subtitle}</p></div>
        <button className="secondary" onClick={() => jobs.refetch()}><RefreshCcw size={15} /> {text.refresh}</button></div>
      <div className="tabs"><button className={tab === "jobs" ? "active" : ""}
        onClick={() => setTab("jobs")}>{text.tabs.jobs}</button><button
        className={tab === "workers" ? "active" : ""}
        onClick={() => setTab("workers")}>{text.tabs.workers}</button><button
        className={tab === "backups" ? "active" : ""}
        onClick={() => setTab("backups")}>{text.tabs.backups}</button></div>
      {tab === "jobs" && <div className="panel table-wrap">
        <table>
          <thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) =>
            <th key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr>)}</thead>
          <tbody>{table.getRowModel().rows.map((row) => <tr key={row.id}>{row.getVisibleCells().map((cell) =>
            <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody>
        </table>
        {!jobs.isLoading && !jobs.data?.items.length &&
          <EmptyState title={text.queueEmpty} detail={text.queueEmptyDetail} />}
      </div>}
      {tab === "workers" && <div className="panel table-wrap">
        <table><thead><tr><th>{text.headers.worker}</th><th>{text.headers.host}</th>
          <th>{text.headers.status}</th><th>{text.headers.mode}</th>
          <th>{text.headers.guard}</th><th>{text.headers.cpu}</th>
          <th>{text.headers.heartbeat}</th><th>{text.headers.processed}</th>
          <th>{text.headers.failed}</th></tr></thead>
          <tbody>{workers.data?.map((worker) => <tr key={worker.worker_id}>
            <td className="mono">{worker.worker_id}</td><td>{worker.hostname}</td>
            <td><Status value={worker.state} label={
              statusLabels[worker.state as keyof typeof statusLabels] ?? worker.state
            } /></td>
            <td>{worker.metadata.watch_mode ?? "—"}</td>
            <td title={worker.metadata.resource_guard_enabled
              ? `Batch ${worker.metadata.embedding_batch_size ?? "?"}, burst ${worker.metadata.burst_jobs ?? "?"}`
              : undefined}>
              {worker.metadata.pause_requested
                ? text.paused
                : worker.metadata.resource_guard_enabled ? text.enabled : "—"}
            </td>
            <td title={worker.metadata.cpu_alert
              ? `Warning threshold: ${worker.metadata.cpu_warning_percent ?? "?"}%`
              : undefined}>
              {typeof worker.metadata.process_cpu_percent === "number"
                ? `${worker.metadata.process_cpu_percent.toFixed(2)}%`
                : "—"}
            </td>
            <td>{new Date(worker.last_seen_at).toLocaleString(
              locale === "ko" ? "ko-KR" : "en-US"
            )}</td>
            <td>{worker.processed_count}</td><td>{worker.failed_count}</td>
          </tr>)}</tbody></table>
        {!workers.isLoading && !workers.data?.length &&
          <EmptyState title={text.noHeartbeat} detail={text.noHeartbeatDetail} />}
      </div>}
      {tab === "backups" && <div className="panel table-wrap">
        <table><thead><tr><th>{text.headers.status}</th><th>{text.headers.created}</th>
          <th>{text.headers.path}</th><th>{text.headers.revision}</th>
          <th>{text.headers.checksum}</th></tr></thead>
          <tbody>{backups.data?.map((backup) => <tr key={backup.id}>
            <td><Status value={backup.status} label={
              statusLabels[backup.status as keyof typeof statusLabels] ?? backup.status
            } /></td>
            <td>{new Date(backup.created_at).toLocaleString(
              locale === "ko" ? "ko-KR" : "en-US"
            )}</td>
            <td className="table-path">{backup.path}</td>
            <td className="mono">{backup.manifest.schema_revision ?? "—"}</td>
            <td className="mono">{backup.manifest.sha256?.slice(0, 12) ?? "—"}</td>
          </tr>)}</tbody></table>
        {!backups.isLoading && !backups.data?.length &&
          <EmptyState title={text.noBackup} detail={text.noBackupDetail} />}
      </div>}
    </section>
  );
}

function Timeline({ locale }: { locale: Locale }) {
  const text = translations[locale].timelineView;
  const events = useQuery({ queryKey: ["timeline"], queryFn: () => api<Array<{ id: number; event: string; path: string; details: object; created_at: string }>>("/api/v1/timeline") });
  return <section><div className="page-title compact"><div><p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
    <p>{text.subtitle}</p></div></div><div className="timeline">
      {events.data?.map((item) => <article key={item.id}><span className="timeline-dot" /><div>
        <strong>{text.events[item.event as keyof typeof text.events] ?? item.event}</strong>
        <p>{item.path ?? text.system}</p><small>{new Date(item.created_at).toLocaleString(
          locale === "ko" ? "ko-KR" : "en-US"
        )}</small></div></article>)}
      {!events.isLoading && !events.data?.length &&
        <EmptyState title={text.emptyTitle} detail={text.emptyDetail} />}
    </div></section>;
}

function GraphNotice({ locale }: { locale: Locale }) {
  const text = translations[locale].graphView;
  return <section><div className="page-title compact"><div><p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
    <p>{text.subtitle}</p></div></div>
    <div className="panel graph-placeholder"><Network size={38} /><h2>{text.emptyTitle}</h2>
      <p>{text.emptyDetail}</p>
      <div className="fake-nodes"><span /><span /><span /><span /></div></div></section>;
}

function ContextPanel({ selected, health, locale }: {
  selected: SearchResult | null;
  health?: { database: boolean; ollama: boolean };
  locale: Locale;
}) {
  const text = translations[locale].contextPanel;
  return <div><p className="nav-heading">{text.title}</p>
    {selected ? <><h2>{selected.title}</h2><p className="context-path">{selected.provenance.relative_path}</p>
      <dl className="context-list">
        <div><dt>{text.lines}</dt><dd>{selected.provenance.start_line}–{selected.provenance.end_line}</dd></div>
        <div><dt>{text.version}</dt><dd className="mono">{selected.provenance.document_version_id.slice(0, 8)}</dd></div>
        <div><dt>{text.chunk}</dt><dd className="mono">{selected.provenance.chunk_id.slice(0, 8)}</dd></div>
        <div><dt>{text.hash}</dt><dd className="mono">{selected.provenance.content_hash.slice(0, 12)}</dd></div>
        <div><dt>{text.indexed}</dt><dd>{new Date(selected.provenance.indexed_timestamp)
          .toLocaleString(locale === "ko" ? "ko-KR" : "en-US")}</dd></div>
      </dl><button className="secondary copy" onClick={() => navigator.clipboard.writeText(
        `${selected.provenance.canonical_path}:L${selected.provenance.start_line}-L${selected.provenance.end_line}`
      )}>{text.copy}</button></> : <div className="context-empty"><BookOpen size={24} /><p>{text.empty}</p></div>}
    <div className="service-status"><p className="nav-heading">{text.services}</p>
      <div><Database size={15} /> PostgreSQL <Status value={health?.database ? "healthy" : "offline"}
        label={health?.database ? text.healthy : text.offline} /></div>
      <div><HeartPulse size={15} /> Ollama <Status value={health?.ollama ? "healthy" : "unavailable"}
        label={health?.ollama ? text.healthy : text.unavailable} /></div>
    </div></div>;
}

function Status({ value, label }: { value: string; label?: string }) {
  const good = ["healthy", "succeeded", "active", "idle", "processing"].includes(value);
  return <span className={`status ${good ? "good" : value === "pending" ? "waiting" : "danger"}`}>
    {good ? <CircleCheck size={12} /> : value === "pending" ? <Clock3 size={12} /> : <AlertTriangle size={12} />}{label ?? value}
  </span>;
}
function EmptyState({ title, detail }: { title: string; detail: string }) {
  return <div className="empty"><Files size={28} /><strong>{title}</strong><p>{detail}</p></div>;
}
function ErrorState({ message }: { message: string }) {
  return <div className="error-state"><AlertTriangle size={17} />{message}</div>;
}
function SkeletonRows() {
  return <div className="skeletons">{[1, 2, 3, 4].map((i) => <div key={i} />)}</div>;
}
