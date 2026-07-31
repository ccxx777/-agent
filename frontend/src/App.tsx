import { useCallback, useEffect, useRef, useState, Component, type ReactNode } from "react";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { ChatMessage } from "./components/ChatMessage";
import { ChatInput } from "./components/ChatInput";
import { Sidebar } from "./components/Sidebar";
import { LoginForm } from "./components/LoginForm";
import { RegisterForm } from "./components/RegisterForm";
import { ContractReviewPage } from "./components/ContractReviewPage";
import { useChatStream, type Message } from "./hooks/useChatStream";

function generateId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function loadConversations(): { id: string; title: string }[] {
  try {
    return JSON.parse(localStorage.getItem("ai_conversations") || "[]") as { id: string; title: string }[];
  } catch {
    return [];
  }
}

function saveConversations(list: { id: string; title: string }[]) {
  localStorage.setItem("ai_conversations", JSON.stringify(list.slice(0, 50)));
}

function ChatPage({ onOpenContract }: { onOpenContract: () => void }) {
  const { token } = useAuth();
  const [conversations, setConversations] = useState<{ id: string; title: string }[]>(loadConversations);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string>(generateId());
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [historyMessages, setHistoryMessages] = useState<Message[]>([]);
  const { messages, sendMessage, isStreaming, cancel } = useChatStream(token!, sessionId, historyMessages);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    saveConversations(conversations);
  }, [conversations]);

  const handleNewChat = useCallback(() => {
    if (activeId && messages.length > 0) {
      setConversations((previous) => {
        if (previous.find((conversation) => conversation.id === activeId)) return previous;
        const title = messages.find((message) => message.role === "user")?.content.slice(0, 30) ?? "新对话";
        return [{ id: activeId, title }, ...previous];
      });
    }
    setSessionId(generateId());
    setActiveId(null);
    setHistoryMessages([]);
  }, [activeId, messages]);

  const handleSelect = useCallback(async (id: string) => {
    setActiveId(id);
    setLoadingHistory(true);
    try {
      const response = await fetch(`/api/chat/history?session_id=${encodeURIComponent(id)}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      const data = (await response.json()) as { messages?: Message[] };
      setHistoryMessages(data.messages || []);
      setSessionId(id);
    } catch {
      setHistoryMessages([]);
      setSessionId(id);
    } finally {
      setLoadingHistory(false);
    }
  }, [token]);

  const handleSend = useCallback((text: string) => {
    if (!activeId) {
      const conversationId = sessionId;
      setActiveId(conversationId);
      setConversations((previous) => [{ id: conversationId, title: text.slice(0, 30) }, ...previous]);
    }
    sendMessage(text);
  }, [activeId, sendMessage, sessionId]);

  return (
    <div className="app-layout">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={handleSelect}
        onNew={handleNewChat}
        activeView="chat"
        onOpenContract={onOpenContract}
      />
      <div className="chat-workspace">
        <header className="chat-header">
          <div className="eyebrow">AI ASSISTANT / GENERAL KNOWLEDGE</div>
          <span className="connection-status"><span /> 在线</span>
        </header>
        <main className="chat-main">
          {messages.length === 0 ? (
            <div className="chat-empty-state">
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
  const { isAuthenticated } = useAuth();
  const [view, setView] = useState<"chat" | "contract">(() => (
    window.location.pathname.startsWith("/contracts") ? "contract" : "chat"
  ));

  const switchView = (next: "chat" | "contract") => {
    setView(next);
    window.history.replaceState({}, "", next === "contract" ? "/contracts" : "/");
  };

  if (!isAuthenticated) return <AuthPage />;
  if (view === "contract") return <ContractReviewPage onOpenChat={() => switchView("chat")} />;
  return <ChatPage onOpenContract={() => switchView("contract")} />;
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
