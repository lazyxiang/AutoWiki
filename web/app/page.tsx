import { RepoCard } from "@/components/RepoCard";
import { IndexForm } from "@/components/IndexForm";
import { getRepositories } from "@/lib/api";

export default async function HomePage() {
  const repos = await getRepositories(); // Assume sorted by indexed_at desc

  return (
    <main className="min-h-screen bg-background">
      {/* Hero Section */}
      <section className="pt-24 pb-16 px-6 text-center border-b border-dashed">
        <h1 className="text-5xl font-extrabold tracking-tight text-foreground">
          Explore Open Source Knowledge
        </h1>
        <p className="mt-4 text-xl text-muted-foreground max-w-2xl mx-auto">
          AI-powered wiki generator for any GitHub repository. Search for a repo or paste a link to get started.
        </p>
        <div className="mt-10 max-w-xl mx-auto">
          <IndexForm />
        </div>
      </section>

      {/* Grid Section */}
      <section className="max-w-7xl mx-auto px-6 py-16">
        <h2 className="text-2xl font-bold mb-8">Recently Indexed</h2>
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
            />
          ))}
        </div>
      </section>
    </main>
  );
}
