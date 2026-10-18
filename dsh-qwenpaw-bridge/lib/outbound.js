/**
 * Outbound ACP relay.
 *
 * dsh-acp writes complete JSON-RPC objects into this stream. The relay has
 * three responsibilities:
 *   1. decorate `initialize` / `session/new` / `session/resume` with
 *      `_meta['qwenpaw']` and a `permission_mode` config option;
 *   2. suppress committed text/thought chunks when live deltas for the same
 *      attempt have already been sent (otherwise QwenPaw would append the
 *      answer twice);
 *   3. keep every non-assistant update (image, tool, usage, config) intact.
 *
 * A decoration failure never drops a frame: the original object is forwarded.
 */

import {
  DROP,
  compact,
  withQwenpawMeta,
} from './protocol.js';
import {
  annotateCatalog,
  currentSelection,
  modelCatalog,
} from './catalog.js';
import {
  currentPreset,
  decorateConfigOptions,
  presetCatalog,
  switchPreset,
} from './presets.js';
import { shouldSuppressCommitted } from './stream.js';
import { decorateToolUpdate } from './toolkind.js';

/** Build the WritableStream handed to dsh-acp. */
export function createOutboundWritable(state) {
  return new WritableStream({
    async write(frame) {
      let out = frame;
      try {
        out = await decorateOutbound(state, frame);
      } catch (error) {
        state.diag(`outbound decoration failed (forwarding original): ${String(error)}`);
        out = frame;
      }
      if (out === DROP || out === null || out === undefined) return;
      await state.write(out);
    },
  });
}

async function decorateOutbound(state, frame) {
  if (frame === null || typeof frame !== 'object') return frame;
  if (typeof frame.method === 'string') return decorateNotification(state, frame);
  if (frame.id === undefined) return frame;

  const pending = state.pending.get(frame.id);
  if (pending === undefined) return frame;
  state.pending.delete(frame.id);

  // Error responses keep their shape; only discard the request bookkeeping.
  if (frame.error !== undefined || frame.result === undefined || frame.result === null || typeof frame.result !== 'object') {
    return frame;
  }
  return await decorateResponse(state, frame, pending);
}

async function decorateResponse(state, frame, pending) {
  switch (pending.method) {
    case 'initialize':
      return withQwenpawMeta(frame, await connectionMeta(state, { initialize: true }));

    case 'session/new':
    case 'session/resume': {
      const result = frame.result;
      const sessionId = typeof result.sessionId === 'string'
        ? result.sessionId
        : typeof pending.params?.sessionId === 'string' ? pending.params.sessionId : undefined;
      if (sessionId === undefined) return frame;

      state.sessions.add(sessionId);
      const requested = requestedPreset(pending.params);
      let applied;
      if (requested !== undefined && state.config.permissionPresets !== false) {
        const switched = switchPreset(state.ctx, sessionId, requested);
        if (switched.ok) applied = requested;
        else state.diag(`requested permission preset ${JSON.stringify(requested)} was not applied: ${switched.error}`);
      }

      const catalog = state.config.permissionPresets === false ? undefined : presetCatalog(state.ctx);
      const configOptions = decorateConfigOptions(state.ctx, result.configOptions, sessionId, catalog);
      const decorated = {
        ...frame,
        result: { ...result, configOptions },
      };
      state.configOptions.set(sessionId, configOptions);
      const meta = await connectionMeta(state, {
        sessionId,
        requestedPreset: requested,
        appliedPreset: applied,
        presetFallback: requested !== undefined && applied === undefined,
      });
      return withQwenpawMeta(decorated, meta);
    }

    case 'session/close': {
      const sessionId = typeof pending.params?.sessionId === 'string' ? pending.params.sessionId : undefined;
      if (sessionId !== undefined) {
        state.sessions.delete(sessionId);
        state.configOptions.delete(sessionId);
        state.live.delete(sessionId);
      }
      return frame;
    }

    case 'session/set_config_option': {
      const sessionId = typeof pending.params?.sessionId === 'string' ? pending.params.sessionId : undefined;
      if (sessionId === undefined) return frame;
      const catalog = state.config.permissionPresets === false ? undefined : presetCatalog(state.ctx);
      const configOptions = decorateConfigOptions(state.ctx, frame.result.configOptions, sessionId, catalog);
      state.configOptions.set(sessionId, configOptions);
      return { ...frame, result: { ...frame.result, configOptions } };
    }

    default:
      return frame;
  }
}

async function decorateNotification(state, frame) {
  if (frame.method !== 'session/update') return frame;
  const params = frame.params;
  if (params === null || typeof params !== 'object') return frame;
  const sessionId = typeof params.sessionId === 'string' ? params.sessionId : undefined;
  const update = params.update;
  if (sessionId === undefined || update === null || typeof update !== 'object') return frame;

  if (update.sessionUpdate === 'agent_message_chunk' || update.sessionUpdate === 'agent_thought_chunk') {
    return shouldSuppressCommitted(state, sessionId, update) ? DROP : frame;
  }

  if (update.sessionUpdate === 'config_option_update') {
    const catalog = state.config.permissionPresets === false ? undefined : presetCatalog(state.ctx);
    const configOptions = decorateConfigOptions(state.ctx, update.configOptions, sessionId, catalog);
    state.configOptions.set(sessionId, configOptions);
    return { ...frame, params: { ...params, update: { ...update, configOptions } } };
  }

  if (update.sessionUpdate === 'tool_call' || update.sessionUpdate === 'tool_call_update') {
    const decorated = decorateToolUpdate(update);
    return decorated === update ? frame : { ...frame, params: { ...params, update: decorated } };
  }

  return frame;
}

/** The complete `_meta['qwenpaw']` payload for one connection/session. */
async function connectionMeta(state, { initialize = false, sessionId, requestedPreset, appliedPreset, presetFallback } = {}) {
  const selected = currentSelection(state, sessionId);
  const rawModels = await modelCatalog(state);
  const models = annotateCatalog(rawModels, selected);
  const catalog = state.config.permissionPresets === false ? undefined : presetCatalog(state.ctx);
  const current = sessionId === undefined
    ? catalog?.default
    : currentPreset(state.ctx, sessionId) ?? catalog?.default;

  return compact({
    protocol: 1,
    bridge: 'dsh-qwenpaw-bridge',
    version: '0.1.0',
    streaming: state.streaming === true,
    models: state.config.models === false ? undefined : models,
    current_model: selected,
    permission_presets: catalog?.options,
    default_permission_preset: catalog?.default,
    permission_preset: current,
    requested_permission_preset: requestedPreset,
    permission_preset_fallback: presetFallback === true ? true : undefined,
    initialized: initialize === true ? true : undefined,
  });
}

/** Read a preset request from either the bridge namespace or a bare `_meta`. */
export function requestedPreset(params) {
  const meta = params?._meta;
  if (meta === null || typeof meta !== 'object') return undefined;
  const scoped = meta.qwenpaw;
  const candidates = [
    scoped?.permission_preset,
    scoped?.permissionPreset,
    scoped?.permission_mode,
    meta.permission_preset,
    meta.permissionPreset,
    meta.permission_mode,
  ];
  for (const value of candidates) {
    if (typeof value === 'string' && value.trim().length > 0) return value.trim();
  }
  return undefined;
}

/** Whether the client asked to disable live deltas on this connection. */
export function requestedStreamingOff(params) {
  return params?._meta?.qwenpaw?.streaming === false;
}
