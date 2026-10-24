// Types mirroring the Claudio backend HTTP API and SSE event protocol.

export type AgentState = 'stopped' | 'starting' | 'running' | 'stopping'

export interface ErrorEnvelope {
  error: {
    code: string
    message: string
    retryable?: boolean
    details?: Record<string, unknown>
  }
}

export interface HealthResponse {
  status: string
  time: string
}

export interface VersionResponse {
  backend: string
  openai_agents: string
  protocol: number
}

export interface AgentStatus {
  state: AgentState
  started_at: string | null
  active_runs: number
  uptime_seconds: number
  config_generation: number
}

export interface ToolInfo {
  name: string
  is_read_only: boolean
  is_destructive: boolean
}

// ---- Config ----

export interface ServerCfg {
  host: string
  port: number
  auth_token: string
  cors_origins: string[]
  expose_raw_config: boolean
}

export interface LlmCfg {
  provider: string
  model: string
  api_key: string
  base_url: string
  temperature: number
  max_tokens: number
  fallback_model: string | null
  timeout_seconds: number
  retry_attempts: number
  price_input_per_mtok: number
  price_output_per_mtok: number
}

export interface AgentCfg {
  name: string
  instructions_file: string
  max_turns: number
  max_budget_usd: number
  approval_mode: 'off' | 'always' | 'on_request'
  workspace_dir: string
  context_window: number
  compaction_reserve: number
  max_output_chars: number
  strict_tools: boolean
  skills_enabled: boolean
  skills_dir: string
  tools: { enabled: string[]; disabled: string[] }
  subagents: Record<string, SubagentCfg>
}

export interface SubagentCfg {
  description: string
  instructions_file?: string
  instructions?: string
  tools?: string[]
  max_turns?: number
}

export interface McpServerEntry {
  id: string
  transport: 'stdio' | 'sse' | 'streamable_http'
  command?: string
  url?: string
  enabled: boolean
}

export interface MonitoringCfg {
  log_level: string
  log_retention_days: number
  metrics_snapshot_interval_seconds: number
}

export interface HealthCheckCfg {
  enabled: boolean
  interval_minutes: number
  disk_warn_percent: number
  disk_crit_percent: number
  mem_crit_percent: number
  swap_warn_percent: number
  load_warn_factor: number
  load_crit_factor: number
  backup_path: string
  backup_max_age_hours: number
  // Editable shell script. When non-empty it replaces the built-in Python
  // diagnostics. Exit 0 = ok, 1 = warning, 2 = critical.
  script: string
}

export interface EmailNotifyCfg {
  enabled: boolean
  smtp_host: string
  smtp_port: number
  username: string
  password: string
  from_addr: string
  to_addr: string
  use_tls: boolean
}

export interface WebhookNotifyCfg {
  enabled: boolean
  url: string
}

export interface NotificationCfg {
  enabled: boolean
  email: EmailNotifyCfg
  webhook: WebhookNotifyCfg
}

export interface AutonomousCfg {
  enabled: boolean
  report_only: boolean
  health_check: HealthCheckCfg
  maintenance: MaintenanceCfg
  recovery: RecoveryCfg
  notification: NotificationCfg
  command_allowlist: string[] | null
}

export interface AppConfig {
  server: ServerCfg
  llm: LlmCfg
  agent: AgentCfg
  mcp: { servers: McpServerEntry[] }
  monitoring: MonitoringCfg
  autonomous: AutonomousCfg
}

export interface ConfigResponse {
  config: AppConfig
  generation: number
}

export interface ConfigHistoryEntry {
  id: number
  saved_at: string
  changed_keys: string[]
}

export interface LlmTestResult {
  ok: boolean
  latency_ms?: number
  model?: string
  error?: string
}

// ---- Chat / runs ----

export interface SessionSummary {
  id: string
  title: string
  created_at: string
  updated_at: string
  message_count: number
  total_cost_usd: number
  total_input_tokens: number
  total_output_tokens: number
}

export interface ChatMessage {
  role: string
  content: string
}

export interface ChatResponse {
  run_id: string
  session_id: string
  output: string
  usage: UsageInfo
}

export interface UsageInfo {
  input_tokens: number
  output_tokens: number
  cost_usd: number
}

export interface RunInfo {
  id: string
  session_id: string
  state: string
  started_at: string
  finished_at: string | null
  turns_used: number
  cost_usd: number
  input_tokens: number
  output_tokens: number
  final_output: string | null
  error_code: string | null
  error_message: string | null
}

