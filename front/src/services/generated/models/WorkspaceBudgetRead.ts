/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Keep application balances separate from unverified provider bills and runtime permission.
 */
export type WorkspaceBudgetRead = {
    managed: boolean;
    currency?: string;
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

