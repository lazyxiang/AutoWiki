import { createHash } from "crypto"
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function repoId(owner: string, repo: string): string {
  return createHash("sha256").update(`github:${owner}/${repo}`).digest("hex").slice(0, 16)
}
