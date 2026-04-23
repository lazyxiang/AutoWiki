import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function repoPath(_owner: string, repo: string, id: string): string {
  return `/${id}/${encodeURIComponent(repo)}`
}
