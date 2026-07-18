import { useState, type FormEvent } from "react";

interface ChatInputProps {
  onSend: (text: string) => void;
  isStreaming: boolean;
  onCancel: () => void;
}

export function ChatInput({ onSend, isStreaming, onCancel }: ChatInputProps) {
  const [input, setInput] = useState("");

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    onSend(trimmed);
    setInput("");
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="flex items-end gap-2 border-t border-gray-200 bg-white p-4"
    >
      <textarea
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSubmit(e);
          }
        }}
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        rows={2}
        className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
        disabled={isStreaming}
      />
      {isStreaming ? (
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg bg-red-500 px-4 py-2 text-sm font-medium text-white hover:bg-red-600 transition-colors"
        >
          停止
        </button>
      ) : (
        <button
          type="submit"
          disabled={!input.trim()}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 transition-colors disabled:opacity-40"
        >
          发送
        </button>
      )}
    </form>
  );
}
