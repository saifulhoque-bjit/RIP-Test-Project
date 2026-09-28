import { cn } from "@/lib/utils";
import { ChevronPrevious } from "@/assets/icons/arrow/ChevronPrevious";
import { ChevronNext } from "@/assets/icons/arrow/ChevronNext";

export interface PaginationProps {
  /** Offset of the current page (0-based, in items). */
  skip: number;
  /** Items per page. */
  limit: number;
  /** Total item count across all pages. */
  total: number;
  /** Called with the new `skip` value when the page changes. */
  onSkipChange: (skip: number) => void;
  /** Plural noun for the summary text, e.g. "projects". */
  itemLabel?: string;
  className?: string;
}

function getPageList(
  current: number,
  totalPages: number,
): (number | "ellipsis")[] {
  const pages: (number | "ellipsis")[] = [1];
  const left = Math.max(2, current - 1);
  const right = Math.min(totalPages - 1, current + 1);

  if (left > 2) pages.push("ellipsis");
  for (let page = left; page <= right; page++) pages.push(page);
  if (right < totalPages - 1) pages.push("ellipsis");
  if (totalPages > 1) pages.push(totalPages);

  return pages;
}

const navButtonClasses = cn(
  "flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--border-primary)] text-[var(--text-secondary)]",
  "hover:bg-[var(--accent-50)] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent",
);

export function Pagination({
  skip,
  limit,
  total,
  onSkipChange,
  itemLabel = "items",
  className,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / limit));
  const currentPage = Math.min(totalPages, Math.floor(skip / limit) + 1);

  if (totalPages <= 1) return null;

  const rangeStart = total === 0 ? 0 : skip + 1;
  const rangeEnd = Math.min(skip + limit, total);

  const goToPage = (page: number) => {
    const clamped = Math.min(Math.max(page, 1), totalPages);
    onSkipChange((clamped - 1) * limit);
  };

  return (
    <nav
      aria-label="Pagination"
      className={cn(
        "flex flex-wrap items-center justify-between gap-3 px-4 py-2",
        className,
      )}
    >
      <span className="text-xs text-[var(--text-secondary)]">
        Showing {rangeStart}-{rangeEnd} of {total} {itemLabel}
      </span>

      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label="Previous page"
          disabled={currentPage === 1}
          onClick={() => goToPage(currentPage - 1)}
          className={navButtonClasses}
        >
          <ChevronPrevious className="h-4 w-4" />
        </button>

        {getPageList(currentPage, totalPages).map((page, index) =>
          page === "ellipsis" ? (
            <span
              key={`ellipsis-${index}`}
              className="flex h-8 w-8 items-center justify-center text-xs text-[var(--text-quaternary)]"
            >
              …
            </span>
          ) : (
            <button
              key={page}
              type="button"
              aria-current={page === currentPage ? "page" : undefined}
              onClick={() => goToPage(page)}
              className={cn(
                "flex h-8 w-8 items-center justify-center rounded-lg border text-xs font-semibold",
                page === currentPage
                  ? "border-transparent bg-[var(--accent-600)] text-white"
                  : "border-[var(--border-primary)] text-[var(--text-secondary)] hover:bg-[var(--accent-50)]",
              )}
            >
              {page}
            </button>
          ),
        )}

        <button
          type="button"
          aria-label="Next page"
          disabled={currentPage === totalPages}
          onClick={() => goToPage(currentPage + 1)}
          className={navButtonClasses}
        >
          <ChevronNext className="h-4 w-4" />
        </button>
      </div>
    </nav>
  );
}

export default Pagination;
