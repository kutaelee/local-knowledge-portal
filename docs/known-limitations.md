# Known limitations

- Codex non-managed hooks require one human `/hooks` trust approval. Installation and merge are
  verified; trust remains `MANUAL_APPROVAL_REQUIRED` by design.
- Long-running watcher endurance, sustained load endurance, and a real long Windows
  suspend/resume cycle were intentionally excluded. Short watcher, reconciliation, process-crash,
  lease-expiry, and PostgreSQL restart recovery were executed.
- Optional local chat generation is disabled until the operator selects and pins a chat model.
  Embedding does not depend on this optional feature.
- The read-only MCP adapter and Windows Scheduled Task installer remain optional backlog items;
  the supported RAG REST API and service scripts are complete.
- Node is available through the Codex runtime on this workstation but is not installed
  system-wide. Scripts therefore require a Node installation or an equivalent PATH entry.
