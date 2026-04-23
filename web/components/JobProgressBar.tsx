"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Progress } from "@/components/ui/progress";
import { useJobProgress } from "@/lib/ws";
import { repoPath } from "@/lib/utils";
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
        className={`h-2 ${retrying ? "opacity-60" : ""}`}
      />
      <p className="text-xs text-muted-foreground">{progress}%</p>
      {progressDetail?.kind === "page" && (
        <div className="flex flex-col gap-2">
          <p className="rounded-md border bg-muted/30 px-3 py-2 text-sm font-medium text-foreground">
            {progressDetail.title}
          </p>
        </div>
      )}
      {progressDetail?.kind === "batch" && (
        <div className="flex flex-col gap-2">
          <ul className="space-y-2">
            {progressDetail.titles.map((title) => (
              <li
                key={title}
                className="rounded-md border bg-muted/30 px-3 py-2 text-sm font-medium text-foreground"
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
                className="rounded-md border bg-muted/30 px-3 py-2"
              >
                <p className="text-sm font-medium text-foreground">{page.title}</p>
                <p className="text-xs text-muted-foreground">{page.stage}</p>
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
