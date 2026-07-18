import { useState, type FormEvent } from "react";
import { useAuth } from "../contexts/AuthContext";

interface Props {
  onSwitchToLogin: () => void;
}

const PWD_RULES = [
  { test: (p: string) => p.length >= 8, label: "至少 8 位" },
  { test: (p: string) => /[A-Z]/.test(p), label: "包含大写字母" },
  { test: (p: string) => /[a-z]/.test(p), label: "包含小写字母" },
  { test: (p: string) => /[0-9]/.test(p), label: "包含数字" },
];

export function RegisterForm({ onSwitchToLogin }: Props) {
  const { register } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const match = password && confirm && password === confirm;
  const passwordValid = PWD_RULES.every((r) => r.test(password));

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!passwordValid) return;
    if (!match) {
      setError("两次密码不一致");
      return;
    }
    setError("");
    setLoading(true);
    try {
      await register(username, password);
      // 注册成功后立即切到登录页，避免 auth 状态更新延迟造成的"卡住"错觉
      onSwitchToLogin();
    } catch (err) {
      setError(err instanceof Error ? err.message : "注册失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-xl font-semibold text-center text-gray-900">注册</h2>
      {error && (
        <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">
          {error}
        </div>
      )}
      <input
        type="text"
        placeholder="用户名（字母、数字、下划线，3-20位）"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        required
        minLength={3}
        maxLength={20}
        pattern="^[a-zA-Z0-9_]+$"
      />
      <div>
        <input
          type="password"
          placeholder="密码"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          required
          minLength={8}
        />
        <div className="mt-1 flex flex-wrap gap-2">
          {PWD_RULES.map((rule) => (
            <span
              key={rule.label}
              className={`text-xs ${
                rule.test(password) ? "text-green-600" : "text-gray-400"
              }`}
            >
              {rule.test(password) ? "✓" : "○"} {rule.label}
            </span>
          ))}
        </div>
      </div>
      <input
        type="password"
        placeholder="确认密码"
        value={confirm}
        onChange={(e) => setConfirm(e.target.value)}
        className={`w-full rounded-lg border px-3 py-2 text-sm focus:outline-none ${
          confirm && !match ? "border-red-400" : "border-gray-300 focus:border-blue-500"
        }`}
        required
      />
      <button
        type="submit"
        disabled={loading || !passwordValid || !match}
        className="w-full rounded-lg bg-blue-600 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors disabled:opacity-50"
      >
        {loading ? "注册中..." : "注册"}
      </button>
      <p className="text-center text-xs text-gray-500">
        已有账号？
        <button
          type="button"
          onClick={onSwitchToLogin}
          className="ml-1 text-blue-600 hover:underline"
        >
          去登录
        </button>
      </p>
    </form>
  );
}
