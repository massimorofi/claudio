import { useCallback, useSyncExternalStore } from 'react'
import { backendStore } from './backendSlice'
import type { BackendProfile, BackendState } from '../types/backend'

/**
 * React binding for the backend slice (#47).
 *
 * `useSyncExternalStore` is the React 18 way to subscribe to an external store:
 * it gives concurrent-safe reads and re-renders only when the snapshot changes.
 * `getSnapshot` must return a stable reference between changes, which the slice
 * guarantees by only replacing `state` on a commit.
 */

function subscribe(onStoreChange: () => void): () => void {
  return backendStore.subscribe(onStoreChange)
}

function getSnapshot(): BackendState {
  return backendStore.getState()
}

/** The whole slice state, re-rendering on any backend change. */
export function useBackendState(): BackendState {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

/** All configured profiles. */
export function useBackends(): BackendProfile[] {
  return useBackendState().backends
}

/** The selected profile, or `null`. */
export function useActiveBackend(): BackendProfile | null {
  const { backends, activeBackendId } = useBackendState()
  return backends.find((b) => b.id === activeBackendId) ?? null
}

/** Mutating actions, bound to the singleton slice. */
export function useBackendActions() {
  return {
    add: useCallback((input: Parameters<typeof backendStore.addBackend>[0]) => backendStore.addBackend(input), []),
    update: useCallback(
      (id: string, patch: Parameters<typeof backendStore.updateBackend>[1]) =>
        backendStore.updateBackend(id, patch),
      [],
    ),
    remove: useCallback((id: string) => backendStore.removeBackend(id), []),
    setActive: useCallback((id: string | null) => backendStore.setActiveBackend(id), []),
  }
}
