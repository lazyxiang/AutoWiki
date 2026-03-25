"use client";
import { use } from "react";
import { useSearchParams } from "next/navigation";
import { JobProgressBar } from "@/components/JobProgressBar";

export default function JobPage({ params }: { params: Promise<{ job_id: string }> }) {
  const { job_id } = use(params);
  const searchParams = useSearchParams();
  const owner = searchParams.get("owner") ?? "";
  const repo = searchParams.get("repo") ?? "";

  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-8 p-8">
      <h2 className="text-xl font-semibold">Generating Wiki…</h2>
      <JobProgressBar jobId={job_id} owner={owner} repo={repo} />
    </main>
  );
}
