"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock3,
  Cpu,
  Gauge,
  GripVertical,
  HardDrive,
  Languages,
  Moon,
  OctagonX,
  RefreshCcw,
  Sun,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { Fragment, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type Locale = "ko" | "en";

type GpuJob = {
  id: string;
  agent_name: string;
  workload_key: string;
  argv: string[];
  cwd: string | null;
  requested_vram_mb: number;
  estimated_seconds: number;
  priority: number;
  max_runtime_seconds: number | null;
  status: string;
  submitted_at: string;
  started_at: string | null;
  finished_at: string | null;
  pid: number | null;
  exit_code: number | null;
  log_path: string | null;
  error: string | null;
  cancel_requested: boolean;
  peak_total_gpu_used_mb: number | null;
  scheduling_note: string | null;
  effective_score?: number | null;
  manual_rank?: number | null;
};

type ExternalGpuWorkload = {
  key: string;
  label: string;
  kind: "ollama_container" | "ollama_host";
  container?: string;
  target?: string;
  state: "active" | "idle" | "stopped" | "error";
  models: {
    name: string;
    model_id: string;
    size: string;
    processor: string;
    context?: string;
    until: string;
  }[];
  can_stop: boolean;
  error: string | null;
};

type GpuQueueStatus = {
  runtime: {
    gpu: {
      name: string;
      total_mb: number;
      used_mb: number;
      free_mb: number;
      utilization_percent: number;
    };
    baseline_used_mb: number;
    safety_vram_mb: number;
    fairness_window_minutes: number;
    max_parallel_jobs: number;
    managed_running: number;
    last_decision: string | null;
    last_error: string | null;
    unmanaged_gpu_processes?: {
      pid: number;
      process_name: string;
      used_mb: number | null;
    }[];
    unmanaged_gpu_process_count?: number;
    gpu_process_scan_error?: string | null;
    external_workloads?: ExternalGpuWorkload[];
    comfyui_bridge?: {
      state: "ready" | "unavailable" | "invalid_response";
      process_id: number | null;
      reservation_mode: "server_managed" | "prompt_reservation" | null;
      requested_vram_mb: number | null;
    };
  };
  jobs: {
    queued: GpuJob[];
    active: GpuJob[];
    completed: GpuJob[];
  };
};

const copy = {
  ko: {
    back: "지식 포털",
    eyebrow: "호스트 GPU 예약 실행기",
    title: "GPU 작업 큐",
    subtitle: "큰 GPU 작업을 직접 실행하지 않고 VRAM 여유와 우선순위에 따라 안전하게 예약합니다.",
    healthy: "스케줄러 정상",
    offline: "스케줄러 연결 안 됨",
    refresh: "새로고침",
    gpuTotal: "전체 VRAM",
    gpuUsed: "사용 중",
    gpuFree: "사용 가능",
    utilization: "GPU 사용률",
    managed: "관리 중 실행",
    safety: "안전 여유",
    baseline: "기준 사용량",
    parallel: "최대 병렬",
    decision: "최근 스케줄 판단",
    error: "최근 오류",
    queued: "대기",
    active: "실행 중",
    completed: "최근 완료",
    empty: "해당 상태의 작업이 없습니다.",
    workload: "작업",
    agent: "요청자",
    vram: "예약 VRAM",
    priority: "우선순위",
    score: "유효 점수",
    status: "상태",
    note: "스케줄 사유",
    submitted: "제출 시각",
    elapsed: "예상 시간",
    result: "결과",
    details: "작업 상세",
    command: "명령",
    cwd: "작업 경로",
    pid: "PID",
    peak: "최대 GPU 사용",
    log: "로그",
    select: "행을 선택하면 개별 작업 API의 상세를 확인할 수 있습니다.",
    unavailable: "호스트 GPU 큐를 불러오지 못했습니다. Windows 스케줄러 서비스와 포털 API 연결을 확인하세요.",
  },
  en: {
    back: "Knowledge portal",
    eyebrow: "Host GPU reservation scheduler",
    title: "GPU work queue",
    subtitle: "Large GPU workloads are reserved according to VRAM headroom and priority instead of running directly.",
    healthy: "Scheduler healthy",
    offline: "Scheduler offline",
    refresh: "Refresh",
    gpuTotal: "Total VRAM",
    gpuUsed: "Used",
    gpuFree: "Free",
    utilization: "GPU utilization",
    managed: "Managed running",
    safety: "Safety reserve",
    baseline: "Baseline usage",
    parallel: "Max parallel",
    decision: "Latest scheduling decision",
    error: "Latest error",
    queued: "Queued",
    active: "Active",
    completed: "Recent completed",
    empty: "No jobs in this state.",
    workload: "Workload",
    agent: "Agent",
    vram: "Reserved VRAM",
    priority: "Priority",
    score: "Effective score",
    status: "Status",
    note: "Scheduling note",
    submitted: "Submitted",
    elapsed: "Estimate",
    result: "Result",
    details: "Job details",
    command: "Command",
    cwd: "Working directory",
    pid: "PID",
    peak: "Peak GPU used",
    log: "Log",
    select: "Select a row to load details from the individual job API.",
    unavailable: "The host GPU queue is unavailable. Check the Windows scheduler service and portal API connection.",
  },
} as const;

function usePreferences() {
  const [locale, setLocaleState] = useState<Locale>("ko");
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  useEffect(() => {
    const savedLocale = localStorage.getItem("lkp-locale");
    const nextLocale: Locale = savedLocale === "en" ? "en" : "ko";
    const nextTheme = localStorage.getItem("lkp-theme") === "light" ? "light" : "dark";
    setLocaleState(nextLocale);
    setTheme(nextTheme);
    document.documentElement.lang = nextLocale;
    document.documentElement.dataset.theme = nextTheme;
  }, []);
  const setLocale = (next: Locale) => {
    setLocaleState(next);
    localStorage.setItem("lkp-locale", next);
    document.documentElement.lang = next;
  };
  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("lkp-theme", next);
    document.documentElement.dataset.theme = next;
  };
  return { locale, setLocale, theme, toggleTheme };
}

