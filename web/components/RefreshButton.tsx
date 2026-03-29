"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw } from "lucide-react";
import { refreshRepo } from "@/lib/api";
import { repoId } from "@/lib/utils";
import { Button } from "./ui/button";

interface Props {
  owner: string;
  repo: string;
}

export function RefreshButton({ owner, repo }: Props) {
  const router = useRouter();
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function handleRefresh() {
    setRefreshing(true);
    setError("");
    try {
      const { job_id } = await refreshRepo(repoId(owner, repo));
      router.push(
        `/jobs/${job_id}?repo_id=${repoId(owner, repo)}&owner=${encodeURIComponent(
          owner
        )}&repo=${encodeURIComponent(repo)}`
      );
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Refresh failed");
      setRefreshing(false);
    }
  }

  return (
    <div className="flex flex-col gap-1 items-end">
      <Button
        variant="ghost"
        size="icon"
        onClick={handleRefresh}
        disabled={refreshing}
        className="h-8 w-8 text-muted-foreground hover:text-foreground"
        title="Refresh wiki"
      >
        <RefreshCw className={`h-4 w-4 ${refreshing ? "animate-spin" : ""}`} />
      </Button>
      {error && <p className="text-destructive text-[10px] absolute mt-8">{error}</p>}
    </div>
  );
}
