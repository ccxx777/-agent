/** 合同审查 API 的前端数据契约。
 *
 * 这里不把后端的原始合同文件带到浏览器之外；页面只使用后端返回的脱敏文本、
 * 结构化事实和证据片段。所有请求都复用当前登录会话的 Bearer token。
 */

export type ReviewStatus =
  | "queued"
  | "extracting"
  | "ready"
  | "needs_confirmation"
  | "failed";

export type ExtractionStatus =
  | "not_started"
  | "running"
  | "ready"
  | "needs_confirmation"
  | "failed"
  | string;

export type ConfirmationStatus =
  | "not_started"
  | "pending"
  | "in_progress"
  | "completed";

export type ConfirmationAction =
  | "confirm"
  | "correct"
  | "supplement"
  | "not_applicable"
  | "defer";

export type ConfirmationState =
  | "unreviewed"
  | "confirmed"
  | "corrected"
  | "supplemented"
  | "not_applicable"
  | "deferred";

export type RiskLevel = "high" | "medium" | "low" | "unconfirmed" | "info";

export interface ContractEvidence {
  page_no: number;
  quote: string;
  start_char?: number | null;
  end_char?: number | null;
  match_type?: string | null;
  clause_id?: string | null;
}

export interface ContractQuality {
  page_count: number;
  text_pages: number;
  native_pages: number;
  hybrid_pages: number;
  scanned_pages: number;
  ocr_pages: number;
  failed_pages: number[];
  suspicious_pages: number[];
  text_coverage: number;
  needs_confirmation: boolean;
}

export interface PrivacyReport {
  redaction_version: string;
  redaction_counts: Record<string, number>;
  zero_width_sequences_detected: number;
  external_raw_image_sent: boolean;
}

export interface ContractPage {
  page_no: number;
  mode: "native" | "hybrid" | "scanned" | string;
  text: string;
  ocr_used: boolean;
  quality_flags: string[];
}

export interface ContractReviewSummary {
  review_id: string;
  session_id: string | null;
  retention_policy: "short" | "long_opt_in" | string;
  expires_at: string | null;
  status: ReviewStatus;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  page_count: number | null;
  quality: ContractQuality | null;
  privacy: PrivacyReport | null;
  extraction_status: ExtractionStatus;
  confirmation_status: ConfirmationStatus;
  confirmation_revision: number;
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ContractExtractionResult {
  extraction_mode?: "single" | "batch" | string;
  clauses?: unknown[];
  facts?: unknown[];
  confirmation_questions?: unknown[];
  [key: string]: unknown;
}

export interface ContractReviewDetail extends ContractReviewSummary {
  pages: ContractPage[];
  extraction: ContractExtractionResult | null;
}

export interface ConfirmationQuestion {
  question_id: string;
  fact_id: string;
  reason: "missing" | "low_confidence" | "no_evidence" | "contradicted" | "ambiguous" | string;
  question_text: string;
  input_type: string;
  required: boolean;
  options: string[];
}

export interface FactConfirmationView {
  fact_id: string;
  field_key: string;
  category: string;
  name: string;
  original_value: unknown;
  normalized_original_value: unknown;
  user_value: unknown;
  effective_value: unknown;
  effective_source: "contract" | "user" | "none" | string;
  confirmation_state: ConfirmationState;
  extraction_status: string;
  confidence: number;
  evidence: ContractEvidence[];
  source_clause_ids: string[];
  question_ids: string[];
  allowed_actions: ConfirmationAction[];
  note: string | null;
}

export interface ContractConfirmationResponse {
  review_id: string;
  confirmation_status: ConfirmationStatus;
  confirmation_revision: number;
  facts: FactConfirmationView[];
  questions: ConfirmationQuestion[];
  unresolved_questions: ConfirmationQuestion[];
  ready_for_legal_review: boolean;
}

export interface FactConfirmationItem {
  fact_id: string;
  action: ConfirmationAction;
  value?: unknown;
  note?: string;
}

export interface FactConfirmationRequest {
  base_revision: number;
  items: FactConfirmationItem[];
  submit: boolean;
  request_id?: string;
}

export interface LegalSource {
  source_level: "A" | "B" | string;
  rule_id?: string | null;
  query: string;
  doc_id: string;
  chunk_id: string;
  title: string;
  source: string;
  rank: number;
  quote: string;
  citation_label: string;
  official_url: string;
  effective_date: string;
  citation_eligible?: boolean | null;
  legal_activation_status: string;
}

export interface ReviewFinding {
  rule_id: string;
  title: string;
  finding_type: string;
  risk_level: RiskLevel;
  summary: string;
  fact_ids: string[];
  legal_references: string[];
  evidence: ContractEvidence[];
  recommendation: string;
  question?: string | null;
}

export interface ContractReviewReport {
  review_id: string;
  report_id?: string | null;
  report_version?: number;
  session_id?: string | null;
  workflow_status: "completed" | "awaiting_confirmation" | "partial" | "out_of_scope" | "failed";
  scope: string;
  generated_at: string;
  findings: ReviewFinding[];
  pending_questions: string[];
  legal_sources: LegalSource[];
  case_sources: LegalSource[];
  warnings: string[];
  disclaimer: string;
}

export interface ContractReviewWorkflowResponse {
  review_id: string;
  workflow_status: ContractReviewReport["workflow_status"];
  report: ContractReviewReport;
}

class ContractApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ContractApiError";
    this.status = status;
  }
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

