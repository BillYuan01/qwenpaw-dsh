/**
 * One-shot model catalog for `_meta['qwenpaw']`.
 *
 * The ACP model picker already exposes a nested provider -> model select, but
 * QwenPaw needs the complete flattenable catalog (including each model's
 * reasoning efforts) in one place so its adapter does not have to probe by
 * switching models. Every lookup is fail-soft: a provider or model whose
 * metadata cannot be resolved is either omitted or emitted without the
 * optional fields.
 */

/** Build the raw catalog once per connection, then reuse until adapters change. */
export async function modelCatalog(state) {
  if (state.config.models === false) return [];
  if (state.catalogPromise === undefined) {
    state.catalogPromise = buildCatalog(state.ctx, state.diag).catch((error) => {
      state.diag(`model catalog unavailable: ${String(error)}`);
      return [];
    });
  }
  return state.catalogPromise;
}

/** Invalidate the cached catalog after a provider/model topology change. */
export function invalidateModelCatalog(state) {
  state.catalogPromise = undefined;
}

/** Resolve the exact model selection currently advertised for a session. */
export function currentSelection(state, sessionId) {
  const options = sessionId === undefined ? undefined : state.configOptions.get(sessionId);
  let selected;
  if (Array.isArray(options)) {
    const model = options.find((option) => option?.id === 'model');
    if (typeof model?.currentValue === 'string') {
      try {
        const parsed = JSON.parse(model.currentValue);
        if (Array.isArray(parsed) && typeof parsed[0] === 'string' && typeof parsed[1] === 'string') {
          selected = { provider: parsed[0], model: parsed[1] };
        }
      } catch (_invalidModelValue) {
        // The opaque option value was not this bridge's JSON route identity.
      }
    }
    const effort = options.find((option) => option?.id === 'reasoning_effort');
    if (selected !== undefined && typeof effort?.currentValue === 'string' && effort.currentValue !== '') {
      selected.reasoning_effort = effort.currentValue;
    }
  }
  const fallback = state.initialSelection;
  if (selected === undefined && fallback !== undefined) {
    selected = {
      provider: fallback.provider,
      model: fallback.model,
      ...(fallback.reasoningEffort === undefined ? {} : { reasoning_effort: fallback.reasoningEffort }),
    };
  }
  return selected;
}

/**
 * Mark the default and the current route on each entry. Kept separate from the
 * cached raw catalog because the current selection changes per session while
 * the expensive provider/model discovery does not.
 */
export function annotateCatalog(models, selected) {
  return models.map((entry) => ({
    ...entry,
    default: selected !== undefined && entry.provider === selected.provider && entry.id === selected.model,
  }));
}

async function buildCatalog(ctx, diag) {
  const llm = safeGet(ctx, 'llm');
  if (llm === undefined || typeof llm.listProviders !== 'function') return [];
  let providers;
  try {
    providers = llm.listProviders();
  } catch (error) {
    diag(`model catalog: listProviders failed: ${String(error)}`);
    return [];
  }
  if (!Array.isArray(providers)) return [];

  const perProvider = await Promise.all(providers.map(async (provider) => {
    if (typeof provider?.id !== 'string' || provider.id.length === 0) return [];
    let models;
    try {
      models = await llm.listModels(provider.id);
    } catch (error) {
      diag(`model catalog: listModels(${provider.id}) failed: ${String(error)}`);
      return [];
    }
    if (!Array.isArray(models)) return [];
    return await Promise.all(models.map((model) => describeModel(llm, provider, model, diag)));
  }));
  return perProvider.flat().filter((entry) => entry !== undefined);
}

async function describeModel(llm, provider, model, diag) {
  if (typeof model?.id !== 'string' || model.id.length === 0) return undefined;
  let info;
  try {
    info = await llm.resolveModelInfo(provider.id, model.id);
  } catch (error) {
    diag(`model catalog: resolveModelInfo(${provider.id}/${model.id}) failed: ${String(error)}`);
  }
  const efforts = Array.isArray(info?.reasoning?.efforts)
    ? info.reasoning.efforts.map((effort) => ({
      id: String(effort.id),
      name: typeof effort.name === 'string' ? effort.name : String(effort.id),
      ...(typeof effort.description === 'string' ? { description: effort.description } : {}),
    }))
    : [];
  const defaultEffort = info?.reasoning?.defaultEffort;
  return {
    id: model.id,
    name: typeof model.name === 'string' ? model.name : model.id,
    provider: provider.id,
    provider_name: typeof provider.name === 'string' ? provider.name : provider.id,
    // Matches the opaque `model` config-option value dsh-acp advertises; the
    // adapter can pass it straight back to session/set_config_option.
    selection_value: JSON.stringify([provider.id, model.id]),
    ...(typeof model.description === 'string' ? { description: model.description } : {}),
    ...(info?.context?.contextWindow === undefined ? {} : { context_window: info.context.contextWindow }),
    ...(info?.defaultMaxTokens === undefined ? {} : { max_tokens: info.defaultMaxTokens }),
    ...(Array.isArray(info?.inputModalities) ? { input_modalities: [...info.inputModalities] } : {}),
    reasoning_efforts: efforts,
    ...(defaultEffort === undefined ? {} : { default_reasoning_effort: String(defaultEffort) }),
  };
}

function safeGet(ctx, name) {
  try {
    return typeof ctx.get === 'function' ? ctx.get(name) : undefined;
  } catch (_missingService) {
    return undefined;
  }
}
