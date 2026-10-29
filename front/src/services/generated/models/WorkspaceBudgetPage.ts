/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkspaceBudgetEntryRead } from './WorkspaceBudgetEntryRead';
/**
 * Bound the response and provide a stable job-ID cursor for subsequent pages.
 */
export type WorkspaceBudgetPage = {
    items: Array<WorkspaceBudgetEntryRead>;
    next_after: (string | null);
};

