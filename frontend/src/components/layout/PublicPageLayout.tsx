import type React from "react";
import { Logo } from "@/components/common/Logo";

interface PublicPageLayoutProps {
  children: React.ReactNode;
}

export default function PublicPageLayout({ children }: PublicPageLayoutProps) {
  return (
    <main className="h-screen flex flex-col">
      <div className="flex h-full">
        {/* Left Panel - Gradient Background */}
        <div className="hidden w-[42%] flex-col bg-gradient-to-br from-[#0A1F44] to-[#0D2847] p-14 text-white md:flex">
          <div className="flex items-center gap-2.5 text-lg font-extrabold tracking-tight">
            <Logo size={34} />
            RIP
            <span className="ml-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-[#8ea6d8]">
              Requirement Intelligence
            </span>
          </div>
          <div className="mt-auto">
            <h1 className="text-3xl font-bold leading-tight">
              The requirements backbone
              <br />
              for modernization.
            </h1>
            <p className="mt-4 max-w-md leading-relaxed text-[#c7d3ec]">
              RIP turns{" "}
              <b className="font-semibold text-white">
                RFPs, specifications and legacy source
              </b>{" "}
              into traceable, versioned requirements — and keeps them current as
              change requests arrive. Every AI output is{" "}
              <b className="font-semibold text-white">
                gated by grounding checks and human review
              </b>
              , so approved baselines broadcast to Jira &amp; TAP with confidence.
            </p>
          </div>
        </div>

        {/* Right Panel - Form Card */}
        <div className="grid flex-1 place-items-center bg-canvas">{children}</div>
      </div>
    </main>
  );
}
