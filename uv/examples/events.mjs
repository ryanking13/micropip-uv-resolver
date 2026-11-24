import { resolve } from "../dist/uv-resolver.mjs";

const result = await resolve({
  requirements: ["flask"],
  onEvent(event) {
    if (event.type === "fetch-start") {
      console.log(`fetch ${event.id} start ${event.kind} ${event.url}`);
    } else if (event.type === "fetch-end") {
      console.log(`fetch ${event.id} end ${event.status} ${event.bytes}B ${event.ms.toFixed(1)}ms`);
    } else {
      console.log(`log ${event.level} ${event.target} ${event.message}`);
    }
  },
});

console.log(JSON.stringify(result, null, 2));
