/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BenchmarkDocument } from './BenchmarkDocument';
/**
 * Import JSON as inert draft content against an owned completed analysis.
 */
export type DraftCreate = {
    analysis_id: string;
    document: BenchmarkDocument;
    request_key: string;
};

