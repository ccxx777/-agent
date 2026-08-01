import { useCallback, useEffect, useRef, useState, Component, type ReactNode } from "react";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { ChatMessage } from "./components/ChatMessage";
import { ChatInput } from "./components/ChatInput";
import { Sidebar, type Conversation } from "./components/Sidebar";
import { LoginForm } from "./components/LoginForm";
import { RegisterForm } from "./components/RegisterForm";
import { ContractReviewPage } from "./components/ContractReviewPage";
import { useChatStream, type Message } from "./hooks/useChatStream";
import { getContractReviewHistory } from "./api/contractReviews";

function generateId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function normalizeConversation(value: unknown): Conversation | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  if (typeof item.id !== "string" || typeof item.title !== "string") return null;
  return {
    id: item.id,
    title: item.title,
    kind: item.kind === "report" ? "report" : "chat",
    ...(typeof item.reviewId === "string" ? { reviewId: item.reviewId } : {}),
    ...(typeof item.sessionId === "string" ? { sessionId: item.sessionId } : {}),
    ...(typeof item.updatedAt === "string" ? { updatedAt: item.updatedAt } : {}),
  };
}

function workspaceStorageKey(userId: string | null | undefined, name: string): string | null {
  if (!userId) return null;
  return `ai:${userId}:${name}`;
}

function loadConversations(storageKey: string | null): Conversation[] {
  if (!storageKey) return [];
  try {
    const raw = JSON.parse(localStorage.getItem(storageKey) || "[]") as unknown;
    return Array.isArray(raw) ? raw.map(normalizeConversation).filter((item): item is Conversation => Boolean(item)) : [];
  } catch {
    return [];
  }
}

function saveConversations(list: Conversation[], storageKey: string | null) {
  if (!storageKey) return;
  localStorage.setItem(storageKey, JSON.stringify(list.slice(0, 50)));
}

type ChatContext = {
  mode: "general" | "legal" | "contract_review";
  reviewId: string | null;
};

function loadStoredChatContext(storageKey: string | null): ChatContext & { sessionId?: string } {
  if (!storageKey) return { mode: "general", reviewId: null };
  try {
    const raw = JSON.parse(localStorage.getItem(storageKey) || "null") as Record<string, unknown> | null;
    if (raw && (raw.mode === "general" || raw.mode === "legal" || raw.mode === "contract_review")) {
      return {
        mode: raw.mode,
        reviewId: typeof raw.reviewId === "string" ? raw.reviewId : null,
        ...(typeof raw.sessionId === "string" ? { sessionId: raw.sessionId } : {}),
      };
    }
  } catch {
    // localStorage may contain an older or partially written value.
  }
  return { mode: "general", reviewId: null };
}

type ReportConversationSource = {
  review_id: string;
  session_id?: string | null;
  filename?: string;
  created_at?: string | null;
  updated_at?: string | null;
  generated_at?: string;
};

function reportConversation(review: ReportConversationSource): Conversation | null {
  const reviewId = review.review_id;
  const sessionId = review.session_id ?? undefined;
  if (!sessionId) return null;
  const filename = review.filename?.trim() || "合同风险报告";
  const updatedAt = review.generated_at ?? review.updated_at ?? review.created_at ?? undefined;
  return {
    id: reviewId,
    sessionId,
    reviewId,
    kind: "report",
    title: `合同风险报告 · ${filename}`,
    ...(updatedAt ? { updatedAt } : {}),
  };
}

