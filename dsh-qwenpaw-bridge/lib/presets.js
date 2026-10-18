/**
 * Permission-preset projection and hot switching.
 *
 * `ctx.permissionPresets` is a public DSH service, but it is mounted by
 * dsh-base rather than by dsh-acp. The bridge therefore treats it as an
 * optional capability: present -> advertise/switch; absent -> do nothing and
 * keep the plain ACP surface.
 */

import { PERMISSION_CONFIG_ID } from './protocol.js';

/** Read the optional service without turning a missing mount into a crash. */
export function presetService(ctx) {
  try {
    return typeof ctx.get === 'function' ? ctx.get('permissionPresets') : undefined;
  } catch (_missingService) {
    return undefined;
  }
}

/** Return the process catalog in the bridge's stable `_meta` shape. */
export function presetCatalog(ctx) {
  const service = presetService(ctx);
  if (service === undefined || typeof service.catalog !== 'function') return undefined;
  try {
    const catalog = service.catalog();
    const options = Array.isArray(catalog?.options)
      ? catalog.options
        .filter((option) => typeof option?.value === 'string' && option.value.length > 0)
        .map((option) => ({
          id: option.value,
          name: typeof option.name === 'string' ? option.name : option.value,
          ...(typeof option.description === 'string' ? { description: option.description } : {}),
        }))
      : [];
    if (options.length === 0) return undefined;
    return {
      options,
      default: typeof catalog.defaultPreset === 'string' ? catalog.defaultPreset : options[0].id,
    };
  } catch (error) {
    return { error: String(error) };
  }
}

/** Current preset for one live session, or undefined when it cannot be read. */
export function currentPreset(ctx, sessionId) {
  const service = presetService(ctx);
  if (service === undefined || typeof service.current !== 'function') return undefined;
  const session = lookupSession(ctx, sessionId);
  if (session === undefined) return undefined;
  try {
    return service.current(session);
  } catch (_notReadable) {
    return undefined;
  }
}

/**
 * Switch one session's preset through the service's canonical write path.
 * @returns `{ok: true}` or `{ok: false, error}`; never throws.
 */
export function switchPreset(ctx, sessionId, name) {
  const service = presetService(ctx);
  if (service === undefined || typeof service.set !== 'function') {
    return { ok: false, error: 'permission presets are not available in this profile' };
  }
  const session = lookupSession(ctx, sessionId);
  if (session === undefined) return { ok: false, error: `unknown session: ${sessionId}` };
  if (typeof name !== 'string' || name.length === 0) return { ok: false, error: 'permission preset must be a non-empty string' };
  try {
    service.set(session, name);
    return { ok: true };
  } catch (error) {
    return { ok: false, error: String(error instanceof Error ? error.message : error) };
  }
}

/** All currently selectable preset ids, used for lightweight validation. */
export function availablePresets(catalog) {
  return new Set((catalog?.options ?? []).map((option) => option.id));
}

/**
 * Append (or replace) the `permission_mode` select in a session's complete
 * config-option array. The current value comes from the service projection,
 * never from a cached guess, so an external `/permission` switch is reflected.
 */
export function decorateConfigOptions(ctx, options, sessionId, catalog) {
  const base = Array.isArray(options) ? options.filter((option) => option?.id !== PERMISSION_CONFIG_ID) : [];
  if (catalog === undefined || Array.isArray(catalog?.options) === false || catalog.options.length === 0) return base;
  const presetOptions = catalog.options;
  const resolved = currentPreset(ctx, sessionId);
  const current = presetOptions.some((option) => option.id === resolved) ? resolved : catalog.default;
  return [...base, {
    id: PERMISSION_CONFIG_ID,
    name: 'Permission mode',
    category: 'permission',
    type: 'select',
    currentValue: current,
    options: presetOptions.map((option) => ({
      value: option.id,
      name: option.name,
      ...(option.description === undefined ? {} : { description: option.description }),
    })),
  }];
}

function lookupSession(ctx, sessionId) {
  if (typeof sessionId !== 'string' || sessionId.length === 0) return undefined;
  try {
    return typeof ctx.sessions?.get === 'function' ? ctx.sessions.get(sessionId) : undefined;
  } catch (_missingSession) {
    return undefined;
  }
}
