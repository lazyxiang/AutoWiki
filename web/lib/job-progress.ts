export type JobProgressDetail =
  | {
      kind: "page";
      title: string;
    }
  | {
      kind: "batch";
      titles: string[];
    }
  | {
      kind: "active";
      pages: Array<{ title: string; stage: string }>;
    };

export function parseJobProgressDetail(
  statusDescription: string | null,
): JobProgressDetail | null {
  if (!statusDescription) return null;

  const pageMatch = statusDescription.match(
    /^(?:Generating|Regenerating) page "(.+)" \((\d+)\/(\d+)\)(?: \[level (\d+)\/(\d+)\])?/,
  );
  if (pageMatch) {
    return {
      kind: "page",
      title: pageMatch[1],
    };
  }

  const batchMatch = statusDescription.match(
    /^(?:Generating|Regenerating) pages (\d+)-(\d+)\/(\d+)(?: \[level (\d+)\/(\d+)\])?: (.+)$/,
  );
  if (batchMatch) {
    return {
      kind: "batch",
      titles: parseQuotedTitles(batchMatch[6]),
    };
  }

  const activeMatch = statusDescription.match(
    /^(?:Generating|Regenerating) active pages: (.+)$/,
  );
  if (!activeMatch) return null;

  return {
    kind: "active",
    pages: parsePageStages(activeMatch[1]),
  };
}

function parseQuotedTitles(value: string): string[] {
  return Array.from(value.matchAll(/"([^"]+)"/g), (match) => match[1]);
}

function parsePageStages(value: string): Array<{ title: string; stage: string }> {
  return Array.from(
    value.matchAll(/"([^"]+)" \[([^\]]+)\]/g),
    (match) => ({
      title: match[1],
      stage: match[2],
    }),
  );
}
