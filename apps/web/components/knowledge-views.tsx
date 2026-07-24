"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BookCheck,
  ChevronRight,
  CircleCheck,
  Cpu,
  FileDiff,
  ShieldAlert,
} from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";

type Locale = "ko" | "en";

const textByLocale = {
  ko: {
    activity: {
      eyebrow: "Codex 활동", title: "활동 이력",
      subtitle: "사용자 지시, 도구 실행, 변경 파일과 독립적으로 확인한 결과를 분리해 기록합니다.",
      global: "전체", empty: "수집된 훅 활동이 없습니다.",
      previous: "이전", next: "다음", page: "페이지",
      select: "활동을 선택하면 실행 근거를 확인할 수 있습니다.",
      instruction: "사용자 지시", command: "실행 명령", exitCode: "종료 코드",
      notObserved: "관측되지 않음", changedFiles: "변경 파일",
      versions: "문서 버전", reported: "보고된 결과", verified: "검증된 결과",
    },
    knowledge: {
      eyebrow: "근거 검증 위키", title: "지식 사례",
      subtitle: "검증된 근거가 관문을 통과한 사례만 표준 지식으로 게시됩니다.",
      cases: "검증된 사례", candidates: "승격 보류·근거 보완", occurrences: "회 발생",
      journal: "프로젝트 개발 일지",
      journalSubtitle: "주요 구현·설정·운영 변경을 사례 승격 여부와 별도로 기록합니다.",
      intent: "작업 의도", changes: "주요 변경", changedFiles: "변경 파일", failures: "실패와 해결",
      verification: "실행 검증", references: "지식베이스 참조",
      noReferences: "이 작업에서 구조화된 지식베이스 참조가 관측되지 않았습니다.",
      previous: "이전", next: "다음", page: "페이지",
      empty: "이 화면에 표시할 기록이 없습니다.", select: "기록을 선택하세요.",
      problem: "문제", symptom: "증상", cause: "근본 원인", solution: "해결 방법",
      reportedVerified: "보고 결과 / 검증 결과",
      evidenceGate: "실행 근거", qualityGate: "지식 품질",
      valueGate: "재사용 가치", valueSignals: "가치 판단 근거",
      approvalPolicy: "게시 방식", humanReview: "로컬 LLM 근거 검증 후 자동 게시",
      qualityReasons: "보완이 필요한 이유",
      pending: "자동 선별 대기: 다음 예약 실행의 시작 스냅샷에 포함됩니다.",
      held: "자동 선별 완료: 현재 근거로는 사례 승격 조건을 충족하지 못했습니다.",
      revisions: "리비전과 발생 이력",
      revisionCount: "리비전", occurrenceCount: "발생", relationCount: "관계",
      curator: "로컬 지식 편집기", nextAttempt: "다음 확인",
      lastSnapshot: "최근 선별", processed: "처리", failed: "실패",
      gpu: "GPU 여유 / 사용률 / 온도", qualification: "모델 적합성",
      schedulerStates: {
        not_started: "시작 대기", waiting_for_gpu: "GPU 유휴 대기",
        gpu_wait_cooldown: "GPU 점검 냉각 중", gpu_probe_error: "GPU 상태 확인 실패",
        model_unavailable: "모델 준비 대기", model_rejected: "모델 적합성 탈락",
        qualification_error: "모델 평가 재시도 대기",
        qualification_error_cooldown: "모델 평가 오류 냉각 중",
        curation_error: "편집 오류 재시도 대기",
        curation_error_cooldown: "편집 오류 냉각 중",
        standby_lock_held: "다른 편집기 인스턴스가 처리 중",
        idle: "대기 중", completed_batch: "최근 편집 완료",
        completed_batch_with_errors: "일부 오류와 함께 편집 완료", disabled: "비활성",
      },
      categoryLabels: {
        error_resolution: "오류 해결", implementation: "구현 방식",
        custom_success: "검증된 성공 사례", performance: "성능·부하",
        operations: "운영·장애",
      },
      qualityReasonLabels: {
        generic_file_change_is_not_a_cause: "파일 변경 수만으로는 원인을 설명할 수 없습니다.",
        artifact_list_is_not_a_reusable_solution: "변경 파일 목록만으로는 재사용 가능한 해결 방법이 아닙니다.",
        auto_report_missing_reusable_structure: "목표·원인 또는 방식·검증 결과를 구조화해야 합니다.",
        human_restructuring_required: "로컬 편집 모델이 재사용 가능한 구조로 정리해야 합니다.",
        local_llm_evidence_validation_required: "로컬 편집 모델의 근거 인용 검증이 필요합니다.",
        not_reusable_knowledge: "반복 활용할 지식이 아닌 일반 활동으로 분류됐습니다.",
        generic_artifact_inventory: "변경 파일 목록만 있고 재사용 가능한 판단이 없습니다.",
        presentation_only_change_without_reusable_decision: "일반 화면 변경으로, 별도 지식 사례 가치가 확인되지 않았습니다.",
        verified_failure_fix_explanation_required: "실패·수정·성공과 재사용 가능한 원인 설명이 함께 필요합니다.",
        reusable_implementation_decision_required: "검증된 변경과 재사용 가능한 구현 판단이 함께 필요합니다.",
        before_after_and_load_cause_required: "성능 사례에는 before/after 측정과 부하 원인이 필요합니다.",
        incident_and_recovery_evidence_required: "운영 사례에는 장애 관측과 복구 성공 근거가 필요합니다.",
        knowledge_value_harness_not_promotable: "결정론적 가치 하니스에서 사례 승격 대상으로 판정되지 않았습니다.",
        previously_quarantined_activity: "이전 근거 감사에서 일반 활동으로 격리되어 자동 상향하지 않습니다.",
      },
      valueTierLabels: {
        promote: "사례 승격 대상", needs_review: "가치 근거 보완", activity_only: "활동 이력만",
      },
    },
    document: {
      loading: "문서를 불러오는 중…", unavailable: "문서를 표시할 수 없습니다.",
      eyebrow: "문서 뷰어", versions: "버전 타임라인",
      latestDiff: "최신 변경 비교", noChange: "텍스트 변경이 없습니다.",
      lines: "줄",
    },
    badge: {
      VERIFIED: "검증됨", verified: "검증됨", published: "게시됨",
      UNVERIFIED: "미검증", NEEDS_EVIDENCE: "근거 필요",
      NEEDS_REVIEW: "자동 승격 보류", candidate: "후보",
    },
  },
  en: {
    activity: {
      eyebrow: "Codex activity", title: "Activity history",
      subtitle: "Instructions, tools, changes, and independently observed results.",
      global: "global", empty: "No hook activity collected.",
      previous: "Previous", next: "Next", page: "Page",
      select: "Select an activity to inspect evidence.",
      instruction: "User instruction", command: "Command", exitCode: "Exit code",
      notObserved: "not observed", changedFiles: "Changed files",
      versions: "Document versions", reported: "Reported result", verified: "Verified result",
    },
    knowledge: {
      eyebrow: "Evidence-gated wiki", title: "Knowledge cases",
      subtitle: "Canonical cases are published only after verified evidence passes the gate.",
      cases: "Verified cases", candidates: "Held candidates", occurrences: "occurrence(s)",
      journal: "Project journal",
      journalSubtitle: "Significant implementation, configuration, and operational changes.",
      intent: "Intent", changes: "Changes", changedFiles: "Changed files", failures: "Failures and resolution",
      verification: "Execution verification", references: "Knowledge references",
      noReferences: "No structured knowledge-base reference was observed.",
      previous: "Previous", next: "Next", page: "Page",
      empty: "No records in this view.", select: "Select a record.",
      problem: "Problem", symptom: "Symptom", cause: "Root cause", solution: "Solution",
      reportedVerified: "Reported / verified",
      evidenceGate: "Execution evidence", qualityGate: "Knowledge quality",
      valueGate: "Reuse value", valueSignals: "Value decision",
      approvalPolicy: "Publication policy", humanReview: "Auto-publish after local LLM evidence validation",
      qualityReasons: "Reasons for review",
      pending: "Pending automatic selection in the next scheduled snapshot.",
      held: "Automatic selection completed; current evidence does not qualify for promotion.",
      revisions: "Revisions & occurrences",
      revisionCount: "revisions", occurrenceCount: "occurrences", relationCount: "relations",
      curator: "Local knowledge editor", nextAttempt: "Next check",
      lastSnapshot: "Last selection", processed: "processed", failed: "failed",
      gpu: "GPU free / utilization / temperature", qualification: "Model qualification",
      schedulerStates: {
        not_started: "not started", waiting_for_gpu: "waiting for idle GPU",
        gpu_wait_cooldown: "GPU check cooldown", gpu_probe_error: "GPU probe failed",
        model_unavailable: "model unavailable", model_rejected: "model rejected",
        qualification_error: "qualification retry pending",
        qualification_error_cooldown: "qualification error cooldown",
        curation_error: "curation retry pending",
        curation_error_cooldown: "curation error cooldown",
        standby_lock_held: "another editor instance is active",
        idle: "idle", completed_batch: "batch completed",
        completed_batch_with_errors: "batch completed with errors", disabled: "disabled",
      },
      categoryLabels: {
        error_resolution: "Error resolution", implementation: "Implementation",
        custom_success: "Validated success", performance: "Performance",
        operations: "Operations",
      },
      qualityReasonLabels: {
        generic_file_change_is_not_a_cause: "A file count does not explain the cause.",
        artifact_list_is_not_a_reusable_solution: "An artifact list is not a reusable solution.",
        auto_report_missing_reusable_structure: "Structure the goal, approach or cause, and validation.",
        human_restructuring_required: "The local editor must restructure this candidate.",
        local_llm_evidence_validation_required: "Local model evidence validation is required.",
        not_reusable_knowledge: "Classified as ordinary activity rather than reusable knowledge.",
        generic_artifact_inventory: "Only an artifact inventory is present.",
        presentation_only_change_without_reusable_decision: "Presentation-only change without a reusable decision.",
        verified_failure_fix_explanation_required: "Failure, fix, success, and a reusable explanation are required.",
        reusable_implementation_decision_required: "A verified change and reusable implementation decision are required.",
        before_after_and_load_cause_required: "Performance cases require before/after metrics and a load cause.",
        incident_and_recovery_evidence_required: "Operations cases require incident and recovery evidence.",
        knowledge_value_harness_not_promotable: "The deterministic value harness did not mark this candidate promotable.",
        previously_quarantined_activity: "A prior evidence audit quarantined this activity; it cannot be auto-promoted.",
      },
      valueTierLabels: {
        promote: "promotion eligible", needs_review: "value evidence needed", activity_only: "activity only",
      },
    },
    document: {
      loading: "Loading document…", unavailable: "Document unavailable.",
      eyebrow: "Document viewer", versions: "Version timeline",
      latestDiff: "Latest diff", noChange: "No textual change.", lines: "L",
    },
    badge: {
      VERIFIED: "VERIFIED", verified: "verified", published: "published",
      UNVERIFIED: "UNVERIFIED", NEEDS_EVIDENCE: "NEEDS EVIDENCE",
      NEEDS_REVIEW: "NEEDS REVIEW", candidate: "candidate",
    },
  },
} as const;

