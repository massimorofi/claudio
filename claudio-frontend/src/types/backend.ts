// Backend profile types shared by the store (#47) and the API client (#48).
//
// Kept dependency-free on purpose: both the store and the HTTP layer import this
// file, so anything imported here would drag the whole app graph into unit tests.

/** One named Claudio backend the UI can talk to. */
export interface BackendProfile {
  /** Stable identifier, unique within the list. */
  id: string
  /** Human label shown in the switcher, e.g. "Production Server". */
  name: string
  /**
   * Origin of the backend, e.g. `http://192.168.1.50:8100`. An empty string
   * means "same origin as this page" (the docker/nginx topology).
   */
  url: string
  /** Optional per-backend bearer token; falls back to the global token. */
  authToken?: string
}

/** Persisted shape of the backend slice. */
export interface BackendState {
  backends: BackendProfile[]
  /** `id` of the selected backend, or `null` when none is selected. */
  activeBackendId: string | null
}

/** Input for creating a profile: the store assigns the `id` when omitted. */
export type NewBackendProfile = Omit<BackendProfile, 'id'> & { id?: string };

/** Fields that may be patched on an existing profile. */
export type BackendPatch = Partial<Omit<BackendProfile, 'id'>>;
