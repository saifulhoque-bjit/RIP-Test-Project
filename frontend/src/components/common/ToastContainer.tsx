import { useEffect } from "react";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import { dismissToast, type ToastType } from "@/store/slices/toastSlice";
import { cn } from "@/lib/cn";

const BORDER: Record<ToastType, string> = {
  success: "border-t-success",
  info: "border-t-info",
  error: "border-t-error",
  warning: "border-t-warn",
};

const ICON: Record<ToastType, string> = {
  success: "✓",
  info: "ℹ",
  error: "✕",
  warning: "⚠",
};

export function ToastContainer() {
  const toasts = useAppSelector((s) => s.ui.toasts);
  const dispatch = useAppDispatch();

  return (
    <div className="pointer-events-none fixed bottom-6 left-1/2 z-[1400] flex -translate-x-1/2 flex-col items-center gap-2.5">
      {toasts.map((t) => (
        <ToastCard key={t.id} id={t.id} kind={t.kind} title={t.title} body={t.body} onDone={() => dispatch(dismissToast(t.id))} />
      ))}
    </div>
  );
}

function ToastCard({
  id,
  kind,
  title,
  body,
  onDone,
}: {
  id: string;
  kind: ToastType;
  title: string;
  body?: string;
  onDone: () => void;
}) {
  useEffect(() => {
    const timer = setTimeout(onDone, 3600);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  return (
    <div
      className={cn(
        "pointer-events-auto flex w-[480px] max-w-[92vw] items-start gap-3 rounded-md border border-border border-t-4 bg-surface p-4 shadow-e2",
        BORDER[kind],
      )}
    >
      <div className="font-extrabold">{ICON[kind]}</div>
      <div>
        <div className="text-[13px] font-bold">{title}</div>
        {body && <div className="mt-0.5 text-xs text-sec">{body}</div>}
      </div>
    </div>
  );
}
