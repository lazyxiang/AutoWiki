"use client";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { upsertToken, deleteToken } from "@/lib/api";

interface TokenFormProps {
  platform: string;
  label: string;
  hasToken: boolean;
  maskedToken: string | null;
}

function maskToken(raw: string): string {
  if (raw.length <= 4) return "••••";
  return "••••••••" + raw.slice(-4);
}

export function TokenForm({
  platform,
  label,
  hasToken,
  maskedToken,
}: TokenFormProps) {
  const [token, setToken] = useState("");
  // State initialized from server props; parent must use key={platform} to remount on prop changes.
  const [currentHas, setCurrentHas] = useState(hasToken);
  const [currentMasked, setCurrentMasked] = useState(maskedToken);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function handleSave() {
    if (!token.trim()) return;
    setSaving(true);
    setError("");
    try {
      await upsertToken(platform, token.trim());
      setCurrentHas(true);
      setCurrentMasked(maskToken(token.trim()));
      setToken("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save token");
    } finally {
      setSaving(false);
    }
  }

  async function handleClear() {
    setSaving(true);
    setError("");
    try {
      await deleteToken(platform);
      setCurrentHas(false);
      setCurrentMasked(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to clear token");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="p-5 border rounded-xl bg-card space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-lg">{label}</h2>
        {currentHas && currentMasked && (
          <span className="text-xs text-muted-foreground font-mono">
            {currentMasked}
          </span>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        {currentHas
          ? "Token stored. Enter a new value to replace it."
          : "No token stored. Public repositories work without a token."}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void handleSave();
        }}
        className="flex gap-2"
      >
        <Input
          type="password"
          aria-label={`${label} personal access token`}
          placeholder={
            currentHas ? "Replace existing token…" : "Personal access token"
          }
          value={token}
          onChange={(e) => setToken(e.target.value)}
          disabled={saving}
          className="flex-1"
        />
        <Button type="submit" disabled={saving || !token.trim()}>
          {saving ? "Saving…" : "Save"}
        </Button>
        {currentHas && (
          <Button
            type="button"
            variant="outline"
            onClick={handleClear}
            disabled={saving}
          >
            Clear
          </Button>
        )}
      </form>
      {error && <p className="text-sm text-destructive">{error}</p>}
    </div>
  );
}