function formatMemory(value: number | null | undefined) {
  if (value == null) return "—";
  return `${(value / 1024).toFixed(value >= 10_240 ? 1 : 2)} GiB`;
}

function formatTime(value: string | null, locale: Locale) {
  if (!value) return "—";
  return new Date(value).toLocaleString(locale === "ko" ? "ko-KR" : "en-US");
}

function formatDuration(seconds: number, locale: Locale) {
  if (seconds < 60) return locale === "ko" ? `${seconds}초` : `${seconds}s`;
  const minutes = Math.round(seconds / 60);
  return locale === "ko" ? `${minutes}분` : `${minutes}m`;
}

function workloadLabel(value: string, locale: Locale) {
  const known: Array<[RegExp, [string, string]]> = [
    [/curation|knowledge-curat/i, ["지식 사례 편집", "Knowledge case editing"]],
    [/dedup/i, ["지식 중복 검사", "Knowledge deduplication"]],
    [/semantic.*recover|reindex|embedding/i, ["의미 검색 색인 복구", "Semantic index recovery"]],
    [/comfy/i, ["ComfyUI 이미지 생성", "ComfyUI image generation"]],
    [/ollama/i, ["로컬 모델 실행", "Local model runtime"]],
  ];
  const match = known.find(([pattern]) => pattern.test(value));
  if (!match) return locale === "ko" ? "GPU 예약 작업" : "GPU workload";
  return match[1][locale === "ko" ? 0 : 1];
}

function jobStatusLabel(value: string, locale: Locale) {
  const labels: Record<string, [string, string]> = {
    queued: ["대기", "Queued"], running: ["실행 중", "Running"],
    completed: ["완료", "Completed"], succeeded: ["성공", "Succeeded"],
    failed: ["실패", "Failed"], canceled: ["중지됨", "Canceled"],
    cancelled: ["중지됨", "Cancelled"],
    timed_out: ["시간 초과", "Timed out"],
  };
  return labels[value]?.[locale === "ko" ? 0 : 1] ??
    (locale === "ko" ? "상태 확인 필요" : "Status needs review");
}

function schedulerNoteLabel(value: string | null, locale: Locale) {
  if (!value) return "—";
  const labels: Record<string, [string, string]> = {
    "queue-empty": ["현재 대기 작업 없음", "No work is currently queued"],
    "head-fits": ["안전 여유 내 실행 가능", "Fits within the safety reserve"],
    "head-blocked": ["GPU 여유 공간 대기", "Waiting for GPU headroom"],
    "max-parallel": ["동시 실행 한도 대기", "Waiting for a parallel slot"],
  };
  return labels[value]?.[locale === "ko" ? 0 : 1] ??
    (locale === "ko" ? "스케줄러가 실행 순서를 계산했습니다" : "Scheduler evaluated the run order");
}

