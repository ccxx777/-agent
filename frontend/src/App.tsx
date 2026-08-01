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

function loadConversations(): Conversation[] {
  try {
    const raw = JSON.parse(localStorage.getItem("ai_conversations") || "[]") as unknown;
    return Array.isArray(raw) ? raw.map(normalizeConversation).filter((item): item is Conversation => Boolean(item)) : [];
  } catch {
    return [];
  }
}

function saveConversations(list: Conversation[]) {
  localStorage.setItem("ai_conversations", JSON.stringify(list.slice(0, 50)));
}

type ChatContext = {
  mode: "general" | "legal" | "contract_review";
  reviewId: string | null;
};

function loadStoredChatContext(): ChatContext & { sessionId?: string } {
  try {
    const raw = JSON.parse(localStorage.getItem("ai_active_chat_context") || "null") as Record<string, unknown> | null;
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
  const { messages, sendMessage, isStreaming, cancel } = useChatStream(
    token!,
    sessionId,
    historyMessages,
    mode,
    reviewId,
  );
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const loadHistory = useCallback(async (historySessionId: string) => {
    if (!token) return;
    setLoadingHistory(true);
    try {
      const response = await fetch(`/api/chat/history/${encodeURIComponent(historySessionId)}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = (await response.json()) as { messages?: Message[] };
      setHistoryMessages(Array.isArray(data.messages) ? data.messages : []);
    } catch {
      setHistoryMessages([]);
    } finally {
      setLoadingHistory(false);
    }
  }, [token]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadHistory(sessionId), 0);
    return () => window.clearTimeout(timer);
  }, [loadHistory, sessionId]);

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
      if (historySessionId === sessionId) void loadHistory(historySessionId);
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
                <span className="chat-mode-chip report-chip">报告专属会话</span>
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
                <strong>当前报告会话</strong>
                <span>针对本次合同风险报告提问</span>
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
                    <span className="report-chat-eyebrow">当前报告会话</span>
                    <h1>针对这份合同风险报告提问</h1>
                    <p>这条对话已经绑定到本次审查报告。你可以继续追问报告中的风险事实、法律依据和修改建议。</p>
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
  const { isAuthenticated, token } = useAuth();
  const [conversations, setConversations] = useState<Conversation[]>(loadConversations);
  const storedContext = loadStoredChatContext();
  const [sessionId, setSessionId] = useState<string>(() => {
    const stored = storedContext.sessionId ?? localStorage.getItem("ai_active_session_id");
    const value = stored || generateId();
    localStorage.setItem("ai_active_session_id", value);
    return value;
  });
  const [chatContext, setChatContext] = useState<ChatContext>(() => ({
    mode: storedContext.mode,
    reviewId: storedContext.reviewId,
  }));
  const [view, setView] = useState<"chat" | "contract">(() => {
    if (window.location.pathname.startsWith("/contracts")) return "contract";
    return localStorage.getItem("ai_active_view") === "contract" ? "contract" : "chat";
  });

  useEffect(() => {
    saveConversations(conversations);
  }, [conversations]);

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
        setConversations((previous) => {
          const byId = new Map(previous.map((item) => [item.id, item]));
          reportItems.forEach((item) => byId.set(item.id, { ...byId.get(item.id), ...item }));
          return [...byId.values()].sort((a, b) => (b.updatedAt ?? "").localeCompare(a.updatedAt ?? "")).slice(0, 50);
        });
      })
      .catch(() => {
        // 历史接口不可用时仍保留本地最近对话，避免影响当前问答。
      });
    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, token]);

  const switchView = useCallback((next: "chat" | "contract", nextReviewId?: string) => {
    setView(next);
    localStorage.setItem("ai_active_view", next);
    if (nextReviewId) localStorage.setItem("contract_review_last_id", nextReviewId);
    window.history.replaceState({}, "", next === "contract" ? "/contracts" : "/");
  }, []);

  const changeSession = useCallback((next: string) => {
    const nextContext: ChatContext = { mode: "general", reviewId: null };
    setSessionId(next);
    setChatContext(nextContext);
    localStorage.setItem("ai_active_session_id", next);
    localStorage.setItem("ai_active_chat_context", JSON.stringify({ ...nextContext, sessionId: next }));
  }, []);

  const openReportChat = useCallback((reviewId: string, reportSessionId: string) => {
    const nextContext: ChatContext = { mode: "contract_review", reviewId };
    setSessionId(reportSessionId);
    setChatContext(nextContext);
    localStorage.setItem("ai_active_session_id", reportSessionId);
    localStorage.setItem("ai_active_chat_context", JSON.stringify({ ...nextContext, sessionId: reportSessionId }));
    switchView("chat");
  }, [switchView]);

  const openConversation = useCallback((conversation: Conversation) => {
    const nextSessionId = conversation.sessionId ?? conversation.id;
    setSessionId(nextSessionId);
    localStorage.setItem("ai_active_session_id", nextSessionId);
    if (conversation.kind === "report" && conversation.reviewId) {
      const nextContext: ChatContext = { mode: "contract_review", reviewId: conversation.reviewId };
      setChatContext(nextContext);
      localStorage.setItem("ai_active_chat_context", JSON.stringify({ ...nextContext, sessionId: nextSessionId }));
    } else {
      const nextContext: ChatContext = { mode: "general", reviewId: null };
      setChatContext(nextContext);
      localStorage.setItem("ai_active_chat_context", JSON.stringify({ ...nextContext, sessionId: nextSessionId }));
    }
  }, []);

  const changeChatMode = useCallback((mode: "general" | "legal", reviewId: string | null = null) => {
    const nextContext: ChatContext = { mode, reviewId };
    setChatContext(nextContext);
    localStorage.setItem("ai_active_chat_context", JSON.stringify({ ...nextContext, sessionId }));
  }, [sessionId]);

  const upsertConversation = useCallback((conversation: Conversation) => {
    setConversations((previous) => [
      conversation,
      ...previous.filter((item) => item.id !== conversation.id),
    ].slice(0, 50));
  }, []);

  const openContract = useCallback((reviewId?: unknown) => {
    switchView("contract", typeof reviewId === "string" ? reviewId : undefined);
  }, [switchView]);

  const handleReportReady = useCallback((review: ReportConversationSource) => {
    const item = reportConversation(review);
    if (item) upsertConversation(item);
  }, [upsertConversation]);

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
        onOpenReportChat={openReportChat}
        onReportReady={handleReportReady}
        conversations={conversations}
        activeConversationId={chatContext.reviewId
          ? conversations.find((item) => item.reviewId === chatContext.reviewId)?.id ?? null
          : conversations.find((item) => (item.sessionId ?? item.id) === sessionId)?.id ?? null}
        onSelectConversation={selectConversationFromContract}
        onNewConversation={startConversationFromContract}
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

export default function App() {
  return (
    <AuthProvider>
      <ErrorBoundary><AppInner /></ErrorBoundary>
    </AuthProvider>
  );
}
