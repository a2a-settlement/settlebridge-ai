"use client";

import { useState } from "react";
import { Check, Copy, Eye, EyeOff, Key } from "lucide-react";
import type { RotatedKeyResult } from "@/lib/managedAgents";

interface Props {
  rotatedKey: RotatedKeyResult;
  onClose: () => void;
}

export default function RotatedKeyReveal({ rotatedKey, onClose }: Props) {
  const [visible, setVisible] = useState(false);
  const [copied, setCopied] = useState(false);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/40">
      <div
        className="bg-white border border-gray-200 rounded-xl max-w-lg w-full p-6 shadow-xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rotated-key-title"
      >
        <div className="flex items-center gap-2 text-navy-900 font-semibold text-sm mb-3">
          <Key className="w-4 h-4 text-money" />
          <span id="rotated-key-title">New key for {rotatedKey.bot_name}</span>
        </div>
        <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg p-3 mb-4">
          {rotatedKey.warning}
        </p>
        <div className="bg-gray-50 rounded-lg p-3 flex items-center gap-2 border border-gray-200 mb-4">
          <code className="flex-1 text-xs font-mono text-gray-800 break-all">
            {visible
              ? rotatedKey.api_key
              : `${rotatedKey.api_key.slice(0, 12)}…${rotatedKey.api_key.slice(-6)}`}
          </code>
          <button
            type="button"
            onClick={() => setVisible(!visible)}
            className="text-gray-400 hover:text-gray-600 flex-shrink-0"
            aria-label={visible ? "Hide key" : "Show key"}
          >
            {visible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
          <button
            type="button"
            onClick={() => {
              void navigator.clipboard.writeText(rotatedKey.api_key);
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }}
            className="flex items-center gap-1 text-xs text-navy-700 hover:text-navy-900 font-medium flex-shrink-0"
          >
            {copied ? (
              <>
                <Check className="w-3.5 h-3.5" /> Copied
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5" /> Copy
              </>
            )}
          </button>
        </div>
        <p className="text-xs text-gray-500 mb-4">
          Old key valid for {rotatedKey.grace_period_minutes} more minutes. Update your credential store now.
        </p>
        <button
          type="button"
          onClick={onClose}
          className="w-full px-4 py-2.5 bg-navy-900 text-white rounded-lg text-sm font-semibold hover:bg-navy-800 transition"
        >
          Done
        </button>
      </div>
    </div>
  );
}