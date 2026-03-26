"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Progress } from "@/components/ui/progress";
import { useJobProgress } from "@/lib/ws";

interface Props {
  jobId: string;
  owner: string;
  repo: string;
}

export function JobProgressBar({ jobId, owner, repo }: Props) {
  const { progress, status, statusDescription, retrying } = useJobProgress(jobId);
  const router = useRouter();

  useEffect(() => {
    if (status === "done") {
      router.push(`/${owner}/${repo}`);
    }
  }, [status, owner, repo, router]);

  return (
    <div className="flex flex-col gap-4 w-full max-w-xl">
      <div className="flex flex-col gap-1">
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
      {status === "failed" && (
        <p className="text-destructive text-sm">Generation failed. Check server logs.</p>
      )}
    </div>
  );
}
