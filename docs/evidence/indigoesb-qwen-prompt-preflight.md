# IndigoESB Qwen3.6 prompt preflight

Date: 2026-07-27

This check ran the production repository task planner and source-window builder with a
non-generating provider, then measured every resulting message with the canonical local
Qwen3.6-27B tokenizer revision
`6a9e13bd6fc8f0983b9b99948120bc37f49c13e9`. It did not call the model, persist prompts, or
write source text.

Runtime limits:

- vLLM maximum sequence length: 16,384 tokens
- reserved completion budget: 2,048 tokens
- maximum source excerpt per initial task: 4,500 characters
- retry source excerpt: 2,250 characters
- task batch: at most 5 files

Measured results:

| Project | Composite source hash | Tasks | Input p95 | Maximum input | Input + output | Margin |
|---|---|---:|---:|---:|---:|---:|
| esb | `1690136f82b85dc01c7630f34a2bec88f47afea627541cdf8eb1f76f0e0155a4` | 607 | 10,896 | 11,507 | 13,555 | 2,829 |
| imc | `21e7f06af4b979ca73bef1193f7677408e1588b199397086c33091d8610ed36a` | 1,556 | 10,452 | 11,417 | 13,465 | 2,919 |
| agent | `170779f407e073e1e4c80f744799c4981ba6bcae3ae08830225b34a261b6d39d` | 621 | 10,663 | 11,421 | 13,469 | 2,915 |

The IMC plan includes all 784 hash-addressed 4,096-character evidence chunks derived from the
3.2 MB one-line `app.js` bundle. The agent plan includes the six path-validated XML members from
the tar-formatted legacy properties artifact. Decompiled internal JAR evidence is included in all
three composite hashes.

Result: all 2,784 planned Qwen analysis prompts fit the qualified 16,384-token runtime limit with
the reserved completion budget included.
