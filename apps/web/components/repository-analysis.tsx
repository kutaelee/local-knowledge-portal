"use client";

import { useGSAP } from "@gsap/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  Boxes,
  CheckCircle2,
  CircleDot,
  FileCode2,
  LoaderCircle,
  Network,
  Play,
  Search,
} from "lucide-react";
import gsap from "gsap";
import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";

gsap.registerPlugin(useGSAP);

type Project = {
  id: string;
  canonical_name: string;
  display_name: string;
  category: string;
  status: string;
  snapshot_id: string | null;
  snapshot_name: string | null;
  source_hash: string | null;
  git_commit: string | null;
  git_branch: string | null;
  dirty_worktree: boolean | null;
  languages: string[] | null;
  build_systems: string[] | null;
  file_count: number | null;
  analysis_status: string | null;
  analyzed_at: string | null;
  stale: boolean | null;
  metrics: Record<string, number>;
  warning_count: number;
};

type ProjectList = { items: Project[]; total: number };
type ProjectDetail = {
  project: Project & {
    local_source_reference: string;
    repository_origin_hash: string;
  };
  counts: Record<string, number>;
  report: KnowledgeItem | null;
  visualization: {
    relation_groups: { key: string; count: number }[];
    dependency_groups: { type: string; classification: string; count: number }[];
    flow_items: {
      source_file: string;
      target_file: string | null;
      relation_type: string;
      count: number;
    }[];
    dependency_items: {
      name: string;
      version: string | null;
      artifact_type: string;
      classification: string;
      relative_path: string;
      scope: string | null;
      analysis_status: string;
    }[];
    components: {
      key: string;
      display_name: string;
      component_type: string;
      responsibility: string;
      file_count: number;
      entry_point_count: number;
      validation_status: string;
      confidence: string;
    }[];
    lifecycle: {
      nodes: {
        key: string;
        phase: string;
        title: string;
        description: string;
        component_key: string;
        sequence: number;
        validation_status: string;
        confidence: string;
      }[];
      edges: {
        source_key: string;
        target_key: string;
        relation_type: string;
        label: string;
        provenance: string;
      }[];
    };
    dependency_usages: {
      dependency_name: string;
      component_key: string;
      usage_type: string;
      provenance: string;
      occurrence_count: number;
    }[];
  };
};
type AnalysisJob = {
  id: string;
  stage: string;
  status: string;
  metrics: Record<string, number>;
  warnings: string[];
  error_code: string | null;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
};
type JobList = { items: AnalysisJob[]; total: number };
type KnowledgeItem = {
  id: string;
  knowledge_type: string;
  title: string;
  summary: string;
  detail: string;
  processing_steps: string[];
  source_references: { file: string; start_line: number }[];
  validation_status: string;
  unknowns: string[];
};
type KnowledgeList = { items: KnowledgeItem[]; total: number };
type ConfigurationItem = {
  config_key: string;
  relative_path: string;
  declaration_line: number;
  referenced_by: string[];
  has_default: boolean;
};
type ConfigurationList = { items: ConfigurationItem[]; total: number };
type EvaluationItem = {
  id: string;
  question: string;
  question_type: string;
  scenario_type: string | null;
  passed: boolean;
  score: number;
  failure_category: string | null;
};
type EvaluationList = { items: EvaluationItem[]; total: number };
type SnapshotItem = {
  id: string;
  snapshot_name: string;
  file_count: number;
  status: string;
  stale: boolean;
  created_at: string;
};
type SnapshotList = { items: SnapshotItem[]; total: number };
type VisualizationDetail = {
  components?: ProjectDetail["visualization"]["components"];
  lifecycle?: ProjectDetail["visualization"]["lifecycle"];
  dependencies?: ProjectDetail["visualization"]["dependency_items"];
  usages?: ProjectDetail["visualization"]["dependency_usages"];
};
type SourceRootList = {
  items: { id: string; name: string; canonical_path: string; read_only: boolean }[];
  total: number;
};
type AnalysisRequest = {
  job_id: string;
  status: string;
  source_path: string;
};
type AnalysisRequestStatus = {
  id: string;
  status: string;
  source_path: string;
  error_message: string | null;
  error_details: Record<string, unknown> | null;
};
type DetailTab =
  | "overview"
  | "architecture"
  | "logic"
  | "configuration"
  | "dependencies"
  | "support"
  | "jobs"
  | "evaluation"
  | "unknowns"
  | "snapshots";

const tabs: { id: DetailTab; label: string }[] = [
  { id: "overview", label: "요약" },
  { id: "architecture", label: "구조" },
  { id: "logic", label: "처리 흐름" },
  { id: "configuration", label: "설정" },
  { id: "dependencies", label: "의존 항목" },
  { id: "support", label: "기술지원" },
  { id: "jobs", label: "작업 상태" },
  { id: "evaluation", label: "검증 결과" },
  { id: "unknowns", label: "확인 필요" },
  { id: "snapshots", label: "분석 이력" },
];

