// A minimal, safe markdown renderer: the input is HTML-escaped first, then a
// small subset of markdown is applied. Citations become clickable chips:
//   (p. 12)                 one document
//   (report.pdf, p. 12)     across documents
//   (receipt.jpg)           a photo

export function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

const FILE = String.raw`[^()\n]{1,80}?\.(?:pdf|docx|txt|md|png|jpe?g|webp)`;
const PAGE = String.raw`pp?\.\s?\d[^()]{0,40}?`;
const CITE_FILE_PAGE = new RegExp(String.raw`\((${FILE}),?\s+(${PAGE})\)`, "gi");
const CITE_FILE = new RegExp(String.raw`\((${FILE})\)`, "gi");
const CITE_PAGE = new RegExp(String.raw`\((${PAGE})\)`, "g");

function citeButton(label, page, file) {
  const fileAttr = file ? ` data-file="${file}"` : "";
  const pageAttr = page ? ` data-page="${page}"` : "";
  return `<button type="button" class="cite"${fileAttr}${pageAttr}>${label}</button>`;
}

function firstNumber(text) {
  return text.match(/\d+/)[0];
}

export function renderInline(text) {
  return text
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*\s][^*]*?)\*/g, "<em>$1</em>")
    .replace(CITE_FILE_PAGE, (_, file, ref) => citeButton(`${file} · ${ref}`, firstNumber(ref), file))
    .replace(CITE_FILE, (_, file) => citeButton(file, "", file))
    .replace(CITE_PAGE, (_, ref) => citeButton(ref, firstNumber(ref), ""));
}

export function renderMarkdown(source) {
  const lines = escapeHtml(source).split("\n");
  let html = "";
  let paragraph = [];
  let listType = null;
  let inCode = false;
  let code = [];
  let tableRows = [];

  const flushParagraph = () => {
    if (paragraph.length) html += `<p>${renderInline(paragraph.join("<br>"))}</p>`;
    paragraph = [];
  };
  const closeList = () => {
    if (listType) html += `</${listType}>`;
    listType = null;
  };
  // Pipe tables: "| a | b |" rows where the second row is "|---|---|".
  const flushTable = () => {
    if (!tableRows.length) return;
    const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    const isSeparator = (row) => /^\|?(\s*:?-{2,}:?\s*\|)*\s*:?-{2,}:?\s*\|?$/.test(row.trim());
    if (tableRows.length >= 2 && isSeparator(tableRows[1])) {
      const head = cells(tableRows[0]).map((c) => `<th>${renderInline(c)}</th>`).join("");
      const body = tableRows.slice(2)
        .map((row) => `<tr>${cells(row).map((c) => `<td>${renderInline(c)}</td>`).join("")}</tr>`)
        .join("");
      html += `<div class="table-wrap"><table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    } else {
      html += `<p>${renderInline(tableRows.join("<br>"))}</p>`; // not a table (yet, while streaming)
    }
    tableRows = [];
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (inCode) {
        html += `<pre><code>${code.join("\n")}</code></pre>`;
        code = [];
      } else {
        flushParagraph();
        closeList();
        flushTable();
      }
      inCode = !inCode;
      continue;
    }
    if (inCode) { code.push(line); continue; }

    if (line.trim().startsWith("|")) {
      flushParagraph();
      closeList();
      tableRows.push(line);
      continue;
    }
    flushTable();

    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    const heading = line.match(/^\s*#{1,6}\s+(.*)$/);

    if (bullet || numbered) {
      flushParagraph();
      const type = bullet ? "ul" : "ol";
      if (listType !== type) { closeList(); html += `<${type}>`; listType = type; }
      html += `<li>${renderInline((bullet || numbered)[1])}</li>`;
    } else if (heading) {
      flushParagraph(); closeList();
      html += `<h4>${renderInline(heading[1])}</h4>`;
    } else if (!line.trim()) {
      flushParagraph(); closeList();
    } else {
      closeList();
      paragraph.push(line);
    }
  }
  if (inCode) html += `<pre><code>${code.join("\n")}</code></pre>`;
  flushTable();
  flushParagraph();
  closeList();
  return html;
}
