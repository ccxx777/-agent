import { useCallback, useEffect, useMemo, useState, type DragEvent, type ChangeEvent } from "react";
import { Sidebar } from "./Sidebar";
import type { Conversation } from "./Sidebar";
import { useAuth } from "../contexts/AuthContext";
import {
  ContractApiError,
  deleteContractReview,
  downloadContractReport,
  getContractConfirmation,
  getContractReview,
  getContractReport,
  runContractWorkflow,
  saveContractConfirmation,
  uploadContract,
  type ConfirmationAction,
  type ContractConfirmationResponse,
  type ContractReviewDetail,
  type ContractReviewReport,
  type FactConfirmationItem,
  type FactConfirmationView,
  type ReviewFinding,
} from "../api/contractReviews";

type ContractStage = "upload" | "processing" | "confirmation" | "report";

interface ContractReviewPageProps {
  onOpenChat: () => void;
  onOpenReportChat: (reviewId: string, sessionId: string) => void;
  onReportReady: (report: {
    review_id: string;
    session_id?: string | null;
    filename?: string;
    generated_at?: string;
    updated_at?: string | null;
    created_at?: string | null;
  }) => void;
  conversations: Conversation[];
  activeConversationId: string | null;
  onSelectConversation: (id: string) => void;
  onNewConversation: () => void;
  sessionId: string;
}

type UiFactAction = "confirm" | "edit" | "not_applicable" | "defer";

interface FactDraft {
  action: UiFactAction;
  value: string;
  note: string;
  savedAction: ConfirmationAction;
  savedValue: string;
}

const REVIEW_ID_KEY = "contract_review_last_id";
const ACCEPTED_EXTENSIONS = [".pdf", ".doc", ".docx"];

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未识别";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function compactValue(value: unknown): string {
  const text = valueText(value).replace(/\s+/g, " ");
  return text.length > 140 ? `${text.slice(0, 140)}…` : text;
}

function categoryName(category: string): string {
  const labels: Record<string, string> = {
    parties: "合同主体",
    term: "期限与试用期",
    work: "工作内容",
    compensation: "薪酬与工时",
    benefits: "社保与福利",
    termination: "解除与终止",
    liability: "责任与违约",
    dispute: "争议解决",
  };
  return labels[category] ?? (category || "其他事实");
}

function actionLabel(action: UiFactAction): string {
  return {
    confirm: "确认原文",
    edit: "修改当前采用值",
    not_applicable: "标记不适用",
    defer: "暂不确认",
  }[action];
}

function actionDescription(action: UiFactAction): string {
  return {
    confirm: "提取结果和合同证据一致，确认后会作为已完成事实。",
    edit: "修改后的值会作为新的用户补充保存，合同原值和原始证据仍会保留。",
    not_applicable: "表示本份合同不涉及该事项，不再把它作为本次审查的待办。",
    defer: "表示信息不足或你暂时不确定；该事项会保留为未解决，不能完成审查门禁。",
  }[action];
}

function confidenceLabel(confidence: number): string {
  if (confidence >= 0.9) return "高置信度";
  if (confidence >= 0.7) return "中等置信度";
  return "低置信度";
}

function findingTone(level: ReviewFinding["risk_level"]): string {
  return `risk-${level}`;
}

function availableUiActions(fact: FactConfirmationView): UiFactAction[] {
  const allowed = fact.allowed_actions;
  if (allowed.length === 0) return ["confirm", "edit", "not_applicable", "defer"];
  const actions: UiFactAction[] = [];
  if (allowed.includes("confirm")) actions.push("confirm");
  if (allowed.includes("correct") || allowed.includes("supplement")) actions.push("edit");
  if (allowed.includes("not_applicable")) actions.push("not_applicable");
  if (allowed.includes("defer")) actions.push("defer");
  return actions.length > 0 ? actions : ["defer"];
}

function getDefaultUiAction(fact: FactConfirmationView): UiFactAction {
  const preferred: UiFactAction = fact.confirmation_state === "confirmed"
    ? "confirm"
    : fact.confirmation_state === "corrected" || fact.confirmation_state === "supplemented"
      ? "edit"
      : fact.confirmation_state === "not_applicable"
        ? "not_applicable"
        : "defer";
  const actions = availableUiActions(fact);
  return actions.includes(preferred) ? preferred : actions[0] ?? "defer";
}

function savedApiAction(fact: FactConfirmationView): ConfirmationAction {
  if (fact.confirmation_state === "confirmed") return "confirm";
  if (fact.confirmation_state === "corrected") return "correct";
  if (fact.confirmation_state === "supplemented") return "supplement";
  if (fact.confirmation_state === "not_applicable") return "not_applicable";
  return "defer";
}

