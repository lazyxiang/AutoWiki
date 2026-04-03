import { RepoCard } from "@/components/RepoCard";
import { HeroSection } from "@/components/HeroSection";
import { getRepositories } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const repos = await getRepositories().catch((err) => {
    console.error("Failed to fetch repositories:", err);
    return [];
  });

  return (
    <main className="min-h-screen bg-background">
      <HeroSection />

      {/* Grid Section */}
      <section className="max-w-7xl mx-auto px-6 py-16">
        <h2 className="text-2xl font-bold mb-8">Recently Indexed</h2>
        {repos.length > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {repos.slice(0, 20).map((repo) => (
              <RepoCard
                key={repo.id}
                owner={repo.owner}
                name={repo.name}
                description={repo.description}
                stars={repo.stars}
                language={repo.language}
                updatedAt={repo.indexed_at_formatted}
                wikiLanguage={repo.wiki_language}
              />
            ))}
          </div>
        ) : (
          <div className="text-center py-12 border rounded-xl bg-slate-50/50">
            <p className="text-muted-foreground">No repositories indexed yet. Be the first!</p>
          </div>
        )}
      </section>
    </main>
  );
}
