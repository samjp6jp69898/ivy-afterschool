export interface ManifestChunk {
  file?: string
  src?: string
  name?: string
  isEntry?: boolean
  imports?: string[]
  dynamicImports?: string[]
}
export interface Violation {
  chunk: string
  chain: string[]
}
export const PARENT_ENTRY: string
export function findViolations(manifest: Record<string, ManifestChunk>): Violation[]