function statusTone(value: string | null | undefined) {
  if (value === "COMPLETED" || value === "SUCCEEDED" || value === "ACTIVE" || value === "succeeded") return "success";
  if (value === "FAILED" || value === "REJECTED" || value === "failed" || value === "dead_letter") return "danger";
  return "warning";
}

function displayName(value: string) {
  const labels: Record<string, string> = {
    repository_analysis: "저장소 분석 모듈",
    "local-knowledge-portal": "로컬 지식 포털",
  };
  if (labels[value]) return labels[value];
  return value.replaceAll("_", " ").replaceAll("-", " ");
}

function languageLabel(value: string) {
  const labels: Record<string, string> = {
    Python: "파이썬",
    TypeScript: "타입스크립트",
    JavaScript: "자바스크립트",
    Java: "자바",
    Kotlin: "코틀린",
    Go: "고",
    Rust: "러스트",
  };
  return labels[value] ?? value;
}

function languageList(values: string[] | null | undefined) {
  return values?.map(languageLabel).join(", ") || "언어 확인 중";
}

function statusLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    ACTIVE: "사용 중",
    COMPLETED: "분석 완료",
    SUCCEEDED: "완료",
    FAILED: "실패",
    REJECTED: "제외",
    pending: "대기 중",
    leased: "작업 준비 중",
    processing: "분석 중",
    succeeded: "분석 완료",
    failed: "실패",
    dead_letter: "재시도 종료",
    cancelled: "취소",
  };
  return labels[value ?? ""] ?? "확인 중";
}

function dependencyLabel(value: string) {
  const labels: Record<string, string> = {
    MAVEN: "자바 패키지",
    NPM: "웹 패키지",
    PYPI: "파이썬 패키지",
    JAR: "내장 자바 파일",
  };
  return labels[value] ?? "기타 의존 항목";
}

function dependencyStateLabel(value: string) {
  const labels: Record<string, string> = {
    ANALYZED: "분석 완료",
    DECLARED: "사용 선언",
    EMBEDDED: "내장 항목",
    INTERNAL: "내부 항목",
    EXTERNAL: "외부 항목",
  };
  return labels[value] ?? "확인됨";
}

function knowledgeTypeLabel(value: string) {
  const labels: Record<string, string> = {
    ARCHITECTURE: "전체 구조",
    COMPONENT: "구성요소",
    COMPONENT_FLOW: "구성요소 흐름",
    LOGIC_FLOW: "처리 흐름",
    DATA_FLOW: "데이터 흐름",
    MESSAGE_FLOW: "메시지 흐름",
    CONFIGURATION: "설정",
    DEPENDENCY: "의존 관계",
    EMBEDDED_JAR: "내장 자바 파일",
    TROUBLESHOOTING: "문제 해결",
    ERROR_HANDLING: "오류 처리",
    RETRY_TIMEOUT: "재시도와 시간 제한",
    CHANGE_IMPACT: "변경 영향",
  };
  return labels[value] ?? "기술지원";
}

