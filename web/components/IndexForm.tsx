"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { submitRepo } from "@/lib/api";

interface IndexFormProps {
  wikiLanguage?: string;
}

export function IndexForm({ wikiLanguage = "en" }: IndexFormProps) {
  const [url, setUrl] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const router = useRouter();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const { repo_id, job_id } = await submitRepo(url, wikiLanguage);
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
    <div className="w-full max-w-2xl mx-auto">
      <form onSubmit={handleSubmit} className="relative group">
        <div className="flex items-center gap-2 p-2 bg-white dark:bg-zinc-900 rounded-2xl border border-border shadow-sm focus-within:ring-2 focus-within:ring-primary/20 focus-within:border-primary transition-all duration-200">
          <Input
            type="text"
            placeholder="Search or paste GitHub URL (e.g. github.com/owner/repo)"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={loading}
            className="flex-1 h-12 border-none shadow-none focus-visible:ring-0 text-lg bg-transparent px-4 font-normal"
          />
          <Button 
            type="submit" 
            disabled={loading || !url.trim()}
            className="h-12 px-8 rounded-xl text-lg font-semibold shadow-sm hover:shadow transition-all"
          >
            {loading ? "Submitting…" : "Get Started"}
          </Button>
        </div>
        {error && <p className="mt-3 text-destructive text-sm text-center font-medium">{error}</p>}
      </form>
    </div>
  );
}