function ChatPage({
  onOpenContract,
  sessionId,
  onSessionChange,
  mode,
  reviewId,
  onModeChange,
  conversations,
  onConversationsChange,
  onConversationSelect,
  onReportUnavailable,
}: {
  onOpenContract: (reviewId?: unknown) => void;
  sessionId: string;
  onSessionChange: (sessionId: string) => void;
  mode: "general" | "legal" | "contract_review";
  reviewId: string | null;
  onModeChange: (mode: "general" | "legal", reviewId?: string | null) => void;
  conversations: Conversation[];
  onConversationsChange: (updater: (previous: Conversation[]) => Conversation[]) => void;
  onConversationSelect: (conversation: Conversation) => void;
  onReportUnavailable: (reviewId: string) => void;
}) {
  const { token } = useAuth();
  const [activeId, setActiveId] = useState<string | null>(() => (
    conversations.find((conversation) => (
      (mode === "contract_review" && conversation.reviewId === reviewId)
      || (mode !== "contract_review" && (conversation.sessionId ?? conversation.id) === sessionId)
    ))?.id ?? null
  ));
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyMessages, setHistoryMessages] = useState<Message[]>([]);
  const historyAbortRef = useRef<AbortController | null>(null);
  const historyRequestRef = useRef(0);
  const { messages, sendMessage, isStreaming, cancel } = useChatStream(
    token!,
    sessionId,
    historyMessages,
    mode,
    reviewId,
  );
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (activeId && !conversations.some((conversation) => conversation.id === activeId)) {
      setActiveId(null);
    }
  }, [activeId, conversations]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const loadHistory = useCallback(async (historySessionId: string, historyReviewId?: string) => {
    if (!token) return;
    const requestId = historyRequestRef.current + 1;
    historyRequestRef.current = requestId;
    historyAbortRef.current?.abort();
    const controller = new AbortController();
    historyAbortRef.current = controller;
    setLoadingHistory(true);
    setHistoryMessages([]);
    try {
      const historyPath = historyReviewId
        ? `/api/chat/history/report/${encodeURIComponent(historyReviewId)}`
        : `/api/chat/history/${encodeURIComponent(historySessionId)}`;
      const response = await fetch(historyPath, {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      });
      if (!response.ok) {
        if (response.status === 404 && historyReviewId) {
          setActiveId(null);
          onReportUnavailable(historyReviewId);
        }
        throw new Error(`HTTP ${response.status}`);
      }
      const data = (await response.json()) as { messages?: Message[] };
      const expectedReviewId = mode === "contract_review" ? reviewId ?? undefined : undefined;
      const isCurrent = requestId === historyRequestRef.current
        && historySessionId === sessionId
        && historyReviewId === expectedReviewId;
      if (!isCurrent) return;
      setHistoryMessages(Array.isArray(data.messages) ? data.messages : []);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (requestId === historyRequestRef.current) setHistoryMessages([]);
    } finally {
      if (requestId === historyRequestRef.current) {
        setLoadingHistory(false);
        historyAbortRef.current = null;
      }
    }
  }, [mode, onReportUnavailable, reviewId, sessionId, token]);

  useEffect(() => () => {
    historyRequestRef.current += 1;
    historyAbortRef.current?.abort();
  }, []);

  useEffect(() => {
    historyRequestRef.current += 1;
    historyAbortRef.current?.abort();
    const timer = window.setTimeout(
      () => void loadHistory(sessionId, mode === "contract_review" ? reviewId ?? undefined : undefined),
      0,
    );
    return () => window.clearTimeout(timer);
  }, [loadHistory, mode, reviewId, sessionId]);

  const handleNewChat = useCallback(() => {
    if (activeId && messages.length > 0) {
      onConversationsChange((previous) => {
        if (previous.find((conversation) => conversation.id === activeId)) return previous;
        const title = messages.find((message) => message.role === "user")?.content.slice(0, 30) ?? "新对话";
        return [{ id: activeId, sessionId: activeId, kind: "chat", title }, ...previous];
      });
    }
    onSessionChange(generateId());
    setActiveId(null);
    setHistoryMessages([]);
  }, [activeId, messages, onConversationsChange, onSessionChange]);

  const handleSelect = useCallback((id: string) => {
    setActiveId(id);
    const conversation = conversations.find((item) => item.id === id);
    if (conversation) {
      onConversationSelect(conversation);
      const historySessionId = conversation.sessionId ?? conversation.id;
      if (historySessionId === sessionId) {
        void loadHistory(
          historySessionId,
          conversation.kind === "report" ? conversation.reviewId : undefined,
        );
      }
      return;
    }
    onSessionChange(id);
    void loadHistory(id);
  }, [conversations, loadHistory, onConversationSelect, onSessionChange, sessionId]);

  const handleSend = useCallback((text: string) => {
    if (!activeId) {
      const conversationId = sessionId;
      setActiveId(conversationId);
      onConversationsChange((previous) => [
        { id: conversationId, sessionId: conversationId, kind: "chat", title: text.slice(0, 30) },
        ...previous.filter((conversation) => conversation.id !== conversationId),
      ]);
    }
    sendMessage(text);
  }, [activeId, onConversationsChange, sendMessage, sessionId]);

  const visibleActiveId = activeId ?? conversations.find((conversation) => (
    (mode === "contract_review" && conversation.reviewId === reviewId)
    || (mode !== "contract_review" && (conversation.sessionId ?? conversation.id) === sessionId)
  ))?.id ?? null;

  return (
    <div className="app-layout">
      <Sidebar
        conversations={conversations}
        activeId={visibleActiveId}
        onSelect={handleSelect}
        onNew={handleNewChat}
        activeView="chat"
        onOpenContract={onOpenContract}
      />
      <div className="chat-workspace">
        <header className="chat-header">
          <div className="eyebrow">{mode === "contract_review" ? "CONTRACT REVIEW / REPORT CHAT" : "AI ASSISTANT / GENERAL KNOWLEDGE"}</div>
          <div className="chat-header-actions">
            {mode === "contract_review" ? (
                <span className="chat-mode-chip report-chip">当前合同上下文</span>
            ) : (
              <>
                <button type="button" className={mode === "general" ? "chat-mode-active" : "chat-mode-button"} onClick={() => onModeChange("general")}>通用问答</button>
                <button type="button" className={mode === "legal" ? "chat-mode-active" : "chat-mode-button"} onClick={() => onModeChange("legal")}>法律问答</button>
              </>
            )}
            <span className="connection-status"><span /> 在线</span>
          </div>
        </header>
        <main className="chat-main">
          {mode === "contract_review" && reviewId && messages.length > 0 && (
            <div className="report-chat-inline-header">
              <span className="report-chat-banner-icon" aria-hidden="true">▣</span>
              <div>
                    <strong>当前合同会话</strong>
                    <span>可以继续追问合同正文、事实和风险报告</span>
              </div>
              <button type="button" className="ghost-button" onClick={() => onOpenContract(reviewId)}>查看报告</button>
            </div>
          )}
          {messages.length === 0 ? (
            <div className={`chat-empty-state ${mode === "contract_review" ? "report-empty-state" : ""}`}>
              {mode === "contract_review" && reviewId && (
                <div className="report-chat-banner" role="status">
                  <div className="report-chat-banner-icon" aria-hidden="true">▣</div>
                  <div className="report-chat-banner-copy">
                    <span className="report-chat-eyebrow">当前合同上下文</span>
                    <h1>继续询问这份合同</h1>
                    <p>这条对话与上传前的文字问答共用同一个会话。你可以追问合同正文、结构化事实、风险报告，也可以继续询问法律依据。</p>
                  </div>
                  <button type="button" className="secondary-button" onClick={() => onOpenContract(reviewId)}>查看完整报告</button>
                </div>
              )}
              <div className="chat-logo-mark" aria-hidden="true">✦</div>
              <h1>有什么我可以帮您的吗？</h1>
              <p>我可以检索知识库、分析文档，也可以帮你完成合同风险预审。</p>
              <div className="suggestion-row">
                <button type="button" onClick={onOpenContract}>审查一份劳动合同</button>
                <button type="button" onClick={() => handleSend("帮我总结最近的知识库内容")}>总结知识库内容</button>
                <button type="button" onClick={() => handleSend("解释一下 RAG 检索流程")}>解释 RAG 检索流程</button>
              </div>
              {loadingHistory && <span className="chat-loading">正在加载历史消息…</span>}
            </div>
          ) : (
            <div className="chat-message-list">
              {messages.map((message, index) => <ChatMessage key={`${index}-${message.role}`} message={message} />)}
            </div>
          )}
          <div ref={bottomRef} />
        </main>
        <ChatInput onSend={handleSend} isStreaming={isStreaming} onCancel={cancel} />
      </div>
    </div>
  );
}

