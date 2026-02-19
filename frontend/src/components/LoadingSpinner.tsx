import { Loader2 } from "lucide-react";

export default function LoadingSpinner({ text = "Loading..." }: { text?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-text-secondary">
      <Loader2 className="h-6 w-6 animate-spin" />
      <span className="text-sm">{text}</span>
    </div>
  );
}
