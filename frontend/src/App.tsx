import { useEffect, useRef, useState, useCallback, Component, type ReactNode } from "react";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { ChatMessage } from "./components/ChatMessage";
import { ChatInput } from "./components/ChatInput";
import { Sidebar } from "./components/Sidebar";
import { LoginForm } from "./components/LoginForm";
import { RegisterForm } from "./components/RegisterForm";
import { useChatStream, type Message } from "./hooks/useChatStream";

function generateId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // HTTP 环境降级
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

function loadConversations(): { id: string; title: string }[] {
  try {
    return JSON.parse(localStorage.getItem("ai_conversations") || "[]");
  } catch {
    return [];
  }
}

function saveConversations(list: { id: string; title: string }[]) {
  localStorage.setItem("ai_conversations", JSON.stringify(list.slice(0, 50)));
}

/** 对话页面（已登录） */
function ChatPage() {
  const { token } = useAuth();
  const [conversations, setConversations] = useState<{ id: string; title: string }[]>(
    loadConversations,
  );
  const [activeId, setActiveId] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string>(generateId());
  const [loadingHistory, setLoadingHistory] = useState(false);
  // 从历史恢复的消息（首次渲染传入 useChatStream）
  const [historyMessages, setHistoryMessages] = useState<Message[]>([]);
  const { messages, sendMessage, isStreaming, cancel } = useChatStream(
    token!,
    sessionId,
    historyMessages,
  );
  const bottomRef = useRef<HTMLDivElement>(null);

  // 自动滚动
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // 对话列表变化时持久化
  useEffect(() => {
    saveConversations(conversations);
  }, [conversations]);

  const handleNewChat = useCallback(() => {
    if (activeId && messages.length > 0) {
      setConversations((prev) => {
        if (prev.find((c) => c.id === activeId)) return prev;
        const title = messages.find((m) => m.role === "user")?.content.slice(0, 30) ?? "新对话";
        return [{ id: activeId, title }, ...prev];
      });
    }
    const newId = generateId();
    setSessionId(newId);
    setActiveId(null);
    setHistoryMessages([]);
  }, [activeId, messages]);

  const handleSelect = useCallback(
    async (id: string) => {
      setActiveId(id);
      setLoadingHistory(true);
      try {
        const res = await fetch(
          `/api/chat/history?session_id=${encodeURIComponent(id)}`,
          { headers: { Authorization: `Bearer ${token}` } },
        );
        const data = await res.json();
        setHistoryMessages(data.messages || []);
        setSessionId(id);
      } catch {
        setHistoryMessages([]);
        setSessionId(id);
      } finally {
        setLoadingHistory(false);
      }
    },
    [token],
  );

  // 发送时自动创建对话记录
  const handleSend = useCallback(
    (text: string) => {
      if (!activeId) {
        const convId = sessionId;
        setActiveId(convId);
        setConversations((prev) => [{ id: convId, title: text.slice(0, 30) }, ...prev]);
      }
      sendMessage(text);
    },
    [sendMessage, activeId, sessionId],
  );

  return (
    <div className="flex h-screen bg-white">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={handleSelect}
        onNew={handleNewChat}
      />
      <div className="flex flex-1 flex-col">
        {/* 顶部 */}
        <header className="border-b border-gray-200 px-4 py-3 text-center text-sm text-gray-500">
          AI 研究助手
        </header>

        {/* 消息区 */}
        <main className="flex-1 overflow-y-auto px-4 py-6">
          {messages.length === 0 ? (
            <div className="flex h-full items-center justify-center text-gray-400">
              {loadingHistory ? "加载历史消息..." : "开始一段新对话"}
            </div>
          ) : (
            <div className="mx-auto flex max-w-2xl flex-col gap-4">
              {messages.map((msg, i) => (
                <ChatMessage key={i} message={msg} />
              ))}
            </div>
          )}
          <div ref={bottomRef} />
        </main>

        {/* 输入框 */}
        <ChatInput onSend={handleSend} isStreaming={isStreaming} onCancel={cancel} />
      </div>
    </div>
  );
}

/** 认证页面（未登录） */
function AuthPage() {
  const [mode, setMode] = useState<"login" | "register">("login");

  return (
    <div className="flex h-screen items-center justify-center bg-gray-50">
      <div className="w-full max-w-sm rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
        {mode === "login" ? (
          <LoginForm onSwitchToRegister={() => setMode("register")} />
        ) : (
          <RegisterForm onSwitchToLogin={() => setMode("login")} />
        )}
      </div>
    </div>
  );
}

/** 根组件 — 根据登录状态分流 */
function AppInner() {
  const { isAuthenticated } = useAuth();

  if (!isAuthenticated) return <AuthPage />;
  return <ChatPage />;
}

/** 兜底：React 渲染崩溃时不白屏 */
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
      return (
        <div className="flex h-screen items-center justify-center text-gray-500">
          页面加载异常，请清除浏览器缓存后刷新重试
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  return (
    <AuthProvider>
      <ErrorBoundary>
        <AppInner />
      </ErrorBoundary>
    </AuthProvider>
  );
}
