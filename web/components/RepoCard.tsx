import { Star, Clock, Code2 } from "lucide-react";
import Link from "next/link";

interface RepoCardProps {
  owner: string;
  name: string;
  description?: string;
  stars?: number;
  language?: string;
  updatedAt?: string;
}

export function RepoCard({ owner, name, description, stars, language, updatedAt }: RepoCardProps) {
  return (
    <Link href={`/${owner}/${name}`} className="group block p-5 bg-card border border-border rounded-xl hover:border-primary/50 hover:shadow-sm transition-all">
      <h3 className="text-lg font-bold group-hover:text-primary transition-colors">
        <span className="text-muted-foreground font-normal">{owner}/</span>{name}
      </h3>
      <p className="mt-2 text-sm text-muted-foreground line-clamp-2 h-10">
        {description || "No description available."}
      </p>
      <div className="mt-4 flex items-center gap-4 text-xs text-muted-foreground">
        {language && (
          <span className="flex items-center gap-1.5">
            <Code2 size={14} className="text-primary" /> {language}
          </span>
        )}
        {stars !== undefined && (
          <span className="flex items-center gap-1.5">
            <Star size={14} className="text-yellow-500 fill-yellow-500" /> {stars.toLocaleString()}
          </span>
        )}
        {updatedAt && (
          <span className="flex items-center gap-1.5 ml-auto">
            <Clock size={14} /> {updatedAt}
          </span>
        )}
      </div>
    </Link>
  );
}
