/** Scraped URLs are untrusted: only http(s) links are ever rendered as `href`. */
export function safeUrl(url: string | null | undefined): string | undefined {
  try {
    const parsed = new URL(url ?? "");
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : undefined;
  } catch {
    return undefined;
  }
}
