import React, { type ReactNode } from "react";

import { cn } from "@/lib/utils";
import Card from "./Card";

export type TableCellValue = string | number | ReactNode;

export interface TableColumn<T> {
  key: keyof T;
  label: string;
  render?: (value: T[keyof T], row: T, index: number) => TableCellValue;
  className?: string;
  align?: "left" | "center" | "right";
}

export interface TableProps<T> {
  title?: string;
  description?: string;
  actionHelperText?: string;
  actionLinkLabel?: string;
  onActionLinkClick?: () => void;
  actionButtonLabel?: string;
  actionButtonDisabled?: boolean;
  onActionClick?: () => void;
  columns: TableColumn<T>[];
  data: T[];
  onRowClick?: (row: T, index: number) => void;
  isClickable?: boolean;
  emptyMessage?: ReactNode;
  striped?: boolean;
  className?: string;
  getRowClassName?: (row: T, index: number) => string | undefined;
}

function Table<T extends Record<string, unknown>>({
  title,
  description,
  actionHelperText,
  actionLinkLabel,
  onActionLinkClick,
  actionButtonLabel,
  actionButtonDisabled = false,
  onActionClick,
  columns,
  data,
  onRowClick,
  isClickable = !!onRowClick,
  emptyMessage = "No data available",
  striped = false,
  className,
  getRowClassName,
}: TableProps<T>) {
  const renderCellValue = (value: unknown): ReactNode => {
    if (React.isValidElement(value)) {
      return value;
    }
    return String(value ?? "");
  };

  const hasRightSlot =
    actionHelperText ||
    (actionLinkLabel && onActionLinkClick) ||
    (actionButtonLabel && onActionClick);

  return (
    <Card className={cn("w-full overflow-hidden", className)}>
      {/* Header Section */}
      {(title || actionButtonLabel || actionLinkLabel) && (
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-primary)] gap-3">
          <div className="flex-1">
            {title && (
              <h3 className="m-0 text-base font-semibold text-[var(--text-primary)]">
                {title}
              </h3>
            )}
            {description && (
              <p className="m-0 mt-1 text-sm text-[var(--text-secondary)]">
                {description}
              </p>
            )}
          </div>

          {hasRightSlot && (
            <div className="flex items-center gap-3">
              {actionHelperText && (
                <span className="whitespace-nowrap text-sm text-[var(--text-secondary)]">
                  {actionHelperText}
                </span>
              )}
              {actionLinkLabel && onActionLinkClick && (
                <button
                  onClick={onActionLinkClick}
                  className="p-0 text-sm font-semibold text-[var(--accent-600)] bg-transparent border-none cursor-pointer hover:underline"
                >
                  {actionLinkLabel}
                </button>
              )}
              {actionButtonLabel && onActionClick && (
                <button
                  onClick={onActionClick}
                  disabled={actionButtonDisabled}
                  className={cn(
                    "inline-flex items-center justify-center gap-2",
                    "rounded-lg border font-semibold",
                    "transition-colors duration-200",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-600)]/35 focus-visible:ring-offset-2",
                    "disabled:cursor-not-allowed disabled:pointer-events-none",
                    "disabled:bg-[#aebfdd] disabled:border-transparent disabled:text-white",
                    "h-9 px-4 text-sm",
                    "bg-[var(--accent-600)] border-transparent text-white hover:brightness-95",
                  )}
                >
                  {actionButtonLabel}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* Table Section */}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-[#fafbfd]">
              {columns.map((column) => (
                <th
                  key={String(column.key)}
                  className={cn(
                    "text-left text-xs font-semibold uppercase tracking-wider letter-spacing-0.5",
                    "text-[var(--text-quaternary)]",
                    "px-4 py-2.5",
                    column.align === "center" && "text-center",
                    column.align === "right" && "text-right",
                    column.className,
                  )}
                >
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>

          <tbody>
            {data.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length}
                  className="px-4 py-8 text-center text-sm text-[var(--text-tertiary)]"
                >
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              data.map((row, rowIndex) => (
                <tr
                  key={rowIndex}
                  onClick={() => isClickable && onRowClick?.(row, rowIndex)}
                  className={cn(
                    "border-t border-[var(--border-primary)] bg-white",
                    isClickable && "cursor-pointer",
                    striped && "bg-white",
                    getRowClassName?.(row, rowIndex),
                  )}
                >
                  {columns.map((column) => {
                    const cellValue = row[column.key];
                    const renderedValue = column.render
                      ? column.render(cellValue, row, rowIndex)
                      : cellValue;

                    return (
                      <td
                        key={String(column.key)}
                        className={cn(
                          "px-4 py-3 text-sm text-[var(--text-primary)]",
                          column.align === "center" && "text-center",
                          column.align === "right" && "text-right",
                          column.className,
                        )}
                      >
                        {renderCellValue(renderedValue)}
                      </td>
                    );
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

export { Table };
export default Table;