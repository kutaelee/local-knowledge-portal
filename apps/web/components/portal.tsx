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
  RefreshCcw, Search, ServerCog, Sun, TerminalSquare, BookCheck,
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

const nav: { id: View; label: string; icon: React.ComponentType<{ size?: number }> }[] = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "explorer", label: "Explorer", icon: Folder },
  { id: "search", label: "Search", icon: Search },
  { id: "activities", label: "Activity", icon: Activity },
  { id: "knowledge", label: "Knowledge cases", icon: BookCheck },
  { id: "operations", label: "Operations", icon: ServerCog },
  { id: "timeline", label: "Timeline", icon: Clock3 },
  { id: "graph", label: "Knowledge graph", icon: Network },
];

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

export function Portal() {
  const [view, setView] = useState<View>("overview");
  const [contextOpen, setContextOpen] = useState(true);
  const [selected, setSelected] = useState<SearchResult | null>(null);
  const [documentId, setDocumentId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const { theme, toggle } = useTheme();
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
        <button className="brand" onClick={() => setView("overview")} aria-label="Overview">
          <span className="brand-mark"><Blocks size={17} /></span>
          <span>Local Knowledge</span>
          <span className="local-pill">LOCAL</span>
        </button>
        <form className="global-search" onSubmit={submitGlobal}>
          <Search size={17} />
          <input
            id="global-search" value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Search documents, paths, symbols…" aria-label="Global search"
          />
          <kbd><Command size={12} />K</kbd>
        </form>
        <div className="top-actions">
          <span className={`health ${health.isSuccess ? "ok" : "bad"}`}>
            <span /> {health.isSuccess ? "Healthy" : "Offline"}
          </span>
          <button className="icon-button" onClick={toggle} aria-label="Toggle theme">
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          </button>
          <button
            className="icon-button" onClick={() => setContextOpen((value) => !value)}
            aria-label="Toggle context panel"
          ><PanelRightClose size={17} /></button>
        </div>
      </header>

      <aside className="sidebar">
        <nav aria-label="Primary navigation">
          <p className="nav-heading">Workspace</p>
          {nav.map((item) => (
            <button
              key={item.id} className={view === item.id ? "active" : ""}
              onClick={() => setView(item.id)}
            >
              <item.icon size={17} /> {item.label}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <p>Pipeline</p>
          <div><span className="pulse-dot" /> Indexing service</div>
          <small>localhost only · read-only sources</small>
        </div>
      </aside>

      <main ref={main} className="main">
        <div className="view-enter" key={view}>
          {view === "overview" && <Overview onNavigate={setView} />}
          {view === "explorer" && <Explorer onOpen={(item) => {
            setDocumentId(item.id); setView("document");
          }} />}
          {view === "document" && documentId && <DocumentViewer documentId={documentId} />}
          {view === "search" && (
            <SearchView initialQuery={query} onSelect={(item) => {
              setSelected(item); setContextOpen(true);
            }} />
          )}
          {view === "operations" && <Operations />}
          {view === "activities" && <ActivityHistory />}
          {view === "knowledge" && <KnowledgeCases />}
          {view === "timeline" && <Timeline />}
          {view === "graph" && <GraphNotice />}
        </div>
      </main>

      <aside className="context">
        <ContextPanel selected={selected} health={health.data} />
      </aside>
    </div>
  );
}

function Overview({ onNavigate }: { onNavigate: (view: View) => void }) {
  const metrics = useQuery({
    queryKey: ["metrics"], queryFn: () => api<Metrics>("/api/v1/metrics/summary"),
    refetchInterval: 10_000,
  });
  const data = metrics.data;
  const cards = [
    ["Projects", data?.projects ?? 0, GitBranch, "Indexed source groups"],
    ["Documents", data?.documents ?? 0, Files, "Active latest versions"],
    ["Chunks", data?.chunks ?? 0, Blocks, "Searchable passages"],
    ["Pending", data?.jobs?.pending ?? 0, Clock3, `${Math.round(data?.oldest_pending_seconds ?? 0)}s oldest`],
  ] as const;
  const throughput = [18, 34, 25, 48, 39, 64, 57, 72, 68, 81, 73, 88].map((value, i) => ({ i, value }));
  return (
    <section>
      <div className="page-title">
        <div><p className="eyebrow">SYSTEM OVERVIEW</p><h1>Your knowledge, observable.</h1>
          <p>Freshness, retrieval, and operational state in one local workspace.</p></div>
        <button className="primary" onClick={() => onNavigate("search")}><Search size={16} /> Search knowledge</button>
      </div>
      {metrics.isError && <ErrorState message="API metrics are unavailable." />}
      <div className="stat-grid">
        {cards.map(([label, value, Icon, note]) => (
          <article className="stat-card" key={label}>
            <div className="stat-head"><span>{label}</span><Icon size={17} /></div>
            <strong>{Number(value).toLocaleString()}</strong><small>{note}</small>
          </article>
        ))}
      </div>
      <div className="overview-grid">
        <article className="panel throughput">
          <div className="panel-head"><div><p className="eyebrow">INGESTION</p><h2>Recent throughput</h2></div>
            <span className="chip success"><Activity size={13} /> streaming</span></div>
          <div className="chart">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={throughput}>
                <defs><linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stopColor="var(--accent)" stopOpacity={0.45} />
                  <stop offset="1" stopColor="var(--accent)" stopOpacity={0} />
                </linearGradient></defs>
                <XAxis dataKey="i" hide /><Tooltip contentStyle={{ background: "#111820", border: "1px solid #28323d" }} />
                <Area type="monotone" dataKey="value" stroke="var(--accent)" fill="url(#fill)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </article>
        <article className="panel system-panel">
          <div className="panel-head"><div><p className="eyebrow">PIPELINE</p><h2>Active revision</h2></div><Database size={18} /></div>
          <dl>
            <div><dt>Embedding</dt><dd>{data?.embedding_model ?? "—"}</dd></div>
            <div><dt>Revision</dt><dd className="mono">{data?.embedding_revision ?? "—"}</dd></div>
            <div><dt>Pipeline</dt><dd>{data?.pipeline_version ?? "—"}</dd></div>
            <div><dt>Workers</dt><dd>{data?.workers ?? 0}</dd></div>
          </dl>
        </article>
      </div>
      <div className="quick-actions">
        <button onClick={() => onNavigate("explorer")}><BookOpen size={18} /><span><strong>Browse sources</strong><small>Project and folder tree</small></span></button>
        <button onClick={() => onNavigate("operations")}><TerminalSquare size={18} /><span><strong>Inspect jobs</strong><small>Retries and failures</small></span></button>
        <button onClick={() => onNavigate("timeline")}><Clock3 size={18} /><span><strong>View timeline</strong><small>Observed changes</small></span></button>
      </div>
    </section>
  );
}

function Explorer({ onOpen }: { onOpen: (item: TreeItem) => void }) {
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
  if (tree.isError) return <ErrorState message="The source tree could not be loaded." />;
  return (
    <section>
      <div className="page-title compact"><div><p className="eyebrow">READ-ONLY EXPLORER</p><h1>Projects & documents</h1>
        <p>{tree.data?.items.length ?? 0} visible documents{tree.data?.truncated ? " · limited view" : ""}</p></div></div>
      <div className="panel tree-panel" role="tree" aria-label="Source tree">
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
                <FileCode2 size={15} /><span>{item.path}</span><em>{item.state}</em>
              </button>)}
            </div>}
          </div>;
        })}
        {!grouped.size && <EmptyState title="No indexed documents" detail="Run a scan and worker to populate the tree." />}
      </div>
    </section>
  );
}

