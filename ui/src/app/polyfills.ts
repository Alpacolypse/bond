// Map.prototype.getOrInsert and getOrInsertComputed (the "upsert" proposal), which pdf.js 6 calls but
// browsers only recently ship, and hosts may embed an older one (an Electron app, say). The app runs
// pdf.js's worker on the main thread, so this one polyfill covers both halves.

type Upsert<K, V> = {
  getOrInsert?(key: K, value: V): V;
  getOrInsertComputed?(key: K, compute: (key: K) => V): V;
  has(key: K): boolean;
  get(key: K): V | undefined;
  set(key: K, value: V): unknown;
};

for (const proto of [Map.prototype, WeakMap.prototype] as Upsert<object, unknown>[]) {
  proto.getOrInsert ??= function (this: Upsert<object, unknown>, key, value) {
    if (!this.has(key)) this.set(key, value);
    return this.get(key);
  };
  proto.getOrInsertComputed ??= function (this: Upsert<object, unknown>, key, compute) {
    if (!this.has(key)) this.set(key, compute(key));
    return this.get(key);
  };
}
