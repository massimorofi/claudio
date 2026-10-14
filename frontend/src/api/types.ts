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

export interface AppConfig {
  server: ServerCfg
  llm: LlmCfg
  agent: AgentCfg
  mcp: { servers: McpServerEntry[] }
  monitoring: MonitoringCfg
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
  output: string
  usage: UsageInfo
}
export interface RunFailedEvent {
  run_id: string
  code: string
  message: string
  retryable: boolean
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
}

export interface LogEntry {
  timestamp: string
  level: string
  logger: string
  message: string
}