type ActivityItem = {
  id: string;
  event_type: string;
  occurred_at: string;
  project: string | null;
  instruction: string | null;
  tool_name: string | null;
  command: string | null;
  exit_code: number | null;
  changed_files: string[];
  document_version_ids: string[];
  reported_result: string | null;
  verified_result: string | null;
  verification_status: string;
};

type Candidate = {
  id: string;
  category: string;
  title: string;
  problem: string;
  symptom: string;
  root_cause: string;
  solution: string;
  status: string;
  evidence_gate_status: string;
  reported_result: string | null;
  verified_result: string | null;
  metadata: {
    quality_gate_status?: string;
    quality_gate_reasons?: string[];
    approval_policy?: string;
    knowledge_value?: {
      revision?: string;
      tier?: "promote" | "needs_review" | "activity_only";
      signals?: string[];
      blockers?: string[];
    };
    curation?: {
      state?: string;
      model?: string;
      validation_status?: string;
      validation_reasons?: string[];
      decision_reason?: string;
    };
  };
};

type Case = {
  id: string;
  category: string;
  title: string;
  problem: string;
  symptom: string;
  root_cause: string;
  solution: string;
  status: string;
  occurrence_count: number;
  revisions?: Array<{
    id: string;
    number: number;
    evidence_summary: object;
    created_at: string;
    content?: { article_markdown?: string };
  }>;
  occurrences?: Array<{ id: string; occurred_at: string; evidence: object }>;
  relations?: Array<{ source_case_id: string; target_case_id: string; type: string }>;
};