export function RepositoryAnalysisConsole({
  embedded = false,
}: {
  embedded?: boolean;
} = {}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<DetailTab>("overview");
  const [sourcePath, setSourcePath] = useState("");
  const [analysisJobId, setAnalysisJobId] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const sourceRoots = useQuery({
    queryKey: ["repository-analysis-source-roots"],
    queryFn: () => api<SourceRootList>("/api/v1/repository-analysis/source-roots"),
  });
  const analyze = useMutation({
    mutationFn: (path: string) =>
      api<AnalysisRequest>("/api/v1/repository-analysis/analyze", {
        method: "POST",
        body: JSON.stringify({ source_path: path }),
      }),
    onSuccess: (request) => setAnalysisJobId(request.job_id),
  });
  const analysisStatus = useQuery({
    queryKey: ["repository-analysis-request", analysisJobId],
    queryFn: () =>
      api<AnalysisRequestStatus>(
        `/api/v1/repository-analysis/analysis-requests/${analysisJobId}`,
      ),
    enabled: Boolean(analysisJobId),
    refetchInterval: (queryState) => {
      const status = queryState.state.data?.status;
      return status && ["succeeded", "failed", "dead_letter", "cancelled"].includes(status)
        ? false
        : 1500;
    },
  });
  const projects = useQuery({
    queryKey: ["repository-analysis-projects"],
    queryFn: () => api<ProjectList>("/api/v1/repository-analysis/projects"),
    refetchInterval: 15_000,
  });
  useEffect(() => {
    if (!selectedId && projects.data?.items[0]) setSelectedId(projects.data.items[0].id);
  }, [projects.data, selectedId]);
  useEffect(() => {
    if (analysisStatus.data?.status !== "succeeded") return;
    const projectId = analysisStatus.data.error_details?.project_id;
    if (typeof projectId === "string") setSelectedId(projectId);
    void queryClient.invalidateQueries({ queryKey: ["repository-analysis-projects"] });
    void queryClient.invalidateQueries({ queryKey: ["repository-analysis-project"] });
    void queryClient.invalidateQueries({ queryKey: ["repository-analysis-jobs"] });
  }, [analysisStatus.data, queryClient]);
  const detail = useQuery({
    queryKey: ["repository-analysis-project", selectedId],
    queryFn: () =>
      api<ProjectDetail>(`/api/v1/repository-analysis/projects/${selectedId}`),
    enabled: Boolean(selectedId),
  });
  const visualizationSection =
    tab === "architecture" || tab === "logic" || tab === "dependencies"
      ? tab
      : null;
  const visualization = useQuery({
    queryKey: ["repository-analysis-visualization", selectedId, visualizationSection],
    queryFn: () =>
      api<VisualizationDetail>(
        `/api/v1/repository-analysis/projects/${selectedId}/visualization?section=${visualizationSection}`,
      ),
    enabled: Boolean(selectedId && visualizationSection),
  });
  const knowledge = useQuery({
    queryKey: ["repository-analysis-knowledge", selectedId],
    queryFn: () =>
      api<KnowledgeList>(
        `/api/v1/repository-analysis/projects/${selectedId}/knowledge?limit=200`,
      ),
    enabled: Boolean(selectedId && (tab === "support" || tab === "unknowns")),
  });
  const configurations = useQuery({
    queryKey: ["repository-analysis-configurations", selectedId],
    queryFn: () =>
      api<ConfigurationList>(
        `/api/v1/repository-analysis/projects/${selectedId}/configurations?limit=100`,
      ),
    enabled: Boolean(selectedId && tab === "configuration"),
  });
  const evaluations = useQuery({
    queryKey: ["repository-analysis-evaluations", selectedId],
    queryFn: () =>
      api<EvaluationList>(
        `/api/v1/repository-analysis/projects/${selectedId}/evaluations`,
      ),
    enabled: Boolean(selectedId && tab === "evaluation"),
  });
  const snapshots = useQuery({
    queryKey: ["repository-analysis-snapshots", selectedId],
    queryFn: () =>
      api<SnapshotList>(
        `/api/v1/repository-analysis/projects/${selectedId}/snapshots`,
      ),
    enabled: Boolean(selectedId && tab === "snapshots"),
  });
  const jobs = useQuery({
    queryKey: ["repository-analysis-jobs", selectedId],
    queryFn: () =>
      api<JobList>(`/api/v1/repository-analysis/projects/${selectedId}/status`),
    enabled: Boolean(selectedId),
    refetchInterval: 10_000,
  });
  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    if (!needle) return projects.data?.items ?? [];
    return (projects.data?.items ?? []).filter((project) =>
      [
        project.display_name,
        project.category,
        ...(project.languages ?? []),
        ...(project.build_systems ?? []),
      ].some((value) => value.toLocaleLowerCase().includes(needle)),
    );
  }, [projects.data, query]);
  const selected = detail.data?.project;
  const counts = detail.data?.counts ?? {};
  const latestJob = jobs.data?.items[0];

  return (
    <div className={`repo-analysis-page${embedded ? " repo-analysis-page-embedded" : ""}`}>
      {!embedded && <header className="gpu-topbar">
        <a className="gpu-back" href="/">
          <ArrowLeft size={17} /> 로컬 지식 포털
        </a>
        <span className="health ok"><span /> 원본 읽기 전용</span>
      </header>}
      <div className="repo-analysis-main">
        <div className="page-title">
          <div>
            <h1>저장소 분석</h1>
          </div>
          <span className="repo-policy-chip"><CheckCircle2 size={15} /> 근거 확인 완료</span>
        </div>
        <form
          className="panel repo-analyze-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (sourcePath.trim()) analyze.mutate(sourcePath.trim());
          }}
        >
          <label htmlFor="repository-source-path">분석할 저장소 경로</label>
          <div>
            <input
              id="repository-source-path"
              list="repository-source-roots"
              value={sourcePath}
              onChange={(event) => setSourcePath(event.target.value)}
              placeholder="/home/kutae/src/저장소"
              autoComplete="off"
            />
            <datalist id="repository-source-roots">
              {sourceRoots.data?.items.map((root) => (
                <option key={root.id} value={root.canonical_path}>{root.name}</option>
              ))}
            </datalist>
            <button
              className="primary-button"
              type="submit"
              disabled={!sourcePath.trim() || analyze.isPending}
            >
              {analyze.isPending ? <LoaderCircle className="spin" size={15} /> : <Play size={15} />}
              분석하기
            </button>
          </div>
          {analysisStatus.data && (
            <p className={["failed", "dead_letter"].includes(analysisStatus.data.status) ? "danger" : ""}>
              {statusLabel(analysisStatus.data.status)}
              {analysisStatus.data.error_message ? ` · ${analysisStatus.data.error_message}` : ""}
            </p>
          )}
          {analyze.isError && <p className="danger">분석 요청을 등록하지 못했습니다.</p>}
        </form>

        {projects.isError && (
          <div className="error-state">
            <AlertTriangle size={17} /> 레포 분석 데이터를 불러오지 못했습니다.
          </div>
        )}
        {projects.isLoading && (
          <div className="empty"><LoaderCircle className="spin" /><strong>프로젝트 불러오는 중</strong></div>
        )}
        {projects.data && projects.data.total === 0 && (
          <div className="panel empty repo-empty">
            <Boxes size={28} />
            <strong>분석된 저장소가 없습니다</strong>
          </div>
        )}
        {projects.data && projects.data.total > 0 && (
          <div className="repo-analysis-layout">
            <section className="panel repo-projects" aria-label="분석 프로젝트">
              <div className="panel-head">
                <div><h2>분석 대상</h2></div>
                <span className="gpu-count">{projects.data.total}</span>
              </div>
              <label className="repo-project-search">
                <Search size={15} />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="저장소 찾기"
                  aria-label="분석 대상 검색"
                />
              </label>
              <div className="repo-project-list">
                {filtered.map((project) => (
                  <button
                    key={project.id}
                    className={selectedId === project.id ? "selected" : ""}
                    onClick={() => { setSelectedId(project.id); setTab("overview"); }}
                  >
                    <span className={`repo-state ${statusTone(project.analysis_status)}`}>
                      <CircleDot size={13} />
                    </span>
                    <span>
                      <strong>{displayName(project.display_name)}</strong>
                      <small>{languageList(project.languages)}</small>
                    </span>
                    <small>{statusLabel(project.analysis_status)}</small>
                  </button>
                ))}
              </div>
            </section>

            <section className="repo-detail">
              {detail.isLoading || !selected ? (
                <div className="panel empty"><LoaderCircle className="spin" /><strong>상세 정보 불러오는 중</strong></div>
              ) : (
                <>
                  <div className="panel repo-snapshot-head">
                    <div>
                      <h2>{displayName(selected.display_name)}</h2>
                      <p>{new Date(selected.analyzed_at ?? Date.now()).toLocaleString("ko-KR")} 분석</p>
                    </div>
                    <div className="repo-snapshot-meta">
                      <span className={`chip ${statusTone(selected.analysis_status)}`}>
                        {statusLabel(selected.analysis_status)}
                      </span>
                      {selected.dirty_worktree && <span className="chip warning">변경 내용 포함</span>}
                    </div>
                  </div>
                  <div className="repo-stat-grid">
                    <Metric icon={FileCode2} label="분석 파일" value={selected.file_count ?? 0} />
                    <Metric icon={Boxes} label="구조 항목" value={counts.symbols ?? 0} />
                    <Metric icon={Network} label="연결 관계" value={counts.relations ?? 0} />
                    <Metric icon={CheckCircle2} label="분석 지식" value={counts.knowledge_items ?? 0} />
                  </div>
                  <div className="tabs repo-tabs" role="tablist" aria-label="프로젝트 분석 상세">
                    {tabs.map((item) => (
                      <button
                        key={item.id}
                        role="tab"
                        aria-selected={tab === item.id}
                        className={tab === item.id ? "active" : ""}
                        onClick={() => setTab(item.id)}
                      >{item.label}</button>
                    ))}
                  </div>
                  <RepoTab
                    tab={tab}
                    project={selected}
                    counts={counts}
                    report={detail.data?.report ?? null}
                    latestJob={latestJob}
                    jobs={jobs.data?.items ?? []}
                    visualization={{
                      ...(detail.data?.visualization ?? {
                        relation_groups: [],
                        dependency_groups: [],
                        flow_items: [],
                        dependency_items: [],
                        components: [],
                        lifecycle: { nodes: [], edges: [] },
                        dependency_usages: [],
                      }),
                      components: visualization.data?.components ?? [],
                      lifecycle: visualization.data?.lifecycle ?? { nodes: [], edges: [] },
                      dependency_items: visualization.data?.dependencies ?? [],
                      dependency_usages: visualization.data?.usages ?? [],
                    }}
                    visualizationLoading={visualization.isLoading}
                    knowledge={knowledge.data}
                    knowledgeLoading={knowledge.isLoading}
                    configurations={configurations.data}
                    configurationsLoading={configurations.isLoading}
                    evaluations={evaluations.data}
                    evaluationsLoading={evaluations.isLoading}
                    snapshots={snapshots.data}
                    snapshotsLoading={snapshots.isLoading}
                  />
                </>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  );
}

function Metric({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ComponentType<{ size?: number }>;
  label: string;
  value: number;
}) {
  return (
    <div className="panel repo-stat">
      <span><Icon size={15} /> {label}</span>
      <strong>{value.toLocaleString()}</strong>
    </div>
  );
}

function RepoTab({
  tab,
  project,
  counts,
  report,
  latestJob,
  jobs,
  visualization,
  visualizationLoading,
  knowledge,
  knowledgeLoading,
  configurations,
  configurationsLoading,
  evaluations,
  evaluationsLoading,
  snapshots,
  snapshotsLoading,
}: {
  tab: DetailTab;
  project: ProjectDetail["project"];
  counts: Record<string, number>;
  report: KnowledgeItem | null;
  latestJob: AnalysisJob | undefined;
  jobs: AnalysisJob[];
  visualization: ProjectDetail["visualization"];
  visualizationLoading: boolean;
  knowledge: KnowledgeList | undefined;
  knowledgeLoading: boolean;
  configurations: ConfigurationList | undefined;
  configurationsLoading: boolean;
  evaluations: EvaluationList | undefined;
  evaluationsLoading: boolean;
  snapshots: SnapshotList | undefined;
  snapshotsLoading: boolean;
}) {
  if (tab === "jobs") {
    return (
      <div className="panel repo-tab-panel">
        <div className="panel-head"><h2>작업 상태</h2><span>{jobs.length}건</span></div>
        <div className="repo-job-list">
          {jobs.map((job) => (
            <div key={job.id}>
              <span className={`repo-state ${statusTone(job.status)}`}><CircleDot size={13} /></span>
              <span><strong>저장소 분석</strong><small>{new Date(job.started_at).toLocaleString("ko-KR")}</small></span>
              <small>{statusLabel(job.status)}</small>
            </div>
          ))}
        </div>
      </div>
    );
  }
  if (tab === "logic") {
    if (visualizationLoading) return <TabLoading />;
    return <LifecycleGraph lifecycle={visualization.lifecycle} />;
  }
  if (tab === "dependencies") {
    if (visualizationLoading) return <TabLoading />;
    return (
      <DependencyVenn
        components={visualization.components}
        dependencies={visualization.dependency_items}
        usages={visualization.dependency_usages}
      />
    );
  }
  if (tab === "architecture") {
    if (visualizationLoading) return <TabLoading />;
    return <ComponentStructure components={visualization.components} />;
  }
  if (tab === "configuration") {
    if (configurationsLoading) return <TabLoading />;
    return (
      <div className="panel repo-tab-panel">
        <div className="panel-head">
          <h2>설정</h2>
          <span>{(configurations?.total ?? 0).toLocaleString()}개</span>
        </div>
        <div className="repo-content-list">
          {configurations?.items.map((item) => (
            <article key={`${item.relative_path}:${item.declaration_line}:${item.config_key}`}>
              <div>
                <h3>{item.config_key}</h3>
                <span>{item.has_default ? "기본값 있음" : "기본값 없음"}</span>
              </div>
              <p>{item.relative_path} · {item.declaration_line.toLocaleString()}번째 줄</p>
              <small>
                {item.referenced_by.length
                  ? `사용 위치 ${item.referenced_by.slice(0, 4).join(", ")}`
                  : "사용 위치가 추가로 확인되지 않음"}
              </small>
            </article>
          ))}
          {!configurations?.items.length && <p className="repo-no-data">확인된 설정이 없습니다.</p>}
        </div>
      </div>
    );
  }
  if (tab === "support") {
    if (knowledgeLoading) return <TabLoading />;
    return (
      <div className="panel repo-tab-panel">
        <div className="panel-head">
          <h2>기술지원 지식</h2>
          <span>{(knowledge?.total ?? 0).toLocaleString()}개</span>
        </div>
        <div className="repo-knowledge-list">
          {knowledge?.items.map((item) => (
            <article key={item.id}>
              <div className="repo-knowledge-head">
                <span>{knowledgeTypeLabel(item.knowledge_type)}</span>
                <small>{item.validation_status === "SOURCE_VERIFIED" ? "원본 확인" : "일부 확인"}</small>
              </div>
              <h3>{item.title}</h3>
              <p>{item.summary}</p>
              {item.processing_steps.length > 1 && (
                <ol>{item.processing_steps.map((step) => <li key={step}>{step}</li>)}</ol>
              )}
              {item.source_references.length > 0 && (
                <small>
                  근거 · {item.source_references.slice(0, 3).map((reference) =>
                    `${reference.file}:${reference.start_line}`,
                  ).join(", ")}
                </small>
              )}
            </article>
          ))}
          {!knowledge?.items.length && (
            <p className="repo-no-data">
              저장된 기술지원 지식이 없습니다. 완료된 분석 결과의 저장 상태를 확인해 주세요.
            </p>
          )}
        </div>
      </div>
    );
  }
  if (tab === "evaluation") {
    if (evaluationsLoading) return <TabLoading />;
    return (
      <div className="panel repo-tab-panel">
        <div className="panel-head">
          <h2>검증 결과</h2>
          <span>{(evaluations?.total ?? 0).toLocaleString()}건</span>
        </div>
        <div className="repo-content-list">
          {evaluations?.items.map((item) => (
            <article key={`${item.id}:${item.scenario_type ?? ""}`}>
              <div>
                <h3>{item.question}</h3>
                <span className={item.passed ? "success" : "danger"}>
                  {item.passed ? "통과" : "미통과"} · {Math.round(item.score * 100)}점
                </span>
              </div>
              <p>{item.scenario_type ?? item.question_type}</p>
              {item.failure_category && <small>확인 필요 · {item.failure_category}</small>}
            </article>
          ))}
          {!evaluations?.items.length && <p className="repo-no-data">검증 결과가 없습니다.</p>}
        </div>
      </div>
    );
  }
  if (tab === "unknowns") {
    if (knowledgeLoading) return <TabLoading />;
    const unknowns = [
      ...(latestJob?.warnings ?? []),
      ...(knowledge?.items.flatMap((item) => item.unknowns) ?? []),
    ];
    return (
      <div className="panel repo-tab-panel">
        <div className="panel-head"><h2>확인 필요</h2><span>{unknowns.length.toLocaleString()}건</span></div>
        <ul className="repo-warning-list">
          {unknowns.map((item, index) => <li key={`${index}:${item}`}><AlertTriangle size={14} />{item}</li>)}
          {unknowns.length === 0 && <li><CheckCircle2 size={14} />추가로 확인할 항목이 없습니다.</li>}
        </ul>
      </div>
    );
  }
  if (tab === "snapshots") {
    if (snapshotsLoading) return <TabLoading />;
    return (
      <div className="panel repo-tab-panel">
        <div className="panel-head"><h2>분석 이력</h2><span>{snapshots?.total ?? 0}건</span></div>
        <div className="repo-content-list">
          {snapshots?.items.map((item) => (
            <article key={item.id}>
              <div><h3>{item.snapshot_name}</h3><span>{item.stale ? "이전 결과" : "현재 결과"}</span></div>
              <p>{new Date(item.created_at).toLocaleString("ko-KR")} · 파일 {item.file_count.toLocaleString()}개</p>
            </article>
          ))}
        </div>
      </div>
    );
  }
  const facts = [
    `상태 · ${statusLabel(project.analysis_status)}`,
    `사용 언어 · ${languageList(project.languages)}`,
    `빌드 방식 · ${(project.build_systems ?? []).join(", ") || "감지되지 않음"}`,
    `분석 파일 · ${(project.file_count ?? 0).toLocaleString()}개`,
    `원본 확인 지식 · ${(counts.knowledge_source_verified ?? 0).toLocaleString()}개`,
    `부분 확인 지식 · ${(counts.knowledge_partially_verified ?? 0).toLocaleString()}개`,
    `추가 자료 필요 · ${(counts.knowledge_additional_data_needed ?? 0).toLocaleString()}개`,
  ];
  const reportSections = report?.detail
    .split(/^## /m)
    .map((section) => section.trim())
    .filter(Boolean)
    .map((section) => {
      const [title, ...lines] = section.split("\n");
      return {
        title,
        lines: lines.map((line) => line.replace(/^- /, "").trim()).filter(Boolean),
      };
    }) ?? [];
  return (
    <div className="panel repo-tab-panel">
      <div className="panel-head">
        <h2>{report?.title ?? "분석 요약"}</h2>
        {report && (
          <span>
            {report.validation_status === "SOURCE_VERIFIED" ? "원본 확인" : "근거 기반 종합"}
          </span>
        )}
      </div>
      {report ? (
        <div className="repo-report">
          <section className="repo-report-purpose">
            <span>무엇을 하는 저장소인가</span>
            <p>{report.summary}</p>
          </section>
          {reportSections.length > 0 && (
            <div className="repo-report-sections">
              {reportSections.map((section) => (
                <section key={section.title}>
                  <h3>{section.title}</h3>
                  <ul>
                    {section.lines.map((line) => <li key={line}>{line}</li>)}
                  </ul>
                </section>
              ))}
            </div>
          )}
          {report.processing_steps.length > 0 && (
            <section className="repo-report-flow">
              <h3>처리 라이프사이클</h3>
              <ol>
                {report.processing_steps.map((step) => <li key={step}>{step}</li>)}
              </ol>
            </section>
          )}
          {report.source_references.length > 0 && (
            <small className="repo-report-evidence">
              근거 · {report.source_references.slice(0, 6).map((reference) =>
                `${reference.file}:${reference.start_line}`,
              ).join(", ")}
            </small>
          )}
        </div>
      ) : (
        <p className="repo-report-missing">
          이 Snapshot에는 저장소 전체 보고서가 없습니다. 최신 분석을 다시 실행하면
          목적·기술·처리 흐름을 근거와 함께 생성합니다.
        </p>
      )}
      <ul className="repo-fact-list">
        {facts.map((fact) => <li key={fact}><CheckCircle2 size={14} /> {fact}</li>)}
      </ul>
    </div>
  );
}

function TabLoading() {
  return (
    <div className="panel empty repo-tab-panel">
      <LoaderCircle className="spin" />
      <strong>내용 불러오는 중</strong>
    </div>
  );
}

function ComponentStructure({
  components,
}: {
  components: ProjectDetail["visualization"]["components"];
}) {
  return (
    <div className="panel repo-tab-panel">
      <div className="panel-head">
        <h2>구조</h2>
        <span>{components.length.toLocaleString()}개 구성요소</span>
      </div>
      <div className="repo-component-grid">
        {components.map((component) => (
          <article key={component.key}>
            <div>
              <span>{component.component_type}</span>
              <small>원본 확인</small>
            </div>
            <h3>{component.display_name}</h3>
            <p>{component.responsibility}</p>
            <dl>
              <div><dt>포함 파일</dt><dd>{component.file_count.toLocaleString()}개</dd></div>
              <div><dt>시작 지점</dt><dd>{component.entry_point_count.toLocaleString()}개</dd></div>
            </dl>
          </article>
        ))}
        {components.length === 0 && <p className="repo-no-data">구조 분석 결과가 없습니다.</p>}
      </div>
    </div>
  );
}

function LifecycleGraph({
  lifecycle,
}: {
  lifecycle: ProjectDetail["visualization"]["lifecycle"];
}) {
  const graphic = useRef<HTMLDivElement>(null);
  const animated = useRef(false);
  const nodes = lifecycle.nodes.slice(0, 8);
  const nodeKeys = new Set(nodes.map((node) => node.key));
  const edges = lifecycle.edges.filter(
    (edge) => nodeKeys.has(edge.source_key) && nodeKeys.has(edge.target_key),
  );
  const positions = new Map(
    nodes.map((node, index) => [
      node.key,
      { x: 95 + index * (700 / Math.max(nodes.length - 1, 1)), y: 120 },
    ]),
  );
  useGSAP(() => {
    if (animated.current) return;
    animated.current = true;
    const timeline = gsap.timeline();
    timeline
      .from(".repo-lifecycle-node", {
        opacity: 0,
        scale: 0.88,
        transformOrigin: "center",
        stagger: 0.08,
        duration: 0.42,
        ease: "back.out(1.4)",
      })
      .from(".repo-lifecycle-edge", {
        strokeDashoffset: 1,
        duration: 0.55,
        stagger: 0.05,
        ease: "power2.out",
      }, "-=.25")
      .from(".repo-lifecycle-detail article", {
        opacity: 0,
        x: -8,
        stagger: 0.05,
        duration: 0.25,
      }, "-=.35");
  }, { scope: graphic });

  return (
    <div ref={graphic} className="panel repo-tab-panel repo-graphic-panel">
      <div className="panel-head">
        <h2>처리 생명주기</h2>
        <span>{nodes.length.toLocaleString()}단계</span>
      </div>
      {nodes.length === 0 ? (
        <p className="repo-no-data">처리 흐름 분석 결과가 없습니다.</p>
      ) : (
        <div className="repo-lifecycle-layout">
        <svg
          className="repo-lifecycle"
          viewBox="0 0 890 240"
          role="img"
          aria-label="저장소가 수행하는 처리 생명주기"
        >
          <defs>
            <marker id="repo-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
              <path d="M0,0 L8,4 L0,8 z" />
            </marker>
          </defs>
          {edges.map((edge, index) => {
            const source = positions.get(edge.source_key);
            const target = positions.get(edge.target_key);
            if (!source || !target) return null;
            return (
              <path
                key={`${edge.source_key}-${edge.target_key}-${index}`}
                className={`repo-lifecycle-edge ${edge.provenance === "STATIC_CONFIRMED" ? "confirmed" : "inferred"}`}
                d={`M ${source.x + 53} ${source.y} C ${source.x + 76} ${source.y - 42}, ${target.x - 76} ${target.y - 42}, ${target.x - 53} ${target.y}`}
                pathLength="1"
                markerEnd="url(#repo-arrow)"
              />
            );
          })}
          {nodes.map((node, index) => {
            const position = positions.get(node.key)!;
            return (
              <g className="repo-lifecycle-node" key={node.key}>
                <title>{`${node.title}: ${node.description}`}</title>
                <rect x={position.x - 53} y={position.y - 25} width="106" height="50" rx="12" />
                <circle className="index-dot" cx={position.x} cy={position.y - 34} r="14" />
                <text className="index" x={position.x} y={position.y - 30} textAnchor="middle">
                  {String(index + 1).padStart(2, "0")}
                </text>
                <text className="label" x={position.x} y={position.y + 5} textAnchor="middle">
                  {node.title.length > 10 ? `${node.title.slice(0, 10)}…` : node.title}
                </text>
              </g>
            );
          })}
        </svg>
        <div className="repo-flow-key">
          <span><i className="confirmed" /> 코드에서 확인된 연결</span>
          <span><i className="inferred" /> 구조상 이어지는 단계</span>
        </div>
        <div className="repo-lifecycle-detail">
          {nodes.map((node, index) => (
            <article key={node.key}>
              <span>{index + 1}</span>
              <div><h3>{node.title}</h3><p>{node.description}</p></div>
            </article>
          ))}
        </div>
      </div>
      )}
    </div>
  );
}

function DependencyVenn({
  components,
  dependencies,
  usages,
}: {
  components: ProjectDetail["visualization"]["components"];
  dependencies: ProjectDetail["visualization"]["dependency_items"];
  usages: ProjectDetail["visualization"]["dependency_usages"];
}) {
  const graphic = useRef<HTMLDivElement>(null);
  const animated = useRef(false);
  const nameByKey = new Map(components.map((item) => [item.key, item.display_name]));
  const componentCounts = new Map<string, number>();
  usages.forEach((item) => {
    componentCounts.set(
      item.component_key,
      (componentCounts.get(item.component_key) ?? 0) + item.occurrence_count,
    );
  });
  const selectedKeys = [...componentCounts.entries()]
    .sort((left, right) => right[1] - left[1])
    .slice(0, 3)
    .map(([key]) => key);
  const sets = selectedKeys.map((key) => new Set(
    usages.filter((item) => item.component_key === key).map((item) => item.dependency_name),
  ));
  const shared = (left: number, right: number) =>
    [...(sets[left] ?? new Set<string>())].filter((name) => sets[right]?.has(name));
  const packages = dependencies.map((item) => ({
    ...item,
    componentNames: [...new Set(
      usages
        .filter((usage) => usage.dependency_name === item.name)
        .map((usage) => nameByKey.get(usage.component_key) ?? displayName(usage.component_key)),
    )],
  }));
  useGSAP(() => {
    if (animated.current) return;
    animated.current = true;
    gsap.timeline()
      .from(".repo-venn-circle", {
        opacity: 0,
        scale: 0.7,
        transformOrigin: "center",
        stagger: 0.12,
        duration: 0.5,
        ease: "back.out(1.3)",
      })
      .from(".repo-dependency-list article", {
        opacity: 0,
        y: 8,
        stagger: 0.025,
        duration: 0.24,
      }, "-=.25");
  }, { scope: graphic });

  const circles = [
    { x: 340, y: 130, tone: "one" },
    { x: 500, y: 130, tone: "two" },
    { x: 420, y: 250, tone: "three" },
  ];
  return (
    <div ref={graphic} className="panel repo-tab-panel repo-graphic-panel">
      <div className="panel-head">
        <h2>의존 관계</h2>
        <span>{dependencies.length.toLocaleString()}개 패키지</span>
      </div>
      {selectedKeys.length === 0 ? (
        <p className="repo-no-data">의존 관계 분석 결과가 없습니다.</p>
      ) : (
        <>
          <div className="repo-venn-wrap">
            <svg className="repo-venn" viewBox="0 0 840 390" role="img" aria-label="구성요소별 의존 패키지 공유 관계">
              {selectedKeys.map((key, index) => {
                const circle = circles[index];
                return (
                  <g className={`repo-venn-circle ${circle.tone}`} key={key}>
                    <circle cx={circle.x} cy={circle.y} r="118" />
                    <text x={circle.x} y={circle.y - 58} textAnchor="middle">
                      {nameByKey.get(key) ?? displayName(key)}
                    </text>
                    <text className="count" x={circle.x} y={circle.y - 33} textAnchor="middle">
                      {(sets[index]?.size ?? 0).toLocaleString()}개
                    </text>
                  </g>
                );
              })}
              {selectedKeys.length > 1 && (
                <text className="repo-venn-shared" x="420" y="112" textAnchor="middle">
                  함께 사용 {shared(0, 1).length}개
                </text>
              )}
              {selectedKeys.length > 2 && (
                <>
                  <text className="repo-venn-shared" x="348" y="222" textAnchor="middle">
                    공유 {shared(0, 2).length}
                  </text>
                  <text className="repo-venn-shared" x="492" y="222" textAnchor="middle">
                    공유 {shared(1, 2).length}
                  </text>
                </>
              )}
            </svg>
          </div>
          <div className="repo-dependency-list">
            {packages.map((item) => (
              <article key={`${item.relative_path}-${item.name}-${item.scope ?? ""}`}>
                <div>
                  <h3>{item.name}{item.version ? ` ${item.version}` : ""}</h3>
                  <span>{dependencyLabel(item.artifact_type)} · {dependencyStateLabel(item.analysis_status)}</span>
                </div>
                <p>
                  {item.componentNames.length
                    ? `${item.componentNames.join(", ")}에서 사용`
                    : "선언 위치만 확인됨"}
                </p>
              </article>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
