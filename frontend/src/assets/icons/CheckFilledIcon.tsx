import type { SVGProps } from "react";

export function CheckFilledIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      {...props}
    >
      <path
        d="M0 8C0 3.58172 3.58172 0 8 0C12.4183 0 16 3.58172 16 8C16 12.4183 12.4183 16 8 16C3.58172 16 0 12.4183 0 8Z"
        fill="#B8EFC6"
      />
      <path
        fillRule="evenodd"
        clipRule="evenodd"
        d="M11.3977 4.92661L6.62434 9.53328L5.35767 8.17994C5.12434 7.95994 4.75767 7.94661 4.491 8.13328C4.231 8.32661 4.15767 8.66661 4.31767 8.93994L5.81767 11.3799C5.96434 11.6066 6.21767 11.7466 6.50434 11.7466C6.77767 11.7466 7.03767 11.6066 7.18434 11.3799C7.42434 11.0666 12.0043 5.60661 12.0043 5.60661C12.6043 4.99328 11.8777 4.45328 11.3977 4.91994V4.92661Z"
        fill="#0E7027"
      />
    </svg>
  );
}
