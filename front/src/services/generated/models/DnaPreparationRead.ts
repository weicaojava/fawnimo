/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaPreparationPayload } from './DnaPreparationPayload';
/**
 * Snapshot identity is an envelope; importing equal DNA preserves the semantic preparation hash.
 */
export type DnaPreparationRead = {
    snapshot_id: string;
    dna_content_hash: string;
    compiler_version: string;
    content_hash: string;
    payload: DnaPreparationPayload;
};

