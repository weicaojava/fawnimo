/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BenchmarkDocument } from './BenchmarkDocument';
/**
 * Replace a draft only at the version the reviewer actually saw.
 */
export type DraftPatch = {
    expected_revision: number;
    document: BenchmarkDocument;
};

