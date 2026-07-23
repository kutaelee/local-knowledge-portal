import { z } from "zod";

export const provenanceSchema = z.object({
  document_id: z.string().uuid(),
  document_version_id: z.string().uuid(),
  chunk_id: z.string().uuid(),
  source_root: z.string(),
  canonical_path: z.string(),
  relative_path: z.string(),
  start_line: z.number().int().positive(),
  end_line: z.number().int().positive(),
  content_hash: z.string().length(64),
  indexed_timestamp: z.string(),
});

export type Provenance = z.infer<typeof provenanceSchema>;
