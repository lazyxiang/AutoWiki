"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Progress } from "@/components/ui/progress";
import { useJobProgress } from "@/lib/ws";
import { repoPath } from "@/lib/utils";

interface Props {
  jobId: string;
  repoId: string;
  owner: string;
  repo: string;
}

export function JobProgressBar({ jobId, repoId, owner, repo }: Props) {
  const { progress, status, statusDescription, retrying } = useJobProgress(jobId);
  const router = useRouter();

  const pageMatch =
    statusDescription?.match(
      /^(?:Generating|Regenerating) page "(.+)" \((\d+)\/(\d+)\)(?: \[level (\d+)\/(\d+)\])?/,
    ) ?? null;
  const currentPageTitle = pageMatch?.[1] ?? null;
  const pageIndex = pageMatch ? Number(pageMatch[2]) : null;
  const totalPages = pageMatch ? Number(pageMatch[3]) : null;
  const levelIndex = pageMatch?.[4] ? Number(pageMatch[4]) : null;
  const totalLevels = pageMatch?.[5] ? Number(pageMatch[5]) : null;

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
        {statusDescription && (
          <p
            className={`text-xs animate-pulse ${
              retrying ? "text-amber-500" : "text-muted-foreground"
            }`}
          >
            {retrying ? "⟳ " : ""}{statusDescription}
          </p>
        )}
      </div>
      <Progress
        value={progress}
        className={`h-2 ${retrying ? "opacity-60" : ""}`}
      />
      <p className="text-xs text-muted-foreground">{progress}%</p>
      {currentPageTitle && pageIndex && totalPages && (
        <div className="rounded-md border bg-muted/30 px-3 py-2">
          <p className="text-xs text-muted-foreground">
            Page {pageIndex} of {totalPages}
            {levelIndex && totalLevels ? ` • Level ${levelIndex}/${totalLevels}` : ""}
          </p>
          <p className="text-sm font-medium text-foreground">{currentPageTitle}</p>
        </div>
      )}
      {status === "failed" && (
        <p className="text-destructive text-sm">Generation failed. Check server logs.</p>
      )}
    </div>
  );
}
