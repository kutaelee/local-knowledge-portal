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

function Badge({ value }: { value: string }) {
  const good = value === "VERIFIED" || value === "verified" || value === "published";
  return <span className={`evidence-badge ${good ? "verified" : "review"}`}>
    {good ? <CircleCheck size={12} /> : <ShieldAlert size={12} />}{value}
  </span>;
}

export function ActivityHistory() {
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
      <p className="eyebrow">CODEX ACTIVITY</p><h1>Activity history</h1>
      <p>Instructions, tools, changes, and independently observed results.</p>
    </div></div>
    <div className="master-detail">
      <div className="panel record-list">
        {activities.data?.items.map((item) => <button
          key={item.id} className={selected === item.id ? "selected" : ""}
          onClick={() => setSelected(item.id)}
        >
          <Activity size={16} /><span><strong>{item.event_type}</strong>
            <small>{item.project ?? "global"} · {new Date(item.occurred_at).toLocaleString()}</small>
          </span><Badge value={item.verification_status} /><ChevronRight size={14} />
        </button>)}
        {!activities.isLoading && !activities.data?.items.length &&
          <div className="inline-empty">No hook activity collected.</div>}
      </div>
      <article className="panel detail-card">
        {detail.data ? <>
          <div className="panel-head"><h2>{detail.data.event_type}</h2>
            <Badge value={detail.data.verification_status} /></div>
          <dl className="detail-grid">
            <div><dt>User instruction</dt><dd>{detail.data.instruction ?? "—"}</dd></div>
            <div><dt>Command</dt><dd className="mono">{detail.data.command ?? "—"}</dd></div>
            <div><dt>Exit code</dt><dd>{detail.data.exit_code ?? "not observed"}</dd></div>
            <div><dt>Changed files</dt><dd>{detail.data.changed_files.join(", ") || "—"}</dd></div>
            <div><dt>Document versions</dt><dd className="mono">
              {detail.data.document_version_ids.join(", ") || "—"}</dd></div>
            <div><dt>Reported result</dt><dd>{detail.data.reported_result ?? "—"}</dd></div>
            <div><dt>Verified result</dt><dd>{detail.data.verified_result ?? "—"}</dd></div>
          </dl>
        </> : <div className="inline-empty">Select an activity to inspect evidence.</div>}
      </article>
    </div>
  </section>;
}

export function KnowledgeCases() {
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
      <p className="eyebrow">EVIDENCE-GATED WIKI</p><h1>Knowledge cases</h1>
      <p>Canonical cases are published only after verified evidence passes the gate.</p>
    </div></div>
    <div className="tabs">
      <button className={tab === "cases" ? "active" : ""} onClick={() => {
        setTab("cases"); setSelected(null);
      }}>Verified cases</button>
      <button className={tab === "candidates" ? "active" : ""} onClick={() => {
        setTab("candidates"); setSelected(null);
      }}>Candidate review</button>
    </div>
    <div className="master-detail">
      <div className="panel record-list">
        {items.map((item) => <button key={item.id} onClick={() => setSelected(item.id)}
          className={selected === item.id ? "selected" : ""}>
          <BookCheck size={16} /><span><strong>{item.title}</strong>
            <small>{item.category}{!("evidence_gate_status" in item)
              ? ` · ${item.occurrence_count} occurrence(s)` : ""}</small></span>
          <Badge value={"evidence_gate_status" in item
            ? item.evidence_gate_status : item.status} /><ChevronRight size={14} />
        </button>)}
        {!items.length && <div className="inline-empty">No records in this view.</div>}
      </div>
      <article className="panel detail-card">
        {selectedDetail ? <>
          <div className="panel-head"><h2>{selectedDetail.title}</h2>
            <Badge value={"evidence_gate_status" in selectedDetail
              ? selectedDetail.evidence_gate_status : selectedDetail.status} /></div>
          <dl className="detail-grid">
            <div><dt>Problem</dt><dd>{selectedDetail.problem}</dd></div>
            <div><dt>Symptom</dt><dd>{selectedDetail.symptom}</dd></div>
            <div><dt>Root cause</dt><dd>{selectedDetail.root_cause}</dd></div>
            <div><dt>Solution</dt><dd>{selectedDetail.solution}</dd></div>
            {"reported_result" in selectedDetail &&
              <div><dt>Reported / verified</dt><dd>
                {selectedDetail.reported_result ?? "—"} / {selectedDetail.verified_result ?? "—"}
              </dd></div>}
          </dl>
          {tab === "candidates" && <button className="primary review-action"
            disabled={publish.isPending} onClick={() => publish.mutate(selectedDetail.id)}>
            <BookCheck size={15} /> Publish if evidence passes
          </button>}
          {publish.data && <p className="mutation-result">{publish.data.outcome}</p>}
          {"revisions" in selectedDetail && <div className="subrecords">
            <h3>Revisions & occurrences</h3>
            <p>{selectedDetail.revisions?.length ?? 0} revisions ·
              {" "}{selectedDetail.occurrences?.length ?? 0} occurrences ·
              {" "}{selectedDetail.relations?.length ?? 0} relations</p>
          </div>}
        </> : <div className="inline-empty">Select a record.</div>}
      </article>
    </div>
  </section>;
}

export function DocumentViewer({ documentId }: { documentId: string }) {
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
  if (document.isLoading) return <div className="inline-empty">Loading document…</div>;
  if (!document.data) return <div className="inline-empty">Document unavailable.</div>;
  return <section>
    <div className="page-title compact"><div>
      <p className="eyebrow">DOCUMENT VIEWER</p><h1>{document.data.filename}</h1>
      <p className="mono">{document.data.canonical_path}</p>
    </div></div>
    <div className="document-layout">
      <article className="panel document-content">
        {document.data.chunks.map((chunk) => <section key={chunk.id}>
          <header><span>{chunk.type}</span><span>L{chunk.start_line}–{chunk.end_line}</span></header>
          {chunk.heading && <h2>{chunk.heading}</h2>}
          {chunk.symbol && <h3>{chunk.symbol}</h3>}
          <pre>{chunk.content}</pre>
        </section>)}
      </article>
      <aside className="panel version-panel">
        <div className="panel-head"><h2>Version timeline</h2><FileDiff size={17} /></div>
        {versions.data?.map((version) => <div className="version-row" key={version.id}>
          <strong>{version.change_type}</strong><small>{new Date(version.detected_at).toLocaleString()}</small>
          <code>{version.content_hash.slice(0, 12)}</code>
        </div>)}
        {pair.length === 2 && <div className="diff-block">
          <h3>Latest diff</h3><pre>{diff.data?.lines.join("\n") || "No textual change."}</pre>
        </div>}
      </aside>
    </div>
  </section>;
}
