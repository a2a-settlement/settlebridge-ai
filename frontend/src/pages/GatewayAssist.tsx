import { useState, useRef, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Send, Shield, X, Loader2, CheckCircle } from "lucide-react";
import ChatMessage from "../components/assist/ChatMessage";
import YamlEditor from "../components/gateway/YamlEditor";
import {
  startSession,
  sendMessage,
  abandonSession,
} from "../services/assist";
import type { AssistSessionStatus } from "../types";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
}

interface PolicyDraft {
  name: string;
  yaml_content: string;
}

interface AlertRuleDraft {
  name: string;
  condition_type: string;
  threshold: number;
  channel: string;
  agent_filter: string | null;
}

const SUGGESTIONS = [
  "Write a policy requiring minimum 0.5 reputation for all agents",
  "Create a rate limit of 30 requests/minute per agent",
  "Set up an alert when any agent's error rate exceeds 10%",
  "Why would an agent get blocked by the gateway?",
  "Help me write a high-value transaction policy for escrows over 1000 ATE",
];

export default function GatewayAssist() {
  const navigate = useNavigate();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [streamingText, setStreamingText] = useState("");
  const [policyDraft, setPolicyDraft] = useState<PolicyDraft | null>(null);
  const [alertRuleDraft, setAlertRuleDraft] = useState<AlertRuleDraft | null>(null);
  const [sessionStatus, setSessionStatus] = useState<AssistSessionStatus>("active");
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [applied, setApplied] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, streamingText, scrollToBottom]);

  function cleanAssistantText(raw: string): string {
    return raw
      .replace(/<policy_draft>[\s\S]*?<\/policy_draft>/g, "")
      .replace(/<alert_rule>[\s\S]*?<\/alert_rule>/g, "")
      .trim();
  }

  function extractDrafts(raw: string) {
    const policyMatch = raw.match(/<policy_draft>\s*([\s\S]*?)\s*<\/policy_draft>/);
    if (policyMatch) {
      try {
        setPolicyDraft(JSON.parse(policyMatch[1]));
      } catch { /* ignore */ }
    }
    const alertMatch = raw.match(/<alert_rule>\s*([\s\S]*?)\s*<\/alert_rule>/);
    if (alertMatch) {
      try {
        setAlertRuleDraft(JSON.parse(alertMatch[1]));
      } catch { /* ignore */ }
    }
  }

  const callbacks = useCallback(
    () => ({
      onToken: (text: string) => {
        setStreamingText((prev) => prev + text);
      },
      onDraft: () => {},
      onStatus: (s: string) => {
        setSessionStatus(s as AssistSessionStatus);
      },
      onDone: () => {
        setStreamingText((prev) => {
          if (prev) {
            extractDrafts(prev);
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
    []
  );

  const handleSend = async () => {
    const msg = input.trim();
    if (!msg || sending) return;

    setInput("");
    setError("");
    setSending(true);
    setApplied(false);
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

  const handleApplyPolicy = async () => {
    if (!policyDraft) return;
    try {
      const api = await import("../services/gateway");
      await api.createPolicy(policyDraft.name, policyDraft.yaml_content);
      setApplied(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to apply policy");
    }
  };

  const handleApplyAlert = async () => {
    if (!alertRuleDraft) return;
    try {
      const api = await import("../services/gateway");
      await api.createAlertRule({
        name: alertRuleDraft.name,
        condition_type: alertRuleDraft.condition_type as any,
        threshold: alertRuleDraft.threshold,
        channel: alertRuleDraft.channel as any,
        agent_filter: alertRuleDraft.agent_filter,
      });
      setApplied(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to apply alert rule");
    }
  };

  const handleClose = async () => {
    if (sessionId) {
      await abandonSession(sessionId);
    }
    navigate("/");
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const isActive = sessionStatus === "active" || sessionStatus === "draft_ready";

  return (
    <div className="flex h-[calc(100vh-64px)] overflow-hidden">
      <div className="flex-1 flex flex-col min-w-0 lg:flex-[3]">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 bg-white">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 bg-blue-50 rounded-xl flex items-center justify-center">
              <Shield className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <h1 className="font-bold text-gray-900 text-base">Gateway Assist</h1>
              <p className="text-xs text-gray-400">
                AI-powered gateway operations assistant
              </p>
            </div>
          </div>
          <button onClick={handleClose} className="text-gray-400 hover:text-gray-600 p-1" title="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-6 space-y-4 bg-gray-50">
          {messages.length === 0 && !streamingText && (
            <div className="flex flex-col items-center justify-center h-full text-center px-4">
              <div className="w-16 h-16 bg-blue-50 rounded-2xl flex items-center justify-center mb-5">
                <Shield className="w-8 h-8 text-blue-600" />
              </div>
              <h2 className="text-xl font-bold text-gray-900 mb-2">
                How can I help with your gateway?
              </h2>
              <p className="text-sm text-gray-500 max-w-md leading-relaxed mb-6">
                I can help you write trust policies, set up alerts, troubleshoot
                agent issues, and explain audit log entries.
              </p>
              <div className="flex flex-wrap gap-2 justify-center max-w-lg">
                {SUGGESTIONS.map((suggestion) => (
                  <button
                    key={suggestion}
                    onClick={() => setInput(suggestion)}
                    className="text-xs bg-white border border-gray-200 rounded-lg px-3 py-2 text-gray-600 hover:border-blue-300 hover:text-blue-700 transition text-left"
                  >
                    {suggestion}
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((msg, i) => (
            <ChatMessage key={i} role={msg.role} content={msg.content} />
          ))}

          {streamingText && (
            <ChatMessage role="assistant" content={cleanAssistantText(streamingText)} isStreaming />
          )}

          {error && (
            <div className="bg-red-50 text-red-700 rounded-lg px-4 py-2.5 text-sm">{error}</div>
          )}
          <div ref={messagesEndRef} />
        </div>

        <div className="border-t border-gray-200 bg-white px-6 py-4">
          <div className="flex gap-3 items-end">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={messages.length === 0 ? "Ask about policies, alerts, agents..." : "Continue the conversation..."}
              disabled={!isActive || sending}
              rows={1}
              className="flex-1 resize-none px-4 py-2.5 rounded-xl border border-gray-300 focus:ring-2 focus:ring-blue-500 focus:border-transparent outline-none text-sm disabled:opacity-50 disabled:bg-gray-50 min-h-[42px] max-h-32"
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
              className="w-10 h-10 rounded-xl bg-blue-600 text-white flex items-center justify-center hover:bg-blue-700 transition disabled:opacity-40 disabled:cursor-not-allowed flex-shrink-0"
            >
              {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            </button>
          </div>
        </div>
      </div>

      <div className="hidden lg:flex lg:flex-[2] flex-col border-l border-gray-200 bg-white overflow-y-auto">
        <div className="px-5 py-4 border-b border-gray-200">
          <h2 className="font-semibold text-gray-900 text-sm">Draft Preview</h2>
          <p className="text-xs text-gray-400 mt-0.5">
            Generated policies and alerts appear here
          </p>
        </div>
        <div className="flex-1 px-5 py-4 overflow-y-auto space-y-6">
          {policyDraft && (
            <div>
              <h3 className="text-sm font-medium text-gray-700 mb-2">Trust Policy: {policyDraft.name}</h3>
              <YamlEditor value={policyDraft.yaml_content} onChange={() => {}} readOnly height="12rem" />
              <button
                onClick={handleApplyPolicy}
                disabled={applied}
                className="mt-3 w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition disabled:opacity-50"
              >
                {applied ? (
                  <>
                    <CheckCircle className="w-4 h-4" /> Applied
                  </>
                ) : (
                  "Apply Policy"
                )}
              </button>
            </div>
          )}

          {alertRuleDraft && (
            <div>
              <h3 className="text-sm font-medium text-gray-700 mb-2">Alert Rule: {alertRuleDraft.name}</h3>
              <div className="bg-gray-50 rounded-lg p-3 text-sm space-y-1">
                <p><span className="text-gray-500">Condition:</span> {alertRuleDraft.condition_type}</p>
                <p><span className="text-gray-500">Threshold:</span> {alertRuleDraft.threshold}</p>
                <p><span className="text-gray-500">Channel:</span> {alertRuleDraft.channel}</p>
                {alertRuleDraft.agent_filter && (
                  <p><span className="text-gray-500">Agent:</span> {alertRuleDraft.agent_filter}</p>
                )}
              </div>
              <button
                onClick={handleApplyAlert}
                disabled={applied}
                className="mt-3 w-full inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700 transition disabled:opacity-50"
              >
                {applied ? (
                  <>
                    <CheckCircle className="w-4 h-4" /> Applied
                  </>
                ) : (
                  "Apply Alert Rule"
                )}
              </button>
            </div>
          )}

          {!policyDraft && !alertRuleDraft && (
            <div className="flex flex-col items-center justify-center h-48 text-center">
              <Shield className="w-10 h-10 text-gray-300 mb-3" />
              <p className="text-sm text-gray-400">
                Ask me to create a policy or alert rule and the draft will appear here.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
