export interface Chunk {
  fileName: string
  isEntry: boolean
  facadeModuleId: string | null
  moduleIds: string[]
  imports: string[]
  dynamicImports: string[]
}
export type ChunkGraph = Chunk[]
export interface Violation {
  module: string
  chunk: string
  chain: string[]
}
export const PARENT_ENTRY: string
export function findViolations(graph: ChunkGraph): Violation[]
