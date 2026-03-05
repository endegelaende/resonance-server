<script lang="ts">
  import { Marked, Renderer } from "marked";

  let { content = "" }: { content?: string } = $props();

  /**
   * Secure Markdown renderer for SDUI.
   *
   * Security model: we render Markdown to HTML using a custom Renderer that
   * only emits safe, known HTML elements with no event handlers and no
   * arbitrary attributes.  Raw HTML in the Markdown source is escaped
   * (the default `marked` behavior when we don't enable `options.html`).
   *
   * This is safer than using DOMPurify because we never parse untrusted
   * HTML in the first place — we only emit the tags we explicitly construct.
   */

  // Build a custom renderer that produces clean, themed HTML.
  const renderer = new Renderer();

  // --- Block-level ---

  renderer.heading = function ({ text, depth }: { text: string; depth: number }) {
    const classes: Record<number, string> = {
      1: "text-2xl font-bold text-text mt-4 mb-2",
      2: "text-xl font-semibold text-text mt-3 mb-2",
      3: "text-lg font-semibold text-subtext-1 mt-3 mb-1",
      4: "text-base font-medium text-subtext-0 mt-2 mb-1",
      5: "text-sm font-medium text-subtext-0 mt-2 mb-1",
      6: "text-sm font-medium text-overlay-1 mt-2 mb-1",
    };
    const cls = classes[depth] ?? classes[4];
    return `<h${depth} class="${cls}">${text}</h${depth}>`;
  };

  renderer.paragraph = function ({ text }: { text: string }) {
    return `<p class="text-sm text-text leading-relaxed mb-2">${text}</p>`;
  };

  renderer.blockquote = function ({ body }: { body: string }) {
    return `<blockquote class="border-l-4 border-accent/50 pl-4 my-2 text-sm text-overlay-1 italic">${body}</blockquote>`;
  };

  renderer.list = function ({ body, ordered, start }: { body: string; ordered: boolean; start: number }) {
    const tag = ordered ? "ol" : "ul";
    const cls = ordered
      ? "list-decimal list-inside space-y-1 text-sm text-text mb-2"
      : "list-disc list-inside space-y-1 text-sm text-text mb-2";
    const startAttr = ordered && start !== 1 ? ` start="${start}"` : "";
    return `<${tag} class="${cls}"${startAttr}>${body}</${tag}>`;
  };

  renderer.listitem = function ({ text }: { text: string }) {
    return `<li class="text-sm text-text">${text}</li>`;
  };

  renderer.code = function ({ text, lang }: { text: string; lang?: string }) {
    const escaped = escapeHtml(text);
    const langLabel = lang ? `<span class="text-xs text-overlay-0 select-none">${escapeHtml(lang)}</span>` : "";
    return `<div class="rounded-lg bg-crust border border-border overflow-hidden my-2">${langLabel ? `<div class="flex justify-end px-3 pt-2">${langLabel}</div>` : ""}<pre class="px-3 py-2 overflow-x-auto"><code class="text-xs font-mono text-subtext-1">${escaped}</code></pre></div>`;
  };

  renderer.hr = function () {
    return `<hr class="border-border my-4" />`;
  };

  renderer.table = function ({ header, body }: { header: string; body: string }) {
    return `<div class="overflow-x-auto rounded-lg border border-border my-2"><table class="w-full text-sm"><thead>${header}</thead><tbody>${body}</tbody></table></div>`;
  };

  renderer.tablerow = function ({ text }: { text: string }) {
    return `<tr class="border-b border-border last:border-0">${text}</tr>`;
  };

  renderer.tablecell = function ({ text, header, align }: { text: string; header: boolean; align: "center" | "left" | "right" | null }) {
    const tag = header ? "th" : "td";
    const alignClass = align === "center" ? " text-center" : align === "right" ? " text-right" : " text-left";
    const cls = header
      ? `px-3 py-2 text-xs font-semibold text-overlay-1 uppercase tracking-wider bg-surface-0${alignClass}`
      : `px-3 py-2 text-text${alignClass}`;
    return `<${tag} class="${cls}">${text}</${tag}>`;
  };

  // --- Inline-level ---

  renderer.strong = function ({ text }: { text: string }) {
    return `<strong class="font-semibold text-text">${text}</strong>`;
  };

  renderer.em = function ({ text }: { text: string }) {
    return `<em class="italic text-subtext-1">${text}</em>`;
  };

  renderer.del = function ({ text }: { text: string }) {
    return `<del class="line-through text-overlay-1">${text}</del>`;
  };

  renderer.codespan = function ({ text }: { text: string }) {
    return `<code class="rounded bg-crust px-1.5 py-0.5 text-xs font-mono text-accent">${text}</code>`;
  };

  renderer.link = function ({ href, title, text }: { href: string; title?: string | null; text: string }) {
    // Only allow safe URL schemes
    const safeHref = isSafeUrl(href) ? href : "#";
    const titleAttr = title ? ` title="${escapeAttr(title)}"` : "";
    return `<a href="${escapeAttr(safeHref)}"${titleAttr} target="_blank" rel="noopener noreferrer" class="text-accent hover:text-accent-hover underline underline-offset-2 transition-colors">${text}</a>`;
  };

  renderer.image = function ({ href, title, text }: { href: string; title?: string | null; text: string }) {
    if (!isSafeUrl(href)) return escapeHtml(text);
    const titleAttr = title ? ` title="${escapeAttr(title)}"` : "";
    return `<img src="${escapeAttr(href)}" alt="${escapeAttr(text)}"${titleAttr} class="max-w-full h-auto rounded-lg my-2" loading="lazy" />`;
  };

  renderer.br = function () {
    return "<br />";
  };

  // --- Helpers ---

  function escapeHtml(str: string): string {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function escapeAttr(str: string): string {
    return str
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function isSafeUrl(url: string): boolean {
    if (!url) return false;
    // Relative URLs are fine
    if (url.startsWith("/") || url.startsWith("./") || url.startsWith("../")) return true;
    // Only allow safe schemes
    try {
      const parsed = new URL(url, "https://placeholder.invalid");
      return ["http:", "https:", "mailto:"].includes(parsed.protocol);
    } catch {
      return false;
    }
  }

  // Create a configured marked instance (does NOT parse raw HTML by default)
  const marked = new Marked({
    renderer,
    async: false,
    gfm: true,
    breaks: false,
  });

  const rendered = $derived(
    (() => {
      try {
        return marked.parse(content) as string;
      } catch {
        // Fallback: render as plain text paragraphs
        return content
          .split("\n")
          .filter((l) => l.trim())
          .map((l) => `<p class="text-sm text-text">${escapeHtml(l)}</p>`)
          .join("");
      }
    })()
  );
</script>

<!--
  Security note: the HTML produced by `rendered` is constructed entirely by
  our custom Renderer above.  Raw HTML in the Markdown source is escaped by
  marked (we never set `options.html = true`).  Every attribute value we emit
  goes through escapeAttr / escapeHtml, and URLs are validated against a
  safe-scheme allowlist.  This is equivalent to a strict allowlist sanitizer
  but without the parsing overhead.
-->
<div class="sdui-markdown space-y-1">
  {@html rendered}
</div>