function draftForFact(fact: FactConfirmationView): FactDraft {
  const value = valueText(fact.user_value ?? fact.effective_value ?? fact.original_value).replace(/^未识别$/, "");
  return {
    action: getDefaultUiAction(fact),
    value,
    note: fact.note ?? "",
    savedAction: savedApiAction(fact),
    savedValue: value,
  };
}

function toApiAction(draft: FactDraft): ConfirmationAction {
  if (draft.action === "edit") {
    // 没有发生修改时保留后端原动作，避免已保存的用户补充被重标为合同来源。
    if (
      draft.value.trim() === draft.savedValue.trim()
      && (draft.savedAction === "correct" || draft.savedAction === "supplement")
    ) {
      return draft.savedAction;
    }
    // 真正修改的值统一作为用户值提交；合同内证据由后端 EvidenceLocator 决定，
    // 前端不再用“任意页面子串命中”猜测 correct/supplement。
    return "supplement";
  }
  return draft.action;
}

function initialOpenGroups(
  facts: FactConfirmationView[],
  questions: { fact_id: string }[],
): Record<string, boolean> {
  const unresolvedIds = new Set(questions.map((question) => question.fact_id));
  const categories = [...new Set(facts.map((fact) => fact.category))];
  return Object.fromEntries(categories.map((category, index) => [
    category,
    index === 0 || facts.some((fact) => fact.category === category && unresolvedIds.has(fact.fact_id)),
  ]));
}