export interface ApprovalRequest {
  id: string
  run_id: string
  session_id: string
  tool_name: string
  call_id: string
  arguments: string
  reason: string
  state: string
  requested_at: string
}

export interface ActiveRunResponse {
  active: boolean
  run_id?: string
  state?: string
  last_seq?: number
}

// ---- SSE events ----

export interface StreamEvent {
  event: string
  data: unknown
  id?: string
}

export interface TokenEvent {
  text: string
}
export interface ReasoningEvent {
  text: string
}
export interface ToolCallEvent {
  call_id: string
  name: string
  arguments: string
}
export interface ToolResultEvent {
  call_id: string
  name: string
  output: string
  is_error: boolean
}
export interface ApprovalRequiredEvent {
  approval_id: string
  run_id: string
  tool: string
  arguments: string
  reason: string
}
export interface UsageEvent extends UsageInfo {}
export interface RunCompletedEvent {
  run_id: string
  final_output: string
  usage: UsageInfo
  summary?: TurnSummary
}

// ---- Phase 4: context usage ----

export interface ContextUsage {
  used_tokens: number
  context_window: number
  usable_tokens: number
  free_tokens: number
  percent_used: number
  compact_threshold: number
  source: 'model' | 'estimate' | 'stored'
  auto_disabled: boolean
}

// ---- Phase 5: completion status ----

export interface TurnSummary {
  files_written: string[]
  commands_run: number
  duration_seconds: number
}
export interface RunFailedEvent {
  run_id: string
  code: string
  message: string
  retryable: boolean
}
export interface RunRestartingEvent {
  reason: string // 'compacted' | 'fallback' | an error code
  attempt: number
}

// ---- Workspace ----

export interface WorkspaceFile {
  path: string
  size: number
  modified: string
}

export interface WorkspaceListResponse {
  workspace: string
  files: WorkspaceFile[]
  truncated: boolean
}

export interface WorkspaceDeleteResponse {
  ok: boolean
  deleted: string[]
  failed: { path: string; error: string }[]
}

export interface WorkspaceNode {
  name: string
  path: string
  type: 'file' | 'dir'
  size?: number
  modified: string
  children?: WorkspaceNode[]
}

export interface WorkspaceTreeResponse {
  workspace: string
  tree: WorkspaceNode[]
  truncated: boolean
}

// ---- File upload (chat attachments) ----

export interface FileUploadResponse {
  ok: boolean
  path: string
  name: string
  size: number
  kind: string
  chars: number
  extracted: boolean
  note: string
}

// ---- Skills ----

export interface SkillEntry {
  directory: string
  name?: string
  description?: string
  valid: boolean
  error?: string
}

export interface SkillsResponse {
  directory: string
  enabled: boolean
  skills: SkillEntry[]
}

// ---- Monitoring ----

export interface HealthCheck {
  name: string
  ok: boolean
  detail?: string
}

export interface MonitoringHealth {
  overall: 'ok' | 'degraded' | 'down'
  checks: HealthCheck[]
}

export interface MetricsSnapshot {
  requests_total: number
  runs_total: number
  runs_active: number
  runs_completed: number
  runs_failed: number
  retry_count: number
  tool_errors_total: number
  tokens_in: number
  tokens_out: number
  cost_usd_total: number
  llm_errors_total: number
  latency_p50_ms: number
  latency_p95_ms: number
  queue_depth: number
  // Engine's own metrics, read from its Prometheus /metrics endpoint (real,
  // not estimated). Absent when the engine is unreachable.
  engine_metrics_available?: boolean
  prefill_tok_s?: number
  decode_tok_s?: number
  lifetime_prefill_tok_s?: number
  lifetime_decode_tok_s?: number
  prompt_tokens_processed?: number
  tokens_predicted?: number
  prompt_tokens_cached?: number
  prompt_cache_hit_ratio?: number
  kv_cache_usage_ratio?: number
  engine_requests_total?: number
  draft_tokens_total?: number
  draft_tokens_accepted?: number
  draft_accept_ratio?: number
}

export interface LogEntry {
  timestamp: string
  level: string
  logger: string
  message: string
}

// ---- Autonomous / daemon (Phase 1: Watch) ----

export type HealthVerdict = 'ok' | 'warning' | 'critical'

export interface HealthCheckItem {
  name: string
  status: 'ok' | 'warning' | 'critical' | 'skipped'
  message: string
  value?: number | null
  threshold?: number | null
  detail?: string
}

