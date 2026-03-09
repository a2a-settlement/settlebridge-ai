import { useState, useCallback } from "react";
import { Download, Eye, Code, Table } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface DeliverableViewerProps {
  content: string;
  contentType: string;
  bountyTitle?: string;
}

type ViewMode = "preview" | "raw";

const EXTENSION_MAP: Record<string, string> = {
  "text/markdown": ".md",
  "application/json": ".json",
  "text/csv": ".csv",
  "text/x-python": ".py",
  "text/plain": ".txt",
};

const LABEL_MAP: Record<string, string> = {
  "text/markdown": "Markdown",
  "application/json": "JSON",
  "text/csv": "CSV",
  "text/x-python": "Python",
  "text/plain": "Plain Text",
};

function tryParseJSON(raw: string): unknown | null {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function parseCSV(raw: string): { headers: string[]; rows: string[][] } {
  const lines = raw.trim().split("\n");
  if (lines.length === 0) return { headers: [], rows: [] };
  const split = (line: string) =>
    line.split(",").map((cell) => cell.trim().replace(/^"|"$/g, ""));
  return {
    headers: split(lines[0]),
    rows: lines.slice(1).map(split),
  };
}

function CSVTable({ raw }: { raw: string }) {
  const { headers, rows } = parseCSV(raw);
  if (headers.length === 0) return <p className="text-gray-500">Empty CSV</p>;

  return (
    <div className="overflow-x-auto -mx-4">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200">
            {headers.map((h, i) => (
              <th
                key={i}
                className="px-4 py-2 text-left font-semibold text-navy-900 bg-gray-100 first:rounded-tl-lg last:rounded-tr-lg whitespace-nowrap"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr
              key={ri}
              className="border-b border-gray-100 last:border-0 hover:bg-gray-50 transition-colors"
            >
              {row.map((cell, ci) => (
                <td key={ci} className="px-4 py-2 text-gray-700 whitespace-nowrap">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-xs text-gray-400 mt-2 px-4">
        {rows.length} row{rows.length !== 1 && "s"} &middot; {headers.length} column{headers.length !== 1 && "s"}
      </p>
    </div>
  );
}

function JSONView({ raw }: { raw: string }) {
  const parsed = tryParseJSON(raw);
  const formatted = parsed !== null ? JSON.stringify(parsed, null, 2) : raw;

  return (
    <pre className="whitespace-pre-wrap text-xs font-mono leading-relaxed text-gray-700 overflow-x-auto">
      {formatted}
    </pre>
  );
}

function CodeView({ raw, language }: { raw: string; language: string }) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-xs font-medium text-gray-400 uppercase tracking-wider">
          {language}
        </span>
      </div>
      <pre className="whitespace-pre-wrap text-xs font-mono leading-relaxed text-gray-800 overflow-x-auto">
        {raw}
      </pre>
    </div>
  );
}

function PreviewContent({ content, contentType }: { content: string; contentType: string }) {
  switch (contentType) {
    case "text/markdown":
      return (
        <div className="prose prose-sm max-w-none prose-headings:text-navy-900 prose-a:text-navy-600 prose-strong:text-navy-900 prose-code:text-navy-700 prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-pre:bg-navy-950 prose-pre:text-gray-300 prose-table:text-sm prose-th:bg-gray-50 prose-th:text-navy-900">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      );
    case "application/json":
      return <JSONView raw={content} />;
    case "text/csv":
      return <CSVTable raw={content} />;
    case "text/x-python":
      return <CodeView raw={content} language="Python" />;
    default:
      return (
        <pre className="whitespace-pre-wrap text-sm text-gray-700 leading-relaxed">
          {content}
        </pre>
      );
  }
}

function viewIcon(contentType: string) {
  switch (contentType) {
    case "text/csv":
      return <Table className="w-3.5 h-3.5" />;
    case "application/json":
    case "text/x-python":
      return <Code className="w-3.5 h-3.5" />;
    default:
      return <Eye className="w-3.5 h-3.5" />;
  }
}

export default function DeliverableViewer({
  content,
  contentType,
  bountyTitle,
}: DeliverableViewerProps) {
  const [mode, setMode] = useState<ViewMode>("preview");

  const slug = (bountyTitle || "deliverable")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40);
  const ext = EXTENSION_MAP[contentType] || ".txt";
  const filename = `${slug}${ext}`;
  const typeLabel = LABEL_MAP[contentType] || contentType;

  const handleDownload = useCallback(() => {
    const blob = new Blob([content], { type: contentType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [content, contentType, filename]);

  return (
    <div>
      {/* Toolbar */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-1 rounded-lg bg-gray-100 p-0.5">
          <button
            onClick={() => setMode("preview")}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition ${
              mode === "preview"
                ? "bg-white text-navy-900 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            {viewIcon(contentType)} Preview
          </button>
          <button
            onClick={() => setMode("raw")}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition ${
              mode === "raw"
                ? "bg-white text-navy-900 shadow-sm"
                : "text-gray-500 hover:text-gray-700"
            }`}
          >
            <Code className="w-3.5 h-3.5" /> Raw
          </button>
        </div>

        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400 hidden sm:inline">{typeLabel}</span>
          <button
            onClick={handleDownload}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-gray-200 text-xs font-medium text-gray-600 hover:bg-gray-50 hover:text-navy-900 transition"
          >
            <Download className="w-3.5 h-3.5" /> Download{" "}
            <span className="text-gray-400">{ext}</span>
          </button>
        </div>
      </div>

      {/* Content area */}
      <div className="bg-gray-50 rounded-lg p-4 min-h-[120px]">
        {mode === "preview" ? (
          <PreviewContent content={content} contentType={contentType} />
        ) : (
          <pre className="whitespace-pre-wrap text-xs font-mono text-gray-600 leading-relaxed overflow-x-auto">
            {content}
          </pre>
        )}
      </div>
    </div>
  );
}
