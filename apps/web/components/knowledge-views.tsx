"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BookCheck,
  ChevronRight,
  CircleCheck,
  Cpu,
  FileDiff,
  FolderTree,
  History,
  ShieldAlert,
  Tag,
} from "lucide-react";
import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import {
  knowledgeCategoryLabel,
  knowledgeTagLabel,
} from "@/lib/knowledge-labels";

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
    localChat: {
      eyebrow: "로컬 모델", title: "로컬 모델 대화",
      subtitle: "Ollama 등 로컬 모델의 완료된 대화를 Codex 작업과 분리해 프로젝트별로 기록합니다. 대화 내용 자체는 검증 근거가 아닙니다.",
      global: "프로젝트 미지정", empty: "수집된 로컬 모델 대화가 없습니다.",
      previous: "이전", next: "다음", page: "페이지",
      select: "대화를 선택하면 사용자 질문과 모델 응답을 확인할 수 있습니다.",
      instruction: "사용자 질문", command: "모델", exitCode: "실행 근거",
      notObserved: "대화만 수집됨", changedFiles: "변경 파일",
      versions: "문서 버전", reported: "모델 응답", verified: "독립 검증 결과",
    },
    knowledge: {
      eyebrow: "프로젝트 지식", title: "프로젝트 지식",
      subtitle: "개발 일지와 재사용 가능한 검증 사례를 원본 파일과 분리해 관리합니다.",
      cases: "검증 지식 사례", candidates: "승격 보류·근거 보완", occurrences: "회 발생",
      hierarchy: "프로젝트 · 작업 특성", allProjects: "전체 프로젝트",
      allCategories: "전체 작업 특성", tags: "태그", allTags: "전체 태그",
      latestFirst: "최신 갱신 순", latest: "최근 갱신",
      journal: "프로젝트 문서",
      journalSubtitle: "프로젝트의 최신 상태를 주제별로 갱신한 문서입니다. 과거 작업은 본문에 합치지 않고 근거와 이력으로 보존합니다.",
      currentDocument: "프로젝트 최신 문서", history: "작업 이력",
      historyCount: "개별 이력", sourceList: "출처",
      sourceHelp: "본문의 작은 번호를 누르면 해당 내용을 만든 원문과 실행 근거를 확인할 수 있습니다.",
      openHistory: "이 프로젝트의 작업 이력 보기",
      integratedLimit: "현재 문서는 최근 200개 이력에서 주제별 최신 근거를 선택했습니다. 전체 기록은 작업 이력에서 확인하세요.",
      sourceArticle: "근거 원문", closeSource: "근거 닫기",
      sourceIntent: "당시 작업 의도", sourceReport: "원문 작업 보고",
      projectSelector: "프로젝트 선택",
      intent: "작업 의도", changes: "주요 변경", changedFiles: "변경 파일", failures: "오류 및 조치",
      noObservedFailure: "관측된 명령 실패가 없습니다. 이 일지는 오류 해결 사례로 주장하지 않습니다.",
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
      curator: "근거 기반 지식 선별기", nextAttempt: "다음 확인",
      editorConnection: "편집기 연결",
      editorConnectionStates: {
        connected: "연결됨", starting: "시작 중", waiting_for_gpu: "GPU 대기",
        scheduled_idle: "예약 대기(정상)",
      },
      lastSnapshot: "최근 선별", processed: "처리", failed: "실패",
      projectArticles: "프로젝트 통합 문서",
      projectArticleStatus: {
        current: "최신", processing: "편집 중", error: "재시도 대기",
        pending: "편집 대기", pending_editor: "편집 대기", stale: "갱신 대기",
      },
      projectArticlePendingTitle: "통합 문서를 편집하고 있습니다",
      projectArticlePendingBody: "등록된 문서와 작업 이력을 근거로 읽기 좋은 최신 문서 한 장을 생성합니다. GPU 예약 작업이 완료되면 이 화면이 자동으로 교체됩니다.",
      recordSummary: "검증 사례 {cases} · 개발 일지 {journals} · 편집 후보 {candidates} · 일반 활동 {activity}",
      gpu: "GPU 여유 / 사용률 / 온도", qualification: "모델 적합성",
      qualificationStates: { PASS: "통과", PENDING: "대기", FAIL: "실패" },
      schedulerStates: {
        not_started: "시작 대기", waiting_for_gpu: "GPU 예약 대기",
        gpu_wait_cooldown: "GPU 점검 냉각 중", gpu_probe_error: "GPU 상태 확인 실패",
        model_unavailable: "모델 준비 대기", model_rejected: "모델 적합성 탈락",
        qualification_error: "모델 평가 재시도 대기",
        qualification_error_cooldown: "모델 평가 오류 냉각 중",
        curation_error: "편집 오류 재시도 대기",
        curation_error_cooldown: "편집 오류 냉각 중",
        standby_lock_held: "다른 편집기 인스턴스가 처리 중",
        running: "GPU 예약에서 편집 중",
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
        incident_recovery_or_editorial_operation_assessment_required: "운영·설정 변경은 근거 인용 가능한 편집 판정을 거쳐야 합니다.",
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
    localChat: {
      eyebrow: "Local model", title: "Local model conversations",
      subtitle: "Completed Ollama and other local-model turns, separated from Codex work and grouped by project. Conversation text is not execution evidence.",
      global: "project not set", empty: "No local-model conversations collected.",
      previous: "Previous", next: "Next", page: "Page",
      select: "Select a conversation to inspect the user prompt and model response.",
      instruction: "User prompt", command: "Model", exitCode: "Execution evidence",
      notObserved: "conversation only", changedFiles: "Changed files",
      versions: "Document versions", reported: "Model response", verified: "Independent verification",
    },
    knowledge: {
      eyebrow: "Project knowledge", title: "Project knowledge",
      subtitle: "Project journals and reusable verified cases are kept apart from source files.",
      cases: "Verified knowledge", candidates: "Held candidates", occurrences: "occurrence(s)",
      hierarchy: "Project · work type", allProjects: "All projects",
      allCategories: "All work types", tags: "Tags", allTags: "All tags",
      latestFirst: "Newest updated first", latest: "Last updated",
      journal: "Project documents",
      journalSubtitle: "A topic-based current project document. Past work remains source evidence and history instead of being concatenated into the article.",
      currentDocument: "Current document", history: "Work history",
      historyCount: "history entries", sourceList: "Sources",
      sourceHelp: "Select a small citation number to inspect the original work record and execution evidence.",
      openHistory: "View this project's work history",
      integratedLimit: "The current document selects the newest source per topic from the latest 200 entries. Use work history for the complete record.",
      sourceArticle: "Source record", closeSource: "Close source",
      sourceIntent: "Original intent", sourceReport: "Original work report",
      projectSelector: "Select project",
      intent: "Intent", changes: "Changes", changedFiles: "Changed files", failures: "Failures and resolution",
      noObservedFailure: "No failed command was observed. This journal does not claim an error-resolution case.",
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
      curator: "Evidence-based knowledge curator", nextAttempt: "Next check",
      editorConnection: "Editor connection",
      editorConnectionStates: {
        connected: "connected", starting: "starting", waiting_for_gpu: "waiting for GPU",
        scheduled_idle: "scheduled idle",
      },
      lastSnapshot: "Last selection", processed: "processed", failed: "failed",
      projectArticles: "Project articles",
      projectArticleStatus: {
        current: "current", processing: "editing", error: "retry pending",
        pending: "editing pending", pending_editor: "editing pending",
        stale: "update pending",
      },
      projectArticlePendingTitle: "The integrated document is being edited",
      projectArticlePendingBody: "The editor is turning registered documents and work history into one readable current article. This view updates automatically after the GPU-reserved job completes.",
      recordSummary: "Verified cases {cases} · project journals {journals} · editorial candidates {candidates} · ordinary activity {activity}",
      gpu: "GPU free / utilization / temperature", qualification: "Model qualification",
      qualificationStates: { PASS: "Passed", PENDING: "Pending", FAIL: "Failed" },
      schedulerStates: {
        not_started: "not started", waiting_for_gpu: "waiting for GPU reservation",
        gpu_wait_cooldown: "GPU check cooldown", gpu_probe_error: "GPU probe failed",
        model_unavailable: "model unavailable", model_rejected: "model rejected",
        qualification_error: "qualification retry pending",
        qualification_error_cooldown: "qualification error cooldown",
        curation_error: "curation retry pending",
        curation_error_cooldown: "curation error cooldown",
        standby_lock_held: "another editor instance is active",
        running: "editing in GPU reservation",
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
        incident_recovery_or_editorial_operation_assessment_required: "Operational/configuration changes require evidence-cited editorial assessment.",
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
  source: "codex" | "local_llm_chat" | string;
  model: string | null;
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
  project: string;
  created_at: string;
  updated_at: string;
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
  project: string;
  tags: string[];
  first_seen_at: string;
  last_seen_at: string;
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

type KnowledgeFacets = {
  projects: Array<{
    key: string;
    count: number;
    latest_at: string;
    categories: Array<{ key: string; count: number; latest_at: string }>;
  }>;
  tags: Array<{ key: string; count: number; latest_at: string }>;
};

type JournalEntry = {
  id: string;
  source_stop_activity_id: string;
  project: string;
  category: string;
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
  citation_number?: number;
};

type JournalProjectSummary = {
  id: string;
  project: string;
  title: string;
  entry_count: number;
  document_count: number;
  latest_at: string;
  verification_status: string;
  article_status?: string;
  revision_number?: number | null;
  last_compared_at?: string | null;
};

type ArticleSentence = {
  text: string;
  source_ids: string[];
};

type ArticleParagraph = {
  sentences: ArticleSentence[];
};

type ArticleSource = {
  id: string;
  citation_number: number;
  source_type: "document_chunk" | "project_journal";
  title: string;
  excerpt?: string;
  content_hash: string;
  document_id?: string;
  document_version_id?: string;
  chunk_id?: string;
  canonical_path?: string;
  relative_path?: string;
  start_line?: number;
  end_line?: number;
  journal_entry_id?: string;
  occurred_at?: string;
  verification_status?: string;
};

type JournalProjectDocument = JournalProjectSummary & {
  visible_entry_count: number;
  truncated: boolean;
  content_type: "canonical_article" | "legacy_extract";
  article_status?: string;
  revision_number?: number | null;
  prompt_version?: string;
  model?: string;
  model_digest?: string;
  standfirst?: ArticleParagraph;
  change_summary?: {
    new_or_changed_sources?: number;
    removed_sources?: number;
    total_sources?: number;
    cited_sources?: number;
    batches?: number;
  };
  sections: Array<{
    id: string;
    category: string;
    title: { ko: string; en: string };
    updated_at: string;
    content: string;
    verification_status: string;
    citation_numbers: number[];
  } | {
    key: string;
    title: string;
    paragraphs: ArticleParagraph[];
  }>;
  sources: Array<{
    citation_number: number;
    entry: JournalEntry;
  } | ArticleSource>;
};

type CurationStatus = {
  enabled: boolean;
  project_article_enabled: boolean;
  auto_publish: boolean;
  model: string;
  prompt_version: string;
  editor_connection: "connected" | "starting" | "waiting_for_gpu" | "scheduled_idle";
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
  project_articles?: {
    projects: number;
    current: number;
    due: number;
    missing: number;
    failed: number;
    processing: number;
    per_run_limit: number;
  };
  record_summary?: {
    verified_cases: number;
    project_journals: number;
    editorial_candidates: number;
    activity_only: number;
  };
};

function MarkdownArticle({ markdown, className = "" }: { markdown: string; className?: string }) {
  const blocks = markdown.trim().split(/\n{2,}/).map((block, index) => {
    const value = block.trim();
    const lines = value.split("\n");
    if (value.startsWith("```")) {
      return <pre key={index}><code>{lines.slice(1, -1).join("\n")}</code></pre>;
    }
    if (value.startsWith("### ")) return <h4 key={index}>{value.slice(4)}</h4>;
    if (value.startsWith("## ")) return <h3 key={index}>{value.slice(3)}</h3>;
    if (value.startsWith("# ")) return <h2 key={index}>{value.slice(2)}</h2>;
    if (value.startsWith("> ")) return <blockquote key={index}>{value.replace(/^>\s?/gm, "")}</blockquote>;
    if (lines.every((line) => /^[-*]\s+/.test(line))) {
      return <ul key={index}>{lines.map((line, itemIndex) => <li key={itemIndex}>{line.replace(/^[-*]\s+/, "")}</li>)}</ul>;
    }
    if (lines.every((line) => /^\d+\.\s+/.test(line))) {
      return <ol key={index}>{lines.map((line, itemIndex) => <li key={itemIndex}>{line.replace(/^\d+\.\s+/, "")}</li>)}</ol>;
    }
    return <p key={index}>{value}</p>;
  });
  return <div className={`knowledge-markdown ${className}`}>{blocks}</div>;
}

function Article({ markdown }: { markdown: string }) {
  return <MarkdownArticle markdown={markdown} className="curated-article" />;
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

function activityTypeLabel(value: string, locale: Locale) {
  const labels: Record<string, [string, string]> = {
    SessionStart: ["작업 세션 시작", "Work session started"],
    UserPromptSubmit: ["사용자 지시", "User instruction"],
    PostToolUse: ["도구 실행 결과", "Tool execution result"],
    Stop: ["작업 결과 보고", "Work result reported"],
    SubagentStart: ["보조 작업 시작", "Subtask started"],
    SubagentStop: ["보조 작업 완료", "Subtask completed"],
    LocalChat: ["로컬 모델 대화", "Local model conversation"],
  };
  return labels[value]?.[locale === "ko" ? 0 : 1] ??
    (locale === "ko" ? "작업 기록" : "Activity record");
}

export function ActivityHistory({
  locale,
  source = "codex",
}: {
  locale: Locale;
  source?: "codex" | "local_llm_chat";
}) {
  const text = source === "local_llm_chat"
    ? textByLocale[locale].localChat
    : textByLocale[locale].activity;
  const [selected, setSelected] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const activities = useQuery({
    queryKey: ["activities", source, page],
    queryFn: () => api<{ items: ActivityItem[]; total: number }>(
      `/api/v1/activities?page=${page}&page_size=${pageSize}&source=${source}`,
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
          <Activity size={16} /><span><strong>{
            item.source === "local_llm_chat"
              ? (item.model ?? activityTypeLabel(item.event_type, locale))
              : activityTypeLabel(item.event_type, locale)
          }</strong>
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
          <div className="panel-head"><h2>{activityTypeLabel(detail.data.event_type, locale)}</h2>
            <Badge value={detail.data.verification_status} locale={locale} /></div>
          <dl className="detail-grid">
            <div><dt>{text.instruction}</dt><dd>{detail.data.instruction ?? "—"}</dd></div>
            <div><dt>{text.exitCode}</dt><dd>{detail.data.exit_code ?? text.notObserved}</dd></div>
            <div><dt>{text.changedFiles}</dt><dd>{detail.data.changed_files.join(", ") || "—"}</dd></div>
            <div><dt>{text.reported}</dt><dd>{detail.data.reported_result ?? "—"}</dd></div>
            <div><dt>{text.verified}</dt><dd>{detail.data.verified_result ?? "—"}</dd></div>
          </dl>
          <details className="technical-disclosure">
            <summary>{locale === "ko" ? "기술 정보" : "Technical details"}</summary>
            <dl className="detail-grid">
              <div><dt>{text.command}</dt><dd className="mono">{detail.data.command ?? "—"}</dd></div>
              <div><dt>{text.versions}</dt><dd className="mono">
                {detail.data.document_version_ids.join(", ") || "—"}</dd></div>
            </dl>
          </details>
        </> : <div className="inline-empty">{text.select}</div>}
      </article>
    </div>
  </section>;
}

function IntegratedProjectJournal({
  document,
  locale,
  onOpenHistory,
}: {
  document: JournalProjectDocument;
  locale: Locale;
  onOpenHistory: () => void;
}) {
  const text = textByLocale[locale].knowledge;
  const [openCitation, setOpenCitation] = useState<{
    number: number;
    location: string;
  } | null>(null);
  const canonical = document.content_type === "canonical_article";
  const articleStatus = document.article_status ?? "pending_editor";
  const articleStatusLabel = text.projectArticleStatus[
    articleStatus as keyof typeof text.projectArticleStatus
  ] ?? articleStatus;
  const articleSources = canonical
    ? document.sources.filter((source): source is ArticleSource => !("entry" in source))
    : [];
  const sourceById = new Map(articleSources.map((source) => [source.id, source]));
  const legacySourceByCitation = new Map(
    document.sources
      .filter((source): source is { citation_number: number; entry: JournalEntry } =>
        "entry" in source)
      .map((source) => [source.citation_number, source.entry]),
  );
  const articleSourceCard = (source: ArticleSource) => (
    <aside className="journal-source-card sentence-source-card"
      id={`journal-source-${source.citation_number}`}
      aria-label={`${text.sourceArticle} ${source.citation_number}`}>
      <div className="journal-source-card-head">
        <span>{source.citation_number}</span>
        <div><strong>{text.sourceArticle}</strong><p>{source.title}</p></div>
        <button type="button" onClick={() => setOpenCitation(null)}
          aria-label={text.closeSource}>×</button>
      </div>
      <dl>
        <div><dt>{locale === "ko" ? "원본 유형" : "Source type"}</dt>
          <dd>{source.source_type === "document_chunk"
            ? (locale === "ko" ? "프로젝트 문서" : "Project document")
            : (locale === "ko" ? "개발 작업 기록" : "Development record")}</dd></div>
        {source.relative_path && <div><dt>{locale === "ko" ? "경로" : "Path"}</dt>
          <dd className="mono">{source.relative_path}</dd></div>}
        {source.start_line != null && <div><dt>{locale === "ko" ? "원본 줄" : "Source lines"}</dt>
          <dd>{source.start_line}–{source.end_line}</dd></div>}
        {source.occurred_at && <div><dt>{text.latest}</dt><dd>{
          new Date(source.occurred_at).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")
        }</dd></div>}
        {source.verification_status && <div><dt>{text.verification}</dt>
          <dd><Badge value={source.verification_status} locale={locale} /></dd></div>}
      </dl>
      {source.excerpt && <>
        <h4>{locale === "ko" ? "인용한 원문" : "Cited original"}</h4>
        <MarkdownArticle markdown={source.excerpt} />
      </>}
    </aside>
  );
  const articleParagraph = (paragraph: ArticleParagraph, key: string) => (
    <div className="project-article-paragraph" key={key}>
      <p>{paragraph.sentences.map((sentence, sentenceIndex) => {
        const sources = sentence.source_ids
          .map((sourceId) => sourceById.get(sourceId))
          .filter((source): source is ArticleSource => Boolean(source));
        return <span className="cited-sentence" key={`${key}-${sentenceIndex}`}>
          {sentence.text}{" "}
          <span className="sentence-citations" aria-label={text.sourceList}>
            {sources.map((source) => {
              const location = `${key}:${sentenceIndex}:${source.id}`;
              const expanded = openCitation?.location === location;
              return <button key={source.id} type="button"
              aria-expanded={expanded}
              aria-controls={`journal-source-${source.citation_number}`}
              onClick={() => setOpenCitation(
                expanded
                  ? null
                  : { number: source.citation_number, location },
              )}>{source.citation_number}</button>;
            })}
          </span>{" "}
        </span>;
      })}</p>
      {paragraph.sentences.flatMap((sentence) => sentence.source_ids)
        .map((sourceId) => sourceById.get(sourceId))
        .filter((source): source is ArticleSource => Boolean(source))
        .filter((source, index, values) =>
          source.citation_number === openCitation?.number
          && openCitation.location.startsWith(`${key}:`)
          && values.findIndex((item) => item.id === source.id) === index)
        .map(articleSourceCard)}
    </div>
  );
  return <div className="journal-document integrated-journal">
    <div className="integrated-journal-intro">
      <div>
        <p className="journal-lead">{text.journalSubtitle}</p>
        <div className="journal-meta-grid">
          <div><small>{text.hierarchy}</small><strong>{document.project}</strong></div>
          <div><small>{text.latest}</small><strong>{new Date(document.latest_at).toLocaleString(
            locale === "ko" ? "ko-KR" : "en-US",
          )}</strong></div>
          <div><small>{text.historyCount}</small><strong>{document.entry_count}</strong></div>
          {canonical && <div><small>{locale === "ko" ? "문서 개정" : "Article revision"}</small>
            <strong>v{document.revision_number}</strong></div>}
          <div><small>{text.projectArticles}</small><strong>{articleStatusLabel}</strong></div>
        </div>
      </div>
      <button className="secondary" onClick={onOpenHistory}>
        <History size={15} />{text.openHistory}
      </button>
    </div>
    {document.truncated && <p className="journal-limit-note">{text.integratedLimit}</p>}
    <p className="journal-citation-help">{text.sourceHelp}</p>
    {canonical && document.change_summary && <div className="article-update-summary">
      <span>{locale === "ko" ? "이번 갱신" : "This update"}</span>
      <strong>{locale === "ko"
        ? `변경 근거 ${document.change_summary.new_or_changed_sources ?? 0}개 · 제거 ${document.change_summary.removed_sources ?? 0}개`
        : `${document.change_summary.new_or_changed_sources ?? 0} changed sources · ${document.change_summary.removed_sources ?? 0} removed`}</strong>
      <small>{locale === "ko"
        ? `전체 ${document.change_summary.total_sources ?? 0}개 원본 비교`
        : `${document.change_summary.total_sources ?? 0} total sources compared`}</small>
    </div>}
    <div className="project-current-body">
      {!canonical && <div className="project-article-pending" role="status">
        <Cpu size={22} />
        <div>
          <h2>{text.projectArticlePendingTitle}</h2>
          <p>{text.projectArticlePendingBody}</p>
          <small>
            {document.document_count ?? 0}{" "}
            {locale === "ko" ? "개 문서" : "documents"}
            {" · "}
            {document.entry_count} {text.historyCount}
          </small>
        </div>
      </div>}
      {canonical && <article className="project-canonical-article">
        <h2>{document.title}</h2>
        {document.standfirst && <div className="project-article-standfirst">
          {articleParagraph(document.standfirst, "standfirst")}
        </div>}
        {document.sections.map((section, sectionIndex) => "paragraphs" in section
          ? <section key={section.key} id={`project-section-${section.key}`}>
            <h3>{section.title}</h3>
            {section.paragraphs.map((paragraph, paragraphIndex) =>
              articleParagraph(paragraph, `${sectionIndex}-${paragraphIndex}`))}
          </section>
          : null)}
      </article>}
      {!canonical && articleStatus === "current"
        && document.sections.map((section) => "content" in section
        ? <section className="project-current-section"
        id={`project-section-${section.id}`} key={section.id}>
        <header>
          <div><p>{new Date(section.updated_at).toLocaleDateString(
            locale === "ko" ? "ko-KR" : "en-US",
          )}</p><h3>{section.title[locale]}</h3></div>
          <div className="section-citations" aria-label={text.sourceList}>
            {section.citation_numbers.map((citation) => <button
              key={citation}
              type="button"
              aria-expanded={
                openCitation?.location === `legacy:${section.id}:${citation}`
              }
              aria-controls={`journal-source-${citation}`}
              onClick={() => {
                const location = `legacy:${section.id}:${citation}`;
                setOpenCitation(openCitation?.location === location
                  ? null
                  : { number: citation, location });
              }}
            >{citation}</button>)}
          </div>
        </header>
        {section.citation_numbers.map((citation) => {
          const entry = legacySourceByCitation.get(citation);
          if (
            !entry
            || openCitation?.location !== `legacy:${section.id}:${citation}`
          ) return null;
          return <aside className="journal-source-card" id={`journal-source-${citation}`}
            key={citation} aria-label={`${text.sourceArticle} ${citation}`}>
            <div className="journal-source-card-head">
              <span>{citation}</span>
              <div><strong>{text.sourceArticle}</strong><p>{entry.title}</p></div>
              <button type="button" onClick={() => setOpenCitation(null)}
                aria-label={text.closeSource}>×</button>
            </div>
            <dl>
              <div><dt>{text.latest}</dt><dd>{new Date(entry.occurred_at).toLocaleString(
                locale === "ko" ? "ko-KR" : "en-US",
              )}</dd></div>
              <div><dt>{text.verification}</dt><dd>{entry.verification.length}</dd></div>
              <div><dt>{text.changedFiles}</dt><dd>{entry.changed_files.length}</dd></div>
            </dl>
            <h4>{text.sourceIntent}</h4>
            <MarkdownArticle markdown={entry.intent} />
            <h4>{text.sourceReport}</h4>
            <MarkdownArticle markdown={entry.change_summary} />
            <h4>{text.verification}</h4>
            {entry.verification.length ? <ul className="evidence-list">
              {entry.verification.map((evidence, index) => <li
                key={`${evidence.command_family}-${index}`}>
                <strong>{evidence.command_family ?? evidence.evidence_type ?? "validation"}</strong>
                <span>exit {evidence.exit_code ?? "—"}</span>
              </li>)}
            </ul> : <p className="muted-copy">—</p>}
            {entry.failures.length > 0 && <>
              <h4>{text.failures}</h4>
              <MarkdownArticle markdown={entry.resolution} />
            </>}
          </aside>;
        })}
        <MarkdownArticle markdown={section.content} />
        <div className="journal-evidence-summary">
          <Badge value={section.verification_status} locale={locale} />
        </div>
      </section> : null)}
    </div>
  </div>;
}

export function KnowledgeCases({ locale }: { locale: Locale }) {
  const text = textByLocale[locale].knowledge;
  // Project work is first recorded as a readable, evidence-labelled journal.
  // Canonical cases are deliberately narrower: they require a reusable claim
  // and must never be used as a count of all completed project work.
  const [tab, setTab] = useState<"cases" | "candidates" | "journal">("journal");
  const [journalView, setJournalView] = useState<"current" | "history">("current");
  const [selected, setSelected] = useState<string | null>(null);
  const [selectedProject, setSelectedProject] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("");
  const [selectedTag, setSelectedTag] = useState("");
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const curation = useQuery({
    queryKey: ["knowledge-curation-status"],
    queryFn: () => api<CurationStatus>("/api/v1/knowledge/curation/status"),
    refetchInterval: 30000,
  });
  const cases = useQuery({
    queryKey: [
      "knowledge-cases",
      page,
      selectedProject,
      selectedCategory,
      selectedTag,
    ],
    queryFn: () => api<{ items: Case[]; total: number }>(
      `/api/v1/knowledge/cases?page=${page}&page_size=${pageSize}`
      + `${selectedProject ? `&project=${encodeURIComponent(selectedProject)}` : ""}`
      + `${selectedCategory ? `&category=${encodeURIComponent(selectedCategory)}` : ""}`
      + `${selectedTag ? `&tags=${encodeURIComponent(selectedTag)}` : ""}`,
    ),
    enabled: tab === "cases",
  });
  const facets = useQuery({
    queryKey: ["knowledge-facets", tab],
    queryFn: () => api<KnowledgeFacets>(
      `/api/v1/knowledge/facets?kind=${encodeURIComponent(tab)}`,
    ),
  });
  const candidates = useQuery({
    queryKey: [
      "knowledge-candidates",
      page,
      selectedProject,
      selectedCategory,
    ],
    queryFn: () => api<{ items: Candidate[]; total: number }>(
      `/api/v1/knowledge/candidates?page=${page}&page_size=${pageSize}&review_only=true`
      + `${selectedProject ? `&project=${encodeURIComponent(selectedProject)}` : ""}`
      + `${selectedCategory ? `&category=${encodeURIComponent(selectedCategory)}` : ""}`,
    ),
    enabled: tab === "candidates",
  });
  const journalProjects = useQuery({
    queryKey: ["project-journal-projects", page, selectedProject],
    queryFn: () => api<{ items: JournalProjectSummary[]; total: number }>(
      `/api/v1/project-journal/projects?page=${page}&page_size=${pageSize}`
      + `${selectedProject ? `&project=${encodeURIComponent(selectedProject)}` : ""}`,
    ),
    enabled: tab === "journal" && journalView === "current",
  });
  const currentJournalProject = selected ?? journalProjects.data?.items[0]?.id ?? null;
  const journalHistory = useQuery({
    queryKey: ["project-journal-history", page, selectedProject, selectedCategory],
    queryFn: () => api<{ items: JournalEntry[]; total: number }>(
      `/api/v1/project-journal?page=${page}&page_size=${pageSize}`
      + `${selectedProject ? `&project=${encodeURIComponent(selectedProject)}` : ""}`
      + `${selectedCategory ? `&category=${encodeURIComponent(selectedCategory)}` : ""}`,
    ),
    enabled: tab === "journal" && journalView === "history",
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
  const journalProjectDetail = useQuery({
    queryKey: ["project-journal-project", currentJournalProject],
    queryFn: () => api<JournalProjectDocument>(
      `/api/v1/project-journal/projects/${encodeURIComponent(currentJournalProject!)}`,
    ),
    enabled: tab === "journal" && journalView === "current"
      && Boolean(currentJournalProject),
  });
  const journalDetail = useQuery({
    queryKey: ["project-journal-entry", selected],
    queryFn: () => api<JournalEntry>(`/api/v1/project-journal/${selected}`),
    enabled: tab === "journal" && journalView === "history" && Boolean(selected),
  });
  const items: Array<Case | Candidate | JournalEntry | JournalProjectSummary> = tab === "cases"
    ? cases.data?.items ?? []
    : tab === "candidates"
      ? candidates.data?.items ?? []
      : journalView === "current"
        ? journalProjects.data?.items ?? []
        : journalHistory.data?.items ?? [];
  const total = tab === "cases"
    ? cases.data?.total ?? 0
    : tab === "candidates"
      ? candidates.data?.total ?? 0
      : journalView === "current"
        ? journalProjects.data?.total ?? 0
        : journalHistory.data?.total ?? 0;
  const selectedDetail = tab === "cases"
    ? caseDetail.data
    : tab === "candidates"
      ? candidateDetail.data
      : journalView === "current"
        ? journalProjectDetail.data
        : journalDetail.data;
  const scheduler = curation.data?.scheduler;
  const schedulerLabel = scheduler?.state
    ? text.schedulerStates[scheduler.state as keyof typeof text.schedulerStates] ?? scheduler.state
    : text.schedulerStates.not_started;
  const qualificationStatus = curation.data?.qualification?.status ?? "PENDING";
  const qualificationLabel = text.qualificationStates[
    qualificationStatus as keyof typeof text.qualificationStates
  ] ?? qualificationStatus;
  const editorConnection = curation.data?.editor_connection ?? "scheduled_idle";
  const editorConnectionLabel = text.editorConnectionStates[
    editorConnection as keyof typeof text.editorConnectionStates
  ] ?? editorConnection;
  const isCurrentJournal = tab === "journal" && journalView === "current";
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
        <strong>{qualificationLabel}</strong></div>
      <div><small>{text.editorConnection}</small>
        <strong>{editorConnectionLabel}</strong></div>
      <div><small>{text.lastSnapshot}</small><strong>
        {scheduler?.processed_candidate_count ?? 0}/{scheduler?.snapshot_candidate_count ?? 0}
        {" "}{text.processed} · {scheduler?.failed_candidate_count ?? 0} {text.failed}
      </strong></div>
      <div><small>{text.projectArticles}</small><strong>
        {curation.data?.project_articles
          ? `${curation.data.project_articles.current}/${curation.data.project_articles.projects} ${text.projectArticleStatus.current} · ${curation.data.project_articles.due} ${text.projectArticleStatus.pending}`
          : "—"}
      </strong></div>
      <div><small>{text.nextAttempt}</small><strong>{scheduler?.next_attempt_at
        ? new Date(scheduler.next_attempt_at).toLocaleString(locale === "ko" ? "ko-KR" : "en-US")
        : "—"}</strong></div>
    </div>
    <div className="tabs">
      <button className={tab === "journal" ? "active" : ""} onClick={() => {
        setTab("journal"); setSelected(null); setSelectedProject("");
        setSelectedCategory(""); setSelectedTag(""); setJournalView("current"); setPage(1);
      }}>{text.journal}<span className="tab-count">{journalProjects.data?.total ?? 0}</span></button>
      <button className={tab === "cases" ? "active" : ""} onClick={() => {
        setTab("cases"); setSelected(null); setSelectedProject("");
        setSelectedCategory(""); setSelectedTag(""); setPage(1);
      }}>{text.cases}<span className="tab-count">{cases.data?.total ?? 0}</span></button>
      <button className={tab === "candidates" ? "active" : ""} onClick={() => {
        setTab("candidates"); setSelected(null); setSelectedProject("");
        setSelectedCategory(""); setSelectedTag(""); setPage(1);
      }}>{text.candidates}<span className="tab-count">{candidates.data?.total ?? 0}</span></button>
    </div>
    {tab === "journal" && <div className="journal-view-switch" role="tablist"
      aria-label={text.journal}>
      <button id="journal-tab-current" role="tab" aria-controls="journal-view-panel"
        aria-selected={journalView === "current"}
        className={journalView === "current" ? "active" : ""} onClick={() => {
          setJournalView("current"); setSelected(null); setSelectedCategory(""); setPage(1);
        }}>{text.currentDocument}</button>
      <button id="journal-tab-history" role="tab" aria-controls="journal-view-panel"
        aria-selected={journalView === "history"}
        className={journalView === "history" ? "active" : ""} onClick={() => {
          setJournalView("history"); setSelected(null); setPage(1);
        }}>{text.history}</button>
    </div>}
    {isCurrentJournal && <nav className="panel journal-project-switcher"
      aria-label={text.projectSelector}>
      <div className="journal-project-buttons">
        {journalProjects.data?.items.map((project) => <button type="button"
          key={project.id}
          className={currentJournalProject === project.id ? "active" : ""}
          aria-current={currentJournalProject === project.id ? "page" : undefined}
          onClick={() => setSelected(project.id)}>
          <span>{project.project}</span>
          <small>{project.article_status === "current" && project.revision_number
            ? `v${project.revision_number}`
            : text.projectArticleStatus[
              (project.article_status ?? "pending_editor") as keyof typeof text.projectArticleStatus
            ] ?? project.article_status}</small>
        </button>)}
      </div>
      <PageControls page={page} pageSize={pageSize} total={journalProjects.data?.total ?? 0}
        onChange={(value) => { setPage(value); setSelected(null); }} labels={text} />
    </nav>}
    <div id={tab === "journal" ? "journal-view-panel" : undefined}
      role={tab === "journal" ? "tabpanel" : undefined}
      aria-labelledby={tab === "journal" ? `journal-tab-${journalView}` : undefined}
      className={`knowledge-browser ${
      isCurrentJournal ? "journal-current" : ""
    }`}>
      {!isCurrentJournal && <aside className="panel knowledge-tree" role="tree"
        aria-label={text.hierarchy}>
        <div className="knowledge-tree-head"><FolderTree size={16} /><strong>{text.hierarchy}</strong></div>
        <button role="treeitem" aria-selected={!selectedProject}
          className={!selectedProject ? "selected" : ""} onClick={() => {
          setSelectedProject(""); setSelectedCategory(""); setSelected(null); setPage(1);
        }}>
          <span>{text.allProjects}</span><small>{tab === "journal" && journalView === "current"
            ? journalProjects.data?.total ?? 0
            : facets.data?.projects.reduce((sum, project) => sum + project.count, 0) ?? 0}</small>
        </button>
        {facets.data?.projects.map((project) => {
          const expanded = selectedProject === project.key;
          return <div key={project.key}>
            <button role="treeitem" aria-expanded={expanded}
              aria-selected={expanded && !selectedCategory}
              className={expanded && !selectedCategory ? "selected" : ""} onClick={() => {
              setSelectedProject(project.key);
              setSelectedCategory("");
              setSelected(null);
              setPage(1);
            }}>
              {expanded ? <ChevronRight className="tree-rotated" size={13} /> : <ChevronRight size={13} />}
              <span>{project.key}</span><small>{project.count}</small>
            </button>
            {expanded && (tab !== "journal" || journalView === "history") &&
              <div className="knowledge-tree-children" role="group">
              <button role="treeitem" aria-selected={!selectedCategory}
                className={!selectedCategory ? "selected" : ""} onClick={() => {
                setSelectedCategory(""); setSelected(null); setPage(1);
              }}>
                <span>{text.allCategories}</span><small>{project.count}</small>
              </button>
              {project.categories.map((category) => <button role="treeitem" key={category.key}
                aria-selected={selectedCategory === category.key}
                className={selectedCategory === category.key ? "selected" : ""}
                onClick={() => {
                  setSelectedCategory(category.key); setSelected(null); setPage(1);
                }}>
                <span>{knowledgeCategoryLabel(category.key, locale)}</span>
                <small>{category.count}</small>
              </button>)}
            </div>}
          </div>;
        })}
        {tab === "cases" && <div className="knowledge-tag-filter">
          <label htmlFor="knowledge-tag"><Tag size={14} />{text.tags}</label>
          <select id="knowledge-tag" value={selectedTag} onChange={(event) => {
            setSelectedTag(event.target.value); setSelected(null); setPage(1);
          }}>
            <option value="">{text.allTags}</option>
            {facets.data?.tags.map((item) => <option key={item.key} value={item.key}>
              {knowledgeTagLabel(item.key, locale)} ({item.count})
            </option>)}
          </select>
        </div>}
      </aside>}
      {!isCurrentJournal && <div className="panel record-list">
        <div className="record-sort">{text.latestFirst}</div>
        {items.map((item) => <button key={item.id} onClick={() => setSelected(item.id)}
          className={selected === item.id ? "selected" : ""}>
          <BookCheck size={16} /><span><strong>{"entry_count" in item
            ? `${item.project} ${locale === "ko" ? "개발 일지" : "development journal"}`
            : item.title}</strong>
            <small>{"entry_count" in item
              ? `${item.entry_count} ${text.historyCount} · ${new Date(item.latest_at).toLocaleString(
                locale === "ko" ? "ko-KR" : "en-US",
              )}`
              : "source_stop_activity_id" in item
              ? `${item.project} · ${knowledgeCategoryLabel(item.category, locale)} · ${new Date(item.occurred_at).toLocaleString(
                locale === "ko" ? "ko-KR" : "en-US",
              )}`
              : `${item.project} · ${knowledgeCategoryLabel(item.category, locale)}${!("evidence_gate_status" in item)
                ? ` · ${new Date(item.last_seen_at).toLocaleString(
                  locale === "ko" ? "ko-KR" : "en-US",
                )}` : ` · ${new Date(item.updated_at).toLocaleString(
                  locale === "ko" ? "ko-KR" : "en-US",
                )}`}`
            }</small></span>
          <Badge value={"entry_count" in item
            ? item.verification_status
            : "source_stop_activity_id" in item
            ? item.verification_status
            : "evidence_gate_status" in item
              ? candidateBadge(item) : item.status} locale={locale} /><ChevronRight size={14} />
        </button>)}
        {!items.length && <div className="inline-empty">{text.empty}</div>}
        <PageControls page={page} pageSize={pageSize} total={total}
          onChange={(value) => { setPage(value); setSelected(null); }} labels={text} />
      </div>}
      <article className="panel detail-card">
        {selectedDetail ? <>
          <div className="panel-head"><h2>{"sections" in selectedDetail
            ? `${selectedDetail.project} ${locale === "ko" ? "프로젝트 문서" : "project document"}`
            : "entry_count" in selectedDetail
            ? `${selectedDetail.project} ${locale === "ko" ? "개발 일지" : "development journal"}`
            : selectedDetail.title}</h2>
            <Badge value={"entry_count" in selectedDetail
              ? selectedDetail.verification_status
              : "source_stop_activity_id" in selectedDetail
              ? selectedDetail.verification_status
              : "evidence_gate_status" in selectedDetail
                ? candidateBadge(selectedDetail) : selectedDetail.status} locale={locale} /></div>
          {"sections" in selectedDetail
            ? <IntegratedProjectJournal document={selectedDetail} locale={locale}
              onOpenHistory={() => {
                setJournalView("history");
                setSelected(null);
                setSelectedProject(selectedDetail.project);
                setSelectedCategory("");
                setPage(1);
              }} />
            : "source_stop_activity_id" in selectedDetail ? <>
            <div className="journal-document">
              <p className="journal-lead">{text.journalSubtitle}</p>
              <div className="journal-meta-grid">
                <div><small>{text.hierarchy}</small><strong>{selectedDetail.project}</strong></div>
                <div><small>{text.latest}</small><strong>{new Date(selectedDetail.occurred_at).toLocaleString(
                  locale === "ko" ? "ko-KR" : "en-US",
                )}</strong></div>
                <div><small>{text.verification}</small><Badge value={selectedDetail.verification_status} locale={locale} /></div>
              </div>
              <section><h3>{text.intent}</h3><MarkdownArticle markdown={selectedDetail.intent} /></section>
              <section><h3>{text.changes}</h3><MarkdownArticle markdown={selectedDetail.change_summary} /></section>
              <section><h3>{text.changedFiles}</h3>
                {selectedDetail.changed_files.length ? <ul className="changed-file-list mono">
                  {selectedDetail.changed_files.map((path) => <li key={path}>{path}</li>)}
                </ul> : <p className="muted-copy">—</p>}
              </section>
              <section><h3>{text.verification}</h3>
                {selectedDetail.verification.length ? <ul className="evidence-list">
                  {selectedDetail.verification.map((item, index) => <li key={`${item.command_family}-${index}`}>
                    <strong>{item.command_family ?? item.evidence_type ?? "validation"}</strong>
                    <span>exit {item.exit_code ?? "—"}</span>
                  </li>)}
                </ul> : <p className="muted-copy">—</p>}
              </section>
              <section><h3>{text.failures}</h3>
                {selectedDetail.failures.length ? <>
                  <ul className="evidence-list">
                    {selectedDetail.failures.map((item, index) => <li key={`${item.command_family}-${index}`}>
                      <strong>{item.command_family ?? "command"}</strong><span>exit {item.exit_code ?? "—"}</span>
                    </li>)}
                  </ul>
                  <MarkdownArticle markdown={selectedDetail.resolution} />
                </> : <p className="muted-copy">{text.noObservedFailure}</p>}
              </section>
              <section><h3>{text.references}</h3>
                {selectedDetail.knowledge_references.length ? <ul className="changed-file-list mono">
                  {selectedDetail.knowledge_references.map((item, index) => <li key={`${item.document_id}-${item.chunk_id}-${index}`}>
                    {item.relative_path ?? item.canonical_path ?? item.document_id ?? "document"}
                    {item.chunk_id ? ` · chunk ${item.chunk_id}` : ""}
                    {item.retrieval_score !== undefined ? ` · ${item.retrieval_score}` : ""}
                  </li>)}
                </ul> : <p className="muted-copy">{text.noReferences}</p>}
              </section>
            </div>
          </> : <>
          <dl className="detail-grid">
            {!("evidence_gate_status" in selectedDetail) && <div><dt>{text.latest}</dt><dd>
              {selectedDetail.project} · {new Date(selectedDetail.last_seen_at).toLocaleString(
                locale === "ko" ? "ko-KR" : "en-US",
              )}
              {!!selectedDetail.tags.length && <div className="knowledge-tags">
                {selectedDetail.tags.map((tag) => <span key={tag}>
                  {knowledgeTagLabel(tag, locale)}
                </span>)}
              </div>}
            </dd></div>}
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
