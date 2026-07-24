"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  CheckCircle2,
  Clock3,
  Cpu,
  Gauge,
  HardDrive,
  Languages,
  Moon,
  RefreshCcw,
  Sun,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
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

function JobTable({
  title,
  jobs,
  locale,
  selected,
  onSelect,
}: {
  title: string;
  jobs: GpuJob[];
  locale: Locale;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const text = copy[locale];
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const pages = Math.max(1, Math.ceil(jobs.length / pageSize));
  const visibleJobs = jobs.slice((page - 1) * pageSize, page * pageSize);
  useEffect(() => {
    if (page > pages) setPage(pages);
  }, [page, pages]);
  return (
    <article className="panel gpu-job-panel">
      <div className="panel-head">
        <h2>{title}</h2>
        <span className="gpu-count">{jobs.length}</span>
      </div>
      {!jobs.length ? <div className="gpu-empty">{text.empty}</div> : (
        <div className="table-wrap">
          <table>
            <thead><tr>
              <th>{text.workload}</th><th>{text.agent}</th><th>{text.vram}</th>
              <th>{text.priority}</th><th>{text.score}</th><th>{text.status}</th>
              <th>{text.note}</th>
            </tr></thead>
            <tbody>
              {visibleJobs.map((job) => (
                <tr
                  key={job.id}
                  className={selected === job.id ? "selected-row" : ""}
                  onClick={() => onSelect(job.id)}
                >
                  <td><strong>{job.workload_key}</strong><small>{job.id.slice(0, 8)}</small></td>
                  <td>{job.agent_name}</td>
                  <td className="mono">{formatMemory(job.requested_vram_mb)}</td>
                  <td>{job.priority}</td>
                  <td>{job.effective_score == null ? "—" : job.effective_score.toFixed(2)}</td>
                  <td><span className={`status ${job.status === "running" || job.status === "completed" ? "good" : "waiting"}`}>{job.status}</span></td>
                  <td className="gpu-note">{job.scheduling_note ?? "—"}</td>
                </tr>
              ))}
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

export function GpuQueue() {
  const { locale, setLocale, theme, toggleTheme } = usePreferences();
  const text = copy[locale];
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
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
  const gpu = data?.runtime.gpu;
  const utilization = Math.max(0, Math.min(100, gpu?.utilization_percent ?? 0));
  const jobDetail = job.data;
  const cards = useMemo(() => [
    [text.gpuTotal, formatMemory(gpu?.total_mb), HardDrive],
    [text.gpuUsed, formatMemory(gpu?.used_mb), Activity],
    [text.gpuFree, formatMemory(gpu?.free_mb), CheckCircle2],
    [text.utilization, `${gpu?.utilization_percent ?? 0}%`, Gauge],
  ] as const, [gpu, text]);

  return (
    <div className="gpu-page">
      <header className="gpu-topbar">
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
      </header>

      <main className="gpu-main">
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
            <strong>{data?.runtime.last_decision ?? "—"}</strong>
            {data?.runtime.last_error && <p><TriangleAlert size={14} /> {text.error}: {data.runtime.last_error}</p>}
          </article>
        </section>

        <section className="gpu-job-grid">
          <JobTable title={text.active} jobs={data?.jobs.active ?? []} locale={locale}
            selected={selectedJobId} onSelect={setSelectedJobId} />
          <JobTable title={text.queued} jobs={data?.jobs.queued ?? []} locale={locale}
            selected={selectedJobId} onSelect={setSelectedJobId} />
        </section>
        <JobTable title={text.completed} jobs={data?.jobs.completed ?? []}
          locale={locale} selected={selectedJobId} onSelect={setSelectedJobId} />

        <article className="panel gpu-detail">
          <div className="panel-head"><h2>{text.details}</h2>
            {job.isFetching && <RefreshCcw size={15} className="spin" />}</div>
          {!selectedJobId ? <div className="gpu-empty">{text.select}</div> : jobDetail ? (
            <dl className="detail-grid">
              <div><dt>ID</dt><dd className="mono">{jobDetail.id}</dd></div>
              <div><dt>{text.command}</dt><dd className="mono">{jobDetail.argv.join(" ")}</dd></div>
              <div><dt>{text.cwd}</dt><dd className="mono">{jobDetail.cwd ?? "—"}</dd></div>
              <div><dt>{text.submitted}</dt><dd>{formatTime(jobDetail.submitted_at, locale)}</dd></div>
              <div><dt>{text.elapsed}</dt><dd>{formatDuration(jobDetail.estimated_seconds, locale)}</dd></div>
              <div><dt>{text.pid}</dt><dd>{jobDetail.pid ?? "—"}</dd></div>
              <div><dt>{text.peak}</dt><dd>{formatMemory(jobDetail.peak_total_gpu_used_mb)}</dd></div>
              <div><dt>{text.result}</dt><dd>{jobDetail.error ?? (jobDetail.exit_code == null ? "—" : `exit ${jobDetail.exit_code}`)}</dd></div>
              <div><dt>{text.log}</dt><dd className="mono">{jobDetail.log_path ?? "—"}</dd></div>
            </dl>
          ) : <div className="gpu-empty">{text.unavailable}</div>}
        </article>
      </main>
    </div>
  );
}
