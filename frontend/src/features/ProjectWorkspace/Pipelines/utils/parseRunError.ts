/**
 * A run's `errors` entries arrive as one flat string that wraps a Python repr
 * of the upstream provider payload:
 *
 *   Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error',
 *   'message': '`temperature` is deprecated for this model.'}, 'request_id': 'req_…'}
 *
 * Only the code and the `message` inside are meaningful to a user, so this
 * pulls the embedded object out, rewrites the Python literal as JSON, and
 * returns those two. Anything it can't parse comes back `null` so the caller
 * can fall back to the raw string rather than showing nothing.
 */

export interface ParsedRunError {
  /** HTTP status from the prefix ("400"), or the payload's own code. Null when neither is present. */
  code: string | null;
  message: string;
}

/**
 * The status in a leading "Error code: 400 - " prefix. Only ever searched in
 * the text ahead of the payload, so a `code` *inside* the object can't be
 * mistaken for the prefix form.
 */
function extractPrefixCode(head: string): string | null {
  const match = /error\s*code:\s*([^\s-]+)/i.exec(head);
  return match ? match[1] : null;
}

/** The payload's own code, read off the same node the message came from. */
function readCode(record: Record<string, unknown>): string | null {
  const code = record.code ?? record.status_code;
  if (typeof code === "number") return String(code);
  if (typeof code === "string" && code.trim()) return code.trim();
  return null;
}

/**
 * Rewrites a Python repr as JSON: single-quoted strings become double-quoted
 * and None/True/False take their JSON spellings.
 *
 * Deliberately scans string-by-string instead of doing a blanket `'` → `"`
 * swap. Python's repr switches to double quotes for a string containing an
 * apostrophe ("can't"), so both quote styles show up in the same payload and a
 * blanket swap would corrupt any message with an apostrophe or a quoted term
 * in it. Escapes shared with JSON (\n, \t, \\, \") pass through untouched.
 */
function pythonLiteralToJson(literal: string): string | null {
  let out = "";
  let i = 0;

  while (i < literal.length) {
    const ch = literal[i];

    if (ch !== "'" && ch !== '"') {
      // Bare literals only appear outside strings, so this is the safe place
      // to translate them.
      if (ch === "N" || ch === "T" || ch === "F") {
        const keyword = /^(None|True|False)\b/.exec(literal.slice(i));
        if (keyword) {
          out += keyword[1] === "None" ? "null" : keyword[1].toLowerCase();
          i += keyword[1].length;
          continue;
        }
      }
      out += ch;
      i += 1;
      continue;
    }

    const quote = ch;
    let body = "";
    i += 1;

    while (i < literal.length && literal[i] !== quote) {
      if (literal[i] !== "\\") {
        const char = literal[i];
        if (char === '"') {
          // Legal unescaped inside '…', but JSON needs it escaped.
          body += '\\"';
        } else if (char < " ") {
          // A raw newline/tab the provider didn't escape is illegal inside a
          // JSON string and would sink the whole parse.
          body += `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`;
        } else {
          body += char;
        }
        i += 1;
        continue;
      }
      const escaped = literal[i + 1] ?? "";
      if (escaped === "'") {
        body += "'"; // \' is not a JSON escape
      } else if (escaped === "x") {
        body += `\\u00${literal.slice(i + 2, i + 4)}`; // \xNN → \u00NN
        i += 2;
      } else {
        body += `\\${escaped}`;
      }
      i += 2;
    }

    if (i >= literal.length) return null; // unterminated string — give up
    i += 1; // closing quote
    out += `"${body}"`;
  }

  return out;
}

/**
 * Providers nest the human-readable text differently — `{error: {message}}` for
 * Anthropic/OpenAI-shaped payloads, a bare `{message}` for others — so this
 * walks the parsed object for the first string `message` it finds. `error` is
 * searched before this level's own keys because a nested one is the specific
 * failure, while a sibling `message` is usually the generic wrapper.
 *
 * Returns the code found beside that message rather than the first `code`
 * anywhere in the payload, so the two always describe the same failure.
 */
function findErrorNode(value: unknown, depth = 0): ParsedRunError | null {
  if (depth > 6 || typeof value !== "object" || value === null) return null;

  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findErrorNode(item, depth + 1);
      if (found) return found;
    }
    return null;
  }

  const record = value as Record<string, unknown>;

  const nested = findErrorNode(record.error, depth + 1);
  if (nested) return nested;

  if (typeof record.message === "string" && record.message.trim()) {
    return { code: readCode(record), message: record.message.trim() };
  }

  for (const child of Object.values(record)) {
    const found = findErrorNode(child, depth + 1);
    if (found) return found;
  }
  return null;
}

/** The code and user-facing message inside a raw run error, or null if it can't be read. */
export function parseRunError(raw: string): ParsedRunError | null {
  const start = raw.indexOf("{");
  const end = raw.lastIndexOf("}");
  if (start === -1 || end <= start) return null;

  const json = pythonLiteralToJson(raw.slice(start, end + 1));
  if (!json) return null;

  let node: ParsedRunError | null;
  try {
    node = findErrorNode(JSON.parse(json));
  } catch {
    return null;
  }
  if (!node) return null;

  // The prefix carries the HTTP status, which is the more useful of the two
  // when a payload also names an internal code.
  return { ...node, code: extractPrefixCode(raw.slice(0, start)) ?? node.code };
}
