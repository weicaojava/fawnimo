/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Distinguish the application ledger from unverified provider billing.
 */
export type ProjectBudgetRead = {
    project_id: string;
    exists: boolean;
    currency: string;
    limit_minor: number;
    reserved_minor: number;
    spent_minor: number;
    available_minor: number;
    revision: number;
    unsettled_jobs: number;
    uncertain_jobs: number;
    provider_bill_verified?: boolean;
    coverage?: string;
};

