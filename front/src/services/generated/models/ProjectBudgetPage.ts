/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ProjectBudgetEntryRead } from './ProjectBudgetEntryRead';
/**
 * Bound the current project's ledger and paginate by immutable job identity.
 */
export type ProjectBudgetPage = {
    items: Array<ProjectBudgetEntryRead>;
    next_after: (string | null);
};

