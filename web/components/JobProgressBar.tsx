"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Progress } from "@/components/ui/progress";
import { useJobProgress } from "@/lib/ws";
import { repoPath, cn } from "@/lib/utils";
import { parseJobProgressDetail } from "@/lib/job-progress";

interface Props {
  jobId: string;
  repoId: string;
  owner: string;
  repo: string;
}

export function JobProgressBar({ jobId, repoId, owner, repo }: Props) {
  const { progress, status, statusDescription, retrying } = useJobProgress(jobId);
  const router = useRouter();
  const progressDetail = parseJobProgressDetail(statusDescription);
  const visibleStatusDescription =
    progressDetail && !retrying ? null : statusDescription;

  useEffect(() => {
    if (status === "done") {
      router.push(repoPath(owner, repo, repoId));
    }
  }, [status, repoId, owner, repo, router]);

  return (
    <div className="flex flex-col gap-4 w-full max-w-xl">
      <div
        className="flex flex-col gap-1"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >
        <p className="text-sm font-medium text-foreground capitalize">{status}…</p>
        {visibleStatusDescription && (
          <p
            className={`text-xs animate-pulse ${
              retrying ? "text-amber-500" : "text-muted-foreground"
            }`}
          >
            {retrying ? "⟳ " : ""}{visibleStatusDescription}
          </p>
        )}
      </div>
      <Progress
        value={progress}
        className={cn("h-2", retrying ? "opacity-60" : "")}
      />
      <p className="text-xs text-muted-foreground">{progress}%</p>
      {progressDetail?.kind === "page" && (
        <div className="flex flex-col gap-2 animate-in fade-in slide-in-from-bottom-2 duration-500">
          <p className="rounded-md border bg-indigo-50/50 border-indigo-100 px-3 py-2 text-sm font-medium text-indigo-900">
            {progressDetail.title}
          </p>
        </div>
      )}
      {progressDetail?.kind === "batch" && (
        <div className="flex flex-col gap-2 animate-in fade-in slide-in-from-bottom-2 duration-500">
          <ul className="space-y-2">
            {progressDetail.titles.map((title) => (
              <li
                key={title}
                className="rounded-md border bg-indigo-50/50 border-indigo-100 px-3 py-2 text-sm font-medium text-indigo-900"
              >
                {title}
              </li>
            ))}
          </ul>
        </div>
      )}
      {progressDetail?.kind === "active" && (
        <div className="flex flex-col gap-2">
          <ul className="space-y-2">
            {progressDetail.pages.map((page) => (
              <li
                key={page.title}
                className="rounded-md border bg-indigo-50/50 border-indigo-100 px-3 py-2 animate-in fade-in slide-in-from-bottom-2 duration-500"
              >
                <div className="flex justify-between items-center">
                  <p className="text-sm font-medium text-indigo-900">{page.title}</p>
                  <span className="flex h-2 w-2 rounded-full bg-indigo-500 animate-pulse" />
                </div>
                <p className="text-xs text-indigo-600/70 mt-0.5 font-medium">{page.stage}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
      {status === "failed" && (
        <p className="text-destructive text-sm">Generation failed. Check server logs.</p>
      )}
    </div>
  );
}
