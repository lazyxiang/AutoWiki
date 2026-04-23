import "server-only";

import { createHash } from "crypto";

export function repoId(
  owner: string,
  repo: string,
  platform = "github",
  options: { ownerIsRepoId?: boolean } = {},
): string {
  if (options.ownerIsRepoId && /^[a-f0-9]{16}$/i.test(owner)) {
    return owner.toLowerCase();
  }
  const separator = owner.indexOf(":");
  const routePlatform = separator === -1 ? platform : owner.slice(0, separator);
  const routeOwner = separator === -1 ? owner : owner.slice(separator + 1);
  return createHash("sha256")
    .update(`${routePlatform}:${routeOwner}/${repo}`)
    .digest("hex")
    .slice(0, 16);
}
