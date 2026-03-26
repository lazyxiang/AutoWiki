"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { submitRepo } from "@/lib/api";

export function IndexForm() {
  const [url, setUrl] = useState("");
  const [force, setForce] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const router = useRouter();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const { repo_id, job_id } = await submitRepo(url, force);
      const match = url.replace(/^https?:\/\//, "").match(/github\.com\/([^/]+)\/([^/]+)/);
      const owner = match?.[1] ?? "";
      const repo = match?.[2]?.replace(/\.git$/, "") ?? "";
      router.push(`/jobs/${job_id}?repo_id=${repo_id}&owner=${owner}&repo=${repo}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4 w-full max-w-xl">
      <Input
        type="text"
        placeholder="github.com/owner/repo"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        disabled={loading}
        className="font-mono"
      />
      <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer select-none">
        <input
          type="checkbox"
          checked={force}
          onChange={(e) => setForce(e.target.checked)}
          disabled={loading}
          className="rounded"
        />
        Force full regeneration
      </label>
      <Button type="submit" disabled={loading || !url.trim()}>
        {loading ? "Submitting…" : "Generate Wiki"}
      </Button>
      {error && <p className="text-destructive text-sm">{error}</p>}
    </form>
  );
}
