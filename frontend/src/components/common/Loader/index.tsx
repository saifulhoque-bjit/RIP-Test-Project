import React from "react";
import { Rings } from "react-loader-spinner";
import { cn } from "@/lib/utils";

interface LoaderProps extends Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "children"
> {
  visible?: boolean;
  height?: number | string;
  width?: number | string;
  color?: string;
  ariaLabel?: string;
  wrapperStyle?: React.CSSProperties;
  wrapperClass?: string;
  subtitle?: React.ReactNode;
}

const Loader = React.forwardRef<HTMLDivElement, LoaderProps>(
  (
    {
      visible = true,
      height = 80,
      width = 80,
      color = "#00a991",
      ariaLabel = "rings-loading",
      wrapperStyle,
      wrapperClass,
      subtitle,
      className,
      ...rest
    },
    ref,
  ) => {
    return (
      <div 
        ref={ref} 
        className={cn("flex flex-col items-center justify-center gap-1", className)} 
        {...rest}
      >
        <Rings
          visible={visible}
          height={height}
          width={width}
          color={color}
          ariaLabel={ariaLabel}
          wrapperStyle={wrapperStyle}
          wrapperClass={wrapperClass}
        />
        {subtitle ? <span className="leading-[1.3]">{subtitle}</span> : null}
      </div>
    );
  },
);

Loader.displayName = "Loader";

export type { LoaderProps };
export { Loader };
export default Loader;
