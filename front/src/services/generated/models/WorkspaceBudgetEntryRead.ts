/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Expose ledger identifiers and amounts without remote IDs, prompts, URLs or credentials.
 */
export type WorkspaceBudgetEntryRead = {
    job_id: string;
    project_id: string;
    project_name: (string | null);
    state: 'reserved' | 'unknown' | 'released' | 'settled';
    quote_minor: number;
    actual_minor: (number | null);
};

