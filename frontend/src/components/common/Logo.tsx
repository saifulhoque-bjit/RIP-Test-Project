/**
 * RIP brand mark — a governed-requirements motif: stacked requirement layers with a
 * grounding/approval check node, on a navy→accent gradient tile. Premium, scalable.
 */
export function Logo({
  size = 32,
  className,
}: {
  size?: number;
  className?: string;
}) {
  const gid = "rip-logo-grad";
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      role="img"
      aria-label="RIP"
    >
      <defs>
        <linearGradient
          id={gid}
          x1="0"
          y1="0"
          x2="32"
          y2="32"
          gradientUnits="userSpaceOnUse"
        >
          <stop stopColor="#12336f" />
          <stop offset="1" stopColor="#1a50c8" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${gid})`} />
      <rect
        x="7.5"
        y="8.5"
        width="17"
        height="3.1"
        rx="1.55"
        fill="#ffffff"
        fillOpacity="0.95"
      />
      <rect
        x="7.5"
        y="13.9"
        width="17"
        height="3.1"
        rx="1.55"
        fill="#ffffff"
        fillOpacity="0.68"
      />
      <rect
        x="7.5"
        y="19.3"
        width="9.5"
        height="3.1"
        rx="1.55"
        fill="#ffffff"
        fillOpacity="0.42"
      />
      <circle
        cx="22.4"
        cy="21.4"
        r="4.6"
        fill="#0e9f6e"
        stroke="#ffffff"
        strokeWidth="1.1"
      />
      <path
        d="M20.4 21.5l1.5 1.5 2.6-3.1"
        stroke="#ffffff"
        strokeWidth="1.35"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
