import { useState, type FormEvent } from "react";
import { useAuth } from "../contexts/AuthContext";

interface Props {
  onSwitchToRegister: () => void;
}

export function LoginForm({ onSwitchToRegister }: Props) {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-center text-xl font-semibold text-gray-900">登录</h2>
      {error && <div className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</div>}
      <input
        type="text"
        placeholder="用户名"
        value={username}
        onChange={(event) => setUsername(event.target.value)}
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        required
      />
      <input
        type="password"
        placeholder="密码"
        value={password}
        onChange={(event) => setPassword(event.target.value)}
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        required
      />
      <button
        type="submit"
        disabled={loading}
        className="w-full rounded-lg bg-blue-600 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? "登录中…" : "登录"}
      </button>
      <p className="text-center text-xs text-gray-500">
        还没有账号？
        <button type="button" onClick={onSwitchToRegister} className="ml-1 text-blue-600 hover:underline">
          立即注册
        </button>
      </p>
    </form>
  );
}