type JournalEntry = {
  id: string;
  source_stop_activity_id: string;
  project: string;
  occurred_at: string;
  title: string;
  intent: string;
  change_summary: string;
  failures: Array<{ command_family?: string; exit_code?: number }>;
  resolution: string;
  verification: Array<{ command_family?: string; exit_code?: number; evidence_type?: string }>;
  changed_files: string[];
  knowledge_references: Array<{
    document_id?: string;
    chunk_id?: string;
    canonical_path?: string;
    relative_path?: string;
    start_line?: number;
    end_line?: number;
    retrieval_score?: number;
  }>;
  significance_reasons: string[];
  verification_status: string;
};

type CurationStatus = {
  enabled: boolean;
  auto_publish: boolean;
  model: string;
  prompt_version: string;
  scheduler: {
    state?: string;
    next_attempt_at?: string;
    model?: string;
    snapshot_candidate_count?: number;
    processed_candidate_count?: number;
    failed_candidate_count?: number;
    last_gpu?: {
      free_mb: number;
      utilization_percent: number;
      temperature_c: number;
    };
  };
  qualification: { status?: string } | null;
};

function Article({ markdown }: { markdown: string }) {
  return <div className="curated-article">{markdown.split(/\n{2,}/).map((block, index) => {
    const value = block.trim();
    if (value.startsWith("## ")) return <h3 key={index}>{value.slice(3)}</h3>;
    if (value.startsWith("> ")) return <blockquote key={index}>{value.slice(2)}</blockquote>;
    return <p key={index}>{value}</p>;
  })}</div>;
}

