import Link from "next/link";
import { getTokens } from "@/lib/api";
import { TokenForm } from "@/components/TokenForm";

export const dynamic = "force-dynamic";

const PLATFORMS = [
  { id: "github", label: "GitHub" },
  { id: "gitlab", label: "GitLab" },
  { id: "bitbucket", label: "Bitbucket" },
];

export default async function SettingsPage() {
  const tokens = await getTokens().catch((err) => {
    console.error("[settings] failed to load tokens:", err);
    return PLATFORMS.map((p) => ({
      platform: p.id,
      has_token: false,
      masked_token: null,
    }));
  });

  return (
    <main className="max-w-2xl mx-auto px-6 py-16">
      <Link
        href="/"
        className="text-sm text-muted-foreground hover:text-foreground mb-8 inline-block"
      >
        ← Back to home
      </Link>
      <h1 className="text-3xl font-bold mb-2">Settings</h1>
      <p className="text-muted-foreground mb-10">
        Store personal access tokens (PATs) to index private repositories.
        Tokens are stored in this AutoWiki instance and used only for requests
        to the selected repository hosting platform.
      </p>
      <div className="space-y-6">
        {PLATFORMS.map((p) => {
          const status = tokens.find((t) => t.platform === p.id);
          return (
            <TokenForm
              key={p.id}
              platform={p.id}
              label={p.label}
              hasToken={status?.has_token ?? false}
              maskedToken={status?.masked_token ?? null}
            />
          );
        })}
      </div>
    </main>
  );
}
