"use client";
import { RepoCard } from "@/components/RepoCard";
import {
  filterRepositoriesForQuery,
  isRepositoryUrl,
} from "@/lib/repo-search";
import type { Repository } from "@/lib/api";

interface RepoGridProps {
  repos: Repository[];
  query: string;
}

export function RepoGrid({ repos, query }: RepoGridProps) {
  const filtered = filterRepositoriesForQuery(query, repos);
  const hasQuery = query.trim().length > 0 && !isRepositoryUrl(query);

  return (
    <section className="max-w-7xl mx-auto px-6 py-16">
      {repos.length > 0 && (
        <h2 className="text-2xl font-bold mb-8">
          {hasQuery ? `Results for "${query}"` : "Recently Indexed"}
        </h2>
      )}
      {filtered.length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {filtered.slice(0, 20).map((repo) => (
            <RepoCard
              id={repo.id}
              key={repo.id}
              owner={repo.owner}
              name={repo.name}
              description={repo.description}
              stars={repo.stars}
              language={repo.language}
              updatedAt={repo.indexed_at_formatted}
              wikiLanguage={repo.wiki_language ?? "en"}
            />
          ))}
        </div>
      ) : (
        <div className="text-center py-12 border rounded-xl bg-slate-50/50">
          <p className="text-muted-foreground">
            {hasQuery
              ? `No repositories match your search.`
              : "No repositories indexed yet. Be the first!"}
          </p>
        </div>
      )}
    </section>
  );
}
