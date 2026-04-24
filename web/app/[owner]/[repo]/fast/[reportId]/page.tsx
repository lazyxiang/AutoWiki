import { FastReportWorkspace } from "@/components/fast-report/FastReportWorkspace";
import { resolveRouteRepo } from "@/lib/repo-route.server";

export default async function FastReportPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string; reportId: string }>;
}) {
  const { owner, repo, reportId } = await params;
  const { repoId, repoMeta } = await resolveRouteRepo(owner, repo);
  const repoLabel = `${repoMeta?.owner ?? owner}/${repoMeta?.name ?? repo}`;

  return (
    <FastReportWorkspace
      owner={owner}
      repo={repo}
      repoId={repoId}
      repoLabel={repoLabel}
      reportId={reportId}
    />
  );
}
