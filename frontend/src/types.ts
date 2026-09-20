export type MaskId = "mask1" | "mask2" | "mask3";

export interface FieldError {
  code: string;
  message: string;
  field: "fragments" | "conflicts" | "stitches";
  line?: number;
  raw?: string;
}

export interface AssignmentRow {
  fragment: number;
  mask: MaskId;
}

export interface CutStitchRow {
  u: number;
  v: number;
  weight: number;
  mask_u: MaskId;
  mask_v: MaskId;
}

export interface SolveStats {
  nodes: number;
  elapsed_ms: number;
}

export interface WitnessPayload {
  assignment: AssignmentRow[];
  cut_stitches: CutStitchRow[];
  canonical: number[];
}

export type SolveResponse =
  | { ok: false; status: "invalid"; errors: FieldError[] }
  | {
      ok: true;
      status: "optimal";
      unique: boolean;
      optimal_weight: number;
      total_stitch_weight: number;
      assignment: AssignmentRow[];
      cut_stitches: CutStitchRow[];
      canonical: number[];
      witness?: WitnessPayload;
      stats: SolveStats;
    }
  | {
      ok: true;
      status: "infeasible" | "inconclusive";
      message: string;
      stats: SolveStats;
    };

export interface SolveRequest {
  fragments: string;
  conflicts: string;
  stitches: string;
}
