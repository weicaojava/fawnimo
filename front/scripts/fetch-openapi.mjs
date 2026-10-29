/** Fetch a validated schema from an explicitly selected local or deployed API. */
import { writeFile } from 'node:fs/promises'

const source = process.env.OPENAPI_URL ?? 'http://127.0.0.1:8000/openapi.json'
const response = await fetch(source, { signal: AbortSignal.timeout(30000) })
if (!response.ok) throw new Error(`OpenAPI fetch failed: ${response.status}`)
const schema = await response.json()
if (!schema.openapi || !schema.paths || !schema.components) throw new Error('Invalid OpenAPI document')
await writeFile(new URL('../openapi.json', import.meta.url), JSON.stringify(schema))
