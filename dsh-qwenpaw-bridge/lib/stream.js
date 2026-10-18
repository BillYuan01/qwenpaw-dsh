/**
 * Live assistant-stream projection.
 *
 * DSH publishes `agent/assistant-stream` as process-local frames *before* the
 * step's durable `assistant/message`. This module turns only text and
 * reasoning deltas into standard ACP `session/update` notifications and marks
 * the session so the outbound relay can suppress the later committed copy.
 *
 * Everything is fail-soft: if the event does not exist (older DSH) or the
 * subscription throws, no live flags are ever set and the stock committed
 * projection passes through unchanged.
 */

/** Subscribe to live deltas for the lifetime of this bridge. */
export function installAssistantStream(state) {
  if (state.config.streaming === false) {
    state.streaming = false;
    state.diag('live streaming disabled by config; committed mode');
    return;
  }
  try {
    state.ctx.on('agent/assistant-stream', ({ agent, frame } = {}) => {
      try {
        onFrame(state, agent, frame);
      } catch (error) {
        state.diag(`assistant-stream frame ignored: ${String(error)}`);
      }
    }, { global: true });
  } catch (error) {
    state.streaming = false;
    state.diag(`agent/assistant-stream unavailable; committed mode: ${String(error)}`);
  }
}

function onFrame(state, agent, frame) {
  const sessionId = agent?.session?.id;
  if (typeof sessionId !== 'string' || !state.sessions.has(sessionId)) return;
  if (frame === undefined || frame === null || typeof frame !== 'object') return;

  if (frame.type === 'start') {
    // A new model attempt starts: the previous committed assistant message has
    // already been projected (or abandoned), so the next committed text is a
    // fresh message and must not inherit the old suppression flags.
    state.live.set(sessionId, {
      attemptId: String(frame.attemptId ?? ''),
      text: false,
      reasoning: false,
    });
    return;
  }

  if (frame.type === 'chunk') {
    const chunk = frame.chunk;
    if (chunk === undefined || chunk === null || typeof chunk !== 'object') return;
    const kind = chunk.type === 'text-delta'
      ? 'agent_message_chunk'
      : chunk.type === 'reasoning-delta'
        ? 'agent_thought_chunk'
        : undefined;
    if (kind === undefined) return;
    const text = typeof chunk.text === 'string' ? chunk.text : '';
    if (text.length === 0) return;
    if (state.streaming !== true) return;

    let live = state.live.get(sessionId);
    if (live === undefined) {
      live = { attemptId: String(frame.attemptId ?? ''), text: false, reasoning: false };
      state.live.set(sessionId, live);
    }
    if (kind === 'agent_message_chunk') live.text = true;
    else live.reasoning = true;

    const notification = {
      jsonrpc: '2.0',
      method: 'session/update',
      params: {
        sessionId,
        update: {
          sessionUpdate: kind,
          ...(live.attemptId.length === 0 ? {} : { messageId: `qwenpaw-live-${live.attemptId}` }),
          content: { type: 'text', text },
        },
      },
    };
    state.send(notification).catch((error) => {
      state.diag(`live session/update delivery failed: ${String(error)}`);
    });
    return;
  }

  // `end` intentionally keeps the flags until the next `start`: dsh-acp queues
  // committed notifications on its output tail, so they may arrive just after
  // the end frame. Clearing here would let the duplicate through.
}

/** Whether a committed text/thought chunk is a duplicate of a live delta. */
export function shouldSuppressCommitted(state, sessionId, update) {
  if (state.streaming !== true) return false;
  const live = state.live.get(sessionId);
  if (live === undefined) return false;
  const content = update?.content;
  if (content === undefined || content === null || content.type !== 'text') return false;
  if (typeof content.text !== 'string' || content.text.length === 0) return false;
  if (update.sessionUpdate === 'agent_message_chunk') return live.text;
  if (update.sessionUpdate === 'agent_thought_chunk') return live.reasoning;
  return false;
}

/** Forget per-session live state when a session closes. */
export function forgetLive(state, sessionId) {
  state.live.delete(sessionId);
}
