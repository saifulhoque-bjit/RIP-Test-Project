import { forwardRef, type HTMLAttributes } from "react";
import noDocUploadImg from "../../../assets/images/no-doc-upload.svg";

export interface NoDocumentUploadProps extends HTMLAttributes<HTMLDivElement> {
  /** Heading shown below the icon. Defaults to "No documents yet". */
  title?: string;
  /** Supporting text below the title. */
  description?: string;
}

const EmptyState = forwardRef<HTMLDivElement, NoDocumentUploadProps>(
  (
    {
      title = "You don’t have any documents",
      description = "Please upload your documents to get started",
      className,
      ...props
    },
    ref,
  ) => {
    return (
      <div
        ref={ref}
        className={`flex w-full flex-col items-center justify-center gap-1 text-center ${className ?? ""}`}
        role="status"
        aria-label={title}
        {...props}
      >
        <img
          src={noDocUploadImg}
          alt=""
          className="h-[116px] w-[204px] overflow-visible 2xl:h-[100px] 2xl:w-[175px] [@media(max-height:768px)]:h-[60px] [@media(max-height:768px)]:w-[105px]"
        />

        {/* Text */}
        <h3 className="text-[#194CBE] text-base font-semibold tracking-[0px] [@media(max-height:768px)]:text-sm">
          {title}
        </h3>
        {description && (
          <p className="text-neutral-400 text-xs font-normal tracking-[0px] [@media(max-height:768px)]:text-[10px]">
            {description}
          </p>
        )}
      </div>
    );
  },
);

EmptyState.displayName = "EmptyState";

export default EmptyState;
