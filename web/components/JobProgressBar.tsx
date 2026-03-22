"use client";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { Progress } from "@/components/ui/progress";
import { useJobProgress } from "@/lib/ws";

interface Props {
  jobId: string;
  repoId: string;
  owner: string;
  repo: string;
}

export function JobProgressBar({ jobId, repoId, owner, repo }: Props) {
  const { progress, status } = useJobProgress(jobId);
  const router = useRouter();

  useEffect(() => {
    if (status === "done") {
      router.push(`/${owner}/${repo}`);
    }
  }, [status, owner, repo, router]);

  return (
    <div className="flex flex-col gap-4 w-full max-w-xl">
      <p className="text-sm text-muted-foreground capitalize">{status}…</p>
      <Progress value={progress} className="h-2" />
      <p className="text-xs text-muted-foreground">{progress}%</p>
      {status === "failed" && (
        <p className="text-destructive text-sm">Generation failed. Check server logs.</p>
      )}
    </div>
  );
}
