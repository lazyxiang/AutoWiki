import { getRepoWiki } from "@/lib/api";
import { WikiSidebar } from "@/components/WikiSidebar";
import crypto from "crypto";

export default async function WikiLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const repoId = crypto.createHash("sha256").update(`github:${owner}/${repo}`).digest("hex").slice(0, 16);
  const { pages } = await getRepoWiki(repoId).catch(() => ({ pages: [] }));

  return (
    <div className="flex h-screen overflow-hidden">
      <WikiSidebar pages={pages} owner={owner} repo={repo} />
      <main className="flex-1 overflow-y-auto">{children}</main>
    </div>
  );
}
