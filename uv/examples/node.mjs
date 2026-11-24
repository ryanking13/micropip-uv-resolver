import { resolve } from "../dist/uv-resolver.mjs";

for (const requirements of [
  ["flask"],
  ["requests[socks]"],
  ["fastapi", "pydantic<2"],
  ["fastapi==0.110.0", "starlette>=0.40"],
]) {
  const result = await resolve({ requirements });
  console.log(JSON.stringify({ requirements, ...result }, null, 2));
}
