/**
 * Optional presentation polish: map a DSH tool name to an ACP `ToolKind`.
 * dsh-acp emits every tool as `kind: "other"`; QwenPaw can render read/edit/
 * execute/search/fetch rows more precisely when the kind is present. Unknown
 * tools stay `other`, so this is purely additive.
 */

const KINDS = [
  [/^(read|read_file|read_text_file|fs_read|cat)$/i, 'read'],
  [/^(write|write_file|write_text_file|edit|str_replace|str-replace|apply_patch|fs_write)$/i, 'edit'],
  [/^(bash|shell|exec|execute|run|command|pwsh|terminal)([_:-].*)?$/i, 'execute'],
  [/^(grep|glob|search|find|ripgrep|fs_search)([_:-].*)?$/i, 'search'],
  [/^(web_fetch|fetch|http|browser)([_:-].*)?$/i, 'fetch'],
];

/** Map a tool title to an ACP kind. */
export function mapToolKind(title) {
  if (typeof title !== 'string' || title.length === 0) return undefined;
  for (const [pattern, kind] of KINDS) {
    if (pattern.test(title)) return kind;
  }
  return undefined;
}

/** Decorate a `tool_call` / `tool_call_update`; returns the input on no change. */
export function decorateToolUpdate(update) {
  if (update === null || typeof update !== 'object') return update;
  if (update.kind !== undefined && update.kind !== 'other') return update;
  const kind = mapToolKind(update.title);
  if (kind === undefined) return update;
  return { ...update, kind };
}
