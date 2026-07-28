"use client";

import { useGSAP } from "@gsap/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ColumnDef, flexRender, getCoreRowModel, useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Background, Controls, MiniMap, ReactFlow, type Edge, type Node,
} from "@xyflow/react";
import {
  Activity, AlertTriangle, Blocks, BookOpen, ChevronDown, ChevronRight,
  CircleCheck, Clock3, Command, Database, FileCode2, Files, Folder,
  GitBranch, HeartPulse, LayoutDashboard, Moon, Network, PanelRightClose,
  RefreshCcw, Search, ServerCog, Sun, TerminalSquare, BookCheck, Languages, Cpu,
  MessageSquareText, Power,
} from "lucide-react";
import gsap from "gsap";
import {
  FormEvent, useEffect, useMemo, useRef, useState,
} from "react";
import {
  Area, AreaChart, ResponsiveContainer, Tooltip, XAxis,
} from "recharts";
import { api, EmbeddingRecovery, Metrics, SearchResult, TreeItem } from "@/lib/api";
import { knowledgeTagLabel } from "@/lib/knowledge-labels";
import { GpuQueue } from "./gpu-queue";
import { ActivityHistory, DocumentViewer, KnowledgeCases } from "./knowledge-views";
import { RepositoryAnalysisConsole } from "./repository-analysis";
import { ServiceManager } from "./service-manager";

gsap.registerPlugin(useGSAP);

type View = "overview" | "explorer" | "document" | "search" | "activities" |
  "localchat" | "knowledge" | "operations" | "timeline" | "graph" |
  "repositoryAnalysis" | "gpuQueue" | "serviceManager";
type NavView = Exclude<View, "document">;
type NavGroupId = "knowledge" | "activity" | "operations";
type Job = {
  id: string; status: string; job_type: string; path: string; attempt_count: number;
  max_attempts: number; error_type: string | null; error_message: string | null; created_at: string;
  available_at: string; started_at: string | null; finished_at: string | null; updated_at: string;
  lease_expires_at: string | null; queue_lead_time_ms: number | null;
  processing_duration_ms: number | null; age_ms: number | null;
};
type SystemService = {
  key: string;
  label: string;
  state: string;
  detail: string | null;
  last_seen_at?: string;
  project?: string;
  service?: string;
  container?: string;
  image?: string;
  ports?: string;
  verification?: "healthcheck" | "running_only";
};
type SystemServiceGroup = {
  key: string;
  category: "portal" | "projects" | "ai" | "infrastructure";
  project: string;
  state: string;
  services: SystemService[];
};
type SystemServicesPayload = {
  overall: string;
  checked_at: string;
  inventory?: {
    state: string;
    checked_at: string | null;
    age_seconds: number | null;
    detail: string;
  };
  groups: SystemServiceGroup[];
  services: SystemService[];
};
type Locale = "ko" | "en";

const navGroups: {
  id: NavGroupId;
  icon: React.ComponentType<{ size?: number }>;
  items: { id: NavView; icon: React.ComponentType<{ size?: number }> }[];
}[] = [
  {
    id: "knowledge",
    icon: BookOpen,
    items: [
      { id: "explorer", icon: Folder },
      { id: "search", icon: Search },
      { id: "knowledge", icon: BookCheck },
      { id: "graph", icon: Network },
    ],
  },
  {
    id: "activity",
    icon: Activity,
    items: [
      { id: "activities", icon: TerminalSquare },
      { id: "localchat", icon: MessageSquareText },
      { id: "timeline", icon: Clock3 },
    ],
  },
  {
    id: "operations",
    icon: ServerCog,
    items: [
      { id: "serviceManager", icon: Power },
      { id: "operations", icon: Database },
      { id: "gpuQueue", icon: Cpu },
      { id: "repositoryAnalysis", icon: FileCode2 },
    ],
  },
];

