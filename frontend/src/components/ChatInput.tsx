import { useState, type FormEvent } from "react";

interface ChatInputProps {
  onSend: (text: string) => void;
  isStreaming: boolean;
  onCancel: () => void;
}

export function ChatInput({ onSend, isStreaming, onCancel }: ChatInputProps) {
  const [input, setInput] = useState("");

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    onSend(trimmed);
    setInput("");
  }

  return (
    <form onSubmit={handleSubmit} className="chat-input-shell">
      <span className="chat-input-plus" aria-hidden="true">＋</span>
      <textarea
        value={input}
        onChange={(event) => setInput(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            handleSubmit(event);
          }
        }}
        placeholder="输入消息…"
        rows={1}
        className="chat-input-textarea"
        disabled={isStreaming}
        aria-label="输入消息"
      />
      {isStreaming ? (
        <button type="button" onClick={onCancel} className="chat-send-button stop" aria-label="停止生成">■</button>
      ) : (
        <button type="submit" disabled={!input.trim()} className="chat-send-button" aria-label="发送消息">➤</button>
      )}
      <span className="chat-input-hint">AI 可能会产生错误，请核实重要信息</span>
    </form>
  );
}
