"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { RefreshCw } from "lucide-react";
import { refreshRepo } from "@/lib/api";
import { Button } from "./ui/button";

/**
 * Props for the RefreshButton component.
 */
interface Props {
  /** The ID of the repository to refresh. */
  repoId: string;
  /** The owner of the repository (for navigation). */
  owner: string;
  /** The name of the repository (for navigation). */
  repo: string;
}

/**
 * A button that triggers a re-index of the repository.
 * Displays a loading spinner and handles error states.
 */
export function RefreshButton({ repoId, owner, repo }: Props) {
  const router = useRouter();
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  /**
   * Handles the click event to trigger repository refresh.
   */
  async function handleRefresh() {
    setRefreshing(true);
    setError("");
    try {
      const { job_id } = await refreshRepo(repoId);
      router.push(
        `/jobs/${job_id}?repo_id=${repoId}&owner=${encodeURIComponent(
          owner
        )}&repo=${encodeURIComponent(repo)}`
      );
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Refresh failed");
      setRefreshing(false);
    }
  }

  return (
    <div className="relative flex flex-col gap-1 items-end">
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
      {error && (
        <p className="absolute top-full mt-1 text-destructive text-[10px] whitespace-nowrap">
          {error}
        </p>
      )}
    </div>
  );
}