const translations = {
  ko: {
    nav: {
      overview: "현황", explorer: "원본 파일", document: "문서",
      search: "통합 검색", activities: "Codex 작업", knowledge: "프로젝트 지식",
      localchat: "로컬 모델 대화",
      operations: "수집·운영", timeline: "변경 기록", graph: "문서 관계",
      gpuQueue: "GPU 작업 큐",
      repositoryAnalysis: "레포 분석",
      serviceManager: "레포·서비스 관리",
    },
    navDescriptions: {
      overview: "최신성·대기열",
      explorer: "저장소·사람 작성 문서",
      search: "파일·코드·지식 검색",
      activities: "원시 작업과 실행 근거",
      localchat: "Ollama 대화·프로젝트 맥락",
      knowledge: "개발 일지·검증 사례",
      operations: "작업·Worker·백업",
      timeline: "파일 수집 이벤트",
      graph: "문서 링크 시각화",
      gpuQueue: "호스트 GPU 예약 현황",
      repositoryAnalysis: "구조·흐름·검증",
      serviceManager: "프로젝트·DB·AI 도구 상태",
    },
    workspace: "작업 공간",
    navGroups: {
      knowledge: "지식",
      activity: "활동",
      operations: "운영",
    } as Record<NavGroupId, string>,
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
      services: "WSL · Docker 연결 상태", checkedAt: "확인",
      healthy: "정상", offline: "연결 안 됨", unavailable: "사용 불가",
      stale: "응답 지연", error: "오류", disabled: "비활성",
      running: "실행 중 · 헬스체크 없음",
      categories: {
        portal: "로컬 지식 포털",
        projects: "프로젝트 웹·API",
        ai: "AI 도구",
        infrastructure: "공유 인프라",
      },
      projectLabels: {
        "local-knowledge-portal": "로컬 지식 포털",
        "unjeong-mining-web": "미닝 운정점 웹사이트",
        "local-voice-agent": "통화비서",
        "gpu-workload-scheduler": "GPU 작업 스케줄러",
        "workstation-edge-ingress": "외부 연결 게이트웨이",
        "workstation-databases": "공유 데이터베이스",
        "interstellar-drift": "Interstellar Drift",
        "workstation-ai": "AI 도구",
        "host-control": "호스트 서비스 제어",
        standalone: "독립 Docker 컨테이너",
      },
      dockerRoles: {
        app: "웹 애플리케이션", web: "웹 UI", api: "API",
        postgres: "PostgreSQL", redis: "Redis",
        proxy: "리버스 프록시", cloudflared: "Cloudflare 터널",
      },
      serviceLabels: {
        web: "지식 포털 UI", api: "지식 API", postgres: "지식 DB",
        embedding: "Ollama 임베딩", generation: "Ollama 지식 편집기",
        worker: "인덱서 Worker", watcher: "파일 Watcher",
        reconciler: "전체 대조", "hook-collector": "Codex 훅 수집기",
        "gpu-scheduler": "호스트 GPU 스케줄러",
        "gpu-embedding-reaper": "GPU 임베딩 정리 보호장치",
        comfyui: "ComfyUI", "ai-toolkit": "AI-Toolkit",
        "service-manager": "호스트 서비스 관리자",
      },
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
      semanticDegraded: "의미 검색 지연",
      semanticGpuRecovery: "GPU 재색인 대기",
      semanticRecoveryTitle: "의미 검색 복구가 GPU 예약을 기다리고 있습니다.",
      semanticRecoveryDetail: "원본·키워드 검색은 정상이며, CPU 임베딩은 재발 방지를 위해 중지된 상태입니다.",
      semanticRecovery: "의미 검색 복구",
      gpuReservation: "GPU 예약",
      requestedVram: "요청 VRAM",
      gpuValidation: "GPU 검색 검증",
      recoveryBacklog: "남은 벡터 작업",
      validationWaiting: "재색인 완료 후 대기",
      validationVerified: "semantic·hybrid 검증됨",
      validationFailed: "검증 실패",
      validationInvalid: "검증 상태 읽기 실패",
      recoveryUnavailable: "GPU 스케줄러 상태를 확인할 수 없습니다.",
      recoveryStates: {
        queued: "예약 대기", active: "재색인 실행 중", completed: "재색인 완료",
        failed: "재색인 실패", canceled: "예약 취소", not_submitted: "예약 없음",
        unavailable: "스케줄러 연결 확인 필요",
      },
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
      title: "원본 저장소와 파일",
      subtitle: "등록 저장소와 사람이 작성한 문서를 탐색합니다. 포털 생성 개발일지·지식사례는 ‘프로젝트 지식’에만 표시됩니다.",
      sourceOnly: "원본 카탈로그",
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
        processed: "처리", failed: "실패", created: "생성 시각", timing: "대기 · 처리 시간 (ms)",
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
      title: "문서 연결 지도",
      subtitle: "문서에 명시적으로 작성된 링크만 보여줍니다. 의미 유사도나 코드 의존성을 추정하지 않습니다.",
      emptyTitle: "프로젝트를 선택하세요",
      emptyDetail: "프로젝트별 관계만 제한해서 표시합니다. 문서 탐색과 검색이 기본 이동 수단입니다.",
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
      overview: "Overview", explorer: "Source files", document: "Document",
      search: "Unified search", activities: "Codex work", knowledge: "Project knowledge",
      localchat: "Local model chats",
      operations: "Ingest & operations", timeline: "Change history", graph: "Document relations",
      gpuQueue: "GPU queue",
      repositoryAnalysis: "Repository analysis",
      serviceManager: "Repositories & services",
    },
    navDescriptions: {
      overview: "Freshness and queue",
      explorer: "Repositories and authored docs",
      search: "Files, code, and knowledge",
      activities: "Raw work and evidence",
      localchat: "Ollama chats by project",
      knowledge: "Journals and verified cases",
      operations: "Jobs, workers, backups",
      timeline: "Ingest events",
      graph: "Document links",
      gpuQueue: "Host GPU reservations",
      repositoryAnalysis: "Structure, evidence, snapshots",
      serviceManager: "Projects, databases, and AI tools",
    },
    workspace: "Workspace",
    navGroups: {
      knowledge: "Knowledge",
      activity: "Activity",
      operations: "Operations",
    } as Record<NavGroupId, string>,
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
      services: "WSL · Docker services", checkedAt: "Checked",
      healthy: "healthy", offline: "offline", unavailable: "unavailable",
      stale: "stale", error: "error", disabled: "disabled",
      running: "running · no healthcheck",
      categories: {
        portal: "Local Knowledge Portal",
        projects: "Project web & APIs",
        ai: "AI tools",
        infrastructure: "Shared infrastructure",
      },
      projectLabels: {
        "local-knowledge-portal": "Local Knowledge Portal",
        "unjeong-mining-web": "MINING Unjeong website",
        "local-voice-agent": "Local Voice Agent",
        "gpu-workload-scheduler": "GPU Workload Scheduler",
        "workstation-edge-ingress": "Edge ingress",
        "workstation-databases": "Shared databases",
        "interstellar-drift": "Interstellar Drift",
        "workstation-ai": "AI tools",
        "host-control": "Host service control",
        standalone: "Standalone Docker containers",
      },
      dockerRoles: {
        app: "Web application", web: "Web UI", api: "API",
        postgres: "PostgreSQL", redis: "Redis",
        proxy: "Reverse proxy", cloudflared: "Cloudflare tunnel",
      },
      serviceLabels: {
        web: "Knowledge portal UI", api: "Knowledge API", postgres: "Knowledge DB",
        embedding: "Ollama embedding", generation: "Ollama knowledge editor",
        worker: "Indexer worker", watcher: "File watcher",
        reconciler: "Reconciler", "hook-collector": "Codex hook collector",
        "gpu-scheduler": "Host GPU scheduler",
        "gpu-embedding-reaper": "GPU embedding cleanup guard",
        comfyui: "ComfyUI", "ai-toolkit": "AI-Toolkit",
        "service-manager": "Host service manager",
      },
    },
    overview: {
      eyebrow: "Knowledge base status",
      title: "See what is current, at a glance.",
      subtitle: "Live document freshness, queue state, failures, and s…12279 tokens truncated…r: text.headers.error, cell: ({ row }) =>
      <span title={`${row.original.error_type ?? ""}: ${row.original.error_message ?? ""}`}>
        {errorTypeLabel(row.original.error_type, locale)}
      </span> },
    { id: "action", header: "", cell: ({ row }) => row.original.status === "failed" || row.original.status === "dead_letter"
      ? <button className="retry" onClick={() => retry.mutate(row.original.id)}><RefreshCcw size={13} /> {text.retry}</button> : null },
  ], [locale, retry, statusLabels, text]);
  const table = useReactTable({ data: jobs.data?.items ?? [], columns, getCoreRowModel: getCoreRowModel() });
  return (
    <section>
      <div className="page-title compact"><div><p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
        <p>{text.subtitle}</p></div>
        <button className="secondary" onClick={() => jobs.refetch()}><RefreshCcw size={15} /> {text.refresh}</button></div>
      <div className="tabs"><button className={tab === "jobs" ? "active" : ""}
        onClick={() => { setTab("jobs"); setPage(1); }}>{text.tabs.jobs}</button><button
        className={tab === "workers" ? "active" : ""}
        onClick={() => { setTab("workers"); setPage(1); }}>{text.tabs.workers}</button><button
        className={tab === "backups" ? "active" : ""}
        onClick={() => { setTab("backups"); setPage(1); }}>{text.tabs.backups}</button></div>
      {tab === "jobs" && <div className="panel table-wrap">
        <table className="operations-table">
          <thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) =>
            <th key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr>)}</thead>
          <tbody>{table.getRowModel().rows.map((row) => <tr key={row.id}>{row.getVisibleCells().map((cell) =>
            <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody>
        </table>
        {!jobs.isLoading && !jobs.data?.items.length &&
          <EmptyState title={text.queueEmpty} detail={text.queueEmptyDetail} />}
        <Pager page={page} pageSize={pageSize} total={jobs.data?.total ?? 0}
          locale={locale} onChange={setPage} />
      </div>}
      {tab === "workers" && <div className="panel table-wrap">
        <table><thead><tr><th>{text.headers.worker}</th><th>{text.headers.host}</th>
          <th>{text.headers.status}</th><th>{text.headers.mode}</th>
          <th>{text.headers.guard}</th><th>{text.headers.cpu}</th>
          <th>{text.headers.heartbeat}</th><th>{text.headers.processed}</th>
          <th>{text.headers.failed}</th></tr></thead>
          <tbody>{workers.data?.items.map((worker) => <tr key={worker.worker_id}>
            <td><strong>{workerModeLabel(worker.metadata.watch_mode, locale)}</strong>
              <small className="table-secondary" title={worker.worker_id}>
                {worker.worker_id.slice(0, 12)}
              </small></td><td>{worker.hostname}</td>
            <td><Status value={worker.state} label={
              statusLabels[worker.state as keyof typeof statusLabels] ?? worker.state
            } /></td>
            <td>{workerModeLabel(worker.metadata.watch_mode, locale)}</td>
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
        {!workers.isLoading && !workers.data?.items.length &&
          <EmptyState title={text.noHeartbeat} detail={text.noHeartbeatDetail} />}
        <Pager page={page} pageSize={pageSize} total={workers.data?.total ?? 0}
          locale={locale} onChange={setPage} />
      </div>}
      {tab === "backups" && <div className="panel table-wrap">
        <table><thead><tr><th>{text.headers.status}</th><th>{text.headers.created}</th>
          <th>{text.headers.path}</th><th>{text.headers.revision}</th>
          <th>{text.headers.checksum}</th></tr></thead>
          <tbody>{backups.data?.items.map((backup) => <tr key={backup.id}>
            <td><Status value={backup.status} label={
              statusLabels[backup.status as keyof typeof statusLabels] ?? backup.status
            } /></td>
            <td>{new Date(backup.created_at).toLocaleString(
              locale === "ko" ? "ko-KR" : "en-US"
            )}</td>
            <td className="table-path">{backup.path}</td>
            <td title={backup.manifest.schema_revision}>
              {backup.manifest.schema_revision
                ? (locale === "ko" ? "기록됨" : "Recorded")
                : "—"}
            </td>
            <td className="mono">{backup.manifest.sha256?.slice(0, 12) ?? "—"}</td>
          </tr>)}</tbody></table>
        {!backups.isLoading && !backups.data?.items.length &&
          <EmptyState title={text.noBackup} detail={text.noBackupDetail} />}
        <Pager page={page} pageSize={pageSize} total={backups.data?.total ?? 0}
          locale={locale} onChange={setPage} />
      </div>}
    </section>
  );
}

function Timeline({ locale }: { locale: Locale }) {
  const text = translations[locale].timelineView;
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const events = useQuery({
    queryKey: ["timeline", page],
    queryFn: () => api<{ items: Array<{
      id: number; event: string; path: string; details: object; created_at: string;
    }>; total: number }>(`/api/v1/timeline?page=${page}&page_size=${pageSize}`),
  });
  return <section><div className="page-title compact"><div><p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
    <p>{text.subtitle}</p></div></div><div className="timeline">
      {events.data?.items.map((item) => <article key={item.id}><span className="timeline-dot" /><div>
        <strong>{text.events[item.event as keyof typeof text.events] ?? item.event}</strong>
        <p>{item.path ?? text.system}</p><small>{new Date(item.created_at).toLocaleString(
          locale === "ko" ? "ko-KR" : "en-US"
        )}</small></div></article>)}
      {!events.isLoading && !events.data?.items.length &&
        <EmptyState title={text.emptyTitle} detail={text.emptyDetail} />}
      <Pager page={page} pageSize={pageSize} total={events.data?.total ?? 0}
        locale={locale} onChange={setPage} />
    </div></section>;
}

function GraphNotice({ locale }: { locale: Locale }) {
  const text = translations[locale].graphView;
  const [project, setProject] = useState("");
  const [linkType, setLinkType] = useState<"all" | "wikilink" | "markdown">("all");
  const [resolution, setResolution] = useState<"resolved" | "all">("resolved");
  const facets = useQuery({
    queryKey: ["document-graph-facets"],
    queryFn: () => api<{
      projects: Array<{ key: string; links: number; resolved: number; unresolved: number }>;
    }>("/api/v1/graph/facets"),
  });
  const graph = useQuery({
    queryKey: ["document-graph", project, linkType, resolution],
    queryFn: () => api<{
      nodes: Array<{
        id: string; label: string; path: string | null; project: string; state: string;
      }>;
      edges: Array<{
        id: string; source: string; target: string; type: string; resolved: boolean;
      }>;
      truncated: boolean;
      summary: {
        visible_links: number; visible_documents: number;
        resolved_links: number; unresolved_links: number;
      };
    }>(`/api/v1/graph?limit=40&project=${encodeURIComponent(project)}`
      + `&link_type=${linkType}&resolution=${resolution}`),
    enabled: Boolean(project),
  });
  const nodes = useMemo<Node[]>(() => (graph.data?.nodes ?? []).map((item, index) => ({
    id: item.id,
    position: {
      x: (index % 4) * 230,
      y: Math.floor(index / 4) * 125,
    },
    data: { label: item.label },
    className: item.state === "unresolved" ? "graph-node unresolved" : "graph-node",
    style: {
      width: 180,
      border: item.state === "unresolved" ? "1px dashed #d8a657" : "1px solid #3a8fa8",
      borderRadius: 10,
      background: "var(--panel)",
      color: "var(--text)",
      fontSize: 12,
      padding: 10,
    },
  })), [graph.data]);
  const edges = useMemo<Edge[]>(() => (graph.data?.edges ?? []).map((item) => ({
    id: item.id,
    source: item.source,
    target: item.target,
    label: item.type === "wikilink"
      ? (locale === "ko" ? "위키 링크" : "Wiki link")
      : (locale === "ko" ? "문서 링크" : "Document link"),
    animated: false,
    style: {
      stroke: item.resolved ? "var(--accent)" : "var(--warning)",
      strokeDasharray: item.resolved ? undefined : "5 5",
    },
  })), [graph.data, locale]);
  const selectedFacet = facets.data?.projects.find((item) => item.key === project);
  return <section><div className="page-title compact"><div><p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
    <p>{text.subtitle}</p></div></div>
    <div className="panel graph-toolbar">
      <label><span>{locale === "ko" ? "프로젝트" : "Project"}</span>
        <select value={project} onChange={(event) => setProject(event.target.value)}>
          <option value="">{locale === "ko" ? "프로젝트 선택" : "Choose a project"}</option>
          {facets.data?.projects.map((item) => <option key={item.key} value={item.key}>
            {item.key} · {item.links.toLocaleString(locale === "ko" ? "ko-KR" : "en-US")}
          </option>)}
        </select>
      </label>
      <label><span>{locale === "ko" ? "관계 종류" : "Relationship type"}</span>
        <select value={linkType} onChange={(event) =>
          setLinkType(event.target.value as "all" | "wikilink" | "markdown")}>
          <option value="all">{locale === "ko" ? "모든 명시적 링크" : "All explicit links"}</option>
          <option value="wikilink">{locale === "ko" ? "Obsidian 위키 링크" : "Obsidian wiki links"}</option>
          <option value="markdown">{locale === "ko" ? "Markdown 문서 링크" : "Markdown links"}</option>
        </select>
      </label>
      <label><span>{locale === "ko" ? "연결 상태" : "Resolution"}</span>
        <select value={resolution} onChange={(event) =>
          setResolution(event.target.value as "resolved" | "all")}>
          <option value="resolved">{locale === "ko" ? "연결된 문서만" : "Resolved only"}</option>
          <option value="all">{locale === "ko" ? "미해결 링크 포함" : "Include unresolved"}</option>
        </select>
      </label>
      <div className="graph-scope-summary">
        <small>{locale === "ko" ? "선택 범위" : "Selected scope"}</small>
        <strong>{selectedFacet
          ? `${selectedFacet.resolved.toLocaleString()} / ${selectedFacet.links.toLocaleString()}`
          : "—"}</strong>
        <span>{locale === "ko" ? "해결된 링크 / 전체 링크" : "resolved / total links"}</span>
      </div>
    </div>
    <div className="graph-basis panel">
      <strong>{locale === "ko" ? "관계 기준" : "Relationship basis"}</strong>
      <span><i className="legend-line wiki" />{locale === "ko" ? "[[문서]] 위키 링크" : "[[document]] wiki link"}</span>
      <span><i className="legend-line markdown" />{locale === "ko" ? "[이름](상대경로) 문서 링크" : "[label](relative path) link"}</span>
      <span><i className="legend-node unresolved" />{locale === "ko" ? "점선 노드: 대상을 찾지 못함" : "Dashed node: target not found"}</span>
    </div>
    <div className="panel graph-canvas">
      {graph.isLoading && project && <div className="loading-block">{locale === "ko" ? "관계를 불러오는 중…" : "Loading relations…"}</div>}
      {!graph.isLoading && !nodes.length && <div className="graph-placeholder">
        <Network size={38} /><h2>{text.emptyTitle}</h2><p>{text.emptyDetail}</p>
      </div>}
      {nodes.length > 0 && <ReactFlow nodes={nodes} edges={edges} fitView nodesDraggable={false}
        nodesConnectable={false} elementsSelectable minZoom={0.2} maxZoom={1.8}>
        <Background /><MiniMap pannable zoomable /><Controls showInteractive={false} />
      </ReactFlow>}
    </div>
    {graph.data?.summary && <p className="graph-visible-summary">
      {locale === "ko"
        ? `현재 문서 ${graph.data.summary.visible_documents}개 · 관계 ${graph.data.summary.visible_links}개를 표시합니다.`
        : `Showing ${graph.data.summary.visible_documents} documents and ${graph.data.summary.visible_links} relationships.`}
    </p>}
    {graph.data?.truncated && <p className="graph-limit-note">
      {locale === "ko" ? "가독성을 위해 앞의 40개 관계만 표시합니다. 관계 종류를 좁혀 보세요." : "Showing the first 40 relationships for readability. Narrow the relationship type."}
    </p>}
  </section>;
}

function ContextPanel({ selected, services, locale }: {
  selected: SearchResult | null;
  services?: SystemServicesPayload;
  locale: Locale;
}) {
  const text = translations[locale].contextPanel;
  const stateLabel = (state: string) => {
    if (state === "healthy" || state === "busy" || state === "idle") return text.healthy;
    if (state === "stale") return text.stale;
    if (state === "disabled") return text.disabled;
    if (state === "running") return text.running;
    if (state === "error") return text.error;
    return text.offline;
  };
  const projectLabel = (project: string) => {
    const labels = text.projectLabels as Record<string, string>;
    return labels[project] ?? project.split("-").map((part) =>
      part ? `${part[0].toUpperCase()}${part.slice(1)}` : part
    ).join(" ");
  };
  const serviceLabel = (service: SystemService) => {
    const fixedLabels = text.serviceLabels as Record<string, string>;
    const dockerRoles = text.dockerRoles as Record<string, string>;
    return service.service
      ? dockerRoles[service.service] ?? service.label
      : fixedLabels[service.key] ?? service.label;
  };
  const serviceDetail = (service: SystemService) => {
    const detail = service.detail?.trim();
    if (!detail) return "—";
    if (/^schema\s+/i.test(detail)) {
      return locale === "ko" ? "데이터 구조 최신" : "Database schema is current";
    }
    if (detail === "queue-empty") {
      return locale === "ko" ? "현재 예약 작업 없음" : "No work is currently queued";
    }
    if (/^(healthy|running|ready)$/i.test(detail)) {
      return locale === "ko" ? "정상 작동 중" : "Operating normally";
    }
    const heartbeat = detail.match(/^(\d+)\s+heartbeat\(s\)$/i);
    if (heartbeat) {
      return locale === "ko"
        ? `최근 상태 신호 ${heartbeat[1]}개 확인`
        : `${heartbeat[1]} recent status signal(s)`;
    }
    if (/^Docker healthcheck passed/i.test(detail)) {
      return locale === "ko" ? "컨테이너 상태 검사 통과" : "Container health check passed";
    }
    if (detail === "read-only health") {
      return locale === "ko" ? "읽기 전용 상태 연결 정상" : "Read-only status connection healthy";
    }
    if (detail === "no temporary GPU embedding batch is running") {
      return locale === "ko" ? "임시 GPU 작업 없음" : "No temporary GPU work is running";
    }
    if (detail === "HTTP 200" || /^\d+\.\d+\.\d+$/.test(detail)) {
      return locale === "ko" ? "응답 확인" : "Response verified";
    }
    return detail;
  };
  const categoryOrder: SystemServiceGroup["category"][] = [
    "portal",
    "projects",
    "ai",
    "infrastructure",
  ];
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
      {services && categoryOrder.map((category) => {
        const groups = services.groups.filter((group) => group.category === category);
        if (!groups.length) return null;
        return <section className="service-category" key={category}>
          <h3>{text.categories[category]}</h3>
          {groups.map((group) => <details className="service-group" key={group.key}
            open={category !== "infrastructure"}>
            <summary>
              <span>{projectLabel(group.project)}</span>
              <Status value={group.state} label={stateLabel(group.state)} />
            </summary>
            <div className="service-group-items">
              {group.services.map((service) => {
                const Icon = service.service === "postgres" || service.key === "postgres"
                  ? Database
                  : service.key === "gpu-scheduler" ? Cpu : HeartPulse;
                return <div key={service.key}><Icon size={15} /><span className="service-info">
                  <strong>{serviceLabel(service)}</strong>
                  <small title={service.detail ?? undefined}>{serviceDetail(service)}</small>
                </span><Status value={service.state} label={stateLabel(service.state)} /></div>;
              })}
            </div>
          </details>)}
        </section>;
      })}
      {!services && <div><AlertTriangle size={15} /> API
        <Status value="offline" label={text.offline} /></div>}
      {services?.checked_at && <small className="service-checked">{text.checkedAt}: {
        new Date(services.checked_at).toLocaleTimeString(locale === "ko" ? "ko-KR" : "en-US")
      }</small>}
    </div></div>;
}

function Status({ value, label }: { value: string; label?: string }) {
  const good = ["healthy", "succeeded", "active", "idle", "processing", "busy"].includes(value);
  const waiting = ["pending", "stale", "disabled", "running"].includes(value);
  return <span className={`status ${good ? "good" : waiting ? "waiting" : "danger"}`}>
    {good ? <CircleCheck size={12} /> : waiting ? <Clock3 size={12} /> : <AlertTriangle size={12} />}{label ?? value}
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
