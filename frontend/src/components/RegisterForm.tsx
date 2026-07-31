import { useState, type FormEvent } from "react";
import { useAuth } from "../contexts/AuthContext";

interface Props {
  onSwitchToLogin: () => void;
}

const PASSWORD_RULES = [
  { test: (value: string) => value.length >= 8, label: "至少 8 位" },
  { test: (value: string) => /[A-Z]/.test(value), label: "包含大写字母" },
  { test: (value: string) => /[a-z]/.test(value), label: "包含小写字母" },
  { test: (value: string) => /[0-9]/.test(value), label: "包含数字" },
];

export function RegisterForm({ onSwitchToLogin }: Props) {
  const { register } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const matches = password !== "" && password === confirm;
  const passwordValid = PASSWORD_RULES.every((rule) => rule.test(password));

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!passwordValid) return;
    if (!matches) {
      setError("两次密码不一致");
      return;
    }
    setError("");
    setLoading(true);
    try {
      await register(username, password);
      onSwitchToLogin();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "注册失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <h2 className="text-center text-xl font-semibold text-gray-900">注册</h2>
      {error && <div className="rounded-lg bg-red-950/50 px-3 py-2 text-sm text-red-300">{error}</div>}
      <input
        type="text"
        placeholder="用户名（字母、数字、下划线，3-20 位）"
        value={username}
        onChange={(event) => setUsername(event.target.value)}
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
          onChange={(event) => setPassword(event.target.value)}
          className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          required
          minLength={8}
        />
        <div className="mt-1 flex flex-wrap gap-2">
          {PASSWORD_RULES.map((rule) => (
            <span key={rule.label} className={`text-xs ${rule.test(password) ? "text-green-400" : "text-gray-400"}`}>
              {rule.test(password) ? "✓" : "○"} {rule.label}
            </span>
          ))}
        </div>
      </div>
      <input
        type="password"
        placeholder="确认密码"
        value={confirm}
        onChange={(event) => setConfirm(event.target.value)}
        className={`w-full rounded-lg border px-3 py-2 text-sm focus:outline-none ${confirm && !matches ? "border-red-400" : "border-gray-300 focus:border-blue-500"}`}
        required
      />
      <button
        type="submit"
        disabled={loading || !passwordValid || !matches}
        className="w-full rounded-lg bg-blue-600 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
      >
        {loading ? "注册中…" : "注册"}
      </button>
      <p className="text-center text-xs text-gray-500">
        已有账号？
        <button type="button" onClick={onSwitchToLogin} className="ml-1 text-blue-600 hover:underline">
          去登录
        </button>
      </p>
    </form>
  );
}
