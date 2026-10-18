/**
 * Stable vocabulary for the `_meta['qwenpaw']` extension carried by this
 * bridge. ACP reserves `_meta` for exactly this purpose; every field here is
 * additive, and a client that does not understand it keeps working.
 *
 * Bump META_PROTOCOL only for a breaking shape change. Adding optional fields
 * is backward compatible.
 */
export const META_KEY = 'qwenpaw';
export const META_PROTOCOL = 1;
export const BRIDGE_NAME = 'dsh-qwenpaw-bridge';
export const BRIDGE_VERSION = '0.1.0';

/** ACP config-option id used for the hot-switchable permission preset. */
export const PERMISSION_CONFIG_ID = 'permission_mode';

/** Aliases accepted on input for the permission preset. */
export const PERMISSION_ALIASES = [PERMISSION_CONFIG_ID, 'permission_preset', 'permissionPreset'];

/** Request methods owned by this bridge (never forwarded to dsh-acp). */
export const BRIDGE_METHODS = {
  permissionSet: 'qwenpaw/permission/set',
  permissionGet: 'qwenpaw/permission/get',
  streamingSet: 'qwenpaw/streaming/set',
};

/** Marker returned by inbound handling when a request was answered locally. */
export const DROP = Symbol('dsh-qwenpaw-bridge.drop');

/** Drop undefined object fields without mutating the input. */
export function compact(value) {
  const out = {};
  for (const [key, item] of Object.entries(value)) {
    if (item !== undefined) out[key] = item;
  }
  return out;
}

/** Add/replace the `_meta.qwenpaw` member while preserving every other field. */
export function withQwenpawMeta(frame, meta) {
  const result = frame?.result;
  if (result === undefined || result === null || typeof result !== 'object') return frame;
  const existing = result._meta;
  return {
    ...frame,
    result: {
      ...result,
      _meta: {
        ...(existing !== undefined && existing !== null && typeof existing === 'object' ? existing : {}),
        [META_KEY]: meta,
      },
    },
  };
}

/** JSON-RPC success response. */
export function resultFrame(id, result) {
  return { jsonrpc: '2.0', id, result };
}

/** JSON-RPC error response. */
export function errorFrame(id, code, message, data) {
  return {
    jsonrpc: '2.0',
    id,
    error: compact({ code, message, data }),
  };
}

/** Whether a frame is a JSON-RPC request (has both id and method). */
export function isRequest(frame) {
  return Boolean(frame) && typeof frame === 'object' && frame.id !== undefined && typeof frame.method === 'string';
}
