import { SUPPORTED_SNAPSHOT_VERSION, type Snapshot } from './types';

export const POLL_INTERVAL_MS = 2_000;
const REQUEST_TIMEOUT_MS = 5_000;

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number | null = null,
  ) {
    super(message);
  }
}

export interface ReplayTarget {
  sessionId: number;
  at: string;
}

/** `?session=<id>&at=<iso>` loads a recorded moment instead of live state. */
export function replayTarget(search: string): ReplayTarget | null {
  const params = new URLSearchParams(search);
  const session = params.get('session');
  const at = params.get('at');
  if (!session || !at || !Number.isFinite(Number(session))) return null;
  return { sessionId: Number(session), at };
}

export async function fetchSnapshot(replay: ReplayTarget | null, fetchFn: typeof fetch = fetch): Promise<Snapshot> {
  const url = replay
    ? `/api/replay?${new URLSearchParams({ session_id: String(replay.sessionId), at: replay.at })}`
    : '/api/state';
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetchFn(url, { signal: controller.signal, headers: { Accept: 'application/json' } });
  } catch (err) {
    throw new ApiError(controller.signal.aborted ? 'Local server timed out' : "Can't reach the local server");
  } finally {
    clearTimeout(timer);
  }
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      // keep the status text
    }
    throw new ApiError(detail, response.status);
  }
  const snapshot = (await response.json()) as Snapshot;
  if (snapshot.version !== SUPPORTED_SNAPSHOT_VERSION) {
    throw new ApiError(`Unsupported snapshot version ${snapshot.version}`);
  }
  return snapshot;
}

/**
 * Poll the local API, one request at a time. Reads cached server state only;
 * the server alone decides when to call the provider.
 */
export function startPolling(
  onSnapshot: (s: Snapshot) => void,
  onError: (e: ApiError) => void,
  options: { replay?: ReplayTarget | null; intervalMs?: number; fetchFn?: typeof fetch } = {},
): () => void {
  const { replay = null, intervalMs = POLL_INTERVAL_MS, fetchFn = fetch } = options;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const tick = async () => {
    try {
      const snapshot = await fetchSnapshot(replay, fetchFn);
      if (!stopped) onSnapshot(snapshot);
    } catch (err) {
      if (!stopped) onError(err instanceof ApiError ? err : new ApiError(String(err)));
    }
    // A recorded moment doesn't change; fetch it once
    if (!stopped && !replay) timer = setTimeout(tick, intervalMs);
  };
  tick();
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}
