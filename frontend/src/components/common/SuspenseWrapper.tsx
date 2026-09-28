import { Suspense } from "react";
import Loader from "@/components/common/Loader";

export default function SuspenseWrapper({ children }: { children: React.ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center min-h-[200px] w-full">
          <Loader />
        </div>
      }
    >
      {children}
    </Suspense>
  );
}