async function parseError(response: Response): Promise<ContractApiError> {
  let message = `请求失败（${response.status}）`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") message = body.detail;
  } catch {
    // 非 JSON 错误响应使用默认信息。
  }
  return new ContractApiError(message, response.status);
}

async function request<T>(token: string, path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { ...authHeaders(token), ...(init?.headers ?? {}) },
  });
  if (!response.ok) throw await parseError(response);
  return (await response.json()) as T;
}

export async function uploadContract(
  token: string,
  file: File,
  sessionId?: string,
  retentionPolicy: "short" | "long_opt_in" = "short",
): Promise<ContractReviewSummary> {
  const body = new FormData();
  body.append("file", file);
  if (sessionId) body.append("session_id", sessionId);
  body.append("retention_policy", retentionPolicy);
  return request<ContractReviewSummary>(token, "/api/contract-reviews", {
    method: "POST",
    body,
  });
}

export function getContractReport(token: string, reviewId: string): Promise<ContractReviewReport> {
  return request<ContractReviewReport>(
    token,
    `/api/contract-reviews/${encodeURIComponent(reviewId)}/report`,
  );
}

export async function downloadContractReport(token: string, reviewId: string): Promise<Blob> {
  const response = await fetch(
    `/api/contract-reviews/${encodeURIComponent(reviewId)}/report.pdf`,
    { headers: authHeaders(token) },
  );
  if (!response.ok) throw await parseError(response);
  return response.blob();
}

export async function deleteContractReview(token: string, reviewId: string): Promise<void> {
  const response = await fetch(
    `/api/contract-reviews/${encodeURIComponent(reviewId)}`,
    { method: "DELETE", headers: authHeaders(token) },
  );
  if (!response.ok) throw await parseError(response);
}

export function getContractReview(token: string, reviewId: string): Promise<ContractReviewDetail> {
  return request<ContractReviewDetail>(token, `/api/contract-reviews/${encodeURIComponent(reviewId)}`);
}

export function getContractConfirmation(
  token: string,
  reviewId: string,
): Promise<ContractConfirmationResponse> {
  return request<ContractConfirmationResponse>(
    token,
    `/api/contract-reviews/${encodeURIComponent(reviewId)}/confirmation`,
  );
}

export function saveContractConfirmation(
  token: string,
  reviewId: string,
  payload: FactConfirmationRequest,
): Promise<ContractConfirmationResponse> {
  return request<ContractConfirmationResponse>(
    token,
    `/api/contract-reviews/${encodeURIComponent(reviewId)}/confirmation`,
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
}

export function runContractWorkflow(
  token: string,
  reviewId: string,
): Promise<ContractReviewWorkflowResponse> {
  return request<ContractReviewWorkflowResponse>(
    token,
    `/api/contract-reviews/${encodeURIComponent(reviewId)}/workflow`,
    { method: "POST" },
  );
}

export { ContractApiError };
