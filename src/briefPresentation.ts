// Keep immutable exported Markdown intact; shorten record identifiers only for reading.
export function briefPresentation(markdown:string){
  const sources=new Map<string,{number:number;url:string}>();
  for(const match of markdown.matchAll(/^- ([\w-]+) · \[[^\n]*?\]\(([^\s]+)\)/gm)){
    if(!sources.has(match[1]))sources.set(match[1],{number:sources.size+1,url:match[2]});
  }
  // Candidate lists also contain valid sources absent from the matrix index.
  for(const match of markdown.matchAll(/^- \[[^\n]*?\]\(([^\s]+)\) · [^\n]*? · ([\w-]+)\s*$/gm)){
    if(!sources.has(match[2]))sources.set(match[2],{number:sources.size+1,url:match[1]});
  }
  if(!sources.size)return markdown;
  const ids=[...sources.keys()].sort((a,b)=>b.length-a.length).map(id=>id.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
  const pattern=new RegExp(`(?<![\\w-])(${ids.join('|')})(?![\\w-])`,'g');
  // Preserve source URLs and existing Markdown links verbatim.
  return markdown.split(/(\[[^\]\n]*\]\([^\s]+\))/g).map(part=>{
    if(/^\[[^\]\n]*\]\([^\s]+\)$/.test(part))return part;
    return part.replace(pattern,id=>{const s=sources.get(id)!;return `[来源 ${s.number}](${s.url})`});
  }).join('');
}