function JobTable({
  title,
  jobs,
  locale,
  selected,
  onSelect,
  onMoveQueued,
  isReordering = false,
  detailJob,
  detailLoading = false,
  stopPending = false,
  onRequestStop,
}: {
  title: string;
  jobs: GpuJob[];
  locale: Locale;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onMoveQueued?: (sourceId: string, targetId: string) => void;
  isReordering?: boolean;
  detailJob?: GpuJob | null;
  detailLoading?: boolean;
  stopPending?: boolean;
  onRequestStop?: (job: GpuJob) => void;
}) {
  const text = copy[locale];
  const [page, setPage] = useState(1);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  const pageSize = 25;
  const pages = Math.max(1, Math.ceil(jobs.length / pageSize));
  const visibleJobs = jobs.slice((page - 1) * pageSize, page * pageSize);
  const editableQueue = Boolean(onMoveQueued);
  const hasStopControl = Boolean(onRequestStop);
  useEffect(() => {
    if (page > pages) setPage(pages);
  }, [page, pages]);
  return (
    <article className="panel gpu-job-panel">
      <div className="panel-head">
        <h2>{title}</h2>
        <span className="gpu-count">{jobs.length}</span>
      </div>
      {editableQueue && (
        <p className="gpu-queue-help">
          {locale === "ko"
            ? "행을 끌어 놓거나 화살표로 대기 순서를 바꿉니다. 안전 여유 VRAM·병렬 제한·안전한 짧은 작업 우선 실행은 유지됩니다."
            : "Drag rows or use arrows to set the queue order. VRAM safety, parallel limits, and safe short-job backfill still apply."}
        </p>
      )}
      {!jobs.length ? <div className="gpu-empty">{text.empty}</div> : (
        <div className="table-wrap">
          <table>
            <thead><tr>
              {editableQueue && <th>{locale === "ko" ? "순서" : "Order"}</th>}
              <th>{text.workload}</th><th>{text.agent}</th><th>{text.vram}</th>
              <th>{text.priority}</th><th>{text.score}</th><th>{text.status}</th>
              <th>{text.note}</th>
              {hasStopControl && <th>{locale === "ko" ? "제어" : "Control"}</th>}
            </tr></thead>
            <tbody>
              {visibleJobs.map((job, visibleIndex) => {
                const jobIndex = (page - 1) * pageSize + visibleIndex;
                const canMoveUp = jobIndex > 0;
                const canMoveDown = jobIndex < jobs.length - 1;
                const expanded = selected === job.id;
                const resolvedDetail = expanded && detailJob?.id === job.id ? detailJob : job;
                const ollamaJob = /ollama|curation|embedding/i.test(
                  `${resolvedDetail.workload_key} ${resolvedDetail.argv.join(" ")}`,
                );
                return (
                <Fragment key={job.id}>
                <tr
                  className={`${selected === job.id ? "selected-row" : ""} ${draggingId === job.id ? "dragging-row" : ""}`}
                  onClick={() => onSelect(expanded ? null : job.id)}
                  aria-expanded={expanded}
                  draggable={editableQueue && !isReordering}
                  onDragStart={(event) => {
                    if (!editableQueue) return;
                    event.dataTransfer.effectAllowed = "move";
                    event.dataTransfer.setData("text/plain", job.id);
                    setDraggingId(job.id);
                  }}
                  onDragOver={(event) => {
                    if (editableQueue) event.preventDefault();
                  }}
                  onDrop={(event) => {
                    if (!editableQueue) return;
                    event.preventDefault();
                    const sourceId = event.dataTransfer.getData("text/plain") || draggingId;
                    if (sourceId && sourceId !== job.id) onMoveQueued?.(sourceId, job.id);
                    setDraggingId(null);
                  }}
                  onDragEnd={() => setDraggingId(null)}
                >
                  {editableQueue && (
                    <td className="gpu-order-cell">
                      <GripVertical size={15} aria-hidden="true" />
                      <span>{job.manual_rank ?? jobIndex + 1}</span>
                      <button
                        aria-label={locale === "ko" ? `${workloadLabel(job.workload_key, locale)} 위로 이동` : `Move ${workloadLabel(job.workload_key, locale)} up`}
                        disabled={!canMoveUp || isReordering}
                        onClick={(event) => {
                          event.stopPropagation();
                          if (canMoveUp) onMoveQueued?.(job.id, jobs[jobIndex - 1].id);
                        }}
                      ><ArrowUp size={13} /></button>
                      <button
                        aria-label={locale === "ko" ? `${workloadLabel(job.workload_key, locale)} 아래로 이동` : `Move ${workloadLabel(job.workload_key, locale)} down`}
                        disabled={!canMoveDown || isReordering}
                        onClick={(event) => {
                          event.stopPropagation();
                          if (canMoveDown) onMoveQueued?.(job.id, jobs[jobIndex + 1].id);
                        }}
                      ><ArrowDown size={13} /></button>
                    </td>
                  )}
                  <td>
                    <button
                      className="gpu-row-toggle"
                      aria-expanded={expanded}
                      aria-label={locale === "ko"
                        ? `${workloadLabel(job.workload_key, locale)} 상세 ${expanded ? "닫기" : "열기"}`
                        : `${expanded ? "Collapse" : "Expand"} ${workloadLabel(job.workload_key, locale)} details`}
                      onClick={(event) => {
                        event.stopPropagation();
                        onSelect(expanded ? null : job.id);
                      }}
                    >
                      {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                      <span><strong>{workloadLabel(job.workload_key, locale)}</strong>
                        <small>{job.id.slice(0, 8)}</small></span>
                    </button>
                  </td>
                  <td>{job.agent_name}</td>
                  <td className="mono">{formatMemory(job.requested_vram_mb)}</td>
                  <td>{job.priority}</td>
                  <td>{job.effective_score == null ? "—" : job.effective_score.toFixed(2)}</td>
                  <td><span className={`status ${job.status === "running" || job.status === "completed" || job.status === "succeeded" ? "good" : "waiting"}`}>{jobStatusLabel(job.status, locale)}</span></td>
                  <td className="gpu-note" title={job.scheduling_note ?? undefined}>
                    {schedulerNoteLabel(job.scheduling_note, locale)}
                  </td>
                  {hasStopControl && (
                    <td className="gpu-row-control">
                      <button
                        className="secondary gpu-stop"
                        disabled={stopPending || job.cancel_requested || job.status !== "running"}
                        onClick={(event) => {
                          event.stopPropagation();
                          onRequestStop?.(job);
                        }}
                      >
                        <OctagonX size={14} />
                        {job.cancel_requested
                          ? (locale === "ko" ? "중지 요청됨" : "Stop requested")
                          : (locale === "ko" ? "안전 중지" : "Safe stop")}
                      </button>
                    </td>
                  )}
                </tr>
                {expanded && (
                  <tr className="gpu-inline-detail-row">
                    <td colSpan={(editableQueue ? 8 : 7) + (hasStopControl ? 1 : 0)}>
                      <section className="gpu-inline-detail" aria-label={text.details}>
                        <div className="panel-head">
                          <h3>{text.details}</h3>
                          <div className="gpu-detail-actions">
                            {detailLoading && <RefreshCcw size={15} className="spin" />}
                            {resolvedDetail.status === "running" && onRequestStop && (
                              <button
                                className="secondary gpu-stop"
                                disabled={stopPending || resolvedDetail.cancel_requested}
                                onClick={(event) => {
                                  event.stopPropagation();
                                  onRequestStop(resolvedDetail);
                                }}
                              >
                                <OctagonX size={15} />
                                {resolvedDetail.cancel_requested
                                  ? (locale === "ko" ? "중지 요청됨" : "Stop requested")
                                  : ollamaJob
                                    ? (locale === "ko" ? "Ollama 중지 요청" : "Stop Ollama job")
                                    : (locale === "ko" ? "안전 중지 요청" : "Request safe stop")}
                              </button>
                            )}
                          </div>
                        </div>
                        <dl className="detail-grid">
                          <div><dt>{text.workload}</dt><dd>{workloadLabel(resolvedDetail.workload_key, locale)}</dd></div>
                          <div><dt>{text.status}</dt><dd>{jobStatusLabel(resolvedDetail.status, locale)}</dd></div>
                          <div><dt>{text.submitted}</dt><dd>{formatTime(resolvedDetail.submitted_at, locale)}</dd></div>
                          <div><dt>{text.elapsed}</dt><dd>{formatDuration(resolvedDetail.estimated_seconds, locale)}</dd></div>
                          <div><dt>{text.peak}</dt><dd>{formatMemory(resolvedDetail.peak_total_gpu_used_mb)}</dd></div>
                          <div><dt>{text.result}</dt><dd>{resolvedDetail.error ?? (resolvedDetail.exit_code == null ? "—" : `exit ${resolvedDetail.exit_code}`)}</dd></div>
                        </dl>
                        <details className="gpu-technical">
                          <summary>{locale === "ko" ? "기술 정보" : "Technical details"}</summary>
                          <dl className="detail-grid">
                            <div><dt>ID</dt><dd className="mono">{resolvedDetail.id}</dd></div>
                            <div><dt>{text.command}</dt><dd className="mono">{resolvedDetail.argv.join(" ")}</dd></div>
                            <div><dt>{text.cwd}</dt><dd className="mono">{resolvedDetail.cwd ?? "—"}</dd></div>
                            <div><dt>{text.pid}</dt><dd>{resolvedDetail.pid ?? "—"}</dd></div>
                            <div><dt>{text.log}</dt><dd className="mono">{resolvedDetail.log_path ?? "—"}</dd></div>
                          </dl>
                        </details>
                      </section>
                    </td>
                  </tr>
                )}
                </Fragment>
              );})}
            </tbody>
          </table>
          <nav className="page-controls" aria-label={locale === "ko" ? "페이지" : "Page"}>
            <button disabled={page <= 1} onClick={() => setPage(page - 1)}>
              {locale === "ko" ? "이전" : "Previous"}
            </button>
            <span>{locale === "ko" ? "페이지" : "Page"} {page} / {pages} · {jobs.length}</span>
            <button disabled={page >= pages} onClick={() => setPage(page + 1)}>
              {locale === "ko" ? "다음" : "Next"}
            </button>
          </nav>
        </div>
      )}
    </article>
  );
}

export function GpuQueue({ embedded = false }: { embedded?: boolean } = {}) {
  const { locale, setLocale, theme, toggleTheme } = usePreferences();
  const text = copy[locale];
  const queryClient = useQueryClient();
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [localQueued, setLocalQueued] = useState<GpuJob[] | null>(null);
  const health = useQuery({
    queryKey: ["gpu-queue-health"],
    queryFn: () => api<Record<string, unknown>>("/api/v1/gpu-queue/health"),
    refetchInterval: 15_000,
    retry: 1,
  });
  const status = useQuery({
    queryKey: ["gpu-queue-status"],
    queryFn: () => api<GpuQueueStatus>("/api/v1/gpu-queue/status"),
    refetchInterval: 3_000,
    retry: 1,
  });
  const job = useQuery({
    queryKey: ["gpu-queue-job", selectedJobId],
    queryFn: () => api<GpuJob>(`/api/v1/gpu-queue/jobs/${selectedJobId}`),
    enabled: Boolean(selectedJobId),
    refetchInterval: selectedJobId ? 3_000 : false,
    retry: 1,
  });
  const data = status.data;
  const queuedJobs = localQueued ?? data?.jobs.queued ?? [];
  const reorder = useMutation({
    mutationFn: (jobIds: string[]) => api<{ ok: boolean }>("/api/v1/gpu-queue/reorder", {
      method: "POST",
      body: JSON.stringify({ job_ids: jobIds }),
    }),
    onSuccess: async () => {
      setLocalQueued(null);
      await queryClient.invalidateQueries({ queryKey: ["gpu-queue-status"] });
    },
    onError: async () => {
      setLocalQueued(null);
      await queryClient.invalidateQueries({ queryKey: ["gpu-queue-status"] });
    },
  });
  const cancel = useMutation({
    mutationFn: (jobId: string) => api<{ ok: boolean }>(`/api/v1/gpu-queue/jobs/${jobId}/cancel`, {
      method: "POST",
      body: "{}",
    }),
    onSuccess: async (_result, jobId) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["gpu-queue-status"] }),
        queryClient.invalidateQueries({ queryKey: ["gpu-queue-job", jobId] }),
      ]);
    },
  });
  const stopExternal = useMutation({
    mutationFn: (workloadKey: string) => api<{ ok: boolean; workload: ExternalGpuWorkload }>(
      `/api/v1/gpu-queue/external-workloads/${workloadKey}/stop`,
      { method: "POST", body: "{}" },
    ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["gpu-queue-status"] });
    },
  });
  const moveQueued = (sourceId: string, targetId: string) => {
    if (reorder.isPending || sourceId === targetId) return;
    const current = localQueued ?? data?.jobs.queued ?? [];
    const sourceIndex = current.findIndex((item) => item.id === sourceId);
    const targetIndex = current.findIndex((item) => item.id === targetId);
    if (sourceIndex < 0 || targetIndex < 0) return;
    const next = [...current];
    const [source] = next.splice(sourceIndex, 1);
    next.splice(targetIndex, 0, source);
    setLocalQueued(next);
    reorder.mutate(next.map((item) => item.id));
  };
  const requestStop = (jobDetail: GpuJob) => {
    if (cancel.isPending || jobDetail.cancel_requested) return;
    const ollamaJob = /ollama|curation|embedding/i.test(
      `${jobDetail.workload_key} ${jobDetail.argv.join(" ")}`,
    );
    const message = locale === "ko"
      ? ollamaJob
        ? `GPU 큐가 소유한 Ollama 작업 '${jobDetail.workload_key}'을 중지할까요? 예약 작업과 이 작업이 시작한 Ollama만 종료하며, 다른 Ollama·GPU 프로그램은 건드리지 않습니다.`
        : `관리 중인 작업 '${jobDetail.workload_key}'에 안전 중지 요청을 보낼까요? 이 작업의 자식 프로세스만 종료하며, 다른 GPU 프로그램은 중지하지 않습니다.`
      : `Request a safe stop for '${jobDetail.workload_key}'? Only this managed child process can be stopped; unrelated GPU programs are not affected.`;
    if (window.confirm(message)) cancel.mutate(jobDetail.id);
  };
  const requestExternalStop = (workload: ExternalGpuWorkload) => {
    if (!workload.can_stop || stopExternal.isPending) return;
    const models = workload.models.map((model) => model.name).join(", ");
    const message = locale === "ko"
      ? `'${workload.label}'에서 현재 적재된 모델(${models})을 내릴까요?\n\n설정에 등록된 Ollama 컨테이너에만 정상 중지 명령을 보내며, ComfyUI나 다른 GPU 프로세스는 종료하지 않습니다.`
      : `Unload the currently loaded model(s) from '${workload.label}' (${models})?\n\nOnly the configured Ollama container receives a graceful stop command. ComfyUI and other GPU processes are not affected.`;
    if (window.confirm(message)) stopExternal.mutate(workload.key);
  };
  const gpu = data?.runtime.gpu;
  const externalProcesses = data?.runtime.unmanaged_gpu_processes ?? [];
  const externalWorkloads = data?.runtime.external_workloads ?? [];
  const comfyBridge = data?.runtime.comfyui_bridge;
  const comfyProcessObserved = Boolean(
    comfyBridge?.process_id && externalProcesses.some((item) => item.pid === comfyBridge.process_id),
  );
  const observedExternalGpuUse = Boolean(
    gpu && gpu.used_mb > 4096 && data?.runtime.managed_running === 0,
  );
  const utilization = Math.max(0, Math.min(100, gpu?.utilization_percent ?? 0));
  const jobDetail = job.data;
  const cards = useMemo(() => [
    [text.gpuTotal, formatMemory(gpu?.total_mb), HardDrive],
    [text.gpuUsed, formatMemory(gpu?.used_mb), Activity],
    [text.gpuFree, formatMemory(gpu?.free_mb), CheckCircle2],
    [text.utilization, `${gpu?.utilization_percent ?? 0}%`, Gauge],
  ] as const, [gpu, text]);

  return (
    <div className={`gpu-page${embedded ? " gpu-page-embedded" : ""}`}>
      {!embedded && <header className="gpu-topbar">
        <Link href="/" className="gpu-back"><ArrowLeft size={16} /> {text.back}</Link>
        <div className="gpu-actions">
          <span className={`health ${health.isSuccess ? "ok" : "bad"}`}>
            <span /> {health.isSuccess ? text.healthy : text.offline}
          </span>
          <label className="language-select">
            <Languages size={15} />
            <select value={locale} onChange={(event) => setLocale(event.target.value as Locale)}>
              <option value="ko">한국어</option>
              <option value="en">English</option>
            </select>
          </label>
          <button className="icon-button" onClick={toggleTheme} aria-label="theme">
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          </button>
        </div>
      </header>}

      <div className="gpu-main">
        <div className="page-title compact">
          <div>
            <p className="eyebrow">{text.eyebrow}</p>
            <h1>{text.title}</h1>
            <p>{text.subtitle}</p>
          </div>
          <button className="secondary" onClick={() => {
            health.refetch();
            status.refetch();
          }}><RefreshCcw size={15} /> {text.refresh}</button>
        </div>

        {status.isError && (
          <div className="error-state"><TriangleAlert size={17} /> {text.unavailable}</div>
        )}

        <section className="stat-grid">
          {cards.map(([label, value, Icon]) => (
            <article className="stat-card" key={label}>
              <div className="stat-head"><span>{label}</span><Icon size={17} /></div>
              <strong>{value}</strong>
            </article>
          ))}
        </section>

        <div className="gpu-meter" aria-label={`${text.utilization} ${utilization}%`}>
          <span style={{ width: `${utilization}%` }} />
        </div>

        {observedExternalGpuUse && (
          <section className="gpu-external-warning" aria-live="polite">
            <div>
              <TriangleAlert size={18} />
              <div>
                <strong>{locale === "ko" ? "큐 밖 GPU 점유 감지" : "External GPU use observed"}</strong>
                <p>{locale === "ko"
                  ? `현재 ${formatMemory(gpu?.used_mb)}가 사용 중이지만 GPU 큐가 관리하는 실행 작업은 없습니다. 이 점유는 안전 중지 버튼의 대상이 아닙니다.`
                  : `${formatMemory(gpu?.used_mb)} is in use while the GPU queue has no managed running job. This usage is not eligible for the safe-stop button.`}</p>
              </div>
            </div>
            {comfyBridge?.state === "ready" && (
              <p className="gpu-external-detail">{locale === "ko"
              ? `ComfyUI GPUQ bridge가 준비되어 있습니다${comfyBridge.process_id ? ` (PID ${comfyBridge.process_id})` : ""}${comfyProcessObserved ? "; 해당 프로세스가 드라이버 GPU 목록에서도 관측됩니다." : "."} ComfyUI 서버가 미리 올린 모델 메모리는 현재 큐 작업으로 귀속되지 않습니다.`
              : `The ComfyUI GPUQ bridge is ready${comfyBridge.process_id ? ` (PID ${comfyBridge.process_id})` : ""}${comfyProcessObserved ? "; that process is also observed by the driver." : "."} Memory held by a pre-warmed ComfyUI server is not currently attributed to a queue job.`}</p>
            )}
            {comfyBridge?.reservation_mode === "prompt_reservation" && <p className="gpu-external-detail">
              {locale === "ko"
                ? "다음 세션에서 모델 로드·중지까지 큐가 소유하도록 하려면 현재 ComfyUI를 사용자가 정상 종료한 뒤 run-comfyui-gpuq.ps1로 시작하세요."
                : "For the next session, stop ComfyUI normally and start it with run-comfyui-gpuq.ps1 so the queue owns model load and stop."}
            </p>}
            <p className="gpu-external-detail">{locale === "ko"
              ? `드라이버가 Windows WDDM에서 프로세스별 VRAM을 제공하지 않을 수 있어, 포털은 추정치 대신 전체 사용량과 관측 프로세스를 분리해서 표시합니다 (${externalProcesses.length}개).`
              : `Windows WDDM can hide per-process VRAM, so the portal separates global usage from observed processes instead of inventing an allocation (${externalProcesses.length} observed).`}</p>
          </section>
        )}

        <section className="panel gpu-external-workloads">
          <div className="panel-head">
            <div>
              <p className="eyebrow">{locale === "ko" ? "허용 목록 기반 제어" : "Allowlisted controls"}</p>
              <h2>{locale === "ko" ? "Ollama 모델 상태" : "Ollama model status"}</h2>
            </div>
            <span className="gpu-count">{externalWorkloads.length}</span>
          </div>
          <p className="gpu-external-intro">
            {locale === "ko"
              ? "지식 포털이 사용하는 Ollama만 표시합니다. 활성 모델은 확인 후 안전하게 내릴 수 있으며 임의 프로세스 종료는 지원하지 않습니다."
              : "Only Ollama runtimes explicitly registered for the knowledge portal appear here. Loaded models can be safely unloaded after confirmation; arbitrary process termination is unavailable."}
          </p>
          <div className="gpu-external-list">
            {externalWorkloads.length === 0 && (
              <div className="gpu-empty">
                {locale === "ko"
                  ? "등록된 Ollama 런타임이 없습니다. 호스트 스케줄러 설정을 확인하세요."
                  : "No Ollama runtime is registered. Check the host scheduler configuration."}
              </div>
            )}
            {externalWorkloads.map((workload) => (
              <article className="gpu-external-row" key={workload.key}>
                <div className="gpu-external-name">
                  <span className={`status ${workload.state === "active" ? "processing" : workload.state === "error" ? "failed" : "active"}`}>
                    {locale === "ko"
                      ? ({ active: "모델 사용 중", idle: "대기", stopped: "중지", error: "확인 실패" } as const)[workload.state]
                      : workload.state}
                  </span>
                  <div>
                    <strong>{locale === "ko"
                      ? ({
                        "local-knowledge-portal-embedding": "지식 포털 임베딩 Ollama",
                        "local-knowledge-portal-generation": "지식 포털 지식 편집 Ollama",
                        "windows-ollama-generation": "통합 Ollama · 포털·Hermes",
                      } as Record<string, string>)[workload.key] ?? workload.label
                      : workload.label}</strong>
                    <small>{workload.container ?? workload.target ?? "—"}</small>
                  </div>
                </div>
                <div className="gpu-model-list">
                  {workload.models.length === 0 ? (
                    <span>{locale === "ko" ? "적재된 모델 없음" : "No loaded model"}</span>
                  ) : workload.models.map((model) => (
                    <div key={`${workload.key}-${model.name}`}>
                      <strong>{model.name}</strong>
                      <span>
                        {model.size} · {model.processor}
                        {model.context ? ` · ${locale === "ko" ? "컨텍스트" : "context"} ${model.context}` : ""}
                        {model.until ? ` · ${locale === "ko" ? "유지" : "until"} ${model.until}` : ""}
                      </span>
                    </div>
                  ))}
                  {workload.error && <span className="gpu-external-error">{workload.error}</span>}
                </div>
                {workload.can_stop ? (
                  <button
                    className="secondary gpu-stop"
                    type="button"
                    disabled={stopExternal.isPending}
                    onClick={() => requestExternalStop(workload)}
                  >
                    <OctagonX size={15} />
                    {stopExternal.isPending && stopExternal.variables === workload.key
                      ? (locale === "ko" ? "중지 중" : "Stopping")
                      : (locale === "ko" ? "모델 내리기" : "Unload model")}
                  </button>
                ) : (
                  <span className="gpu-external-no-action">
                    {locale === "ko" ? "중지할 모델 없음" : "Nothing to unload"}
                  </span>
                )}
              </article>
            ))}
          </div>
          {stopExternal.isError && (
            <div className="gpu-control-error">
              {locale === "ko"
                ? "Ollama 모델 중지 요청을 처리하지 못했습니다. 런타임 상태를 새로고침한 뒤 다시 시도하세요."
                : "The Ollama model stop request failed. Refresh the runtime state and try again."}
            </div>
          )}
        </section>

        <section className="gpu-runtime-grid">
          <article className="panel system-panel">
            <div className="panel-head"><h2>{gpu?.name ?? "GPU"}</h2><Cpu size={18} /></div>
            <dl>
              <div><dt>{text.managed}</dt><dd>{data?.runtime.managed_running ?? "—"}</dd></div>
              <div><dt>{text.safety}</dt><dd>{formatMemory(data?.runtime.safety_vram_mb)}</dd></div>
              <div><dt>{text.baseline}</dt><dd>{formatMemory(data?.runtime.baseline_used_mb)}</dd></div>
              <div><dt>{text.parallel}</dt><dd>{data?.runtime.max_parallel_jobs ?? "—"}</dd></div>
            </dl>
          </article>
          <article className="panel gpu-decision">
            <div><Clock3 size={18} /><span>{text.decision}</span></div>
            <strong title={data?.runtime.last_decision ?? undefined}>
              {schedulerNoteLabel(data?.runtime.last_decision ?? null, locale)}
            </strong>
            {data?.runtime.last_error && <p><TriangleAlert size={14} /> {text.error}: {data.runtime.last_error}</p>}
          </article>
        </section>

        <section className="gpu-job-grid">
          <JobTable title={text.active} jobs={data?.jobs.active ?? []} locale={locale}
            selected={selectedJobId} onSelect={setSelectedJobId}
            detailJob={jobDetail} detailLoading={job.isFetching}
            stopPending={cancel.isPending} onRequestStop={requestStop} />
          <JobTable title={text.queued} jobs={queuedJobs} locale={locale}
            selected={selectedJobId} onSelect={setSelectedJobId}
            detailJob={jobDetail} detailLoading={job.isFetching}
            onMoveQueued={moveQueued} isReordering={reorder.isPending} />
        </section>
        <JobTable title={text.completed} jobs={data?.jobs.completed ?? []}
          locale={locale} selected={selectedJobId} onSelect={setSelectedJobId}
          detailJob={jobDetail} detailLoading={job.isFetching} />

        {(cancel.isError || reorder.isError) && <article className="panel gpu-detail">
          {cancel.isError && <div className="gpu-control-error">
            {locale === "ko"
              ? "중지 요청을 처리하지 못했습니다. 호스트 스케줄러 제어 연결과 최신 작업 상태를 확인하세요."
              : "The stop request was not accepted. Check the host scheduler control connection and current job state."}
          </div>}
          {reorder.isError && <div className="gpu-control-error">
            {locale === "ko"
              ? "대기열이 변경되어 순서를 적용하지 못했습니다. 최신 목록으로 다시 시도하세요."
              : "The queue changed before this order could be applied. Refresh and try again."}
          </div>}
        </article>}
      </div>
    </div>
  );
}
