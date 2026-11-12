import {useEffect, useState} from "react";
import {splitTerms} from "../pipelines";

/** 多值文本框：按换行、逗号、顿号拆分；保留正在输入的分隔符，外部值变化时同步。 */
export default function TermsArea({value, onChange, placeholder, invalid, disabled, rows = 4, label}: {
  value: string[]; onChange: (next: string[]) => void; placeholder?: string; invalid?: boolean; disabled?: boolean; rows?: number; label: string;
}) {
  const [text, setText] = useState(value.join("\n"));
  useEffect(() => {
    if (splitTerms(text).join("\n") !== value.join("\n")) setText(value.join("\n"));
    // 只在外部值变化时同步，避免覆盖用户正在输入的分隔符
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return <textarea aria-label={label} aria-invalid={invalid || undefined} className={invalid ? "invalid" : ""} rows={rows} value={text} disabled={disabled} placeholder={placeholder}
    onChange={(e) => { setText(e.target.value); onChange(splitTerms(e.target.value)); }} />;
}

/** 字段下方的 422 错误说明。 */
export function FieldError({show, message}: {show: boolean; message?: string}) {
  return show && message ? <small className="pipeline-field-error" role="alert">{message}</small> : null;
}
