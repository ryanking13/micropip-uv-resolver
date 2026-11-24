addToLibrary({
  uv_js_fetch__async: true,
  uv_js_fetch: async function (urlPtr, acceptPtr, outLenPtr, outStatusPtr) {
    const url = UTF8ToString(urlPtr);
    const accept = UTF8ToString(acceptPtr);
    const id = (globalThis.__uvFetchId ?? 0) + 1;
    globalThis.__uvFetchId = id;
    const kind = url.endsWith(".metadata") ? "metadata" : "index";
    const startedAt = performance.now();
    const emit = (event) => globalThis.__uvOnFetchEvent?.(event);

    emit({ type: "fetch-start", id, url, kind });
    try {
      // A host (e.g. micropip) can route requests through its own fetch.
      let status, bytes;
      if (globalThis.__uvFetch) {
        const result = await globalThis.__uvFetch(url, accept);
        status = result.status;
        bytes = result.body;
      } else {
        const response = await fetch(url, { headers: { Accept: accept } });
        status = response.status;
        bytes = new Uint8Array(await response.arrayBuffer());
      }
      const output = _malloc(bytes.length);
      HEAPU8.set(bytes, output);
      HEAPU32[outLenPtr >>> 2] = bytes.length;
      HEAPU32[outStatusPtr >>> 2] = status;
      emit({
        type: "fetch-end",
        id,
        url,
        kind,
        status,
        bytes: bytes.length,
        ms: performance.now() - startedAt,
      });
      return output;
    } catch (error) {
      // Never throw into wasm: a JS exception unwinds the Rust frames without
      // running their destructors (the build uses panic = "abort"), which
      // leaves the module's state broken (e.g. futures' block_on still marked
      // as entered) and makes every later call abort. Return a null body
      // instead; the Rust side treats it as a failed fetch.
      HEAPU32[outLenPtr >>> 2] = 0;
      HEAPU32[outStatusPtr >>> 2] = 0;
      emit({
        type: "fetch-end",
        id,
        url,
        kind,
        status: 0,
        bytes: 0,
        error: String(error),
        ms: performance.now() - startedAt,
      });
      return 0;
    }
  },
});
