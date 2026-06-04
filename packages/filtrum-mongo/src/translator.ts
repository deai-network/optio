import type { Filter, Document } from 'mongodb'
import { compile } from 'filtrum-core'
import { makeMongoDialect, type MongoOpHandler } from './dialect'

export interface CreateMongoFilterTranslatorOptions {
  fieldPrefix?: string
  ops?: Record<string, MongoOpHandler>
}

export function createMongoFilterTranslator(
  options: CreateMongoFilterTranslatorOptions = {},
): (filter: unknown) => Filter<Document> {
  const { fieldPrefix = '', ops = {} } = options
  const dialect = makeMongoDialect(ops)
  return (filter: unknown) => compile(filter, dialect, { fieldPrefix })
}
