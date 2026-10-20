import type { ExternalMcpServer, TranslationState, TranslationStatus } from "./types";

// Never silently pick a different service when a saved selection was removed.
export function reconcileNewsServer(selected: string, servers: ExternalMcpServer[]): string {
  if (selected) return servers.some(server => server.id === selected) ? selected : "";
  return servers.length === 1 ? servers[0].id : "";
}

export function canImportNews(selected: string, servers: ExternalMcpServer[]): boolean {
  const server = servers.find(item => item.id === selected);
  return Boolean(server?.configured && server.news_import?.supported !== false);
}

export function titleTranslationLabel(record: TranslationState, status?: TranslationStatus): string {
  if (record.title_translation_state !== "untranslated") return "";
  if (status?.available === false) return "原文 · 翻译未配置";
  if (status?.running) return "原文 · 当前快照翻译中";
  return "原文 · 暂无中文译文";
}
