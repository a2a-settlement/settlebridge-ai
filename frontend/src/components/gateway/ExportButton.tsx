import { Download } from "lucide-react";
import { useState } from "react";

interface ExportButtonProps {
  onExport: (format: "json" | "csv") => Promise<string>;
  filename?: string;
}

export default function ExportButton({ onExport, filename = "export" }: ExportButtonProps) {
  const [exporting, setExporting] = useState(false);
  const [showMenu, setShowMenu] = useState(false);

  const handleExport = async (format: "json" | "csv") => {
    setExporting(true);
    setShowMenu(false);
    try {
      const content = await onExport(format);
      const blob = new Blob([content], {
        type: format === "csv" ? "text/csv" : "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${filename}.${format}`;
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="relative">
      <button
        onClick={() => setShowMenu(!showMenu)}
        disabled={exporting}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 transition"
      >
        <Download className="w-4 h-4" />
        {exporting ? "Exporting..." : "Export"}
      </button>
      {showMenu && (
        <div className="absolute right-0 mt-1 w-32 bg-white border border-gray-200 rounded-lg shadow-lg z-10">
          <button
            onClick={() => handleExport("json")}
            className="block w-full text-left px-4 py-2 text-sm hover:bg-gray-50"
          >
            JSON
          </button>
          <button
            onClick={() => handleExport("csv")}
            className="block w-full text-left px-4 py-2 text-sm hover:bg-gray-50"
          >
            CSV
          </button>
        </div>
      )}
    </div>
  );
}
