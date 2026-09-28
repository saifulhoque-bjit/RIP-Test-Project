export default function Card({
  children,
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`overflow-hidden rounded-[12px] border border-[var(--border-primary)] bg-white shadow-[0_1px_3px_rgba(16,24,40,.08),0_1px_2px_rgba(16,24,40,.06)] ${className}`} {...props}>
      {children}
    </div>
  );
}
