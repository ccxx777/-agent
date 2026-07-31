import type { Message } from "../hooks/useChatStream";

interface ChatMessageProps {
  message: Message;
}

export function ChatMessage({ message }: ChatMessageProps) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[75%] rounded-lg px-4 py-2 text-sm leading-relaxed ${isUser ? "bg-blue-600 text-white" : "bg-gray-100 text-gray-900"}`}>
        {message.content || (
          <span className="inline-flex items-center gap-1 text-gray-400" aria-label="正在生成">
            <span className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-current" />
            <span className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:0.15s]" />
            <span className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:0.3s]" />
          </span>
        )}
      </div>
    </div>
  );
}