function SearchView({ initialQuery, onSelect }: { initialQuery: string; onSelect: (result: SearchResult) => void }) {
  const [value, setValue] = useState(initialQuery);
  const [mode, setMode] = useState("hybrid");
  const [submitted, setSubmitted] = useState(initialQuery);
  const parent = useRef<HTMLDivElement>(null);
  const results = useQuery({
    queryKey: ["search", submitted, mode],
    queryFn: () => api<{ confidence: string; results: SearchResult[]; total: number }>("/api/v1/search/hybrid", {
      method: "POST", body: JSON.stringify({ query: submitted, mode, top_k: 50 }),
    }),
    enabled: submitted.trim().length > 0,
  });
  const virtual = useVirtualizer({
    count: results.data?.results.length ?? 0, getScrollElement: () => parent.current,
    estimateSize: () => 190, overscan: 4, useFlushSync: false,
  });
  return (
    <section>
      <div className="page-title compact"><div><p className="eyebrow">RETRIEVAL</p><h1>Search the source of truth</h1>
        <p>Every result carries document, version, chunk, path, and line provenance.</p></div></div>
      <form className="search-box" onSubmit={(event) => { event.preventDefault(); setSubmitted(value); }}>
        <Search size={18} /><input value={value} onChange={(e) => setValue(e.target.value)} placeholder="Filename, error, ADR, symbol, or natural language…" />
        <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="Search mode">
          <option value="hybrid">Hybrid</option><option value="keyword">Keyword</option>
          <option value="semantic">Semantic</option><option value="path">Path</option><option value="symbol">Symbol</option>
        </select><button className="primary">Search</button>
      </form>
      <div className="result-meta"><span>{results.data?.total ?? 0} results</span>
        {results.data && <span className={`confidence ${results.data.confidence}`}>{results.data.confidence} confidence</span>}</div>
      {results.isLoading && <SkeletonRows />}
      {results.isError && <ErrorState message="Search failed. Semantic mode requires Ollama; keyword mode remains available." />}
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
              {result.heading_or_symbol && <h3>{result.heading_or_symbol}</h3>}
              <p className="snippet">{result.snippet}</p>
              <div className="score-row">
                {result.lexical_rank != null && <span>lex {result.lexical_rank.toFixed(3)}</span>}
                {result.vector_similarity != null && <span>vec {result.vector_similarity.toFixed(3)}</span>}
                <span>rrf {result.fused_rank.toFixed(4)}</span>
                <em>{result.match_reason.join(" + ")}</em>
              </div>
            </button>;
          })}
        </div>
      </div>
      {!results.isLoading && submitted && !results.data?.results.length && <EmptyState title="No grounded result" detail="Try a filename, path segment, exact symbol, or broader wording." />}
    </section>
  );
}

