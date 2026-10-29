/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Expose one project's amount/state without remote identifiers, prompts or credentials.
 */
export type ProjectBudgetEntryRead = {
    job_id: string;
    project_id: string;
    state: 'reserved' | 'unknown' | 'released' | 'settled';
    quote_minor: number;
    actual_minor: (number | null);
};