export interface HealthCheckRecord {
  id: number
  created_at: string
  verdict: HealthVerdict
  summary: string
  details: HealthCheckItem[]
  duration_ms: number
  source: string
}

export interface AutonomousStatus {
  enabled: boolean
  report_only: boolean
  health_check_enabled: boolean
  interval_minutes: number
  notification_enabled: boolean
  notification_channels: string[]
  last_check: HealthCheckRecord | null
  open_issues_count: number
  scheduler_running: boolean
  last_run_at: string | null
  next_run_at: string | null
  maintenance_enabled: boolean
  maintenance_schedule: string
  maintenance_next_run: string | null
  recovery_enabled: boolean
  task_count: number
  task_scheduler_running: boolean
  next_task_fire_at: string | null
}

// ---- Tasks (user-defined scheduled playbooks) ----

export type TaskVerdict = 'ok' | 'warning' | 'critical' | 'skipped'
export type TaskType = 'prompt' | 'script'

export interface Task {
  key: string
  name: string
  type: TaskType
  prompt: string
  script: string | null
  schedule: string
  approval_mode: string
  report_only: boolean
  notify_on: string[]
  enabled: boolean
  last_run_at: string | null
  next_run_at: string | null
  last_verdict: string | null
  created_at: string
  updated_at: string
  // Computed by the API on read.
  next_run?: string | null
}

export interface TaskCreate {
  key: string
  name: string
  type?: TaskType
  prompt: string
  script?: string | null
  schedule: string
  notify_on?: string[]
  report_only?: boolean
  enabled?: boolean
}

export interface TaskUpdate {
  name?: string
  type?: TaskType
  prompt?: string
  script?: string | null
  schedule?: string
  notify_on?: string[]
  report_only?: boolean
  enabled?: boolean
}

export interface TaskRun {
  id: number
  task_key: string
  started_at: string
  finished_at: string | null
  verdict: string | null
  summary: string | null
  output: string | null
  exit_code: number | null
  trigger: string
}

export interface TaskReport {
  verdict: TaskVerdict
  summary: string
  report: string
  details: Record<string, unknown>
}

// ---- Maintenance / recovery config ----

export interface MaintenanceCfg {
  enabled: boolean
  schedule: string
  vacuum_size: string
  temp_dirs: string[]
  audit_retention_days: number
  apt_update: boolean
  vacuum_logs: boolean
  temp_cleanup: boolean
  autoremove: boolean
  full_upgrade: boolean
  docker_prune: boolean
  // Editable shell script. When non-empty it replaces the built-in playbook.
  // Skipped while report_only is in effect. Exit 0 = ok, 1 = warning, 2 = critical.
  script: string
}

export interface RecoveryCfg {
  enabled: boolean
  expected_services: string[]
  restart_allowlist: string[]
}

export interface HealthRunResult {
  id: number
  verdict: HealthVerdict
  summary: string
  duration_ms: number
  checks: HealthCheckItem[]
  new_issue: number | null
}

export type IssueStatus = 'open' | 'acknowledged' | 'resolved'

export interface Issue {
  id: number
  created_at: string
  source: string
  severity: 'warning' | 'critical'
  title: string
  detail: string
  status: IssueStatus
  resolved_at: string | null
  resolution: string | null
}

export interface NotifyTestResult {
  enabled: boolean
  channels: Record<string, { ok: boolean; error: string }>
  ok: boolean
  message: string
}

// ---- Fix suggestions (agent proposes, operator approves each command) ----

export type SuggestionStatus = 'pending' | 'executed' | 'failed' | 'dismissed'
export type FixRisk = 'low' | 'medium' | 'high'

export interface FixSuggestion {
  id: number
  issue_id: number
  created_at: string
  command: string
  explanation: string
  risk: FixRisk
  reversible: number
  status: SuggestionStatus
  executed_at: string | null
  exit_code: number | null
  output: string | null
}

export interface FixExecutionResult {
  ok: boolean
  exit_code: number | null
  stdout: string
  stderr: string
  duration_ms: number
}

export interface AcceptFixResult {
  executed: FixExecutionResult
  recheck: { verdict: HealthVerdict; summary: string }
  issue_resolved: boolean
  // When the fix did not resolve the issue, the agent proposes a different one
  // here (null once the attempt ceiling is reached).
  next_suggestion: FixSuggestion | null
}
