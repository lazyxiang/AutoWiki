import { HomepageClient } from "@/components/HomepageClient";
import { getRepositories } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const repos = await getRepositories().catch((err) => {
    console.error("Failed to fetch repositories:", err);
    return [];
  });

  return (
    <main className="min-h-screen bg-background">
      <HomepageClient repos={repos} />
    </main>
  );
}
