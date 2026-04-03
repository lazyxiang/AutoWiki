"use client";
import { Globe } from "lucide-react";

interface LanguageSwitcherProps {
  value: string;
  onChange: (lang: string) => void;
}

const LANGUAGES = [
  { code: "en", label: "EN" },
  { code: "zh", label: "中文" },
];

export function LanguageSwitcher({ value, onChange }: LanguageSwitcherProps) {
  return (
    <div className="flex items-center gap-1.5">
      <Globe size={14} className="text-muted-foreground" />
      <div className="flex rounded-lg border border-border overflow-hidden text-xs font-medium">
        {LANGUAGES.map((lang) => (
          <button
            key={lang.code}
            type="button"
            onClick={() => onChange(lang.code)}
            className={
              value === lang.code
                ? "px-2.5 py-1 bg-primary text-primary-foreground transition-colors"
                : "px-2.5 py-1 bg-background text-muted-foreground hover:bg-muted transition-colors"
            }
          >
            {lang.label}
          </button>
        ))}
      </div>
    </div>
  );
}
