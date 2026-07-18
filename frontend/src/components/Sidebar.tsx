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
}

export function Sidebar({ conversations, activeId, onSelect, onNew }: Props) {
  const { user, logout } = useAuth();

  return (
    <aside className="flex w-[260px] shrink-0 flex-col border-r border-gray-200 bg-gray-50">
      {/* 新对话按钮 */}
      <div className="p-3">
        <button
          onClick={onNew}
          className="w-full rounded-lg border border-gray-300 bg-white py-2 text-sm text-gray-700 hover:bg-gray-100 transition-colors"
        >
          + 新对话
        </button>
      </div>

      {/* 对话列表 */}
      <nav className="flex-1 overflow-y-auto px-2">
        {conversations.map((c) => (
          <button
            key={c.id}
            onClick={() => onSelect(c.id)}
            className={`w-full truncate rounded-lg px-3 py-2 text-left text-sm transition-colors ${
              c.id === activeId
                ? "bg-blue-100 text-blue-700"
                : "text-gray-700 hover:bg-gray-200"
            }`}
          >
            {c.title}
          </button>
        ))}
        {conversations.length === 0 && (
          <p className="px-3 py-4 text-center text-xs text-gray-400">
            暂无对话
          </p>
        )}
      </nav>

      {/* 用户区 */}
      <div className="border-t border-gray-200 p-3">
        <div className="flex items-center justify-between">
          <span className="truncate text-sm text-gray-700">
            {user?.username ?? "未登录"}
          </span>
          <button
            onClick={logout}
            className="text-xs text-gray-400 hover:text-red-500 transition-colors"
          >
            退出
          </button>
        </div>
      </div>
    </aside>
  );
}
