/**
 * Mermaid diagram sanitisation utilities.
 *
 * Quotes node labels and edge labels that contain characters with
 * special syntactic meaning in Mermaid (parentheses, pipes, curly
 * braces, angle brackets, slashes).
 */

const SPECIAL_CHARS = /[(){}|<>/]/;

/**
 * One regex per bracket type. Negative lookahead prevents single-bracket
 * patterns from matching double-bracket compound shapes like (()) / {{}}.
 */
const SQUARE_RE = /(\b\w+\[)(?!\[)([^"\]]+)(\])/g;
const ROUND_RE = /(\b\w+\()(?!\()([^")]+)(\))/g;
const CURLY_RE = /(\b\w+\{)(?!\{)([^"}]+)(\})/g;
const DOUBLE_ROUND_RE = /(\b\w+\(\()([^"]+)(\)\))/g;
const DOUBLE_CURLY_RE = /(\b\w+\{\{)([^"]+)(\}\})/g;

/** Edge label pattern: matches |label| where label is not already quoted. */
const EDGE_LABEL_RE = /(\|)([^"|][^|]*?)(\|)/g;

/**
 * Matches undirected labelled edges: --|  that is not already  -->|  or  ---|.
 * Negative lookbehind excludes  >  and  -  so existing directed arrows and
 * plain undirected lines are left untouched.
 */
const UNDIRECTED_EDGE_RE = /(?<![->])--\|/g;
const CLASS_INHERITANCE_FLOWCHART_RE =
  /^(\s*)([A-Za-z_][\w~$-]*)\s+-->\|>\s+([A-Za-z_][\w~$-]*)(\s*:\s*.*)?$/;

const COMPOUND_PAIRS: Record<string, string> = { "(": ")", "[": "]", "{": "}" };

function isCompoundShape(label: string): boolean {
  if (label.length < 2) return false;
  return COMPOUND_PAIRS[label[0]] === label[label.length - 1];
}

function nodeReplacer(_match: string, prefix: string, label: string, close: string): string {
  if (isCompoundShape(label)) {
    if (label.length >= 3) {
      const inner = label.slice(1, -1);
      if (SPECIAL_CHARS.test(inner)) {
        const escaped = inner.replace(/"/g, "#quot;");
        return `${prefix}${label[0]}"${escaped}"${label[label.length - 1]}${close}`;
      }
    }
    return _match;
  }
  if (SPECIAL_CHARS.test(label)) {
    const escaped = label.replace(/"/g, "#quot;");
    return `${prefix}"${escaped}"${close}`;
  }
  return _match;
}

function doubleBracketReplacer(_match: string, prefix: string, label: string, close: string): string {
  if (SPECIAL_CHARS.test(label)) {
    const escaped = label.replace(/"/g, "#quot;");
    return `${prefix}"${escaped}"${close}`;
  }
  return _match;
}

function edgeReplacer(_match: string, open: string, label: string, close: string): string {
  if (SPECIAL_CHARS.test(label)) {
    const escaped = label.replace(/"/g, "#quot;");
    return `${open}"${escaped}"${close}`;
  }
  return _match;
}

function diagramType(lines: string[]): string {
  for (const line of lines) {
    const first = line.trim().toLowerCase();
    if (!first) continue;
    if (first.startsWith("classdiagram")) return "classdiagram";
    return "";
  }
  return "";
}

function repairClassDiagramRelation(line: string, type: string): string {
  if (type !== "classdiagram") return line;
  const match = CLASS_INHERITANCE_FLOWCHART_RE.exec(line);
  if (!match) return line;
  const [, indent, child, parent, label = ""] = match;
  return `${indent}${parent} <|-- ${child}${label}`;
}

/**
 * Sanitise Mermaid diagram text by quoting node and edge labels that
 * contain special characters (parentheses, pipes, braces, angle brackets).
 */
export function sanitizeMermaid(text: string): string {
  const lines = text.split("\n");
  const type = diagramType(lines);
  return lines
    .flatMap(line => {
      // Strip embedded code-fence markers, e.g. ```mermaid text| NodeScanner["..."]
      // Keep any Mermaid content that follows the | separator; drop plain ```.
      if (line.trim().startsWith("```")) {
        const remainder = line.trim().replace(/^```[^|]*\|?\s*/, "");
        return remainder ? [remainder] : [];
      }
      return [line];
    })
    .map(line => {
      line = repairClassDiagramRelation(line, type);
      line = line.replace(UNDIRECTED_EDGE_RE, "-->|");
      line = line.replace(EDGE_LABEL_RE, edgeReplacer);
      line = line.replace(DOUBLE_ROUND_RE, doubleBracketReplacer);
      line = line.replace(DOUBLE_CURLY_RE, doubleBracketReplacer);
      line = line.replace(SQUARE_RE, nodeReplacer);
      line = line.replace(ROUND_RE, nodeReplacer);
      line = line.replace(CURLY_RE, nodeReplacer);
      return line;
    }).join("\n");
}
