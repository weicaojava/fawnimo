/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaPreparedShot } from './DnaPreparedShot';
/**
 * Deterministic semantic body excludes random snapshot identity and all runtime configuration.
 */
export type DnaPreparationPayload = {
    schema_version?: number;
    compiler_version?: string;
    dna_content_hash: string;
    source: Record<string, any>;
    asset_versions: Array<Record<string, any>>;
    entries: Array<DnaPreparedShot>;
    execution_enabled?: boolean;
    model_qualified?: boolean;
    original_author_prompt?: boolean;
    prompt_provenance?: string;
    human_declarations_only?: boolean;
};

