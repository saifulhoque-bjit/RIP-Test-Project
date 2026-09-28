const fileNameBtnStyle = (isActive: boolean, isDisabled: boolean) =>
  `h-5.5 px-2 inline-flex justify-center items-center ${
    isDisabled
      ? "bg-[var(--color-blue-neutral-50)] text-[color:var(--color-neutral-200)] opacity-50 cursor-not-allowed"
      : isActive
        ? "bg-[var(--color-brand-green-50)] text-[color:var(--color-brand-green-500)]"
        : "bg-[var(--color-blue-neutral-50)] text-[color:var(--color-neutral-300)] hover:bg-[var(--color-brand-green-100)] transition-colors"
  } text-[length:var(--font-size-xxsm)] rounded rounded-[4px)]`;

export default function FileNameButton({
  fileName,
  isSelected,
  onClick,
  disabled = false,
}: {
  fileName: string;
  isSelected: boolean;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      className={fileNameBtnStyle(isSelected, disabled)}
      onClick={onClick}
      disabled={disabled}
      title={disabled ? "Not related to the selected acceptance criterion" : undefined}
    >
      {fileName}
    </button>
  );
}