function isAcceptedFile(file: File): boolean {
  const lower = file.name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((extension) => lower.endsWith(extension));
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function errorMessage(error: unknown): string {
  if (error instanceof ContractApiError) return error.message;
  if (error instanceof Error) return error.message;
  return "暂时无法完成操作，请稍后重试。";
}

function reviewStatusLabel(review: ContractReviewDetail | null): string {
  if (!review) return "等待上传";
  if (review.status === "failed" || review.extraction_status === "failed") return "处理失败";
  if (review.extraction_status === "needs_confirmation") return "等待事实确认";
  if (review.extraction_status === "ready") return "提取完成";
  if (review.status === "extracting" || review.extraction_status === "running") return "正在解析合同";
  return "排队处理中";
}

function stepState(
  step: "upload" | "parse" | "extract" | "confirm",
  review: ContractReviewDetail | null,
): "done" | "current" | "todo" {
  if (step === "upload") return "done";
  if (!review) return "todo";
  if (step === "parse") {
    return review.status === "queued" ? "current" : "done";
  }
  if (step === "extract") {
    if (review.extraction_status === "ready" || review.extraction_status === "needs_confirmation") return "done";
    return "current";
  }
  if (review.extraction_status === "ready" || review.extraction_status === "needs_confirmation") return "current";
  return "todo";
}

function FactCard({
  fact,
  draft,
  focused,
  onChange,
}: {
  fact: FactConfirmationView;
  draft: FactDraft;
  focused: boolean;
  onChange: (patch: Partial<FactDraft>) => void;
}) {
  const actions = availableUiActions(fact);
  const needsValue = draft.action === "edit";
  const serverCompleted = ["confirmed", "corrected", "supplemented", "not_applicable"].includes(fact.confirmation_state);
  const draftReady = draft.action === "edit" && draft.value.trim().length > 0;
  const draftChanged = draft.action !== getDefaultUiAction(fact)
    || (draft.action === "edit" && draft.value.trim() !== draft.savedValue.trim());
  const statusClass = draftChanged ? "pending" : serverCompleted ? "complete" : "unresolved";
  const statusIcon = draftChanged ? "…" : serverCompleted ? "✓" : "×";
  const statusText = draftChanged ? "待提交" : serverCompleted ? "已完成" : "待处理";
  const original = compactValue(fact.original_value);
  const effective = draftReady ? compactValue(draft.value) : compactValue(fact.effective_value);

  return (
    <article id={`fact-card-${fact.fact_id}`} tabIndex={-1} className={`fact-card ${statusClass} ${focused ? "focused" : ""}`}>
      <div className="fact-card-header">
        <div className="fact-title-wrap">
          <span className={`fact-completion-icon ${statusClass}`} aria-label={statusText}>{statusIcon}</span>
          <div>
            <div className="fact-name">{fact.name}</div>
            <div className="fact-key">{fact.field_key}</div>
          </div>
        </div>
        <div className="fact-status-row">
          <span className={`confidence-dot ${fact.confidence >= 0.9 ? "strong" : ""}`} />
          <span className="fact-confidence">{confidenceLabel(fact.confidence)}</span>
        </div>
      </div>

      <div className="fact-values">
        <div>
          <span className="fact-label">合同原值</span>
          <p>{original}</p>
        </div>
        <div>
          <span className="fact-label">当前采用值 {draftReady ? "· 待提交" : ""}</span>
          <p className={fact.effective_source === "user" || draftReady ? "user-value" : ""}>{effective}</p>
        </div>
      </div>

      {fact.evidence.length > 0 ? (
        <details className="fact-evidence">
          <summary>查看证据 · {fact.evidence.length} 条</summary>
          <div className="evidence-list">
            {fact.evidence.map((evidence, index) => (
              <div className="evidence-item" key={`${fact.fact_id}-${index}`}>
                <span>第 {evidence.page_no} 页</span>
                <q>{evidence.quote}</q>
              </div>
            ))}
          </div>
        </details>
      ) : (
        <div className="fact-no-evidence">未找到可定位的合同原文，请修改当前采用值或暂不确认。</div>
      )}

      <div className="fact-action-row">
        <label className="sr-only" htmlFor={`action-${fact.fact_id}`}>事实处理方式</label>
        <select
          id={`action-${fact.fact_id}`}
          value={draft.action}
          onChange={(event) => onChange({ action: event.target.value as UiFactAction })}
        >
          {actions.map((action) => (
            <option key={action} value={action}>{actionLabel(action)}</option>
          ))}
        </select>
        {needsValue && (
          <input
            value={draft.value}
            onChange={(event) => onChange({ value: event.target.value })}
            placeholder="请输入新的当前采用值"
            aria-label={`${fact.name}的新当前采用值`}
          />
        )}
      </div>
      <p className="fact-action-help">{actionDescription(draft.action)}</p>
      <textarea
        className="fact-note"
        value={draft.note}
        onChange={(event) => onChange({ note: event.target.value })}
        placeholder="可选：补充说明（不会覆盖合同原值）"
        rows={1}
      />
    </article>
  );
}

export function ContractReviewPage({
  onOpenChat,
  onOpenReportChat,
  onReportReady,
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  sessionId,
}: ContractReviewPageProps) {
  const { token } = useAuth();
  const [reviewId, setReviewId] = useState<string | null>(() => localStorage.getItem(REVIEW_ID_KEY));
  const [review, setReview] = useState<ContractReviewDetail | null>(null);
  const [confirmation, setConfirmation] = useState<ContractConfirmationResponse | null>(null);
  const [report, setReport] = useState<ContractReviewReport | null>(null);
  const [stage, setStage] = useState<ContractStage>(reviewId ? "processing" : "upload");
  const [drafts, setDrafts] = useState<Record<string, FactDraft>>({});
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [workflowBusy, setWorkflowBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [focusedFactId, setFocusedFactId] = useState<string | null>(null);
  const [focusRequestId, setFocusRequestId] = useState(0);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const [retentionPolicy, setRetentionPolicy] = useState<"short" | "long_opt_in">("short");

  const groupedFacts = useMemo(() => {
    if (!confirmation) return [] as [string, FactConfirmationView[]][];
    const groups = new Map<string, FactConfirmationView[]>();
    confirmation.facts.forEach((fact) => {
      const list = groups.get(fact.category) ?? [];
      list.push(fact);
      groups.set(fact.category, list);
    });
    return [...groups.entries()];
  }, [confirmation]);

  const loadConfirmation = useCallback(async (id: string): Promise<boolean> => {
    if (!token) return false;
    try {
      const data = await getContractConfirmation(token, id);
      setConfirmation(data);
      setDrafts(Object.fromEntries(data.facts.map((fact) => [fact.fact_id, draftForFact(fact)])));
      setOpenGroups(initialOpenGroups(data.facts, data.unresolved_questions));
      setFocusedFactId(null);
      setStage("confirmation");
      return true;
    } catch (requestError) {
      if (requestError instanceof ContractApiError && requestError.status === 409) return false;
      setError(errorMessage(requestError));
      return false;
    }
  }, [token]);

  useEffect(() => {
    if (!token || !reviewId) return;
    let stopped = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const latest = await getContractReview(token, reviewId);
        if (stopped) return;
        setReview(latest);
        if (latest.status === "failed" || latest.extraction_status === "failed") {
          setStage("processing");
          setError(latest.error_message || "合同处理失败，请重新上传。 ");
          return;
        }
        try {
          const persistedReport = await getContractReport(token, reviewId);
          if (!stopped) {
            setReport(persistedReport);
            setStage("report");
            onReportReady({
              review_id: persistedReport.review_id,
              session_id: persistedReport.session_id ?? latest.session_id,
              filename: latest.filename,
              generated_at: persistedReport.generated_at,
              updated_at: latest.updated_at,
              created_at: latest.created_at,
            });
          }
          return;
        } catch (reportError) {
          if (!(reportError instanceof ContractApiError && reportError.status === 404)) {
            // 报告读取失败不阻断上传/确认轮询；下一轮继续尝试恢复。
            if (reportError instanceof ContractApiError && reportError.status >= 500) {
              setNotice("报告恢复暂时不可用，正在重试。");
            }
          }
        }
        if (latest.extraction_status === "ready" || latest.extraction_status === "needs_confirmation") {
          if (await loadConfirmation(reviewId)) return;
        }
        setStage("processing");
        timer = window.setTimeout(poll, 2000);
      } catch (requestError) {
        if (!stopped) {
          setError(errorMessage(requestError));
          timer = window.setTimeout(poll, 4000);
        }
      }
    };

    void poll();
    return () => {
      stopped = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [loadConfirmation, onReportReady, reviewId, token]);

  const beginUpload = useCallback(async (file: File) => {
    if (!token) return;
    setError(null);
    setNotice(null);
    if (!isAcceptedFile(file)) {
      setError("目前支持 PDF、DOC 和 DOCX 文件。请换一个文件后重试。");
      return;
    }
    setBusy(true);
    try {
      const summary = await uploadContract(token, file, sessionId, retentionPolicy);
      localStorage.setItem(REVIEW_ID_KEY, summary.review_id);
      setReviewId(summary.review_id);
      setReview(summary as ContractReviewDetail);
      setConfirmation(null);
      setReport(null);
      setFocusedFactId(null);
      setFocusRequestId(0);
      setOpenGroups({});
      setStage("processing");
      setNotice("文件已安全上传，正在进行解析和事实提取。");
    } catch (uploadError) {
      setError(errorMessage(uploadError));
    } finally {
      setBusy(false);
    }
  }, [retentionPolicy, sessionId, token]);

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) void beginUpload(file);
    event.target.value = "";
  };

  const handleDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (file) void beginUpload(file);
  };

  const updateDraft = (factId: string, patch: Partial<FactDraft>) => {
    setDrafts((current) => ({
      ...current,
      [factId]: { ...current[factId], ...patch },
    }));
  };

  const saveConfirmation = async (submit: boolean) => {
    if (!token || !reviewId || !confirmation) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    const incompleteEdit = confirmation.facts.find((fact) => {
      const draft = drafts[fact.fact_id];
      return draft?.action === "edit" && !draft.value.trim();
    });
    if (incompleteEdit) {
      const focusCategory = incompleteEdit.category;
      setOpenGroups((current) => ({ ...current, [focusCategory]: true }));
      setFocusedFactId(incompleteEdit.fact_id);
      setFocusRequestId((current) => current + 1);
      setError(`请先填写“${incompleteEdit.name}”的当前采用值，或选择“暂不确认”。`);
      setBusy(false);
      return;
    }
    const items: FactConfirmationItem[] = confirmation.facts.map((fact) => {
      const draft = drafts[fact.fact_id] ?? {
        action: "defer" as UiFactAction,
        value: "",
        note: "",
        savedAction: "defer" as ConfirmationAction,
        savedValue: "",
      };
      const item: FactConfirmationItem = {
        fact_id: fact.fact_id,
        action: toApiAction(draft),
      };
      if (draft.action === "edit") item.value = draft.value.trim();
      if (draft.note.trim()) item.note = draft.note.trim();
      return item;
    });
    try {
      const updated = await saveContractConfirmation(token, reviewId, {
        base_revision: confirmation.confirmation_revision,
        items,
        submit,
        request_id: crypto.randomUUID?.(),
      });
      setConfirmation(updated);
      setDrafts(Object.fromEntries(updated.facts.map((fact) => [fact.fact_id, draftForFact(fact)])));
      const nextFocus = updated.unresolved_questions[0]?.fact_id ?? null;
      if (nextFocus) {
        const focusCategory = updated.facts.find((fact) => fact.fact_id === nextFocus)?.category;
        if (focusCategory) setOpenGroups((current) => ({ ...current, [focusCategory]: true }));
      }
      setFocusedFactId(submit ? nextFocus : null);
      if (submit && nextFocus) setFocusRequestId((current) => current + 1);
      setNotice(submit && updated.ready_for_legal_review
        ? "事实已确认，可以开始合同风险审查。"
        : submit && nextFocus
          ? `还有 ${updated.unresolved_questions.length} 项事实待处理，已为你定位到第一项。`
          : "确认进度已保存。 ");
    } catch (saveError) {
      const fallbackFocus = confirmation.unresolved_questions[0]?.fact_id
        ?? null;
      if (fallbackFocus) {
        const focusCategory = confirmation.facts.find((fact) => fact.fact_id === fallbackFocus)?.category;
        if (focusCategory) setOpenGroups((current) => ({ ...current, [focusCategory]: true }));
      }
      setFocusedFactId(fallbackFocus);
      if (fallbackFocus) setFocusRequestId((current) => current + 1);
      setError(errorMessage(saveError));
    } finally {
      setBusy(false);
    }
  };

  const startWorkflow = async () => {
    if (!token || !reviewId || !confirmation?.ready_for_legal_review) return;
    setWorkflowBusy(true);
    setError(null);
    try {
      const response = await runContractWorkflow(token, reviewId);
      setReport(response.report);
      setStage("report");
      onReportReady({
        review_id: response.report.review_id,
        session_id: response.report.session_id ?? review?.session_id,
        filename: review?.filename,
        generated_at: response.report.generated_at,
        updated_at: review?.updated_at,
        created_at: review?.created_at,
      });
    } catch (workflowError) {
      setError(errorMessage(workflowError));
    } finally {
      setWorkflowBusy(false);
    }
  };

  const resetReview = () => {
    localStorage.removeItem(REVIEW_ID_KEY);
    setReviewId(null);
    setReview(null);
    setConfirmation(null);
    setReport(null);
    setDrafts({});
    setFocusedFactId(null);
    setFocusRequestId(0);
    setOpenGroups({});
    setStage("upload");
    setNotice(null);
    setError(null);
  };

  const handleDownloadReport = async () => {
    if (!token || !reviewId) return;
    try {
      const blob = await downloadContractReport(token, reviewId);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `contract-review-${reviewId}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (downloadError) {
      setError(errorMessage(downloadError));
    }
  };

  const handleDeleteReview = async () => {
    if (!token || !reviewId || !window.confirm("确定删除这份合同、报告和相关脱敏数据吗？")) return;
    try {
      await deleteContractReview(token, reviewId);
      resetReview();
      setNotice("合同和报告已删除。");
    } catch (deleteError) {
      setError(errorMessage(deleteError));
    }
  };

  return (
    <div className="flex h-screen min-h-[680px] bg-[var(--color-background)] text-[var(--color-text)]">
      <Sidebar
        conversations={conversations}
        activeId={activeConversationId}
        onSelect={onSelectConversation}
        onNew={onNewConversation}
        activeView="contract"
        onOpenContract={() => undefined}
      />
      <main className="contract-main">
        <header className="contract-topbar">
          <div>
            <div className="eyebrow">WORKSPACE / CONTRACT REVIEW</div>
            <h1>合同风险助手</h1>
          </div>
          <div className="contract-topbar-actions">
            <button type="button" className="ghost-button" onClick={onOpenChat}>返回对话</button>
            {reviewId && <button type="button" className="ghost-button danger-ghost" onClick={resetReview}>重新上传</button>}
          </div>
        </header>

        <div className="contract-content">
          {(notice || error) && (
            <div className={`contract-alert ${error ? "error" : "success"}`} role={error ? "alert" : "status"}>
              <span aria-hidden="true">{error ? "!" : "✓"}</span>
              <span>{error ?? notice}</span>
              {error && <button type="button" onClick={() => setError(null)} aria-label="关闭提示">×</button>}
            </div>
          )}

          {stage === "upload" && (
            <UploadStage
              busy={busy}
              dragging={dragging}
              retentionPolicy={retentionPolicy}
              onRetentionPolicyChange={setRetentionPolicy}
              onChange={handleFileChange}
              onDrop={handleDrop}
              onDrag={setDragging}
            />
          )}

          {stage === "processing" && (
            <ProcessingStage review={review} onReset={resetReview} />
          )}

          {stage === "confirmation" && confirmation && (
            <ConfirmationStage
              review={review}
              confirmation={confirmation}
              groupedFacts={groupedFacts}
              drafts={drafts}
              focusedFactId={focusedFactId}
              focusRequestId={focusRequestId}
              openGroups={openGroups}
              onToggleGroup={(category) => setOpenGroups((current) => ({ ...current, [category]: !current[category] }))}
              busy={busy}
              workflowBusy={workflowBusy}
              onUpdateDraft={updateDraft}
              onSave={saveConfirmation}
              onRunWorkflow={startWorkflow}
            />
          )}

          {stage === "report" && report && (
            <ReportStage
              report={report}
              onBack={() => setStage("confirmation")}
              onOpenChat={() => onOpenReportChat(report.review_id, report.session_id ?? review?.session_id ?? sessionId)}
              onDownload={handleDownloadReport}
              onDelete={handleDeleteReview}
            />
          )}
        </div>
      </main>
    </div>
  );
}

function UploadStage({
  busy,
  dragging,
  retentionPolicy,
  onRetentionPolicyChange,
  onChange,
  onDrop,
  onDrag,
}: {
  busy: boolean;
  dragging: boolean;
  retentionPolicy: "short" | "long_opt_in";
  onRetentionPolicyChange: (value: "short" | "long_opt_in") => void;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
  onDrop: (event: DragEvent<HTMLLabelElement>) => void;
  onDrag: (value: boolean) => void;
}) {
  return (
    <section className="upload-stage">
      <div className="stage-heading">
        <div>
          <div className="stage-kicker">STEP 01 · UPLOAD</div>
          <h2>上传一份劳动合同</h2>
          <p>我们会先提取合同条款与事实，再把需要你确认的内容列成表单。</p>
        </div>
        <div className="privacy-chip"><span aria-hidden="true">◈</span> 脱敏后再送往模型</div>
      </div>

      <label
        className={`contract-dropzone ${dragging ? "dragging" : ""} ${busy ? "disabled" : ""}`}
        onDragOver={(event) => { event.preventDefault(); onDrag(true); }}
        onDragLeave={() => onDrag(false)}
        onDrop={onDrop}
      >
        <input type="file" accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" onChange={onChange} disabled={busy} />
        <div className="upload-icon" aria-hidden="true">↑</div>
        <strong>{busy ? "正在上传…" : "拖拽文件到这里，或点击选择"}</strong>
        <span>支持 PDF、DOC、DOCX · 建议单个文件不超过 20 MB</span>
        <span className="upload-secondary">原始文件仅用于本次审查，不会展示给模型以外的服务。</span>
      </label>

      <label className="retention-choice">
        <input
          type="checkbox"
          checked={retentionPolicy === "long_opt_in"}
          onChange={(event) => onRetentionPolicyChange(event.target.checked ? "long_opt_in" : "short")}
          disabled={busy}
        />
        <span>需要更长时间查看时，选择保留 30 天（默认仅保留 7 天）</span>
      </label>

      <div className="upload-feature-grid">
        <FeatureCard icon="01" title="格式识别" text="兼容原生 PDF、Word 和扫描件，自动选择文本或 OCR 路径。" />
        <FeatureCard icon="02" title="事实提取" text="识别主体、期限、薪酬、工时、社保和解除条款等核心字段。" />
        <FeatureCard icon="03" title="人工确认" text="保留合同原值、用户值与证据来源，不直接覆盖原始提取结果。" />
      </div>
      <p className="upload-disclaimer">目前首版聚焦中国大陆通用劳动合同规则，仅提供参考性意见，不替你决定是否签署。</p>
    </section>
  );
}

function FeatureCard({ icon, title, text }: { icon: string; title: string; text: string }) {
  return (
    <div className="feature-card">
      <span className="feature-icon">{icon}</span>
      <strong>{title}</strong>
      <p>{text}</p>
    </div>
  );
}

function ProcessingStage({ review, onReset }: { review: ContractReviewDetail | null; onReset: () => void }) {
  const steps = [
    ["upload", "文件上传"],
    ["parse", "文档解析"],
    ["extract", "事实提取"],
    ["confirm", "待你确认"],
  ] as const;
  return (
    <section className="processing-stage">
      <div className="stage-heading">
        <div>
          <div className="stage-kicker">PROCESSING</div>
          <h2>{reviewStatusLabel(review)}</h2>
          <p>系统会在后台完成文件质量检查、脱敏和结构化提取。这个过程可能需要几十秒。</p>
        </div>
        <button type="button" className="ghost-button" onClick={onReset}>取消任务</button>
      </div>
      <div className="processing-card">
        <div className="processing-file">
          <div className="file-mark">DOC</div>
          <div className="min-w-0">
            <strong className="truncate">{review?.filename ?? "合同文件"}</strong>
            <span>{review ? `${formatBytes(review.size_bytes)} · ${review.page_count ?? "—"} 页` : "正在读取文件…"}</span>
          </div>
          <span className="processing-pulse" aria-label="处理中" />
        </div>
        <div className="stepper">
          {steps.map(([key, label], index) => {
            const state = stepState(key, review);
            return (
              <div className={`step-item ${state}`} key={key}>
                <div className="step-circle">{state === "done" ? "✓" : index + 1}</div>
                <span>{label}</span>
                {index < steps.length - 1 && <div className="step-line" />}
              </div>
            );
          })}
        </div>
        {review?.quality && (
          <div className="quality-strip">
            <span>文本覆盖率 {(review.quality.text_coverage * 100).toFixed(0)}%</span>
            <span>{review.quality.ocr_pages > 0 ? `OCR ${review.quality.ocr_pages} 页` : "原生文本"}</span>
            {review.quality.needs_confirmation && <span className="warning-text">版式需人工确认</span>}
          </div>
        )}
      </div>
      <p className="processing-footnote">可以离开这个页面，回到合同审查时会继续读取最近一次任务。</p>
    </section>
  );
}

function ConfirmationStage({
  review,
  confirmation,
  groupedFacts,
  drafts,
  focusedFactId,
  focusRequestId,
  openGroups,
  onToggleGroup,
  busy,
  workflowBusy,
  onUpdateDraft,
  onSave,
  onRunWorkflow,
}: {
  review: ContractReviewDetail | null;
  confirmation: ContractConfirmationResponse;
  groupedFacts: [string, FactConfirmationView[]][];
  drafts: Record<string, FactDraft>;
  focusedFactId: string | null;
  focusRequestId: number;
  openGroups: Record<string, boolean>;
  onToggleGroup: (category: string) => void;
  busy: boolean;
  workflowBusy: boolean;
  onUpdateDraft: (factId: string, patch: Partial<FactDraft>) => void;
  onSave: (submit: boolean) => void;
  onRunWorkflow: () => void;
}) {
  const unresolved = confirmation.unresolved_questions.length;
  const allGroupsOpen = groupedFacts.every(([category]) => openGroups[category]);
  useEffect(() => {
    if (!focusedFactId) return;
    const target = document.getElementById(`fact-card-${focusedFactId}`);
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
    target?.focus({ preventScroll: true });
  }, [focusRequestId, focusedFactId]);
  return (
    <section className="confirmation-stage">
      <div className="stage-heading">
        <div>
          <div className="stage-kicker">STEP 03 · CONFIRM FACTS</div>
          <h2>先确认合同事实，再开始风险审查</h2>
          <p>{review?.filename ?? "这份合同"} 已完成结构化提取。请检查高亮项，必要时修改、补充或标记不适用。</p>
        </div>
        <div className={`progress-chip ${unresolved === 0 ? "complete" : ""}`}>
          {unresolved === 0 ? "全部已解决" : `${unresolved} 项待确认`}
        </div>
      </div>

      <div className="confirmation-layout">
        <div className="facts-column">
          <div className="confirmation-toolbar">
            <span>共 {confirmation.facts.length} 项事实 · 绿色勾表示已完成</span>
            <button
              type="button"
              className="group-toggle-all"
              onClick={() => groupedFacts.forEach(([category]) => {
                if (allGroupsOpen) {
                  if (openGroups[category]) onToggleGroup(category);
                } else if (!openGroups[category]) {
                  onToggleGroup(category);
                }
              })}
            >
              {allGroupsOpen ? "全部收起" : "展开全部"}
            </button>
          </div>
          {groupedFacts.map(([category, facts]) => (
            <div className="fact-group" key={category}>
              <div className="fact-group-heading">
                <h3>{categoryName(category)}</h3>
                <button type="button" className="group-category-toggle" onClick={() => onToggleGroup(category)} aria-expanded={Boolean(openGroups[category])} aria-controls={`fact-group-${category}`}>
                  <span>
                    {facts.filter((fact) => !["confirmed", "corrected", "supplemented", "not_applicable"].includes(fact.confirmation_state)).length > 0
                      ? `${facts.filter((fact) => !["confirmed", "corrected", "supplemented", "not_applicable"].includes(fact.confirmation_state)).length} 项待处理`
                      : "全部完成"}
                    <b aria-hidden="true">{openGroups[category] ? "−" : "+"}</b>
                  </span>
                </button>
              </div>
              {openGroups[category] && (
                <div className="facts-grid" id={`fact-group-${category}`}>
                  {facts.map((fact) => (
                    <FactCard
                      key={fact.fact_id}
                      fact={fact}
                      focused={focusedFactId === fact.fact_id}
                      draft={drafts[fact.fact_id] ?? draftForFact(fact)}
                      onChange={(patch) => onUpdateDraft(fact.fact_id, patch)}
                    />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <aside className="confirmation-aside">
          <div className="aside-card question-card">
            <div className="aside-card-heading"><span className="aside-icon">?</span><h3>需要你补充</h3></div>
            {confirmation.unresolved_questions.length === 0 ? (
              <p className="aside-muted">暂时没有未解决的问题。提交后即可运行审查。</p>
            ) : (
              <div className="question-list">
                {confirmation.unresolved_questions.map((question) => (
                  <div className="question-item" key={question.question_id}>
                    <span className="question-reason">{question.reason === "missing" ? "缺失" : "待核对"}</span>
                    <p>{question.question_text}</p>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="aside-card status-guide-card">
            <div className="aside-card-heading"><span className="aside-icon">i</span><h3>状态说明</h3></div>
            <div className="status-guide-row"><span className="guide-pill not-applicable">不适用</span><p>本份合同不涉及该事项，不再作为本次审查待办。</p></div>
            <div className="status-guide-row"><span className="guide-pill deferred">暂不确认</span><p>信息不足或暂时不确定，仍会保留为未解决。</p></div>
          </div>
          <div className="aside-card privacy-card">
            <div className="aside-card-heading"><span className="aside-icon">◈</span><h3>数据边界</h3></div>
            <p>原始合同值会一直保留，用户修改只作为新的事实来源。证据仅展示脱敏后的定位片段。</p>
          </div>
          <div className="confirmation-actions">
            <button type="button" className="secondary-button" disabled={busy} onClick={() => onSave(false)}>
              {busy ? "保存中…" : "保存进度"}
            </button>
            <button type="button" className="primary-button" disabled={busy} onClick={() => onSave(true)}>
              提交确认
            </button>
            <button type="button" className="workflow-button" disabled={!confirmation.ready_for_legal_review || workflowBusy} onClick={onRunWorkflow}>
              {workflowBusy ? "正在生成报告…" : confirmation.ready_for_legal_review ? "开始风险审查 →" : "完成确认后开始审查"}
            </button>
          </div>
        </aside>
      </div>
    </section>
  );
}

function ReportStage({
  report,
  onBack,
  onOpenChat,
  onDownload,
  onDelete,
}: {
  report: ContractReviewReport;
  onBack: () => void;
  onOpenChat: () => void;
  onDownload: () => void;
  onDelete: () => void;
}) {
  const counts = report.findings.reduce<Record<string, number>>((result, finding) => {
    result[finding.risk_level] = (result[finding.risk_level] ?? 0) + 1;
    return result;
  }, {});
  return (
    <section className="report-stage">
      <div className="stage-heading report-heading">
        <div>
          <div className="stage-kicker">STEP 04 · REVIEW REPORT</div>
          <h2>合同风险评估报告</h2>
          <p>报告基于已确认事实与当前可用的全国通用规则，仅供参考，不替代律师意见。</p>
        </div>
        <div className="report-heading-actions">
          <button type="button" className="ghost-button" onClick={onBack}>返回事实表单</button>
          <button type="button" className="secondary-button" onClick={onDownload}>下载 PDF</button>
          <button type="button" className="primary-button" onClick={onOpenChat}>针对报告提问</button>
          <button type="button" className="ghost-button danger-ghost" onClick={onDelete}>删除审查</button>
        </div>
      </div>

      <div className="report-summary-grid">
        <div className="report-summary-main"><span>审查状态</span><strong>{report.workflow_status === "completed" ? "已完成" : report.workflow_status}</strong></div>
        <div><span>高风险</span><strong className="summary-high">{counts.high ?? 0}</strong></div>
        <div><span>中风险</span><strong className="summary-medium">{counts.medium ?? 0}</strong></div>
        <div><span>待确认</span><strong>{counts.unconfirmed ?? 0}</strong></div>
      </div>

      {report.warnings.length > 0 && (
        <div className="report-warnings">
          {report.warnings.map((warning) => <p key={warning}>⚠ {warning}</p>)}
        </div>
      )}

      <div className="findings-list">
        {report.findings.length === 0 ? (
          <div className="empty-report"><span aria-hidden="true">✓</span><h3>暂未发现可展示的风险项</h3><p>这不等于合同不存在风险，请结合原文和专业意见继续核对。</p></div>
        ) : report.findings.map((finding, index) => (
          <FindingCard finding={finding} index={index} key={`${finding.rule_id}-${index}`} />
        ))}
      </div>

      {(report.legal_sources.length > 0 || report.case_sources.length > 0) && (
        <div className="sources-section">
          <div className="fact-group-heading"><h3>依据与来源</h3><span>{report.legal_sources.length + report.case_sources.length} 条</span></div>
          <div className="source-list">
            {[...report.legal_sources, ...report.case_sources].map((source, index) => (
              <div className="source-card" key={`${source.doc_id}-${index}`}>
                <span className={`source-level level-${source.source_level}`}>{source.source_level} 级</span>
                <div><strong>{source.title || source.citation_label || "法律依据"}</strong><p>{source.quote || "暂无摘录"}</p></div>
                {source.official_url && <a href={source.official_url} target="_blank" rel="noreferrer">官方来源 ↗</a>}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="report-disclaimer">{report.disclaimer}</div>
      <div className="report-meta">生成时间：{formatTime(report.generated_at)}</div>
    </section>
  );
}

function FindingCard({ finding, index }: { finding: ReviewFinding; index: number }) {
  return (
    <article className="finding-card">
      <div className="finding-index">{String(index + 1).padStart(2, "0")}</div>
      <div className="finding-body">
        <div className="finding-topline">
          <h3>{finding.title}</h3>
          <span className={`risk-badge ${findingTone(finding.risk_level)}`}>{finding.risk_level}</span>
        </div>
        <p className="finding-summary">{finding.summary}</p>
        {finding.evidence.length > 0 && (
          <div className="finding-evidence"><span>合同证据</span><q>{finding.evidence[0].quote}</q><small>第 {finding.evidence[0].page_no} 页</small></div>
        )}
        {finding.recommendation && <div className="finding-recommendation"><span>建议</span><p>{finding.recommendation}</p></div>}
        {finding.question && <div className="finding-question">待确认：{finding.question}</div>}
      </div>
    </article>
  );
}
