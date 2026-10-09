'use client';

// The native launcher passes a revocable opaque session in the fragment only.
// It never enters an HTTP query, request body, persistent browser store or log.
let bearer: string | null = null;
let fragmentConsumed = false;

function consumeLocalSession(): void {
  if (fragmentConsumed || typeof window === 'undefined') return;
  fragmentConsumed = true;
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  if (!fragment.has('orion_session')) return;
  const supplied = fragment.get('orion_session') ?? '';
  window.history.replaceState(null, '', window.location.pathname + window.location.search);
  if (/^[A-Za-z0-9_-]{32,512}$/.test(supplied)) bearer = supplied;
}

export function getApiRoot(): string {
  const configured = process.env.NEXT_PUBLIC_ASSURANCE_API_URL;
  if (configured) return configured;
  if (typeof window !== 'undefined' && ['localhost', '127.0.0.1'].includes(window.location.hostname)) {
    return `${window.location.origin}/api/v1`;
  }
  return 'http://127.0.0.1:8765/api/v1';
}

export async function authenticatedFetch(input: string, init?: RequestInit): Promise<Response> {
  consumeLocalSession();
  const headers = new Headers(init?.headers);
  if (bearer && typeof window !== 'undefined') {
    const target = new URL(input, window.location.href);
    if (target.origin !== window.location.origin) {
      throw new Error('A sessão local só pode ser enviada à API da mesma origem.');
    }
    headers.set('Authorization', `Bearer ${bearer}`);
  }
  return fetch(input, { ...init, headers, credentials: 'omit', redirect: 'error', cache: 'no-store' });
}

export function clearApiSession(): void {
  bearer = null;
  fragmentConsumed = true;
}
