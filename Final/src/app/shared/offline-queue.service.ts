import { Injectable } from '@angular/core';

/**
 * OfflineQueueService — Module 16 PWA (scaffold for Module 17 mobile)
 *
 * Minimal IndexedDB-backed queue for write requests (POST/PUT/DELETE) that
 * cannot reach the network. The contract intentionally stays simple:
 *
 *   - enqueue(req)  : persists a request descriptor for later replay
 *   - peek()        : lists pending requests (debug/UI use)
 *   - flush()       : iterates pending requests and tries to replay them
 *                     against the network using fetch(). On success the entry
 *                     is removed; on failure it stays for the next flush().
 *
 * The MVP does NOT auto-flush on `online` events — the caller (e.g. a
 * Module 17 mobile sync orchestrator) is expected to trigger flush()
 * explicitly when ready (we don't want to fire writes before a user has
 * authenticated, etc.).
 *
 * If IndexedDB is not available (private browsing, SSR, very old browsers),
 * the service silently degrades to in-memory storage. This keeps the
 * surface area predictable for unit tests and avoids hard runtime failures.
 */

export interface OfflineRequest {
  /** Auto-generated id. May be left undefined when calling enqueue(). */
  id?: number;
  /** HTTP method to replay (POST / PUT / PATCH / DELETE). */
  method: string;
  /** Full or relative URL. */
  url: string;
  /** Optional JSON body. Stringified at enqueue time. */
  body?: unknown;
  /** Optional header bag. Auth headers are intentionally NOT stored here. */
  headers?: Record<string, string>;
  /** UNIX timestamp (ms) of enqueue. */
  createdAt: number;
}

export interface FlushResult {
  attempted: number;
  succeeded: number;
  failed: number;
}

const DB_NAME = 'gestion-ee-offline';
const DB_VERSION = 1;
const STORE = 'requests';

@Injectable({ providedIn: 'root' })
export class OfflineQueueService {
  private dbPromise: Promise<IDBDatabase | null> | null = null;
  private memoryFallback: OfflineRequest[] = [];
  private memoryAutoId = 1;

  private openDb(): Promise<IDBDatabase | null> {
    if (this.dbPromise) {
      return this.dbPromise;
    }
    this.dbPromise = new Promise((resolve) => {
      if (typeof indexedDB === 'undefined') {
        resolve(null);
        return;
      }
      try {
        const req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = () => {
          const db = req.result;
          if (!db.objectStoreNames.contains(STORE)) {
            db.createObjectStore(STORE, { keyPath: 'id', autoIncrement: true });
          }
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => resolve(null);
        req.onblocked = () => resolve(null);
      } catch {
        resolve(null);
      }
    });
    return this.dbPromise;
  }

  /** Persist a request descriptor for later replay. Returns the assigned id. */
  async enqueue(request: Omit<OfflineRequest, 'id' | 'createdAt'>): Promise<number> {
    const entry: OfflineRequest = {
      method: request.method.toUpperCase(),
      url: request.url,
      body: request.body,
      headers: request.headers,
      createdAt: Date.now(),
    };

    const db = await this.openDb();
    if (!db) {
      const id = this.memoryAutoId++;
      this.memoryFallback.push({ ...entry, id });
      return id;
    }
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      const store = tx.objectStore(STORE);
      const req = store.add(entry);
      req.onsuccess = () => resolve(req.result as number);
      req.onerror = () => reject(req.error);
    });
  }

  /** List all pending requests (oldest first). */
  async peek(): Promise<OfflineRequest[]> {
    const db = await this.openDb();
    if (!db) {
      return [...this.memoryFallback];
    }
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const store = tx.objectStore(STORE);
      const req = store.getAll();
      req.onsuccess = () => resolve((req.result || []) as OfflineRequest[]);
      req.onerror = () => reject(req.error);
    });
  }

  /** Remove a single request by id. */
  async remove(id: number): Promise<void> {
    const db = await this.openDb();
    if (!db) {
      this.memoryFallback = this.memoryFallback.filter((r) => r.id !== id);
      return;
    }
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      const req = tx.objectStore(STORE).delete(id);
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }

  /** Clear the entire queue. Useful for tests and for sign-out flows. */
  async clear(): Promise<void> {
    const db = await this.openDb();
    if (!db) {
      this.memoryFallback = [];
      this.memoryAutoId = 1;
      return;
    }
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      const req = tx.objectStore(STORE).clear();
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }

  /**
   * Replay all queued requests against the network in insertion order.
   * Entries that succeed (HTTP < 500) are removed; entries that fail stay
   * in the queue for a future flush().
   */
  async flush(): Promise<FlushResult> {
    const pending = await this.peek();
    let succeeded = 0;
    let failed = 0;

    for (const entry of pending) {
      try {
        const init: RequestInit = {
          method: entry.method,
          headers: entry.headers,
          body:
            entry.body == null
              ? undefined
              : typeof entry.body === 'string'
                ? entry.body
                : JSON.stringify(entry.body),
        };
        const response = await fetch(entry.url, init);
        if (response.ok || (response.status >= 400 && response.status < 500)) {
          // 2xx success, or 4xx client error (won't succeed by retrying).
          if (entry.id != null) {
            await this.remove(entry.id);
          }
          succeeded++;
        } else {
          failed++;
        }
      } catch {
        failed++;
      }
    }

    return { attempted: pending.length, succeeded, failed };
  }
}
