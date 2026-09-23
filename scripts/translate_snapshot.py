"""Run once or after imports. Translates public evidence locally; no text uploads."""
import os
import re
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.store import snapshots,get_snapshot,data_dir,digest
from backend.chinese import translation_cache,save_translation,is_chinese
from backend.translation_quality import language,normalize_terms

def translation_input(text):
    replacements={r'\bFM\b':'Foreign Minister',r'\bIRGC\b':'Islamic Revolutionary Guard Corps',r'\bUNSC\b':'United Nations Security Council',r'\bSCO\b':'Shanghai Cooperation Organization',r'\bBrig\. Gen\.':'Brigadier General',r'\brare-earth\b':'rare earth minerals',r'\bDevotees of Iran\b':'patriotic supporters of Iran',r'\bJan.Fada\b':'patriotic volunteer campaign',r'\bGhalibaf\b':'Iranian parliament speaker Ghalibaf'}
    for pattern,value in replacements.items():text=re.sub(pattern,value,text)
    return text

def terminology(text,original):
    if re.search(r'\btanker',original,re.I) and re.search(r'\b(oil|crude|shipping|maritime)\b',original,re.I) and not re.search(r'\btanks?\b',original,re.I):
        text=text.replace('坦克','油轮').replace('卡车','油轮')
    if re.search(r'\bdestroyer',original,re.I):text=text.replace('破坏者','驱逐舰').replace('摧毁者','驱逐舰')
    return text

def run():
    data_dir().mkdir(parents=True,exist_ok=True)
    lock=(data_dir()/'translation.lock').open('a+b')
    lock.write(b'0');lock.flush();lock.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        print('Translation worker already running',flush=True)
        return
    import ctranslate2
    from transformers import MarianTokenizer
    # SentencePiece on Windows cannot open some Unicode absolute paths.
    # Keep the model path relative to this project's process working directory.
    path=Path(os.path.relpath(data_dir()/'models'/'opus-mt-en-zh'))
    tokenizer=MarianTokenizer.from_pretrained(str(path),local_files_only=True)
    converted=path.parent/'opus-mt-en-zh-int8'
    if not (converted/'model.bin').exists():
        print('Preparing compact local translation model',flush=True)
        ctranslate2.converters.TransformersConverter(str(path)).convert(str(converted),quantization='int8',force=True)
    model=ctranslate2.Translator(str(converted),device='cpu',compute_type='int8',intra_threads=4)
    persian_path=path.parent/'persian-english'/'translate-fa_en-1_5'
    persian_model=persian_tokenizer=None
    if (persian_path/'model'/'model.bin').exists():
        import sentencepiece
        persian_tokenizer=sentencepiece.SentencePieceProcessor(model_file=str(persian_path/'sentencepiece.model'))
        persian_model=ctranslate2.Translator(str(persian_path/'model'),device='cpu',compute_type='int8',intra_threads=4)
    snapshot=get_snapshot(snapshots()[0]['id'])
    records=sorted(snapshot['records'],key=lambda r:(bool(r['topics']),r['published_at'] or ''),reverse=True)
    cache=translation_cache()
    texts=list(dict.fromkeys([r['title'] for r in records]+[r['source_name'] for r in records]+[r['text'] for r in records if r['text']]))
    texts=[t for t in texts if not is_chinese(t) and digest(t) not in cache and language(t) in ('en','fa')]
    print(f'Pending texts: {len(texts)}',flush=True)
    started=time.time()
    for offset in range(0,len(texts),8):
        batch=texts[offset:offset+8]
        # Split full excerpts at sentence boundaries; never silently truncate source material.
        segments=[];owners=[]
        for i,text in enumerate(batch):
            pieces=re.split(r'(?<=[.!?])\s+',text)
            chunk=''
            for piece in pieces:
                if len(chunk)+len(piece)>550 and chunk:
                    segments.append(chunk);owners.append(i);chunk=''
                while len(piece)>650:
                    cut=piece.rfind(' ',0,550)
                    cut=cut if cut>0 else 550
                    segments.append(piece[:cut]);owners.append(i);piece=piece[cut:].strip()
                chunk=(chunk+' '+piece).strip()
            if chunk:segments.append(chunk);owners.append(i)
        translated=[[] for _ in batch]
        for pos in range(0,len(segments),8):
            selected=segments[pos:pos+8]
            for j,source in enumerate(selected):
                if language(batch[owners[pos+j]])=='fa':
                    if persian_model is None:raise RuntimeError('波斯语翻译模型尚未准备，不能使用英中模型替代')
                    pivot=persian_model.translate_batch([persian_tokenizer.encode(source,out_type=str)],beam_size=4,max_decoding_length=350)[0]
                    selected[j]=persian_tokenizer.decode(pivot.hypotheses[0])
            encoded=[tokenizer.convert_ids_to_tokens(tokenizer.encode('>>cmn_Hans<< '+translation_input(text))) for text in selected]
            output=model.translate_batch(encoded,beam_size=4,max_decoding_length=350)
            decoded=[tokenizer.decode(tokenizer.convert_tokens_to_ids(x.hypotheses[0]),skip_special_tokens=True) for x in output]
            for owner,value in zip(owners[pos:pos+8],decoded):
                translated[owner].append(value)
        for text,parts in zip(batch,translated):
            result=normalize_terms(terminology(' '.join(parts),text))
            if is_chinese(result):save_translation(text,result,'本地波斯语转译' if re.search(r'[\u0600-\u06ff]',text) else '本地机器翻译（术语校准）')
        print(f'Translated {min(offset+8,len(texts))}/{len(texts)}; seconds={time.time()-started:.0f}',flush=True)

if __name__=='__main__':run()
