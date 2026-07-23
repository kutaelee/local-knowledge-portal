"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  BookCheck,
  ChevronRight,
  CircleCheck,
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
      select: "활동을 선택하면 실행 근거를 확인할 수 있습니다.",
      instruction: "사용자 지시", command: "실행 명령", exitCode: "종료 코드",
      notObserved: "관측되지 않음", changedFiles: "변경 파일",
      versions: "문서 버전", reported: "보고된 결과", verified: "검증된 결과",
    },
    knowledge: {
      eyebrow: "근거 검증 위키", title: "지식 사례",
      subtitle: "검증된 근거가 관문을 통과한 사례만 표준 지식으로 게시됩니다.",
      cases: "검증된 사례", candidates: "승격 후보 검토", occurrences: "회 발생",
      empty: "이 화면에 표시할 기록이 없습니다.", select: "기록을 선택하세요.",
      problem: "문제", symptom: "증상", cause: "근본 원인", solution: "해결 방법",
      reportedVerified: "보고 결과 / 검증 결과",
      publish: "근거 통과 시 게시", revisions: "리비전과 발생 이력",
      revisionCount: "리비전", occurrenceCount: "발생", relationCount: "관계",
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
      NEEDS_REVIEW: "검토 필요", candidate: "후보",
    },
  },
  en: {
    activity: {
      eyebrow: "Codex activity", title: "Activity history",
      subtitle: "Instructions, tools, changes, and independently observed results.",
      global: "global", empty: "No hook activity collected.",
      select: "Select an activity to inspect evidence.",
      instruction: "User instruction", command: "Command", exitCode: "Exit code",
      notObserved: "not observed", changedFiles: "Changed files",
      versions: "Document versions", reported: "Reported result", verified: "Verified result",
    },
    knowledge: {
      eyebrow: "Evidence-gated wiki", title: "Knowledge cases",
      subtitle: "Canonical cases are published only after verified evidence passes the gate.",
      cases: "Verified cases", candidates: "Candidate review", occurrences: "occurrence(s)",
      empty: "No records in this view.", select: "Select a record.",
      problem: "Problem", symptom: "Symptom", cause: "Root cause", solution: "Solution",
      reportedVerified: "Reported / verified",
      publish: "Publish if evidence passes", revisions: "Revisions & occurrences",
      revisionCount: "revisions", occurrenceCount: "occurrences", relationCount: "relations",
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
  revisions?: Array<{ id: string; number: number; evidence_summary: object; created_at: string }>;
  occurrences?: Array<{ id: string; occurred_at: string; evidence: object }>;
  relations?: Array<{ source_case_id: string; target_case_id: string; type: string }>;
};

type DocumentDetail = {
  id: string;
  filename: string;
  canonical_path: string;
  relative_path: string;
  content_hash: string;
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

export function ActivityHistory({ locale }: { locale: Locale }) {
  const text = textByLocale[locale].activity;
  const [selected, setSelected] = useState<string | null>(null);
  const activities = useQuery({
    queryKey: ["activities"],
    queryFn: () => api<{ items: ActivityItem[]; total: number }>(
      "/api/v1/activities?page_size=100",
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
  const [tab, setTab] = useState<"cases" | "candidates">("cases");
  const [selected, setSelected] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const cases = useQuery({
    queryKey: ["knowledge-cases"],
    queryFn: () => api<{ items: Case[]; total: number }>("/api/v1/knowledge/cases"),
  });
  const candidates = useQuery({
    queryKey: ["knowledge-candidates"],
    queryFn: () => api<{ items: Candidate[]; total: number }>(
      "/api/v1/knowledge/candidates",
    ),
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
  const publish = useMutation({
    mutationFn: (id: string) => api<{ outcome: string }>(
      `/api/v1/knowledge/candidates/${id}/publish`, { method: "POST" },
    ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["knowledge-candidates"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-cases"] });
      queryClient.invalidateQueries({ queryKey: ["knowledge-candidate", selected] });
    },
  });
  const items = tab === "cases" ? cases.data?.items ?? [] : candidates.data?.items ?? [];
  const selectedDetail = tab === "cases" ? caseDetail.data : candidateDetail.data;
  return <section>
    <div className="page-title compact"><div>
      <p className="eyebrow">{text.eyebrow}</p><h1>{text.title}</h1>
      <p>{text.subtitle}</p>
    </div></div>
    <div className="tabs">
      <button className={tab === "cases" ? "active" : ""} onClick={() => {
        setTab("cases"); setSelected(null);
      }}>{text.cases}</button>
      <button className={tab === "candidates" ? "active" : ""} onClick={() => {
        setTab("candidates"); setSelected(null);
      }}>{text.candidates}</button>
    </div>
    <div className="master-detail">
      <div className="panel record-list">
        {items.map((item) => <button key={item.id} onClick={() => setSelected(item.id)}
          className={selected === item.id ? "selected" : ""}>
          <BookCheck size={16} /><span><strong>{item.title}</strong>
            <small>{item.category}{!("evidence_gate_status" in item)
              ? ` · ${item.occurrence_count}${locale === "ko" ? text.occurrences : ` ${text.occurrences}`}` : ""}</small></span>
          <Badge value={"evidence_gate_status" in item
            ? item.evidence_gate_status : item.status} locale={locale} /><ChevronRight size={14} />
        </button>)}
        {!items.length && <div className="inline-empty">{text.empty}</div>}
      </div>
      <article className="panel detail-card">
        {selectedDetail ? <>
          <div className="panel-head"><h2>{selectedDetail.title}</h2>
            <Badge value={"evidence_gate_status" in selectedDetail
              ? selectedDetail.evidence_gate_status : selectedDetail.status} locale={locale} /></div>
          <dl className="detail-grid">
            <div><dt>{text.problem}</dt><dd>{selectedDetail.problem}</dd></div>
            <div><dt>{text.symptom}</dt><dd>{selectedDetail.symptom}</dd></div>
            <div><dt>{text.cause}</dt><dd>{selectedDetail.root_cause}</dd></div>
            <div><dt>{text.solution}</dt><dd>{selectedDetail.solution}</dd></div>
            {"reported_result" in selectedDetail &&
              <div><dt>{text.reportedVerified}</dt><dd>
                {selectedDetail.reported_result ?? "—"} / {selectedDetail.verified_result ?? "—"}
              </dd></div>}
          </dl>
          {tab === "candidates" && <button className="primary review-action"
            disabled={publish.isPending} onClick={() => publish.mutate(selectedDetail.id)}>
            <BookCheck size={15} /> {text.publish}
          </button>}
          {publish.data && <p className="mutation-result">{publish.data.outcome}</p>}
          {"revisions" in selectedDetail && <div className="subrecords">
            <h3>{text.revisions}</h3>
            <p>{selectedDetail.revisions?.length ?? 0} {text.revisionCount} ·
              {" "}{selectedDetail.occurrences?.length ?? 0} {text.occurrenceCount} ·
              {" "}{selectedDetail.relations?.length ?? 0} {text.relationCount}</p>
          </div>}
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
  const document = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => api<DocumentDetail>(`/api/v1/documents/${documentId}`),
  });
  const versions = useQuery({
    queryKey: ["document-versions", documentId],
    queryFn: () => api<Version[]>(`/api/v1/documents/${documentId}/versions`),
  });
  const pair = useMemo(() => versions.data?.slice(0, 2) ?? [], [versions.data]);
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
      </article>
      <aside className="panel version-panel">
        <div className="panel-head"><h2>{text.versions}</h2><FileDiff size={17} /></div>
        {versions.data?.map((version) => <div className="version-row" key={version.id}>
          <strong>{version.change_type}</strong><small>{new Date(version.detected_at)
            .toLocaleString(locale === "ko" ? "ko-KR" : "en-US")}</small>
          <code>{version.content_hash.slice(0, 12)}</code>
        </div>)}
        {pair.length === 2 && <div className="diff-block">
          <h3>{text.latestDiff}</h3><pre>{diff.data?.lines.join("\n") || text.noChange}</pre>
        </div>}
      </aside>
    </div>
  </section>;
}
