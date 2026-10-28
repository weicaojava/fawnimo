/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { DnaDocument_Input } from './DnaDocument_Input';
/**
 * Register a source-bound draft or ask the server to generate its exact cut template.
 */
export type DnaCreate = {
    analysis_id: string;
    expected_analysis_revision: number;
    request_key: string;
    document?: (DnaDocument_Input | null);
};

