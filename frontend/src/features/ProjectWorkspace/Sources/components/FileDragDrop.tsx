interface FileDragDropProps {
  openFileDialog: () => void;
  handleDropZoneKeyDown: (event: React.KeyboardEvent<HTMLDivElement>) => void;
  handleDropZoneDragOver: (event: React.DragEvent<HTMLDivElement>) => void;
  handleDropZoneDragLeave: (event: React.DragEvent<HTMLDivElement>) => void;
  handleDropZoneDrop: (event: React.DragEvent<HTMLDivElement>) => void;
  handleInputChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  isDragActive: boolean;
  isDisabledState: boolean;
  uploadScenario: "rfp-initial" | "codebase-initial" | "incremental" | null;
  acceptValue: string | undefined;
  inputRef: React.RefObject<HTMLInputElement | null>;
}

export default function FileDragDrop({
  openFileDialog,
  handleDropZoneKeyDown,
  handleDropZoneDragOver,
  handleDropZoneDragLeave,
  handleDropZoneDrop,
  handleInputChange,
  isDragActive,
  isDisabledState,
  uploadScenario,
  acceptValue,
  inputRef,
}: FileDragDropProps) {
  return (
    <section
      role="button"
      tabIndex={0}
      onClick={openFileDialog}
      onKeyDown={handleDropZoneKeyDown}
      onDragOver={handleDropZoneDragOver}
      onDragLeave={handleDropZoneDragLeave}
      onDrop={handleDropZoneDrop}
      className={`w-full rounded-[12px] border-2 border-dashed bg-white p-[26px] text-center transition-colors ${
        isDragActive
          ? "border-[var(--accent)] bg-[var(--accent-50)]"
          : "border-[#9dc0ee]"
      } ${isDisabledState ? "cursor-not-allowed opacity-70" : "cursor-pointer"}`}
    >
      <div className="mx-auto mb-[14px] grid h-[52px] w-[52px] place-items-center rounded-[13px] bg-[var(--info-50)] text-[22px] text-[var(--info)]">
        📄
      </div>
      <div className="font-semibold text-[var(--text-primary)]">
        Drag &amp; drop files
      </div>
      <div className="mt-1 text-[11px] text-[var(--text-tertiary)]">
        {uploadScenario === "codebase-initial"
          ? "ZIP (PB, VB6, COBOL) · 200MB"
          : "Supports PDF up to 50MB"}
      </div>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={acceptValue}
        onChange={handleInputChange}
        disabled={isDisabledState}
        className="hidden"
        aria-label="Select files to upload"
      />
    </section>
  );
}
