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

/**
 * Sanitise Mermaid diagram text by quoting node and edge labels that
 * contain special characters (parentheses, pipes, braces, angle brackets).
 */
export function sanitizeMermaid(text: string): string {
  return text.split("\n").map(line => {
    line = line.replace(EDGE_LABEL_RE, edgeReplacer);
    line = line.replace(DOUBLE_ROUND_RE, doubleBracketReplacer);
    line = line.replace(DOUBLE_CURLY_RE, doubleBracketReplacer);
    line = line.replace(SQUARE_RE, nodeReplacer);
    line = line.replace(ROUND_RE, nodeReplacer);
    line = line.replace(CURLY_RE, nodeReplacer);
    return line;
  }).join("\n");
}
