import { useState, useRef, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Send, Sparkles, ArrowRight, X, Loader2 } from "lucide-react";
import ChatMessage from "../components/assist/ChatMessage";
import BountyDraftPreview from "../components/assist/BountyDraftPreview";
import {
  startSession,
  sendMessage,
  finalizeSession,
  abandonSession,
} from "../services/assist";
import type { BountyDraft, AssistSessionStatus } from "../types";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
}

export default function BountyAssist() {
  const navigate = useNavigate();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [draft, setDraft] = useState<BountyDraft | null>(null);
  const [sessionStatus, setSessionStatus] = useState<AssistSessionStatus>("active");
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [error, setError] = useState("");
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingText, scrollToBottom]);

  const callbacks = useCallback(
    () => ({
      onToken: (text: string) => {
        setStreamingText((prev) => prev + text);
      },
      onDraft: (d: BountyDraft) => {
        setDraft(d);
      },
      onStatus: (s: string) => {
        setSessionStatus(s as AssistSessionStatus);
      },
      onDone: () => {
        setStreamingText((prev) => {
          if (prev) {
            const cleaned = cleanAssistantText(prev);
            setMessages((msgs) => [...msgs, { role: "assistant", content: cleaned }]);
          }
          return "";
        });
        setSending(false);
      },
      onError: (err: string) => {
        setError(err);
        setSending(false);
      },
    }),
    [],
  );

  function cleanAssistantText(raw: string): string {
    return raw.replace(/<bounty_draft>[\s\S]*?<\/bounty_draft>/g, "").trim();
  }

  const handleSend = async () => {
    const msg = input.trim();
    if (!msg || sending) return;

    setInput("");
    setError("");
    setSending(true);
    setMessages((prev) => [...prev, { role: "user", content: msg }]);
    setStreamingText("");

    abortRef.current = new AbortController();

    try {
      if (!sessionId) {
        const sid = await startSession(msg, callbacks(), abortRef.current.signal);
        if (sid) setSessionId(sid);
      } else {
        await sendMessage(sessionId, msg, callbacks(), abortRef.current.signal);
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== "AbortError") {
        setError(err.message || "Something went wrong");
        setSending(false);
      }
    }
  };

  const handleFinalize = async () => {
    if (!sessionId || finalizing) return;
    setFinalizing(true);
    setError("");
    try {
      const bounty = await finalizeSession(sessionId);
      navigate(`/bounties/${bounty.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Finalization failed");
      setFinalizing(false);
    }
  };

  const handleAbandon = async () => {
    if (sessionId) {
      await abandonSession(sessionId);
    }
    navigate("/bounties");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const isActive = sessionStatus === "active" || sessionStatus === "draft_ready";
  const showFinalize = sessionStatus === "draft_ready" && !sending;

  return (
    <div className="flex h-[calc(100vh-64px)] overflow-hidden">
      {/* Left panel: Chat */}
      <div className="flex-1 flex flex-col min-w-0 lg:flex-[3]">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 bg-white">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-money/20 rounded-xl flex items-center justify-center">
              <Sparkles className="w-5 h-5 text-money-dark" />
            </div>
            <div>
              <h1 className="font-bold text-navy-900 text-base">Bounty Assist</h1>
              <p className="text-xs text-gray-400">
                AI-guided intelligence requirement builder
              </p>
            </div>
          </div>
          <button
            onClick={handleAbandon}
            className="text-gray-400 hover:text-gray-600 p-1"
            title="Close"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-4 bg-gray-50">
          {messages.length === 0 && !streamingText && (
            <div className="flex flex-col items-center justify-center h-full text-center px-4">
              <div className="w-16 h-16 bg-money/10 rounded-2xl flex items-center justify-center mb-5">
                <Sparkles className="w-8 h-8 text-money-dark" />
              </div>
              <h2 className="text-xl font-bold text-navy-900 mb-2">
                What do you need to know?
              </h2>
              <p className="text-sm text-gray-500 max-w-md leading-relaxed mb-6">
                Describe your question or concern in plain language. I'll help you
                transform it into a precise, structured bounty that attracts
                high-quality analytical intelligence.
              </p>
              <div className="flex flex-wrap gap-2 justify-center max-w-lg">
                {[
                  "I want to know if the market is going to crash",
                  "What's the regulatory risk for AI startups?",
                  "Is commercial real estate about to collapse?",
                ].map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => setInput(suggestion)}
                    className="text-xs bg-white border border-gray-200 rounded-lg px-3 py-2 text-gray-600 hover:border-navy-300 hover:text-navy-700 transition text-left"
                  >
                    "{suggestion}"
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <ChatMessage key={i} role={msg.role} content={msg.content} />
          ))}

          {streamingText && (
            <ChatMessage
              role="assistant"
              content={cleanAssistantText(streamingText)}
              isStreaming
            />
          )}

          {error && (
            <div className="bg-red-50 text-red-700 rounded-lg px-4 py-2.5 text-sm">
              {error}
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input area */}
        <div className="border-t border-gray-200 bg-white px-6 py-4">
          {showFinalize && (
            <div className="flex items-center gap-3 mb-3 p-3 bg-money/10 rounded-xl">
              <Sparkles className="w-5 h-5 text-money-dark flex-shrink-0" />
              <p className="text-sm text-navy-900 font-medium flex-1">
                Your bounty draft is ready! Review it in the panel, then post it.
              </p>
              <button
                onClick={handleFinalize}
                disabled={finalizing}
                className="inline-flex items-center gap-1.5 px-4 py-2 bg-money text-navy-900 rounded-lg font-bold text-sm hover:bg-money-dark transition disabled:opacity-60"
              >
                {finalizing ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <>
                    Post Bounty <ArrowRight className="w-4 h-4" />
                  </>
                )}
              </button>
            </div>
          )}

          <div className="flex gap-3 items-end">
            <textarea
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={
                messages.length === 0
                  ? "Describe what you need to know..."
                  : "Reply to refine your bounty..."
              }
              disabled={!isActive || sending}
              rows={1}
              className="flex-1 resize-none px-4 py-2.5 rounded-xl border border-gray-300 focus:ring-2 focus:ring-navy-500 focus:border-transparent outline-none text-sm disabled:opacity-50 disabled:bg-gray-50 min-h-[42px] max-h-32"
              style={{ height: "auto" }}
              onInput={(e) => {
                const el = e.currentTarget;
                el.style.height = "auto";
                el.style.height = Math.min(el.scrollHeight, 128) + "px";
              }}
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || !isActive || sending}
              className="w-10 h-10 rounded-xl bg-navy-900 text-white flex items-center justify-center hover:bg-navy-800 transition disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0"
            >
              {sending ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
            </button>
          </div>

          {isActive && messages.length > 0 && !showFinalize && (
            <p className="text-[10px] text-gray-400 mt-2 text-center">
              Shift+Enter for newline &middot; You can also continue the
              conversation to refine the bounty further
            </p>
          )}
        </div>
      </div>

      {/* Right panel: Preview */}
      <div className="hidden lg:flex lg:flex-[2] flex-col border-l border-gray-200 bg-white overflow-y-auto">
        <div className="px-5 py-4 border-b border-gray-200">
          <h2 className="font-semibold text-navy-900 text-sm">Bounty Preview</h2>
          <p className="text-xs text-gray-400 mt-0.5">
            Live draft — updates as you chat
          </p>
        </div>
        <div className="flex-1 px-5 py-4 overflow-y-auto">
          <BountyDraftPreview draft={draft} />
        </div>

        {showFinalize && (
          <div className="px-5 py-4 border-t border-gray-200">
            <button
              onClick={handleFinalize}
              disabled={finalizing}
              className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 bg-money text-navy-900 rounded-xl font-bold text-sm hover:bg-money-dark transition disabled:opacity-60"
            >
              {finalizing ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <>
                  Post This Bounty <ArrowRight className="w-4 h-4" />
                </>
              )}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