function Operations() {
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
    { accessorKey: "status", header: "Status", cell: ({ getValue }) => <Status value={String(getValue())} /> },
    { accessorKey: "job_type", header: "Type" },
    { accessorKey: "path", header: "Path", cell: ({ getValue }) => <span className="table-path">{String(getValue())}</span> },
    { id: "attempts", header: "Attempts", cell: ({ row }) => `${row.original.attempt_count}/${row.original.max_attempts}` },
    { accessorKey: "error_type", header: "Error", cell: ({ row }) => <span title={row.original.error_message ?? ""}>{row.original.error_type ?? "—"}</span> },
    { id: "action", header: "", cell: ({ row }) => row.original.status === "failed" || row.original.status === "dead_letter"
      ? <button className="retry" onClick={() => retry.mutate(row.original.id)}><RefreshCcw size={13} /> Retry</button> : null },
  ], [retry]);
  const table = useReactTable({ data: jobs.data?.items ?? [], columns, getCoreRowModel: getCoreRowModel() });
  return (
    <section>
      <div className="page-title compact"><div><p className="eyebrow">OPERATIONS</p><h1>Durable queue</h1>
        <p>Retries create a new auditable job; original history is retained.</p></div>
        <button className="secondary" onClick={() => jobs.refetch()}><RefreshCcw size={15} /> Refresh</button></div>
      <div className="tabs"><button className={tab === "jobs" ? "active" : ""}
        onClick={() => setTab("jobs")}>Jobs</button><button
        className={tab === "workers" ? "active" : ""}
        onClick={() => setTab("workers")}>Workers</button><button
        className={tab === "backups" ? "active" : ""}
        onClick={() => setTab("backups")}>Backups</button></div>
      {tab === "jobs" && <div className="panel table-wrap">
        <table>
          <thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) =>
            <th key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr>)}</thead>
          <tbody>{table.getRowModel().rows.map((row) => <tr key={row.id}>{row.getVisibleCells().map((cell) =>
            <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody>
        </table>
        {!jobs.isLoading && !jobs.data?.items.length && <EmptyState title="Queue is empty" detail="No ingest jobs have been created yet." />}
      </div>}
      {tab === "workers" && <div className="panel table-wrap">
        <table><thead><tr><th>Worker</th><th>Host</th><th>State</th>
          <th>Heartbeat</th><th>Processed</th><th>Failed</th></tr></thead>
          <tbody>{workers.data?.map((worker) => <tr key={worker.worker_id}>
            <td className="mono">{worker.worker_id}</td><td>{worker.hostname}</td>
            <td><Status value={worker.state} /></td>
            <td>{new Date(worker.last_seen_at).toLocaleString()}</td>
            <td>{worker.processed_count}</td><td>{worker.failed_count}</td>
          </tr>)}</tbody></table>
        {!workers.isLoading && !workers.data?.length &&
          <EmptyState title="No worker heartbeat" detail="Start the indexer worker." />}
      </div>}
      {tab === "backups" && <div className="panel table-wrap">
        <table><thead><tr><th>Status</th><th>Created</th><th>Path</th>
          <th>Revision</th><th>SHA-256</th></tr></thead>
          <tbody>{backups.data?.map((backup) => <tr key={backup.id}>
            <td><Status value={backup.status} /></td>
            <td>{new Date(backup.created_at).toLocaleString()}</td>
            <td className="table-path">{backup.path}</td>
            <td className="mono">{backup.manifest.schema_revision ?? "—"}</td>
            <td className="mono">{backup.manifest.sha256?.slice(0, 12) ?? "—"}</td>
          </tr>)}</tbody></table>
        {!backups.isLoading && !backups.data?.length &&
          <EmptyState title="No backup evidence" detail="Run scripts/backup.ps1." />}
      </div>}
    </section>
  );
}

function Timeline() {
  const events = useQuery({ queryKey: ["timeline"], queryFn: () => api<Array<{ id: number; event: string; path: string; details: object; created_at: string }>>("/api/v1/timeline") });
  return <section><div className="page-title compact"><div><p className="eyebrow">OBSERVED HISTORY</p><h1>Timeline</h1>
    <p>Filesystem and pipeline events, newest first.</p></div></div><div className="timeline">
      {events.data?.map((item) => <article key={item.id}><span className="timeline-dot" /><div><strong>{item.event}</strong>
        <p>{item.path ?? "system event"}</p><small>{new Date(item.created_at).toLocaleString()}</small></div></article>)}
      {!events.isLoading && !events.data?.length && <EmptyState title="No events yet" detail="Indexed, failed, and retried work will appear here." />}
    </div></section>;
}

function GraphNotice() {
  return <section><div className="page-title compact"><div><p className="eyebrow">RELATIONSHIPS</p><h1>Knowledge graph</h1>
    <p>Scoped graph views prevent the browser from rendering an unbounded dataset.</p></div></div>
    <div className="panel graph-placeholder"><Network size={38} /><h2>Graph adapter is ready for links</h2>
      <p>Markdown wikilinks and document links will appear after link extraction. Explorer and search remain the primary navigation.</p>
      <div className="fake-nodes"><span /><span /><span /><span /></div></div></section>;
}

function ContextPanel({ selected, health }: { selected: SearchResult | null; health?: { database: boolean; ollama: boolean } }) {
  return <div><p className="nav-heading">Context</p>
    {selected ? <><h2>{selected.title}</h2><p className="context-path">{selected.provenance.relative_path}</p>
      <dl className="context-list">
        <div><dt>Lines</dt><dd>{selected.provenance.start_line}–{selected.provenance.end_line}</dd></div>
        <div><dt>Version</dt><dd className="mono">{selected.provenance.document_version_id.slice(0, 8)}</dd></div>
        <div><dt>Chunk</dt><dd className="mono">{selected.provenance.chunk_id.slice(0, 8)}</dd></div>
        <div><dt>Hash</dt><dd className="mono">{selected.provenance.content_hash.slice(0, 12)}</dd></div>
        <div><dt>Indexed</dt><dd>{new Date(selected.provenance.indexed_timestamp).toLocaleString()}</dd></div>
      </dl><button className="secondary copy" onClick={() => navigator.clipboard.writeText(
        `${selected.provenance.canonical_path}:L${selected.provenance.start_line}-L${selected.provenance.end_line}`
      )}>Copy citation</button></> : <div className="context-empty"><BookOpen size={24} /><p>Select a result to inspect immutable provenance.</p></div>}
    <div className="service-status"><p className="nav-heading">Services</p>
      <div><Database size={15} /> PostgreSQL <Status value={health?.database ? "healthy" : "offline"} /></div>
      <div><HeartPulse size={15} /> Ollama <Status value={health?.ollama ? "healthy" : "unavailable"} /></div>
    </div></div>;
}

function Status({ value }: { value: string }) {
  const good = ["healthy", "succeeded", "active", "idle", "processing"].includes(value);
  return <span className={`status ${good ? "good" : value === "pending" ? "waiting" : "danger"}`}>
    {good ? <CircleCheck size={12} /> : value === "pending" ? <Clock3 size={12} /> : <AlertTriangle size={12} />}{value}
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
