import { useCallback } from "react";

interface YamlEditorProps {
  value: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  height?: string;
}

export default function YamlEditor({
  value,
  onChange,
  readOnly = false,
  height = "20rem",
}: YamlEditorProps) {
  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      onChange(e.target.value);
    },
    [onChange]
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Tab") {
        e.preventDefault();
        const target = e.currentTarget;
        const start = target.selectionStart;
        const end = target.selectionEnd;
        const newVal = value.substring(0, start) + "  " + value.substring(end);
        onChange(newVal);
        requestAnimationFrame(() => {
          target.selectionStart = target.selectionEnd = start + 2;
        });
      }
    },
    [value, onChange]
  );

  return (
    <div className="relative rounded-lg border border-gray-300 overflow-hidden">
      <div className="bg-gray-50 px-3 py-1.5 border-b border-gray-200 flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500 uppercase tracking-wider">YAML</span>
        {readOnly && (
          <span className="text-xs text-gray-400">Read only</span>
        )}
      </div>
      <textarea
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        readOnly={readOnly}
        spellCheck={false}
        className="w-full font-mono text-sm p-3 bg-white text-gray-800 resize-none focus:outline-none"
        style={{ height, tabSize: 2 }}
      />
    </div>
  );
}
