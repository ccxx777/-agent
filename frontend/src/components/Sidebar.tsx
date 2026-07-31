import { useAuth } from "../contexts/AuthContext";

interface Conversation {
  id: string;
  title: string;
}

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  activeView?: "chat" | "contract";
  onOpenContract?: () => void;
}

/**
 * Stitch 风格的应用侧栏。
 *
 * 侧栏只负责导航，不持有合同审查状态；因此聊天和合同页面可以独立演进，
 * 后续接入真实路由时也不需要改变页面内部的数据流。
 */
export function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  activeView = "chat",
  onOpenContract,
}: Props) {
  const { user, logout } = useAuth();

  return (
    <aside className="app-sidebar flex w-[260px] shrink-0 flex-col">
      <div className="p-3">
        <button type="button" onClick={onNew} className="sidebar-new-button w-full">
          <span aria-hidden="true">＋</span> 新建对话
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-2" aria-label="主导航">
        <button
          type="button"
          onClick={onOpenContract}
          className={`sidebar-nav-item ${activeView === "contract" ? "active" : ""}`}
        >
          <span aria-hidden="true">▣</span>
          <span>合同风险审查</span>
          <span className="sidebar-nav-badge">Beta</span>
        </button>

        <div className="sidebar-section-label">最近对话</div>
        {conversations.map((conversation) => (
          <button
            type="button"
            key={conversation.id}
            onClick={() => onSelect(conversation.id)}
            className={`sidebar-nav-item ${conversation.id === activeId ? "active" : ""}`}
          >
            <span className="sidebar-nav-icon" aria-hidden="true">◌</span>
            <span className="truncate">{conversation.title}</span>
          </button>
        ))}
        {conversations.length === 0 && <p className="sidebar-empty">暂无对话</p>}
      </nav>

      <div className="sidebar-footer">
        <div className="sidebar-footer-links">
          <button type="button">▣ 归档</button>
          <button type="button">⚙ 设置</button>
          <button type="button">◐ 亮色/暗色模式</button>
          <button type="button">? 帮助</button>
        </div>
        <div className="sidebar-user">
          <div className="sidebar-avatar" aria-hidden="true">
            {(user?.username ?? "U").slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-[var(--color-text)]">
              {user?.username ?? "未登录"}
            </div>
            <div className="text-xs text-[var(--color-text-muted)]">个人账户</div>
          </div>
          <button type="button" className="sidebar-logout" onClick={logout} aria-label="退出登录">
            ↗
          </button>
        </div>
      </div>
    </aside>
  );
}