type DocumentDetail = {
  id: string;
  filename: string;
  canonical_path: string;
  relative_path: string;
  content_hash: string;
  chunk_page: number;
  chunk_page_size: number;
  chunk_total: number;
  chunks: Array<{
    id: string;
    index: number;
    type: string;
    heading: string | null;
    symbol: string | null;
    start_line: number;
    end_line: number;
    content: string;
  }>;
};

type Version = {
  id: string;
  content_hash: string;
  change_type: string;
  detected_at: string;
  line_count: number;
  parser_version: string;
  chunker_version: string;
};

function Badge({ value, locale }: { value: string; locale: Locale }) {
  const good = value === "VERIFIED" || value === "verified" || value === "published";
  const labels = textByLocale[locale].badge;
  return <span className={`evidence-badge ${good ? "verified" : "review"}`}>
    {good ? <CircleCheck size={12} /> : <ShieldAlert size={12} />}
    {labels[value as keyof typeof labels] ?? value}
  </span>;
}

function PageControls({
  page,
  pageSize,
  total,
  onChange,
  labels,
}: {
  page: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
  labels: { previous: string; next: string; page: string };
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  return <nav className="page-controls" aria-label={labels.page}>
    <button disabled={page <= 1} onClick={() => onChange(page - 1)}>{labels.previous}</button>
    <span>{labels.page} {page} / {pages} · {total}</span>
    <button disabled={page >= pages} onClick={() => onChange(page + 1)}>{labels.next}</button>
  </nav>;
}

function candidateBadge(candidate: Candidate): string {
  if (candidate.metadata.quality_gate_status === "NEEDS_REVIEW") {
    return "NEEDS_REVIEW";
  }
  return candidate.status === "published"
    ? "published"
    : candidate.evidence_gate_status;
}

export function ActivityHistory({ locale }: { locale: Locale }) {
  const text = textByLocale[locale].activity;
  const [selected, setSelected] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const activities = useQuery({
    queryKey: ["activities", page],
    queryFn: () => api<{ items: ActivityItem[]; total: number }>(
      `/api/v1/activities?page=${page}&page_size=${pageSize}`,
    ),
    refetchInterval: 5000,
  });
  const detail = useQuery({
    queryKey: ["activity", selected],
    queryFn: () => api<ActivityItem & { evidence: object[] }>(
      `/api/v1/activities/${selected}`,
    ),
    enabled: Boolean(selected),
  });
  return <section>
    <div className="page-title compact"><div>
      <p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
      <p>{text.subtitle}</p>
    </div></div>
    <div className="master-detail">
      <div className="panel record-list">
        {activities.data?.items.map((item) => <button
          key={item.id} className={selected === item.id ? "selected" : ""}
          onClick={() => setSelected(item.id)}
        >
          <Activity size={16} /><span><strong>{item.event_type}</strong>
            <small>{item.project ?? text.global} · {new Date(item.occurred_at).toLocaleString(
              locale === "ko" ? "ko-KR" : "en-US"
            )}</small>
          </span><Badge value={item.verification_status} locale={locale} /><ChevronRight size={14} />
        </button>)}
        {!activities.isLoading && !activities.data?.items.length &&
          <div className="inline-empty">{text.empty}</div>}
        <PageControls page={page} pageSize={pageSize} total={activities.data?.total ?? 0}
          onChange={(value) => { setPage(value); setSelected(null); }} labels={text} />
      </div>
      <article className="panel detail-card">
        {detail.data ? <>
          <div className="panel-head"><h2>{detail.data.event_type}</h2>
            <Badge value={detail.data.verification_status} locale={locale} /></div>
          <dl className="detail-grid">
            <div><dt>{text.instruction}</dt><dd>{detail.data.instruction ?? "—"}</dd></div>
            <div><dt>{text.command}</dt><dd className="mono">{detail.data.command ?? "—"}</dd></div>
            <div><dt>{text.exitCode}</dt><dd>{detail.data.exit_code ?? text.notObserved}</dd></div>
            <div><dt>{text.changedFiles}</dt><dd>{detail.data.changed_files.join(", ") || "—"}</dd></div>
            <div><dt>{text.versions}</dt><dd className="mono">
              {detail.data.document_version_ids.join(", ") || "—"}</dd></div>
            <div><dt>{text.reported}</dt><dd>{detail.data.reported_result ?? "—"}</dd></div>
            <div><dt>{text.verified}</dt><dd>{detail.data.verified_result ?? "—"}</dd></div>
          </dl>
        </> : <div className="inline-empty">{text.select}</div>}
      </article>
    </div>
  </section>;
}

export function KnowledgeCases({ locale }: { locale: Locale }) {
  const text = textByLocale[locale].knowledge;
  const [tab, setTab] = useState<"cases" | "candidates" | "journal">("cases");
  const [selected, setSelected] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const curation = useQuery({
    queryKey: ["knowledge-curation-status"],
    queryFn: () => api<CurationStatus>("/api/v1/knowledge/curation/status"),
    refetchInterval: 30000,
  });
  const cases = useQuery({
    queryKey: ["knowledge-cases", page],
    queryFn: () => api<{ items: Case[]; total: number }>(
      `/api/v1/knowledge/cases?page=${page}&page_size=${pageSize}`,
    ),
    enabled: tab === "cases",
  });
  const candidates = useQuery({
    queryKey: ["knowledge-candidates", page],
    queryFn: () => api<{ items: Candidate[]; total: number }>(
      `/api/v1/knowledge/candidates?page=${page}&page_size=${pageSize}`,
    ),
    enabled: tab === "candidates",
  });
  const journal = useQuery({
    queryKey: ["project-journal", page],
    queryFn: () => api<{ items: JournalEntry[]; total: number }>(
      `/api/v1/project-journal?page=${page}&page_size=${pageSize}`,
    ),
    enabled: tab === "journal",
  });
  const caseDetail = useQuery({
    queryKey: ["knowledge-case", selected],
    queryFn: () => api<Case>(`/api/v1/knowledge/cases/${selected}`),
    enabled: tab === "cases" && Boolean(selected),
  });
  const candidateDetail = useQuery({
    queryKey: ["knowledge-candidate", selected],
    queryFn: () => api<Candidate & { evidence: Array<{ type: string; verified: boolean; claim: string }> }>(
      `/api/v1/knowledge/candidates/${selected}`,
    ),
    enabled: tab === "candidates" && Boolean(selected),
  });
  const journalDetail = useQuery({
    queryKey: ["project-journal-entry", selected],
    queryFn: () => api<JournalEntry>(`/api/v1/project-journal/${selected}`),
    enabled: tab === "journal" && Boolean(selected),
  });
  const items: Array<Case | Candidate | JournalEntry> = tab === "cases"
    ? cases.data?.items ?? []
    : tab === "candidates"
      ? candidates.data?.items.filter((candidate) =>
        !["published", "activity_only"].includes(candidate.status)
      ) ?? []
      : journal.data?.items ?? [];
  const total = tab === "cases"
    ? cases.data?.total ?? 0
    : tab === "candidates"
      ? candidates.data?.total ?? 0
      : journal.data?.total ?? 0;
  const selectedDetail = tab === "cases"
    ? caseDetail.data
    : tab === "candidates"
      ? candidateDetail.data
      : journalDetail.data;
  const scheduler = curation.data?.scheduler;
  const schedulerLabel = scheduler?.state
    ? text.schedulerStates[scheduler.state as keyof typeof text.schedulerStates] ?? scheduler.state
    : text.schedulerStates.not_started;
  return <section>
    <div className="page-title compact"><div>
      <p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
      <p>{text.subtitle}</p>
    </div></div>
    <div className="panel curator-status">
      <Cpu size={18} />
      <div><strong>{text.curator}</strong>
        <span>{curation.data?.model || "gemma4:e4b"} ·
          {" "}{curation.data?.prompt_version || "evidence-blog-v9"} · {schedulerLabel}</span></div>
      <div><small>{text.gpu}</small><strong>{scheduler?.last_gpu
        ? `${Math.round(scheduler.last_gpu.free_mb / 1024)} GB / ${scheduler.last_gpu.utilization_percent}% / ${scheduler.last_gpu.temperature_c}°C`
        : "—"}</strong></div>
      <div><small>{text.qualification}</small>
        <strong>{curation.data?.qualification?.status ?? "PENDING"}</strong></div>
      <div><small>{text.lastSnapshot}</small><strong>
        {scheduler?.processed_candidate_count ?? 0}/{scheduler?.snapshot_candidate_count ?? 0}
        {" "}{text.processed} · {scheduler?.failed_candidate_count ?? 0} {text.failed}
      </strong></div>
      <div><small>{text.nextAttempt}</small><strong>{scheduler?.next_attempt_at
        ? new Date(scheduler.next_attempt_at).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")
        : "—"}</strong></div>
    </div>
    <div className="tabs">
      <button className={tab === "cases" ? "active" : ""} onClick={() => {
        setTab("cases"); setSelected(null); setPage(1);
      }}>{text.cases}</button>
      <button className={tab === "candidates" ? "active" : ""} onClick={() => {
        setTab("candidates"); setSelected(null); setPage(1);
      }}>{text.candidates}</button>
      <button className={tab === "journal" ? "active" : ""} onClick={() => {
        setTab("journal"); setSelected(null); setPage(1);
      }}>{text.journal}</button>
    </div>
    <div className="master-detail">
      <div className="panel record-list">
        {items.map((item) => <button key={item.id} onClick={() => setSelected(item.id)}
          className={selected === item.id ? "selected" : ""}>
          <BookCheck size={16} /><span><strong>{item.title}</strong>
            <small>{"project" in item
              ? `${item.project} · ${new Date(item.occurred_at).toLocaleString(
                locale === "ko" ? "ko-KR" : "en-US",
              )}`
              : `${text.categoryLabels[
                item.category as keyof typeof text.categoryLabels
              ] ?? item.category}${!("evidence_gate_status" in item)
                ? ` · ${item.occurrence_count}${locale === "ko" ? text.occurrences : ` ${text.occurrences}`}` : ""}`
            }</small></span>
          <Badge value={"project" in item
            ? item.verification_status
            : "evidence_gate_status" in item
              ? candidateBadge(item) : item.status} locale={locale} /><ChevronRight size={14} />
        </button>)}
        {!items.length && <div className="inline-empty">{text.empty}</div>}
        <PageControls page={page} pageSize={pageSize} total={total}
          onChange={(value) => { setPage(value); setSelected(null); }} labels={text} />
      </div>
      <article className="panel detail-card">
        {selectedDetail ? <>
          <div className="panel-head"><h2>{selectedDetail.title}</h2>
            <Badge value={"project" in selectedDetail
              ? selectedDetail.verification_status
              : "evidence_gate_status" in selectedDetail
                ? candidateBadge(selectedDetail) : selectedDetail.status} locale={locale} /></div>
          {"project" in selectedDetail ? <>
            <p>{text.journalSubtitle}</p>
            <dl className="detail-grid">
              <div><dt>{text.intent}</dt><dd>{selectedDetail.intent}</dd></div>
              <div><dt>{text.changes}</dt><dd>{selectedDetail.change_summary}</dd></div>
              <div><dt>{text.changedFiles}</dt><dd className="mono">
                {selectedDetail.changed_files.join(", ") || "—"}</dd></div>
              <div><dt>{text.failures}</dt><dd>
                {selectedDetail.failures.length
                  ? `${selectedDetail.failures.map((item) =>
                    `${item.command_family ?? "command"} (exit ${item.exit_code})`
                  ).join(", ")} · ${selectedDetail.resolution}`
                  : "—"}
              </dd></div>
              <div><dt>{text.verification}</dt><dd>
                {selectedDetail.verification.map((item) =>
                  `${item.command_family ?? item.evidence_type ?? "validation"} (exit ${item.exit_code})`
                ).join(", ") || "—"}
              </dd></div>
              <div><dt>{text.references}</dt><dd>
                {selectedDetail.knowledge_references.length
                  ? selectedDetail.knowledge_references.map((item) =>
                    `${item.relative_path ?? item.canonical_path ?? item.document_id ?? "document"}`
                    + `${item.chunk_id ? ` · chunk ${item.chunk_id}` : ""}`
                    + `${item.retrieval_score !== undefined ? ` · ${item.retrieval_score}` : ""}`
                  ).join("\n")
                  : text.noReferences}
              </dd></div>
            </dl>
          </> : <>
          <dl className="detail-grid">
            <div><dt>{text.problem}</dt><dd>{selectedDetail.problem}</dd></div>
            <div><dt>{text.symptom}</dt><dd>{selectedDetail.symptom}</dd></div>
            <div><dt>{text.cause}</dt><dd>{selectedDetail.root_cause}</dd></div>
            <div><dt>{text.solution}</dt><dd>{selectedDetail.solution}</dd></div>
            {"reported_result" in selectedDetail &&
              <div><dt>{text.reportedVerified}</dt><dd>
                {selectedDetail.reported_result ?? "—"} / {selectedDetail.verified_result ?? "—"}
              </dd></div>}
            {"evidence_gate_status" in selectedDetail && <>
              <div><dt>{text.evidenceGate}</dt>
                <dd><Badge value={selectedDetail.evidence_gate_status} locale={locale} /></dd></div>
              <div><dt>{text.qualityGate}</dt><dd>
                <Badge value={selectedDetail.metadata.quality_gate_status ?? "NEEDS_REVIEW"}
                  locale={locale} />
              </dd></div>
              {selectedDetail.metadata.knowledge_value?.tier &&
                <div><dt>{text.valueGate}</dt><dd>
                  {text.valueTierLabels[selectedDetail.metadata.knowledge_value.tier]}
                </dd></div>}
              {!!selectedDetail.metadata.knowledge_value?.blockers?.length &&
                <div><dt>{text.valueSignals}</dt><dd>
                  {selectedDetail.metadata.knowledge_value.blockers.map((reason) =>
                    text.qualityReasonLabels[
                      reason as keyof typeof text.qualityReasonLabels
                    ] ?? reason
                  ).join(" ")}</dd></div>}
              <div><dt>{text.approvalPolicy}</dt><dd>{text.humanReview}</dd></div>
              {!!selectedDetail.metadata.quality_gate_reasons?.length &&
                <div><dt>{text.qualityReasons}</dt>
                  <dd>{selectedDetail.metadata.quality_gate_reasons.map((reason) =>
                    text.qualityReasonLabels[
                      reason as keyof typeof text.qualityReasonLabels
                    ] ?? reason
                  ).join(" ")}</dd></div>}
            </>}
          </dl>
          {tab === "candidates" && <p className="mutation-result">
            {"metadata" in selectedDetail && selectedDetail.metadata.curation
              ? text.held : text.pending}
          </p>}
          {"revisions" in selectedDetail &&
            selectedDetail.revisions?.[0]?.content?.article_markdown &&
            <Article markdown={selectedDetail.revisions[0].content.article_markdown} />}
          {"revisions" in selectedDetail && <div className="subrecords">
            <h3>{text.revisions}</h3>
            <p>{selectedDetail.revisions?.length ?? 0} {text.revisionCount} ·
              {" "}{selectedDetail.occurrences?.length ?? 0} {text.occurrenceCount} ·
              {" "}{selectedDetail.relations?.length ?? 0} {text.relationCount}</p>
          </div>}
          </>}
        </> : <div className="inline-empty">{text.select}</div>}
      </article>
    </div>
  </section>;
}

export function DocumentViewer({ documentId, locale }: {
  documentId: string;
  locale: Locale;
}) {
  const text = textByLocale[locale].document;
  const [chunkPage, setChunkPage] = useState(1);
  const [versionPage, setVersionPage] = useState(1);
  const chunkPageSize = 50;
  const versionPageSize = 25;
  const document = useQuery({
    queryKey: ["document", documentId, chunkPage],
    queryFn: () => api<DocumentDetail>(
      `/api/v1/documents/${documentId}?chunk_page=${chunkPage}&chunk_page_size=${chunkPageSize}`,
    ),
  });
  const versions = useQuery({
    queryKey: ["document-versions", documentId, versionPage],
    queryFn: () => api<{ items: Version[]; total: number }>(
      `/api/v1/documents/${documentId}/versions?page=${versionPage}&page_size=${versionPageSize}`,
    ),
  });
  const pair = useMemo(() => versions.data?.items.slice(0, 2) ?? [], [versions.data]);
  const diff = useQuery({
    queryKey: ["document-diff", documentId, pair.map((item) => item.id).join(":")],
    queryFn: () => api<{ lines: string[]; changed: boolean }>(
      `/api/v1/documents/${documentId}/diff?from_version=${pair[1].id}&to_version=${pair[0].id}`,
    ),
    enabled: pair.length === 2,
  });
  if (document.isLoading) return <div className="inline-empty">{text.loading}</div>;
  if (!document.data) return <div className="inline-empty">{text.unavailable}</div>;
  return <section>
    <div className="page-title compact"><div>
      <p className="eyebrow">{text.eyebrow}</p><h1>{document.data.filename}</h1>
      <p className="mono">{document.data.canonical_path}</p>
    </div></div>
    <div className="document-layout">
      <article className="panel document-content">
        {document.data.chunks.map((chunk) => <section key={chunk.id}>
          <header><span>{chunk.type}</span><span>{text.lines}{chunk.start_line}–{chunk.end_line}</span></header>
          {chunk.heading && <h2>{chunk.heading}</h2>}
          {chunk.symbol && <h3>{chunk.symbol}</h3>}
          <pre>{chunk.content}</pre>
        </section>)}
        <PageControls page={chunkPage} pageSize={chunkPageSize}
          total={document.data.chunk_total} onChange={setChunkPage}
          labels={{
            previous: locale === "ko" ? "이전" : "Previous",
            next: locale === "ko" ? "다음" : "Next",
            page: locale === "ko" ? "페이지" : "Page",
          }} />
      </article>
      <aside className="panel version-panel">
        <div className="panel-head"><h2>{text.versions}</h2><FileDiff size={17} /></div>
        {versions.data?.items.map((version) => <div className="version-row" key={version.id}>
          <strong>{version.change_type}</strong><small>{new Date(version.detected_at)
            .toLocaleString(locale === "ko" ? "ko-KR" : "en-US")}</small>
          <code>{version.content_hash.slice(0, 12)}</code>
        </div>)}
        <PageControls page={versionPage} pageSize={versionPageSize}
          total={versions.data?.total ?? 0} onChange={setVersionPage}
          labels={{
            previous: locale === "ko" ? "이전" : "Previous",
            next: locale === "ko" ? "다음" : "Next",
            page: locale === "ko" ? "페이지" : "Page",
          }} />
        {pair.length === 2 && <div className="diff-block">
          <h3>{text.latestDiff}</h3><pre>{diff.data?.lines.join("\n") || text.noChange}</pre>
        </div>}
      </aside>
    </div>
  </section>;
}
