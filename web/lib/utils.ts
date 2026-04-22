import { createHash } from "crypto"
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function repoId(owner: string, repo: string, platform = "github"): string {
  if (/^[a-f0-9]{16}$/i.test(owner)) {
    return owner
  }
  const [routePlatform, routeOwner] = owner.includes(":")
    ? owner.split(/:(.*)/s).filter(Boolean)
    : [platform, owner]
  return createHash("sha256")
    .update(`${routePlatform}:${routeOwner}/${repo}`)
    .digest("hex")
    .slice(0, 16)
}

export function repoPath(_owner: string, repo: string, id: string): string {
  return `/${id}/${encodeURIComponent(repo)}`
}
