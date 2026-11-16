// @ts-check

"use strict";

// A markdownlint rule that reports each GFM table micromark finds, for oracle.py: the line
// of each row (document lines, front matter included), whether the row starts with `|`, and
// whether a block quote holds the table.

const ROWS = new Set([ "tableRow", "tableDelimiterRow" ]);

/** @type {import("markdownlint").Rule} */
module.exports = {
  "names": [ "table-lines" ],
  "description": "GFM table",
  "tags": [ "check" ],
  "parser": "micromark",
  "function": (params, onError) => {
    const offset = params.frontMatterLines.length;
    const rows = (tokens, found) => {
      for (const token of tokens) {
        if (ROWS.has(token.type)) {
          found.push([ token.startLine + offset, token.text.trimStart().startsWith("|") ]);
        } else {
          rows(token.children, found);
        }
      }
      return found;
    };
    const visit = (tokens, quoted) => {
      for (const token of tokens) {
        if (token.type === "table") {
          const detail = JSON.stringify({ quoted, "rows": rows(token.children, []) });
          onError({ "lineNumber": token.startLine, detail });
        } else if (token.type !== "htmlFlow") {
          // markdownlint parses an HTML block's content again, as Markdown without HTML, into
          // its children; MD060 leaves the tables there alone, and so does this rule
          visit(token.children, quoted || token.type === "blockQuote");
        }
      }
    };
    visit(params.parsers.micromark.tokens, false);
  }
};
