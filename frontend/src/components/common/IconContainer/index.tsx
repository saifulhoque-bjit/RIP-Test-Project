import React from 'react';
import { cn } from '@/lib/utils';

export interface IconContainerProps
  extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode;
}

const IconContainer = React.forwardRef<HTMLDivElement, IconContainerProps>(
  ({ children, className, ...rest }, ref) => {
    return (
      <div
        ref={ref}
        className={cn(
          "flex items-center justify-center flex-shrink-0",
          "w-8 h-8 xl:w-9 xl:h-9 2xl:w-10 2xl:h-10",
          "opacity-100 rounded-lg",
          "bg-[var(--color-brand-blue-50)]",
          "text-[var(--color-brand-blue-500)]",
          "[&>svg]:w-3.5 [&>svg]:h-3.5 xl:[&>svg]:w-[15px] xl:[&>svg]:h-[15px] 2xl:[&>svg]:w-4 2xl:[&>svg]:h-4",
          className
        )}
        {...rest}
      >
        {children}
      </div>
    );
  }
);

IconContainer.displayName = 'IconContainer';

export default IconContainer;
