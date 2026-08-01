import { useCallback, useRef, useState, useEffect } from "react";

/** 单条消息 */
export interface Message {
  role: "user" | "assistant";
  content: string;
}

/** SSE 解析器内部状态 */
interface SSEDecoder {
  buffer: string;
}

/**
 * 增量 SSE 帧解析器。
 *
 * 工作方式：
 * 1. 每次收到数据块，追加到 buffer
 * 2. 按 \n\n 切分完整帧
 * 3. 完整帧 → 去掉 "data: " 前缀 → JSON.parse → 回调
 * 4. 不完整帧 → 留在 buffer，等下一块到达后拼接
 *
 * 这样就解决了"流式 JSON 断断续续到达时如何解析"的问题——
 * SSE 用 \n\n 作为帧边界，JSON 内部即使有 \n 也会被转义为 \\n，
 * 所以双换行分割永远不会在 JSON 字符串内部被误触发。
 */
function createSSEReader(
  onEvent: (event: Record<string, unknown>) => void,
): { push: (chunk: string) => void; reset: () => void } {
  const state: SSEDecoder = { buffer: "" };

  function push(chunk: string) {
    state.buffer += chunk;

    // 持续切分，直到 buffer 中没有完整的帧为止
    while (true) {
      const idx = state.buffer.indexOf("\n\n");
      if (idx === -1) break; // 没有完整帧，等待更多数据

      const raw = state.buffer.slice(0, idx);
      state.buffer = state.buffer.slice(idx + 2);

      // 去掉 "data: " 前缀后解析 JSON
      const dataLine = raw
        .split("\n")
        .find((line) => line.startsWith("data: "));
      if (!dataLine) continue;

      try {
        const parsed = JSON.parse(dataLine.slice(6)) as Record<string, unknown>;
        onEvent(parsed);
      } catch {
        // 非 JSON 行（如注释 :keepalive），忽略
      }
    }
  }

  function reset() {
    state.buffer = "";
  }

  return { push, reset };
}

/**
 * useChatStream — 连接 POST /api/chat/stream 的自定义 Hook。
 *
 * 为什么不用 EventSource？
 * EventSource 只支持 GET，而我们后端是 POST 端点（需要携带 user_id、
 * session_id、message），所以用 fetch + ReadableStream 替代。
 *
 * 返回：
 * - messages: 完整消息列表
 * - sendMessage: 发送消息并开始流式接收
 * - isStreaming: 是否正在接收回复
 */
export function useChatStream(
  token: string,
  sessionId: string,
  initialMessages: Message[] = [],
  mode: "general" | "legal" | "contract_review" = "general",
  reviewId: string | null = null,
) {
  const [messages, setMessages] = useState<Message[]>(initialMessages);
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // sessionId 变化时重置（切换/新建对话）
  useEffect(() => {
    // 聊天会话切换时必须同步清空当前 UI；这是该 Hook 的状态重置边界。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setMessages(initialMessages);
    setIsStreaming(false);
    abortRef.current?.abort();
  }, [initialMessages, sessionId]);

  const sendMessage = useCallback(
    async (text: string) => {
      // 1. 用户消息立即加入列表
      const userMsg: Message = { role: "user", content: text };
      setMessages((prev) => [...prev, userMsg]);

      // 2. 占位一条空的 assistant 消息（后续 token 追加到此）
      setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

      setIsStreaming(true);

      const abort = new AbortController();
      abortRef.current = abort;

      // 3. SSE 解析器 —— 核心
      const reader = createSSEReader((event) => {
        switch (event.type) {
          case "token": {
            const token = String(event.content ?? "");
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant") {
                next[next.length - 1] = {
                  ...last,
                  content: last.content + token,
                };
              }
              return next;
            });
            break;
          }
          case "done":
            setIsStreaming(false);
            break;
          case "error":
            setMessages((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              if (last && last.role === "assistant") {
                next[next.length - 1] = {
                  ...last,
                  content: last.content || `[错误] ${event.message}`,
                };
              }
              return next;
            });
            setIsStreaming(false);
            break;
        }
      });

      try {
        const response = await fetch("/api/chat/stream", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({
            query: text,
            session_id: sessionId,
            mode,
            ...(reviewId ? { review_id: reviewId } : {}),
          }),
          signal: abort.signal,
        });

        if (!response.ok || !response.body) {
          throw new Error(`HTTP ${response.status}`);
        }

        // 4. 逐块读取 ReadableStream
        const decoder = new TextDecoder();
        const streamReader = response.body.getReader();

        while (true) {
          const { done, value } = await streamReader.read();
          if (done) break;
          reader.push(decoder.decode(value, { stream: true }));
        }
      } catch (err: unknown) {
        if (err instanceof DOMException && err.name === "AbortError") return;
        reader.push(
          JSON.stringify({
            type: "error",
            message: err instanceof Error ? err.message : "网络请求失败",
          }) + "\n\n",
        );
      } finally {
        reader.reset();
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [mode, reviewId, sessionId, token],
  );

  const cancel = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  return { messages, sendMessage, isStreaming, cancel };
}
