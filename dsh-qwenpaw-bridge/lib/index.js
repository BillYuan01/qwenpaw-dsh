/**
 * @local/dsh-qwenpaw-bridge
 *
 * A DSH bundle plugin that replaces the stock `acp` transport row with the
 * same @deepseek-ai/dsh-acp implementation plus an ACP stream relay. It adds
 * three opt-in capabilities for QwenPaw without changing DSH core:
 *
 *   1. live streaming: `agent/assistant-stream` text/reasoning deltas become
 *      standard `session/update` chunks; the duplicate committed text is
 *      suppressed in the outbound relay (fail-soft to committed mode);
 *   2. hot-switchable permission presets: `ctx.permissionPresets` is advertised
 *      in `_meta['qwenpaw']` and through a `permission_mode` config option, so
 *      switching never restarts a process;
 *   3. model catalog: `initialize` / `session/new` carry every model with its
 *      reasoning efforts in `_meta['qwenpaw']`.
 *
 * No DSH core file is touched. Every non-public dependency is optional and
 * degrades to the stock ACP behavior.
 */

import { Readable, Writable } from 'node:stream';
import { ndJsonStream } from '@agentclientprotocol/sdk';
import * as acp from '@deepseek-ai/dsh-acp';

import { BRIDGE_NAME, BRIDGE_VERSION } from './protocol.js';
import { invalidateModelCatalog } from './catalog.js';
import { createInboundGate } from './inbound.js';
import { createOutboundWritable } from './outbound.js';
import { installAssistantStream } from './stream.js';

export const name = 'qwenpaw-bridge';
// The stock acp row declares these; acpAppStartup keeps --help from claiming
// stdio before the launcher accepts the invocation.
export const inject = [...acp.inject, 'acpAppStartup'];

/** Mount the bridge in place of the stock ACP transport. */
export function apply(ctx, rawConfig = {}) {
  const config = normalizeConfig(rawConfig);
  const diag = makeDiagnostics(ctx);
  const state = createState(ctx, config, diag);
  state.initialSelection = resolveInitialSelection(ctx, config, diag);

  // One NDJSON connection owns process stdio. `wire.writable` accepts parsed
  // objects and serializes them; the byte sink holds the single stdout writer.
  const byteSink = createByteSink(Writable.toWeb(process.stdout));
  const wire = ndJsonStream(byteSink, Readable.toWeb(process.stdin));
  const writer = wire.writable.getWriter();
  state.write = (frame) => writer.write(frame);
  state.send = (frame) => state.write(frame).catch((error) => {
    diag(`outbound write failed: ${String(error)}`);
  });

  installAssistantStream(state);
  ctx.on('llm/adapters-updated', () => invalidateModelCatalog(state));

  const stream = {
    readable: createInboundGate(wire.readable, state),
    writable: createOutboundWritable(state),
  };

  diag(`starting on dsh-acp ${acp.name}; streaming=${state.streaming}; `
    + `permissionPresets=${state.config.permissionPresets}; models=${state.config.models}`);
  acp.apply(ctx, {
    ...state.initialSelection,
    ...(config.sessionListPageSize === undefined ? {} : { sessionListPageSize: config.sessionListPageSize }),
    stream,
  });
}

function normalizeConfig(raw) {
  const value = raw !== null && typeof raw === 'object' ? raw : {};
  return {
    streaming: value.streaming !== false,
    permissionPresets: (value.permissionPresets ?? value.permission_presets) !== false,
    models: value.models !== false,
    provider: typeof value.provider === 'string' && value.provider.length > 0 ? value.provider : undefined,
    model: typeof value.model === 'string' && value.model.length > 0 ? value.model : undefined,
    reasoningEffort: nonEmpty(value.reasoningEffort ?? value.reasoning_effort),
    sessionListPageSize: value.sessionListPageSize,
  };
}

function resolveInitialSelection(ctx, config, diag) {
  let provider = config.provider;
  let model = config.model;
  let reasoningEffort = config.reasoningEffort;
  const fallback = safeGet(ctx, 'agentDefaultModel')?.currentSelection?.();

  // A partial explicit route is not a route. Fall back to the process default
  // rather than creating sessions whose model picker has no selection.
  if (provider === undefined || model === undefined) {
    if (fallback !== undefined && typeof fallback.provider === 'string' && typeof fallback.model === 'string') {
      provider = fallback.provider;
      model = fallback.model;
      reasoningEffort ??= fallback.reasoningEffort;
    }
  } else if (
    reasoningEffort === undefined
    && fallback?.provider === provider
    && fallback?.model === model
  ) {
    // The explicit route matches the process default; inherit its effort so
    // the initial config option reflects the deployment's choice.
    reasoningEffort = fallback.reasoningEffort;
  }
  if (provider === undefined || model === undefined) {
    diag('no provider/model route resolved; session/new will advertise no model config option');
    return {};
  }
  return {
    provider,
    model,
    ...(reasoningEffort === undefined ? {} : { reasoningEffort }),
  };
}

function createState(ctx, config, diag) {
  return {
    ctx,
    config,
    diag,
    /** Whether live deltas are enabled for this connection. */
    streaming: config.streaming !== false,
    /** Sessions owned by this ACP connection. */
    sessions: new Set(),
    /** sessionId -> {attemptId, text, reasoning} for duplicate suppression. */
    live: new Map(),
    /** JSON-RPC request id -> {method, params} awaiting a response. */
    pending: new Map(),
    /** sessionId -> complete configOptions last advertised. */
    configOptions: new Map(),
    /** Cached model catalog promise. */
    catalogPromise: undefined,
    /** Fallback route advertised when a session has no selection yet. */
    initialSelection: {},
    /** Assigned once the wire writer exists. */
    write: undefined,
    send: undefined,
  };
}

function createByteSink(stream) {
  const writer = stream.getWriter();
  return new WritableStream({
    write(chunk) {
      return writer.write(chunk);
    },
    close() {
      return writer.close();
    },
    abort(reason) {
      return writer.abort(reason);
    },
  });
}

function makeDiagnostics(ctx) {
  const debug = process.env.DSH_QWENPAW_BRIDGE_DEBUG === '1';
  return (message) => {
    const text = `${BRIDGE_NAME} v${BRIDGE_VERSION}: ${message}`;
    try {
      if (debug) ctx.logger?.info?.(text);
      else ctx.logger?.debug?.(text);
    } catch (_loggerUnavailable) {
      // Diagnostics must never break the transport.
    }
  };
}

function safeGet(ctx, service) {
  try {
    return typeof ctx.get === 'function' ? ctx.get(service) : undefined;
  } catch (_missingService) {
    return undefined;
  }
}

function nonEmpty(value) {
  return typeof value === 'string' && value.length > 0 ? value : undefined;
}
