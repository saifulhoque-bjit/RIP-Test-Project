import { DeleteIcon } from "@/assets/icons/DeleteIcon";
import { RefreshIcon } from "@/assets/icons/RefreshIcon";
import { CheckFilledIcon } from "@/assets/icons/CheckFilledIcon";
import ProgressBar from "@/components/common/ProgressBar";
import { cn } from "@/lib/utils";

interface UploadFileItemProps {
  file: File;
  index: number;
  onRemove?: (index: number) => void;
  progressPercentage?: number;
  errorMessage?: string;
  onRetry?: (index: number) => void;
  isSuccess?: boolean;
  disabled?: boolean;
}

export default function UploadFileItem({
  file,
  index,
  onRemove,
  progressPercentage,
  errorMessage,
  onRetry,
  isSuccess,
  disabled = false,
}: UploadFileItemProps) {
  return (
    <li className="w-full">
      <div
        className={cn(
          "flex items-center justify-between rounded-[8px] border bg-white px-3 py-2.5 text-[13px]",
          errorMessage ? "border-[#e31c26]" : "border-[var(--border-primary)]",
          isSuccess && "border-[var(--color-brand-green-200)]",
        )}
      >
        <span className="min-w-0 truncate text-[var(--text-primary)]">
          {`📄 ${file.name}`}
        </span>

        {!isSuccess && onRemove && (
          <button
            type="button"
            className="inline-flex items-center gap-1 border-none bg-transparent p-0 text-[13px] font-medium text-[var(--error)] cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
            disabled={disabled}
            onMouseDown={(event) => event.stopPropagation()}
            onClick={(event) => {
              event.preventDefault();
              event.stopPropagation();
              if (!disabled) onRemove(index);
            }}
          >
            <DeleteIcon />
            remove
          </button>
        )}

        {isSuccess && (
          <span className="flex items-center gap-1 text-sm font-medium text-[var(--color-brand-green-500)] whitespace-nowrap shrink-0 [&_svg]:w-4 [&_svg]:h-4">
            <CheckFilledIcon />
            Completed
          </span>
        )}
      </div>

      {errorMessage && (
        <div className="flex flex-row items-center gap-2 px-1 pt-2">
          <span className="text-sm font-normal text-[#e31c26] flex-1 min-w-0">
            {errorMessage}
          </span>
          {onRetry && (
            <button
              type="button"
              className="flex items-center gap-1 bg-transparent border-none cursor-pointer text-sm font-medium text-[var(--color-brand-green-500)] py-1 px-2 rounded-sm whitespace-nowrap shrink-0 transition-colors duration-150 ease-in-out hover:text-[var(--color-brand-green-700)] [&_svg]:w-3.5 [&_svg]:h-3.5"
              onClick={() => onRetry(index)}
            >
              <RefreshIcon />
              Try Again
            </button>
          )}
        </div>
      )}

      {!errorMessage &&
        !isSuccess &&
        progressPercentage !== undefined &&
        progressPercentage !== null && (
          <div className="w-full px-1 pt-2">
            <ProgressBar value={progressPercentage} />
          </div>
        )}
    </li>
  );
}
