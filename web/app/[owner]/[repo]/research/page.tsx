import { resolveRouteRepo } from "@/lib/repo-route.server";
import { getRepo } from "@/lib/api";

export default async function ResearchPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const { repoId, repoMeta } = await resolveRouteRepo(owner, repo);

  // B5: Deep Research is temporarily disabled pending KeywordIndex migration (issue #43).
  // The features flag from the API confirms the disabled state.
  let deepResearchEnabled = false;
  try {
    const repoData = await getRepo(repoId);
    deepResearchEnabled = repoData.features?.deep_research !== false;
  } catch {
    deepResearchEnabled = false;
  }

  const title = `Deep Research — ${repoMeta?.owner ?? owner}/${repoMeta?.name ?? repo}`;

  if (!deepResearchEnabled) {
    return (
      <div style={{ padding: "1rem" }}>
        <h1
          style={{
            fontSize: "1.25rem",
            fontWeight: "bold",
            marginBottom: "1rem",
          }}
        >
          {title}
        </h1>
        <p style={{ color: "#6b7280" }}>
          Deep Research is temporarily unavailable while migrating to keyword
          retrieval. See{" "}
          <a
            href="https://github.com/lazyxiang/AutoWiki/issues/43"
            style={{ color: "#4f46e5", textDecoration: "underline" }}
          >
            issue #43
          </a>{" "}
          for the migration plan.
        </p>
      </div>
    );
  }

  // Unreachable while feature is disabled — kept for re-enable reference.
  const { default: ResearchPanel } = await import(
    "@/components/ResearchPanel"
  );
  return (
    <div style={{ padding: "1rem" }}>
      <h1
        style={{ fontSize: "1.25rem", fontWeight: "bold", marginBottom: "1rem" }}
      >
        {title}
      </h1>
      <ResearchPanel repoId={repoId} />
    </div>
  );
}
