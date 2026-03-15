export type SessionSummary = {
  id: string;
  driver_id: string;
  team_id?: string | null;
  car: string;
  track?: string | null;
  track_config?: string | null;
  best_lap_time?: number | null;
  lap_number?: number | null;
  wing_angle?: number | null;
  status: "processing" | "complete" | "error";
  created_at?: string | null;
};

export type SessionResult = {
  id: string;
  status: "processing" | "complete" | "error";
  error?: string | null;
  results?: Record<string, unknown> | null;
  report_text?: string | null;
  sto_storage_path?: string | null;
  created_at?: string | null;
};

export type TeamKnowledge = {
  session_count: number;
  driver_session_count?: number;
  fallback_mode: string;
  drivers: Array<Record<string, unknown>>;
  recurring_issues: Array<Record<string, unknown>>;
  individual_models: Array<Record<string, unknown>>;
  team_model?: Record<string, unknown> | null;
};

