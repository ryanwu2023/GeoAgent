import { useMemo } from "react";
import { BookOpen, ChevronDown, ExternalLink } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { briefPresentation } from "./briefPresentation";

type Section = { title: string; id: string };

const SOURCE_HEADING = /(证据|信源|来源|参考资料|sources?|references?)/i;

function headingId(title: string, index: number) {
  const clean = title.trim().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-|-$/g, "");
  return `brief-section-${clean || index + 1}`;
}

function prepare(markdown: string) {
  const presented = briefPresentation(markdown);
  const matches = [...presented.matchAll(/^##\s+(.+)$/gm)];
  const sections: Section[] = matches.map((match, index) => ({
    title: match[1].trim(),
    id: headingId(match[1], index),
  }));
  const sourceIndex = matches.findIndex((match) => SOURCE_HEADING.test(match[1]));
  const splitAt = sourceIndex >= 0 ? matches[sourceIndex].index ?? -1 : -1;
  const body = splitAt >= 0 ? presented.slice(0, splitAt).trim() : presented;
  const sources = splitAt >= 0 ? presented.slice(splitAt).trim() : "";
  const sourceCount = sources ? new Set([...sources.matchAll(/\[来源\s+(\d+)\]/g)].map((item) => item[1])).size : 0;
  return { body, sources, sections: sourceIndex >= 0 ? sections.slice(0, sourceIndex) : sections, sourceCount };
}

export default function BriefViewer({ markdown }: { markdown: string }) {
  const brief = useMemo(() => prepare(markdown), [markdown]);
  let headingIndex = 0;
  const components = {
    h2: ({ children }: { children?: React.ReactNode }) => {
      const section = brief.sections[headingIndex++];
      return <h2 id={section?.id}>{children}</h2>;
    },
    a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
      <a href={href} target="_blank" rel="noreferrer">{children}<ExternalLink size={11} aria-hidden="true" /></a>
    ),
  };

  return <div className="brief-reader">
    {brief.sections.length > 1 && <nav className="brief-outline" aria-label="简报章节">
      <span><BookOpen size={14} />快速阅读</span>
      <div>{brief.sections.map(section => <a key={section.id} href={`#${section.id}`}>{section.title}</a>)}</div>
    </nav>}
    <article className="brief-document">
      <Markdown remarkPlugins={[remarkGfm]} components={components}>{brief.body}</Markdown>
    </article>
    {brief.sources && <details className="brief-sources">
      <summary><span>证据与信源{brief.sourceCount ? `（${brief.sourceCount} 条）` : ""}</span><small>展开核验原始资料</small><ChevronDown size={16} /></summary>
      <div className="brief-source-list"><Markdown remarkPlugins={[remarkGfm]}>{brief.sources}</Markdown></div>
    </details>}
  </div>;
}
