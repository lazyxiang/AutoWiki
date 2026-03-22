import { IndexForm } from "@/components/IndexForm";

export default function HomePage() {
  return (
    <main className="min-h-screen flex flex-col items-center justify-center gap-8 p-8 bg-background">
      <div className="text-center">
        <h1 className="text-4xl font-bold tracking-tight">AutoWiki</h1>
        <p className="text-muted-foreground mt-2">AI-powered wiki generator for GitHub repositories</p>
      </div>
      <IndexForm />
    </main>
  );
}