function AuthPage() {
  const [mode, setMode] = useState<"login" | "register">("login");
  return (
    <div className="auth-screen">
      <div className="auth-brand"><span>✦</span><strong>合同风险助手</strong></div>
      <div className="auth-card">
        <div className="auth-kicker">PRIVATE WORKSPACE</div>
        {mode === "login" ? (
          <LoginForm onSwitchToRegister={() => setMode("register")} />
        ) : (
          <RegisterForm onSwitchToLogin={() => setMode("login")} />
        )}
      </div>
    </div>
  );
}

function AppInner() {
  const { isAuthenticated, token, user } = useAuth();
  const userId = user?.user_id ?? null;
  const conversationsStorageKey = workspaceStorageKey(userId, "conversations");
  const sessionStorageKey = workspaceStorageKey(userId, "active_session_id");
  const contextStorageKey = workspaceStorageKey(userId, "active_chat_context");
  const viewStorageKey = workspaceStorageKey(userId, "active_view");
  const reviewStorageKey = workspaceStorageKey(userId, "contract_review_last_id");
  const storedContext = loadStoredChatContext(contextStorageKey);
  const storedReviewId = reviewStorageKey ? localStorage.getItem(reviewStorageKey) : null;
  const initialReportId = storedContext.mode === "contract_review"
    && storedContext.reviewId
    && storedContext.reviewId === storedReviewId
    ? storedContext.reviewId
    : null;
  const [conversations, setConversations] = useState<Conversation[]>(() => loadConversations(conversationsStorageKey));
  const [sessionId, setSessionId] = useState<string>(() => {
    const stored = storedContext.sessionId ?? (sessionStorageKey ? localStorage.getItem(sessionStorageKey) : null);
    const value = stored || generateId();
    if (sessionStorageKey) localStorage.setItem(sessionStorageKey, value);
    return value;
  });
  const [chatContext, setChatContext] = useState<ChatContext>(() => ({
    mode: storedContext.mode === "contract_review" && !initialReportId ? "general" : storedContext.mode,
    reviewId: initialReportId,
  }));
  const [view, setView] = useState<"chat" | "contract">(() => {
    if (window.location.pathname.startsWith("/contracts")) return "contract";
    return viewStorageKey && localStorage.getItem(viewStorageKey) === "contract" ? "contract" : "chat";
  });

  const removeReportConversation = useCallback((reviewId: string) => {
    setConversations((previous) => previous.filter(
      (conversation) => !(conversation.kind === "report" && conversation.reviewId === reviewId),
    ));
    const nextContext: ChatContext = { mode: "general", reviewId: null };
    setChatContext((current) => current.reviewId === reviewId ? nextContext : current);
    const storedContext = loadStoredChatContext(contextStorageKey);
    if (storedContext.reviewId === reviewId && contextStorageKey) {
      localStorage.setItem(contextStorageKey, JSON.stringify({ ...nextContext, sessionId }));
    }
    if (reviewStorageKey && localStorage.getItem(reviewStorageKey) === reviewId) {
      localStorage.removeItem(reviewStorageKey);
    }
  }, [contextStorageKey, reviewStorageKey, sessionId]);

  useEffect(() => {
    saveConversations(conversations, conversationsStorageKey);
  }, [conversations, conversationsStorageKey]);

  useEffect(() => {
    if (!isAuthenticated || !token) return;
    let cancelled = false;
    void getContractReviewHistory(token)
      .then((response) => {
        if (cancelled) return;
        const reportItems = response.reviews
          .filter((review) => Boolean(review.report_id))
          .map((review) => reportConversation(review))
          .filter((item): item is Conversation => Boolean(item));
        const serverReportIds = new Set(reportItems.map((item) => item.reviewId));
        setConversations((previous) => {
          const byId = new Map(previous.map((item) => [item.id, item]));
          previous.forEach((item) => {
            if (item.kind === "report" && !serverReportIds.has(item.reviewId)) byId.delete(item.id);
          });
          reportItems.forEach((item) => byId.set(item.id, { ...byId.get(item.id), ...item }));
          return [...byId.values()].sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "")).slice(0, 50);
        });
        if (chatContext.reviewId && !serverReportIds.has(chatContext.reviewId)) {
          removeReportConversation(chatContext.reviewId);
        }
      })
      .catch(() => {
        // 历史接口不可用时仍保留本地最近对话，避免影响当前问答。
      });
    return () => {
      cancelled = true;
    };
  }, [chatContext.reviewId, isAuthenticated, removeReportConversation, token]);

  const switchView = useCallback((next: "chat" | "contract", nextReviewId?: string) => {
    setView(next);
    if (viewStorageKey) localStorage.setItem(viewStorageKey, next);
    if (next === "contract" && reviewStorageKey) {
      if (nextReviewId) localStorage.setItem(reviewStorageKey, nextReviewId);
      else localStorage.removeItem(reviewStorageKey);
    }
    window.history.replaceState({}, "", next === "contract" ? "/contracts" : "/");
  }, [reviewStorageKey, viewStorageKey]);

  const changeSession = useCallback((next: string) => {
    const nextContext: ChatContext = { mode: "general", reviewId: null };
    setSessionId(next);
    setChatContext(nextContext);
    if (sessionStorageKey) localStorage.setItem(sessionStorageKey, next);
    if (contextStorageKey) localStorage.setItem(contextStorageKey, JSON.stringify({ ...nextContext, sessionId: next }));
  }, [contextStorageKey, sessionStorageKey]);

  const openReportChat = useCallback((reviewId: string, reportSessionId: string) => {
    const nextContext: ChatContext = { mode: "contract_review", reviewId };
    setSessionId(reportSessionId);
    setChatContext(nextContext);
    if (sessionStorageKey) localStorage.setItem(sessionStorageKey, reportSessionId);
    if (contextStorageKey) localStorage.setItem(contextStorageKey, JSON.stringify({ ...nextContext, sessionId: reportSessionId }));
    switchView("chat");
  }, [contextStorageKey, sessionStorageKey, switchView]);

  const openConversation = useCallback((conversation: Conversation) => {
    const nextSessionId = conversation.sessionId ?? conversation.id;
    setSessionId(nextSessionId);
    if (sessionStorageKey) localStorage.setItem(sessionStorageKey, nextSessionId);
    if (conversation.kind === "report" && conversation.reviewId) {
      const nextContext: ChatContext = { mode: "contract_review", reviewId: conversation.reviewId };
      setChatContext(nextContext);
      if (contextStorageKey) localStorage.setItem(contextStorageKey, JSON.stringify({ ...nextContext, sessionId: nextSessionId }));
    } else {
      const nextContext: ChatContext = { mode: "general", reviewId: null };
      setChatContext(nextContext);
      if (contextStorageKey) localStorage.setItem(contextStorageKey, JSON.stringify({ ...nextContext, sessionId: nextSessionId }));
    }
  }, [contextStorageKey, sessionStorageKey]);

  const changeChatMode = useCallback((mode: "general" | "legal", reviewId: string | null = null) => {
    const nextContext: ChatContext = { mode, reviewId };
    setChatContext(nextContext);
    if (contextStorageKey) localStorage.setItem(contextStorageKey, JSON.stringify({ ...nextContext, sessionId }));
  }, [contextStorageKey, sessionId]);

  const upsertConversation = useCallback((conversation: Conversation) => {
    setConversations((previous) => [
      conversation,
      ...previous.filter((item) => item.id !== conversation.id),
    ].slice(0, 50));
  }, []);

  const openContract = useCallback((reviewId?: unknown) => {
    const nextReviewId = typeof reviewId === "string" && reviewId.trim() ? reviewId : undefined;
    if (!nextReviewId) {
      const nextContext: ChatContext = { mode: "general", reviewId: null };
      setChatContext(nextContext);
      if (contextStorageKey) {
        localStorage.setItem(contextStorageKey, JSON.stringify({ ...nextContext, sessionId }));
      }
    }
    switchView("contract", nextReviewId);
  }, [contextStorageKey, sessionId, switchView]);

  const resetReportContext = useCallback(() => {
    const nextContext: ChatContext = { mode: "general", reviewId: null };
    setChatContext(nextContext);
    if (contextStorageKey) {
      localStorage.setItem(contextStorageKey, JSON.stringify({ ...nextContext, sessionId }));
    }
  }, [contextStorageKey, sessionId]);

  const handleReportReady = useCallback((review: ReportConversationSource) => {
    const item = reportConversation(review);
    if (item) upsertConversation(item);
    // 报告生成后立即把当前会话切换到合同上下文；用户返回聊天时，
    // 仍使用上传前的 session_id，而不是创建第二条 report thread。
    if (review.review_id && review.session_id) {
      const nextContext: ChatContext = {
        mode: "contract_review",
        reviewId: review.review_id,
      };
      setSessionId(review.session_id);
      setChatContext(nextContext);
      if (sessionStorageKey) localStorage.setItem(sessionStorageKey, review.session_id);
      if (contextStorageKey) {
        localStorage.setItem(
          contextStorageKey,
          JSON.stringify({ ...nextContext, sessionId: review.session_id }),
        );
      }
    }
  }, [contextStorageKey, sessionStorageKey, upsertConversation]);

  const selectConversationFromContract = useCallback((id: string) => {
    const conversation = conversations.find((item) => item.id === id);
    if (!conversation) return;
    openConversation(conversation);
    switchView("chat");
  }, [conversations, openConversation, switchView]);

  const startConversationFromContract = useCallback(() => {
    changeSession(generateId());
    switchView("chat");
  }, [changeSession, switchView]);

  if (!isAuthenticated) return <AuthPage />;
  if (view === "contract") {
    return (
      <ContractReviewPage
        sessionId={sessionId}
        onOpenChat={() => switchView("chat")}
        onResetReportContext={resetReportContext}
        onRemoveConversation={removeReportConversation}
        onOpenReportChat={openReportChat}
        onReportReady={handleReportReady}
        conversations={conversations}
        activeConversationId={chatContext.reviewId
          ? conversations.find((item) => item.reviewId === chatContext.reviewId)?.id ?? null
          : conversations.find((item) => (item.sessionId ?? item.id) === sessionId)?.id ?? null}
        onSelectConversation={selectConversationFromContract}
        onNewConversation={startConversationFromContract}
        reviewStorageKey={reviewStorageKey ?? ""}
      />
    );
  }
  return (
    <ChatPage
      sessionId={sessionId}
      onSessionChange={changeSession}
      mode={chatContext.mode}
      reviewId={chatContext.reviewId}
      onModeChange={changeChatMode}
      onOpenContract={openContract}
      conversations={conversations}
      onConversationsChange={(updater) => setConversations(updater)}
      onConversationSelect={openConversation}
      onReportUnavailable={removeReportConversation}
    />
  );
}

class ErrorBoundary extends Component<{ children: ReactNode }, { hasError: boolean }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError() {
    return { hasError: true };
  }

  render() {
    if (this.state.hasError) {
      return <div className="app-error">页面加载异常，请刷新后重试。</div>;
    }
    return this.props.children;
  }
}

function UserScopedApp() {
  const { user } = useAuth();
  return <AppInner key={user?.user_id ?? "logged-out"} />;
}

export default function App() {
  return (
    <AuthProvider>
      <ErrorBoundary><UserScopedApp /></ErrorBoundary>
    </AuthProvider>
  );
}
