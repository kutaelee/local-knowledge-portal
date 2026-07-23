# Known limitations

- Ollama was not running and `qwen3-embedding:0.6b` was not downloaded, so production semantic/hybrid retrieval is implemented but not end-to-end validated.
- The validation fixture uses a clearly labeled deterministic test embedding revision.
- Code chunking uses deterministic symbol patterns and line fallback; the installed tree-sitter adapter is not yet wired for every requested grammar.
- The watcher module handles recursive debounced events, but its long-running supervisor, lease renewal thread, suspend/resume hook, and portal health record are not complete.
- Full deletion/rename reconciliation, append-only log offsets, link/tag extraction, generated
  wiki page types other than managed Codex sessions, and the optional read-only MCP adapter remain
  incomplete.
- The web shell implements overview, virtualized search, explorer, operations, timeline, provenance context, themes, keyboard search focus, reduced motion, and responsive layout. Document rendering/diff, Headless Tree integration, React Flow data graph, command palette actions, resizable panes, and full operations facets remain incomplete.
- The Playwright baseline covers overview and primary navigation, not all ten requested data-dependent flows.
- The full `C:\Dev\Repos` source root was not scanned to avoid creating roughly 9,854 jobs without user review.
- Node.js was available through the Codex runtime for validation but was not installed system-wide or added to PATH.
- Windows Scheduled Task installation is not included; Codex capture starts through the portal
  script or a reviewed Codex `Stop` hook.
